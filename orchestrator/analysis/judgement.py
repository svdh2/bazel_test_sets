"""Separate judgement execution for test verdict re-evaluation.

Executes judgement targets with stored measurement files, enabling
retroactive re-evaluation of test results without re-running the full test.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.analysis.log_parser import parse_test_output
from orchestrator.analysis.measurements import load_measurements


@dataclass
class JudgementResult:
    """Result of executing a judgement target."""

    status: str  # "passed", "failed", "skipped", "judgement_error"
    reason: str  # Human-readable reason for the status
    measurements_file: str | None  # Path to measurements file used
    judgement_output: dict[str, Any] | None  # Parsed structured log output
    exit_code: int | None  # Process exit code (None if not executed)


def execute_judgement(
    test_name: str,
    manifest: dict[str, Any],
    measurements_dir: str | Path,
    timeout: float = 60.0,
) -> JudgementResult:
    """Execute a judgement target with stored measurements.

    Looks up the judgement_executable for the test in the manifest,
    loads stored measurements, and runs the judgement target with the
    measurement file path as its sole argument.

    Args:
        test_name: Name of the test to re-judge.
        manifest: Parsed manifest dict with test_set_tests.
        measurements_dir: Directory containing stored measurement files.
        timeout: Maximum time in seconds for judgement execution.

    Returns:
        JudgementResult with status and details.
    """
    test_set_tests = manifest.get("test_set_tests", {})
    test_data = test_set_tests.get(test_name)

    if test_data is None:
        return JudgementResult(
            status="skipped",
            reason=f"test '{test_name}' not found in manifest",
            measurements_file=None,
            judgement_output=None,
            exit_code=None,
        )

    judgement_executable = test_data.get("judgement_executable")
    if judgement_executable is None:
        return JudgementResult(
            status="skipped",
            reason="no judgement target configured",
            measurements_file=None,
            judgement_output=None,
            exit_code=None,
        )

    # Load stored measurements
    loaded = load_measurements(test_name, measurements_dir)
    if loaded is None:
        return JudgementResult(
            status="skipped",
            reason="no stored measurements available",
            measurements_file=None,
            judgement_output=None,
            exit_code=None,
        )

    # Find the measurement file path
    measurements_dir_path = Path(measurements_dir)
    from orchestrator.analysis.measurements import _label_to_filename
    measurement_file = str(
        measurements_dir_path / (_label_to_filename(test_name) + ".json")
    )

    # Execute judgement target
    try:
        proc = subprocess.run(
            [judgement_executable, measurement_file],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return JudgementResult(
            status="judgement_error",
            reason=f"judgement executable not found: {judgement_executable}",
            measurements_file=measurement_file,
            judgement_output=None,
            exit_code=None,
        )
    except subprocess.TimeoutExpired:
        return JudgementResult(
            status="judgement_error",
            reason=f"judgement timed out after {timeout}s",
            measurements_file=measurement_file,
            judgement_output=None,
            exit_code=None,
        )

    # Parse structured log output from judgement
    judgement_output = parse_test_output(proc.stdout)

    status = "passed" if proc.returncode == 0 else "failed"

    return JudgementResult(
        status=status,
        reason="" if status == "passed" else f"exit code {proc.returncode}",
        measurements_file=measurement_file,
        judgement_output=judgement_output,
        exit_code=proc.returncode,
    )


def find_rejudgeable_tests(
    manifest: dict[str, Any],
    measurements_dir: str | Path,
) -> list[str]:
    """Find tests that can be re-judged.

    A test is re-judgeable if it has both a judgement_executable in the
    manifest and stored measurements in the measurements directory.

    Args:
        manifest: Parsed manifest dict.
        measurements_dir: Directory containing stored measurement files.

    Returns:
        List of test names that can be re-judged.
    """
    test_set_tests = manifest.get("test_set_tests", {})
    eligible: list[str] = []

    for test_name, test_data in test_set_tests.items():
        if test_data.get("judgement_executable") is None:
            continue
        loaded = load_measurements(test_name, measurements_dir)
        if loaded is not None:
            eligible.append(test_name)

    return eligible
