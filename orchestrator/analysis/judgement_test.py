"""Tests for separate judgement execution."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path

import pytest

from orchestrator.analysis.judgement import (
    JudgementResult,
    execute_judgement,
    find_rejudgeable_tests,
)
from orchestrator.analysis.measurements import store_measurements


def _make_judgement_script(tmpdir: str, name: str, exit_code: int = 0,
                           stdout: str = "") -> str:
    """Create a simple judgement script that exits with given code."""
    script_path = Path(tmpdir) / name
    script_content = f"""#!/bin/bash
MEASUREMENT_FILE="$1"
if [ ! -f "$MEASUREMENT_FILE" ]; then
    echo "Error: measurement file not found: $MEASUREMENT_FILE" >&2
    exit 1
fi
{f'echo {repr(stdout)}' if stdout else ''}
exit {exit_code}
"""
    script_path.write_text(script_content)
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return str(script_path)


SAMPLE_MANIFEST_BASE = {
    "test_set": {"name": "tests", "assertion": "Tests work"},
    "test_set_tests": {},
}


def _manifest_with_tests(**test_defs) -> dict:
    """Create a manifest with the given test definitions."""
    manifest = {
        "test_set": {"name": "tests", "assertion": "Tests work"},
        "test_set_tests": test_defs,
    }
    return manifest


class TestExecuteJudgementPass:
    """Tests for successful judgement execution."""

    def test_judgement_passes(self):
        """Judgement target exits 0 = passed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = _make_judgement_script(tmpdir, "judge.sh", exit_code=0)
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": script,
                }
            )

            result = execute_judgement("test_a", manifest, measurements_dir)
            assert result.status == "passed"
            assert result.exit_code == 0
            assert result.measurements_file is not None


class TestExecuteJudgementFail:
    """Tests for failed judgement execution."""

    def test_judgement_fails(self):
        """Judgement target exits non-zero = failed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = _make_judgement_script(tmpdir, "judge.sh", exit_code=1)
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": script,
                }
            )

            result = execute_judgement("test_a", manifest, measurements_dir)
            assert result.status == "failed"
            assert result.exit_code == 1


class TestExecuteJudgementSkip:
    """Tests for skipped judgement execution."""

    def test_skip_no_judgement_target(self):
        """Test without judgement_executable is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                }
            )

            result = execute_judgement("test_a", manifest, measurements_dir)
            assert result.status == "skipped"
            assert "no judgement target" in result.reason

    def test_skip_no_stored_measurements(self):
        """Test without stored measurements is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            script = _make_judgement_script(tmpdir, "judge.sh")
            measurements_dir = Path(tmpdir) / "measurements"
            measurements_dir.mkdir()

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": script,
                }
            )

            result = execute_judgement("test_a", manifest, measurements_dir)
            assert result.status == "skipped"
            assert "no stored measurements" in result.reason

    def test_skip_test_not_in_manifest(self):
        """Test not in manifest is skipped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_dir = Path(tmpdir) / "measurements"
            manifest = _manifest_with_tests()

            result = execute_judgement("nonexistent", manifest, measurements_dir)
            assert result.status == "skipped"
            assert "not found" in result.reason


class TestExecuteJudgementError:
    """Tests for judgement execution errors."""

    def test_missing_executable(self):
        """Non-existent judgement executable produces judgement_error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": "/nonexistent/judge",
                }
            )

            result = execute_judgement("test_a", manifest, measurements_dir)
            assert result.status == "judgement_error"
            assert "not found" in result.reason

    def test_timeout(self):
        """Judgement that exceeds timeout produces judgement_error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a slow script
            script_path = Path(tmpdir) / "slow_judge.sh"
            script_path.write_text("#!/bin/bash\nsleep 60\n")
            script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": str(script_path),
                }
            )

            result = execute_judgement(
                "test_a", manifest, measurements_dir, timeout=0.1
            )
            assert result.status == "judgement_error"
            assert "timed out" in result.reason


class TestJudgementStructuredOutput:
    """Tests for structured log parsing from judgement output."""

    def test_judgement_output_parsed(self):
        """Judgement stdout is parsed for structured log events."""
        with tempfile.TemporaryDirectory() as tmpdir:
            stdout_line = '[TST] {"type": "result", "status": "pass", "message": "within threshold"}'
            script = _make_judgement_script(
                tmpdir, "judge.sh", exit_code=0, stdout=stdout_line
            )
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test assertion",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": script,
                }
            )

            result = execute_judgement("test_a", manifest, measurements_dir)
            assert result.judgement_output is not None
            assert len(result.judgement_output["results"]) >= 0


class TestFindRejudgeableTests:
    """Tests for finding re-judgeable tests."""

    def test_find_eligible(self):
        """Find tests with both judgement target and measurements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": "/bin/judge",
                },
                test_b={
                    "assertion": "test",
                    "executable": "/bin/test",
                    "depends_on": [],
                },
            )

            eligible = find_rejudgeable_tests(manifest, measurements_dir)
            assert "test_a" in eligible
            assert "test_b" not in eligible

    def test_no_measurements(self):
        """Test with judgement but no measurements is not eligible."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_dir = Path(tmpdir) / "measurements"
            measurements_dir.mkdir()

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test",
                    "executable": "/bin/test",
                    "depends_on": [],
                    "judgement_executable": "/bin/judge",
                }
            )

            eligible = find_rejudgeable_tests(manifest, measurements_dir)
            assert eligible == []

    def test_no_judgement_target(self):
        """Test without judgement is not eligible even with measurements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_dir = Path(tmpdir) / "measurements"
            store_measurements("test_a", [{"name": "x", "value": 1}], measurements_dir)

            manifest = _manifest_with_tests(
                test_a={
                    "assertion": "test",
                    "executable": "/bin/test",
                    "depends_on": [],
                }
            )

            eligible = find_rejudgeable_tests(manifest, measurements_dir)
            assert eligible == []

    def test_empty_manifest(self):
        """Empty manifest returns no eligible tests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            eligible = find_rejudgeable_tests({"test_set_tests": {}}, tmpdir)
            assert eligible == []
