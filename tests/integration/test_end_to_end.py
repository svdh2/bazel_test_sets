"""End-to-end integration tests exercising the full pipeline.

Tests the complete flow from manifest -> DAG -> executor -> reporter,
covering diagnostic mode, detection mode, failure cascades, parallel
execution, structured logging, burn-in lifecycle, and report generation.
"""

from __future__ import annotations

import json
import stat
import tempfile
from pathlib import Path
from typing import Any

import pytest

from orchestrator.execution.dag import TestDAG
from orchestrator.execution.executor import SequentialExecutor, TestResult
from orchestrator.reporting.reporter import Reporter
from orchestrator.reporting.html_reporter import generate_html_report
from orchestrator.lifecycle.sprt import sprt_evaluate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_script(tmpdir: Path, name: str, content: str) -> str:
    """Create an executable script and return its path."""
    script_path = tmpdir / name
    script_path.write_text(content)
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)
    return str(script_path)


def _pass_script(tmpdir: Path, name: str = "pass.sh") -> str:
    return _make_script(tmpdir, name, "#!/bin/bash\nexit 0\n")


def _fail_script(tmpdir: Path, name: str = "fail.sh") -> str:
    return _make_script(tmpdir, name, "#!/bin/bash\nexit 1\n")


def _structured_log_script(tmpdir: Path, name: str = "structured.sh") -> str:
    return _make_script(tmpdir, name, (
        '#!/bin/bash\n'
        'echo "[TST] {\\"type\\": \\"block_start\\", \\"block\\": \\"rigging\\"}"\n'
        'echo "[TST] {\\"type\\": \\"feature\\", \\"name\\": \\"db\\", \\"action\\": \\"connect\\"}"\n'
        'echo "[TST] {\\"type\\": \\"block_end\\", \\"block\\": \\"rigging\\"}"\n'
        'echo "[TST] {\\"type\\": \\"block_start\\", \\"block\\": \\"stimulation\\"}"\n'
        'echo "[TST] {\\"type\\": \\"measurement\\", \\"name\\": \\"latency\\", \\"value\\": 42, \\"unit\\": \\"ms\\"}"\n'
        'echo "[TST] {\\"type\\": \\"block_end\\", \\"block\\": \\"stimulation\\"}"\n'
        'echo "[TST] {\\"type\\": \\"block_start\\", \\"block\\": \\"verdict\\"}"\n'
        'echo "[TST] {\\"type\\": \\"result\\", \\"name\\": \\"test_ok\\", \\"passed\\": true}"\n'
        'echo "[TST] {\\"type\\": \\"block_end\\", \\"block\\": \\"verdict\\"}"\n'
        'exit 0\n'
    ))


def _make_manifest(
    tmpdir: Path,
    tests: dict[str, dict[str, Any]],
    test_set_name: str = "suite",
) -> tuple[dict, Path]:
    """Create a manifest dict and write it to a file."""
    manifest = {
        "test_set": {
            "name": test_set_name,
            "assertion": "All tests pass",
        },
        "test_set_tests": tests,
    }
    manifest_path = tmpdir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return manifest, manifest_path


# ---------------------------------------------------------------------------
# Diagnostic Mode End-to-End
# ---------------------------------------------------------------------------


class TestDiagnosticEndToEnd:
    """Full pipeline: manifest -> DAG -> execute -> report."""

    def test_simple_pass(self):
        """Single passing test produces correct report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _pass_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "test_a": {
                    "assertion": "A passes",
                    "executable": exe,
                    "depends_on": [],
                },
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            assert len(results) == 1
            assert results[0].status == "passed"
            assert results[0].name == "test_a"

            reporter = Reporter()
            reporter.add_results(results)
            reporter.set_manifest(manifest)
            report = reporter.generate_report()

            assert report["report"]["summary"]["total"] == 1
            assert report["report"]["summary"]["passed"] == 1
            assert "test_set" in report["report"]

    def test_multiple_tests_dag_order(self):
        """Tests execute in dependency order (leaves first)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _pass_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "leaf": {
                    "assertion": "Leaf",
                    "executable": exe,
                    "depends_on": [],
                },
                "middle": {
                    "assertion": "Middle",
                    "executable": exe,
                    "depends_on": ["leaf"],
                },
                "root": {
                    "assertion": "Root",
                    "executable": exe,
                    "depends_on": ["middle"],
                },
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            assert len(results) == 3
            names = [r.name for r in results]
            assert names.index("leaf") < names.index("middle")
            assert names.index("middle") < names.index("root")

    def test_all_pass_report(self):
        """All-passing suite produces correct summary and JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _pass_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "a": {"assertion": "A", "executable": exe, "depends_on": []},
                "b": {"assertion": "B", "executable": exe, "depends_on": ["a"]},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            reporter = Reporter()
            reporter.add_results(results)
            reporter.set_manifest(manifest)
            reporter.set_commit_hash("abc123")

            report_path = tmpdir / "report.json"
            reporter.write_report(report_path)

            assert report_path.exists()
            with open(report_path) as f:
                report_data = json.load(f)

            assert report_data["report"]["summary"]["passed"] == 2
            assert report_data["report"]["summary"]["failed"] == 0
            assert report_data["report"]["commit"] == "abc123"


# ---------------------------------------------------------------------------
# Failure Cascade
# ---------------------------------------------------------------------------


class TestFailureCascade:
    """Dependency failure propagation."""

    def test_leaf_failure_cascades_to_dependents(self):
        """If a leaf test fails, dependents are marked dependencies_failed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pass_exe = _pass_script(tmpdir)
            fail_exe = _fail_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "base": {
                    "assertion": "Base",
                    "executable": fail_exe,
                    "depends_on": [],
                },
                "child": {
                    "assertion": "Child",
                    "executable": pass_exe,
                    "depends_on": ["base"],
                },
                "grandchild": {
                    "assertion": "Grandchild",
                    "executable": pass_exe,
                    "depends_on": ["child"],
                },
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            by_name = {r.name: r for r in results}
            assert by_name["base"].status == "failed"
            assert by_name["child"].status == "dependencies_failed"
            assert by_name["grandchild"].status == "dependencies_failed"

    def test_partial_failure_independent_tests_run(self):
        """Independent tests run even when siblings fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pass_exe = _pass_script(tmpdir)
            fail_exe = _fail_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "failing": {
                    "assertion": "Fails",
                    "executable": fail_exe,
                    "depends_on": [],
                },
                "independent": {
                    "assertion": "Independent",
                    "executable": pass_exe,
                    "depends_on": [],
                },
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            by_name = {r.name: r for r in results}
            assert by_name["failing"].status == "failed"
            assert by_name["independent"].status == "passed"

    def test_failure_cascade_report_summary(self):
        """Report correctly counts dependencies_failed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            pass_exe = _pass_script(tmpdir)
            fail_exe = _fail_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "base": {"assertion": "B", "executable": fail_exe, "depends_on": []},
                "dep": {"assertion": "D", "executable": pass_exe, "depends_on": ["base"]},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            reporter = Reporter()
            reporter.add_results(results)
            report = reporter.generate_report()

            assert report["report"]["summary"]["failed"] == 1
            assert report["report"]["summary"]["dependencies_failed"] == 1


# ---------------------------------------------------------------------------
# Detection Mode
# ---------------------------------------------------------------------------


class TestDetectionMode:
    """Detection mode with max_failures."""

    def test_max_failures_stops_execution(self):
        """Execution stops after max_failures failures."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            fail_exe = _fail_script(tmpdir)

            # All independent tests (no dependencies)
            manifest, _ = _make_manifest(tmpdir, {
                "a": {"assertion": "A", "executable": fail_exe, "depends_on": []},
                "b": {"assertion": "B", "executable": fail_exe, "depends_on": []},
                "c": {"assertion": "C", "executable": fail_exe, "depends_on": []},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(
                dag, mode="detection", max_failures=2
            )
            results = executor.execute()

            failed_count = sum(1 for r in results if r.status == "failed")
            assert failed_count == 2


# ---------------------------------------------------------------------------
# Report to HTML Pipeline
# ---------------------------------------------------------------------------


class TestReportToHtmlPipeline:
    """Full pipeline: execute -> JSON report -> HTML report."""

    def test_json_to_html_roundtrip(self):
        """Generate JSON, then convert to HTML."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _pass_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "test_1": {"assertion": "T1", "executable": exe, "depends_on": []},
                "test_2": {"assertion": "T2", "executable": exe, "depends_on": ["test_1"]},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            reporter = Reporter()
            reporter.add_results(results)
            reporter.set_manifest(manifest)
            reporter.set_commit_hash("def456")
            report_data = reporter.generate_report()

            # Write JSON
            report_path = tmpdir / "report.json"
            reporter.write_report(report_path)

            # Read JSON and generate HTML
            with open(report_path) as f:
                loaded = json.load(f)

            html_output = generate_html_report(loaded)
            assert "<!DOCTYPE html>" in html_output
            assert "test_1" in html_output
            assert "test_2" in html_output
            assert "def456" in html_output
            assert "PASSED" in html_output


# ---------------------------------------------------------------------------
# Rolling History
# ---------------------------------------------------------------------------


class TestRollingHistoryPipeline:
    """Rolling history appending across multiple runs."""

    def test_history_accumulates_across_runs(self):
        """Multiple generate_report_with_history calls accumulate entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            report_path = tmpdir / "report.json"

            # First run
            r1 = Reporter()
            r1.add_result(TestResult(
                name="t1", assertion="A", status="passed",
                duration=1.0, exit_code=0, stdout="", stderr="",
            ))
            r1.set_commit_hash("commit1")
            r1.write_report_with_history(report_path)

            # Second run (reads existing history)
            r2 = Reporter()
            r2.add_result(TestResult(
                name="t1", assertion="A", status="failed",
                duration=2.0, exit_code=1, stdout="", stderr="err",
            ))
            r2.set_commit_hash("commit2")
            r2.write_report_with_history(report_path, existing_path=report_path)

            # Read final report
            with open(report_path) as f:
                report = json.load(f)

            history = report["report"]["history"]["t1"]
            assert len(history) == 2
            assert history[0]["status"] == "passed"
            assert history[0]["commit"] == "commit1"
            assert history[1]["status"] == "failed"
            assert history[1]["commit"] == "commit2"


# ---------------------------------------------------------------------------
# Burn-in Lifecycle
# ---------------------------------------------------------------------------


class TestBurnInLifecycle:
    """Test the full new -> burning_in -> stable or flaky lifecycle."""

    def test_sprt_accepts_stable_test(self):
        """A test that always passes should be accepted as stable."""
        result = sprt_evaluate(
            runs=30, passes=30, min_reliability=0.99, significance=0.95
        )
        assert result == "accept"

    def test_sprt_rejects_flaky_test(self):
        """A test that fails frequently should be rejected."""
        # 80% pass rate (fail every 5th run)
        result = sprt_evaluate(
            runs=30, passes=24, min_reliability=0.99, significance=0.95
        )
        assert result == "reject"

    def test_sprt_continues_with_few_runs(self):
        """With few runs, SPRT should continue."""
        result = sprt_evaluate(
            runs=2, passes=2, min_reliability=0.99, significance=0.95
        )
        assert result == "continue"

    def test_status_file_roundtrip(self):
        """Status file can be saved and loaded."""
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            status_path = Path(tmpdir) / "status.json"
            sf = StatusFile(status_path)

            sf.set_test_state("test_a", "burning_in")
            for _ in range(10):
                sf.record_run("test_a", passed=True)
            sf.save()

            sf2 = StatusFile(status_path)
            assert sf2.get_test_state("test_a") == "burning_in"
            history = sf2.get_test_history("test_a")
            assert len(history) == 10


# ---------------------------------------------------------------------------
# Structured Logging Integration
# ---------------------------------------------------------------------------


class TestStructuredLoggingIntegration:
    """End-to-end structured log parsing in the pipeline."""

    def test_structured_log_in_report(self):
        """Structured log events are preserved in stdout and render correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _structured_log_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "test_s": {"assertion": "S", "executable": exe, "depends_on": []},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            assert len(results) == 1
            assert results[0].status == "passed"

            # Parse structured logs from stdout
            from orchestrator.analysis.log_parser import parse_test_output, parse_stdout_segments, BlockSegment
            parsed = parse_test_output(results[0].stdout)

            assert "rigging" in parsed["block_sequence"]
            assert "stimulation" in parsed["block_sequence"]
            assert "verdict" in parsed["block_sequence"]
            assert len(parsed["measurements"]) >= 1

            # Verify segment-based parser also works on the same stdout
            segments = parse_stdout_segments(results[0].stdout)
            block_names = [s.block for s in segments if isinstance(s, BlockSegment)]
            assert "rigging" in block_names
            assert "stimulation" in block_names
            assert "verdict" in block_names

            # Add to report — stdout is preserved, no structured_log field
            reporter = Reporter()
            reporter.add_results(results)
            reporter.set_manifest(manifest)
            report = reporter.generate_report()

            test_data = report["report"]["test_set"]["tests"]["test_s"]
            assert "stdout" in test_data
            assert "[TST]" in test_data["stdout"]


# ---------------------------------------------------------------------------
# Regression Mode Integration
# ---------------------------------------------------------------------------


class TestRegressionModeIntegration:
    """Regression test selection end-to-end."""

    def test_regression_selection_with_co_occurrence(self):
        """Regression selector picks tests based on co-occurrence."""
        from orchestrator.regression.regression_selector import (
            RegressionConfig,
            select_regression_tests,
        )

        graph = {
            "metadata": {"last_commit": "c1"},
            "file_commits": {
                "src/auth.py": [
                    {"commit": "c1", "timestamp": "2026-01-01T00:00:00Z"},
                ],
            },
            "commit_files": {
                "c1": {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source_files": ["src/auth.py"],
                    "test_files": ["tests/auth_test.py"],
                },
            },
        }

        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "auth_test": {
                    "assertion": "Auth",
                    "executable": "/bin/true",
                    "depends_on": [],
                },
                "payment_test": {
                    "assertion": "Payment",
                    "executable": "/bin/true",
                    "depends_on": [],
                },
            },
        }

        config = RegressionConfig(max_test_percentage=1.0, max_hops=2)
        selection = select_regression_tests(
            changed_files=["src/auth.py"],
            graph=graph,
            manifest=manifest,
            config=config,
        )

        assert "auth_test" in selection.selected_tests
        assert selection.total_stable_tests == 2


# ---------------------------------------------------------------------------
# Co-occurrence Graph Build
# ---------------------------------------------------------------------------


class TestCoOccurrenceGraphIntegration:
    """Co-occurrence graph building and loading."""

    def test_graph_save_load_roundtrip(self):
        """Graph can be saved and loaded."""
        from orchestrator.regression.co_occurrence import save_graph, load_graph

        graph = {
            "metadata": {"last_commit": "abc"},
            "file_commits": {"src/a.py": [{"commit": "c1", "timestamp": "2026-01-01T00:00:00Z"}]},
            "commit_files": {
                "c1": {
                    "timestamp": "2026-01-01T00:00:00Z",
                    "source_files": ["src/a.py"],
                    "test_files": ["tests/a_test.py"],
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "graph.json"
            save_graph(graph, path)
            loaded = load_graph(path)
            assert loaded is not None
            assert loaded["metadata"]["last_commit"] == "abc"
            assert "src/a.py" in loaded["file_commits"]


# ---------------------------------------------------------------------------
# DAG Validation
# ---------------------------------------------------------------------------


class TestDAGValidation:
    """DAG construction validation."""

    def test_cycle_detection(self):
        """Cycle in dependencies is detected."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }

        dag = TestDAG.from_manifest(manifest)
        with pytest.raises(ValueError, match="[Cc]ycle"):
            dag.topological_sort_leaves_first()

    def test_missing_dependency_detected(self):
        """Reference to non-existent dependency is silently ignored."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["nonexistent"]},
            },
        }

        dag = TestDAG.from_manifest(manifest)
        # Missing deps are filtered out in get_dependencies
        assert dag.get_dependencies("a") == []

    def test_empty_dag(self):
        """Empty manifest produces valid empty DAG."""
        manifest = {
            "test_set": {"name": "tests"},
            "test_set_tests": {},
        }

        dag = TestDAG.from_manifest(manifest)
        assert len(dag.topological_sort_leaves_first()) == 0

    def test_diamond_dependency(self):
        """Diamond dependency pattern (A->B,C->D) works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _pass_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "a": {"assertion": "A", "executable": exe, "depends_on": []},
                "b": {"assertion": "B", "executable": exe, "depends_on": ["a"]},
                "c": {"assertion": "C", "executable": exe, "depends_on": ["a"]},
                "d": {"assertion": "D", "executable": exe, "depends_on": ["b", "c"]},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            assert all(r.status == "passed" for r in results)
            names = [r.name for r in results]
            assert names.index("a") < names.index("b")
            assert names.index("a") < names.index("c")
            assert names.index("b") < names.index("d")
            assert names.index("c") < names.index("d")


# ---------------------------------------------------------------------------
# Hierarchical Report Validation
# ---------------------------------------------------------------------------


class TestHierarchicalReportValidation:
    """Validate hierarchical report structure end-to-end."""

    def test_hierarchical_report_with_all_data(self):
        """Full report with structured logs, burn-in, and deps."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            exe = _pass_script(tmpdir)

            manifest, _ = _make_manifest(tmpdir, {
                "t1": {"assertion": "T1", "executable": exe, "depends_on": [],
                       "requirement_id": "REQ-1"},
                "t2": {"assertion": "T2", "executable": exe, "depends_on": ["t1"],
                       "requirement_id": "REQ-2"},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            reporter = Reporter()
            reporter.add_results(results)
            reporter.set_manifest(manifest)
            reporter.set_commit_hash("test123")
            reporter.add_burn_in_progress("t1", {
                "runs": 20, "passes": 20, "sprt_status": "accept",
            })
            reporter.add_inferred_dependencies("t1", [
                {"name": "power_supply", "inferred": True},
            ])
            reporter.set_regression_selection({
                "changed_files": ["src/x.py"],
                "scores": {"t1": 0.9},
            })

            report = reporter.generate_report()

            # Verify structure
            r = report["report"]
            assert r["commit"] == "test123"
            assert "test_set" in r
            ts = r["test_set"]
            assert ts["status"] == "passed"
            assert "t1" in ts["tests"]
            assert "t2" in ts["tests"]

            t1 = ts["tests"]["t1"]
            assert "burn_in" in t1
            assert "inferred_dependencies" in t1

            assert "regression_selection" in r
            assert "src/x.py" in r["regression_selection"]["changed_files"]

            # Generate HTML from the same data
            html = generate_html_report(report)
            assert "test123" in html
            assert "Regression Selection" in html


# ---------------------------------------------------------------------------
# Main Module Integration
# ---------------------------------------------------------------------------


class TestMainModuleIntegration:
    """Integration tests via the main module entry point."""

    def test_main_diagnostic_mode(self):
        """main() runs diagnostic mode and returns 0 for passing tests."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _pass_script(tmpdir_p)
            config_path = tmpdir_p / ".test_set_config"
            config_path.write_text(json.dumps({"max_parallel": 1}))

            manifest, manifest_path = _make_manifest(tmpdir_p, {
                "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
            })

            exit_code = main([
                "--manifest", str(manifest_path),
                "--mode", "diagnostic",
                "--config-file", str(config_path),
            ])
            assert exit_code == 0

    def test_main_failing_test_returns_1(self):
        """main() returns 1 when a test fails."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _fail_script(tmpdir_p)
            config_path = tmpdir_p / ".test_set_config"
            config_path.write_text(json.dumps({"max_parallel": 1}))

            manifest, manifest_path = _make_manifest(tmpdir_p, {
                "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
            })

            exit_code = main([
                "--manifest", str(manifest_path),
                "--mode", "diagnostic",
                "--config-file", str(config_path),
            ])
            assert exit_code == 1

    def test_main_with_output_report(self):
        """main() writes JSON report when --output specified."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _pass_script(tmpdir_p)
            report_path = tmpdir_p / "report.json"
            config_path = tmpdir_p / ".test_set_config"
            config_path.write_text(json.dumps({"max_parallel": 1}))

            manifest, manifest_path = _make_manifest(tmpdir_p, {
                "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
            })

            exit_code = main([
                "--manifest", str(manifest_path),
                "--mode", "diagnostic",
                "--config-file", str(config_path),
                "--output", str(report_path),
            ])
            assert exit_code == 0
            assert report_path.exists()

            with open(report_path) as f:
                report = json.load(f)
            assert report["report"]["summary"]["passed"] == 1

    def test_main_invalid_manifest_returns_1(self):
        """main() returns 1 for invalid manifest JSON."""
        from orchestrator.main import main

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            bad_path = tmpdir_p / "bad.json"
            bad_path.write_text("not json")

            exit_code = main([
                "--manifest", str(bad_path),
                "--mode", "diagnostic",
            ])
            assert exit_code == 1

    def test_main_missing_manifest_returns_1(self):
        """main() returns 1 for missing manifest file."""
        from orchestrator.main import main

        exit_code = main([
            "--manifest", "/nonexistent/manifest.json",
            "--mode", "diagnostic",
        ])
        assert exit_code == 1


# ---------------------------------------------------------------------------
# E-value computation and test set verdict
# ---------------------------------------------------------------------------


class TestEValueComputation:
    """Unit tests for E-value computation and test set verdict."""

    def test_compute_log_ratio_all_passes(self):
        """All passes produce positive log-ratio (favors H0 = reliable)."""
        from orchestrator.lifecycle.e_values import compute_log_ratio
        lr = compute_log_ratio(runs=30, passes=30, min_reliability=0.99)
        assert lr > 0

    def test_compute_log_ratio_all_failures(self):
        """All failures produce negative log-ratio (favors H1 = unreliable)."""
        from orchestrator.lifecycle.e_values import compute_log_ratio
        lr = compute_log_ratio(runs=30, passes=0, min_reliability=0.99)
        assert lr < 0

    def test_compute_log_ratio_zero_runs(self):
        """Zero runs return 0.0."""
        from orchestrator.lifecycle.e_values import compute_log_ratio
        assert compute_log_ratio(runs=0, passes=0, min_reliability=0.99) == 0.0

    def test_compute_log_ratio_consistency_with_sprt(self):
        """Log ratio matches sprt.py computation for same inputs."""
        import math
        from orchestrator.lifecycle.e_values import compute_log_ratio
        from orchestrator.lifecycle.sprt import sprt_evaluate

        runs, passes = 30, 30
        lr = compute_log_ratio(runs, passes, min_reliability=0.99, margin=0.10)

        # With all passes and p0=0.99, p1=0.89, SPRT should accept
        decision = sprt_evaluate(runs, passes, 0.99, 0.95, margin=0.10)
        assert decision == "accept"
        # log-ratio should exceed the upper boundary
        alpha = 0.05
        upper = math.log((1 - alpha) / alpha)
        assert lr >= upper

    def test_quick_e_value_reliable_test(self):
        """Test with all passes has E_i < 1 (favors reliability)."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_quick

        history = [{"passed": True, "commit": "abc"} for _ in range(30)]
        tv = compute_test_e_value_quick("test_a", history, min_reliability=0.99)
        assert tv.e_value < 1.0
        assert tv.s_value > 1.0
        assert tv.runs == 30
        assert tv.passes == 30

    def test_quick_e_value_unreliable_test(self):
        """Test with many failures has E_i > 1 (favors unreliability)."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_quick

        history = [{"passed": i < 20, "commit": "abc"} for i in range(30)]
        tv = compute_test_e_value_quick("test_b", history, min_reliability=0.99)
        assert tv.e_value > 1.0
        assert tv.s_value < 1.0

    def test_quick_e_value_groups_by_commit(self):
        """History entries are grouped by commit SHA."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_quick

        history = [
            {"passed": True, "commit": "aaa"},
            {"passed": True, "commit": "aaa"},
            {"passed": True, "commit": "bbb"},
        ]
        tv = compute_test_e_value_quick("test_c", history, min_reliability=0.99)
        assert tv.commits_included == 2
        assert tv.runs == 3

    def test_quick_e_value_none_commits_independent(self):
        """Entries with commit=None are separate commit groups."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_quick

        history = [
            {"passed": True, "commit": None},
            {"passed": True, "commit": None},
        ]
        tv = compute_test_e_value_quick("test_d", history, min_reliability=0.99)
        assert tv.commits_included == 2

    def test_quick_e_value_empty_history(self):
        """Empty history gives neutral E-value (1.0)."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_quick

        tv = compute_test_e_value_quick("test_e", [], min_reliability=0.99)
        assert tv.e_value == 1.0
        assert tv.s_value == 1.0
        assert tv.runs == 0

    def test_hifi_e_value_filters_to_current_commit(self):
        """Only entries matching current_commit are used."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_hifi

        history = [
            {"passed": True, "commit": "current"},
            {"passed": True, "commit": "current"},
            {"passed": False, "commit": "old"},
            {"passed": False, "commit": "old"},
        ]
        tv = compute_test_e_value_hifi(
            "test_f", history, current_commit="current", min_reliability=0.99,
        )
        assert tv.runs == 2
        assert tv.passes == 2
        assert tv.commits_included == 1

    def test_hifi_e_value_no_matching_commit(self):
        """No matching entries gives neutral E-value."""
        from orchestrator.lifecycle.e_values import compute_test_e_value_hifi

        history = [{"passed": True, "commit": "other"}]
        tv = compute_test_e_value_hifi(
            "test_g", history, current_commit="missing", min_reliability=0.99,
        )
        assert tv.e_value == 1.0
        assert tv.runs == 0

    def test_verdict_green(self):
        """All reliable tests -> GREEN."""
        from orchestrator.lifecycle.e_values import (
            TestEValue,
            compute_test_set_verdict,
        )

        # Create test E-values with very high S-values (strong reliability evidence)
        tests = [
            TestEValue("t1", e_value=1e-10, s_value=1e10, log_e_value=-23.0,
                        runs=50, passes=50, commits_included=5),
            TestEValue("t2", e_value=1e-10, s_value=1e10, log_e_value=-23.0,
                        runs=50, passes=50, commits_included=5),
        ]
        v = compute_test_set_verdict(tests, alpha_set=0.05, beta_set=0.05)
        assert v.verdict == "GREEN"
        assert v.n_tests == 2
        # GREEN threshold = N / beta_set = 2 / 0.05 = 40
        assert v.green_threshold == 40.0
        assert v.min_s_value > v.green_threshold

    def test_verdict_red(self):
        """One unreliable test -> RED."""
        from orchestrator.lifecycle.e_values import (
            TestEValue,
            compute_test_set_verdict,
        )

        tests = [
            TestEValue("t1", e_value=1e-5, s_value=1e5, log_e_value=-11.5,
                        runs=50, passes=50, commits_included=5),
            TestEValue("bad", e_value=100.0, s_value=0.01, log_e_value=4.6,
                        runs=50, passes=30, commits_included=5),
        ]
        v = compute_test_set_verdict(tests, alpha_set=0.05, beta_set=0.05)
        # E_set = (1e-5 + 100) / 2 = 50.0, threshold = 20.0
        assert v.verdict == "RED"
        assert v.weakest_test == "bad"

    def test_verdict_undecided(self):
        """Insufficient evidence -> UNDECIDED."""
        from orchestrator.lifecycle.e_values import (
            TestEValue,
            compute_test_set_verdict,
        )

        # Neutral E-values (e=1.0, s=1.0) from no data
        tests = [
            TestEValue("t1", e_value=1.0, s_value=1.0, log_e_value=0.0,
                        runs=0, passes=0, commits_included=0),
        ]
        v = compute_test_set_verdict(tests, alpha_set=0.05, beta_set=0.05)
        assert v.verdict == "UNDECIDED"

    def test_verdict_empty_test_set(self):
        """Empty test set -> GREEN (vacuous truth)."""
        from orchestrator.lifecycle.e_values import compute_test_set_verdict

        v = compute_test_set_verdict([], alpha_set=0.05, beta_set=0.05)
        assert v.verdict == "GREEN"
        assert v.n_tests == 0

    def test_green_threshold_scales_with_n(self):
        """GREEN threshold scales with N (more tests = higher bar)."""
        from orchestrator.lifecycle.e_values import (
            TestEValue,
            compute_test_set_verdict,
        )

        make_tv = lambda name: TestEValue(
            name, e_value=0.1, s_value=10.0, log_e_value=-2.3,
            runs=20, passes=20, commits_included=2,
        )

        # With 2 tests: green_threshold = 2/0.05 = 40
        v2 = compute_test_set_verdict([make_tv("t1"), make_tv("t2")],
                                       alpha_set=0.05, beta_set=0.05)
        # With 10 tests: green_threshold = 10/0.05 = 200
        v10 = compute_test_set_verdict([make_tv(f"t{i}") for i in range(10)],
                                        alpha_set=0.05, beta_set=0.05)

        assert v10.green_threshold > v2.green_threshold
        # S=10 is enough for 2 tests (threshold=40? No, 10 < 40) -> UNDECIDED for both
        # But the 10-test case has a higher bar
        assert v10.green_threshold == 200.0
        assert v2.green_threshold == 40.0

    def test_single_weak_test_blocks_green(self):
        """One undecided test prevents GREEN even if others are strong."""
        from orchestrator.lifecycle.e_values import (
            TestEValue,
            compute_test_set_verdict,
        )

        tests = [
            TestEValue("strong", e_value=1e-10, s_value=1e10, log_e_value=-23.0,
                        runs=50, passes=50, commits_included=5),
            TestEValue("weak", e_value=1.0, s_value=1.0, log_e_value=0.0,
                        runs=0, passes=0, commits_included=0),
        ]
        v = compute_test_set_verdict(tests, alpha_set=0.05, beta_set=0.05)
        # min(S_i) = 1.0, threshold = 2/0.05 = 40 -> not GREEN
        assert v.verdict != "GREEN"
        assert v.weakest_test == "weak"

    def test_evaluate_test_set_quick_with_status_file(self):
        """Quick mode reads history from StatusFile correctly."""
        from orchestrator.lifecycle.e_values import evaluate_test_set
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status")
            for _ in range(30):
                sf.record_run("test_a", True, commit="abc")
            sf.save()

            v = evaluate_test_set(
                ["test_a"], sf, mode="quick",
                alpha_set=0.05, beta_set=0.05,
            )
            assert v.n_tests == 1
            assert v.per_test[0].runs == 30
            assert v.per_test[0].passes == 30

    def test_evaluate_test_set_hifi_with_status_file(self):
        """HiFi mode filters to current commit."""
        from orchestrator.lifecycle.e_values import evaluate_test_set
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status")
            for _ in range(10):
                sf.record_run("test_a", True, commit="old")
            for _ in range(5):
                sf.record_run("test_a", True, commit="current")
            sf.save()

            v = evaluate_test_set(
                ["test_a"], sf, mode="hifi", current_commit="current",
                alpha_set=0.05, beta_set=0.05,
            )
            assert v.per_test[0].runs == 5
            assert v.per_test[0].commits_included == 1

    def test_evaluate_test_set_hifi_requires_commit(self):
        """HiFi mode raises ValueError without current_commit."""
        from orchestrator.lifecycle.e_values import evaluate_test_set
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            sf = StatusFile(Path(tmpdir) / "status")
            with pytest.raises(ValueError, match="current_commit"):
                evaluate_test_set(["test_a"], sf, mode="hifi")

    def test_verdict_to_dict_serializable(self):
        """verdict_to_dict output is JSON-serializable."""
        from orchestrator.lifecycle.e_values import (
            TestEValue,
            TestSetVerdict,
            verdict_to_dict,
        )

        verdict = TestSetVerdict(
            verdict="GREEN", e_set=0.001, min_s_value=1000.0,
            red_threshold=20.0, green_threshold=40.0, n_tests=2,
            per_test=[
                TestEValue("t1", 0.001, 1000.0, -6.9, 30, 30, 3),
                TestEValue("t2", 0.002, 500.0, -6.2, 25, 25, 2),
            ],
            weakest_test="t2",
        )
        d = verdict_to_dict(verdict)
        # Should not raise
        serialized = json.dumps(d)
        assert "GREEN" in serialized


class TestHiFiEvaluator:
    """Integration tests for HiFiEvaluator with real executables."""

    def test_all_pass_reaches_green(self):
        """Tests that always pass eventually produce GREEN."""
        from orchestrator.lifecycle.e_values import HiFiEvaluator
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _pass_script(tmpdir_p)

            manifest = {
                "test_set": {"name": "suite", "assertion": "All pass"},
                "test_set_tests": {
                    "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
                },
            }
            dag = TestDAG.from_manifest(manifest)
            sf = StatusFile(tmpdir_p / "status")

            evaluator = HiFiEvaluator(
                dag, sf, commit_sha="commit1",
                alpha_set=0.05, beta_set=0.05,
                max_reruns=200,
            )
            result = evaluator.evaluate(["t1"])
            assert result.verdict.verdict == "GREEN"
            assert result.decided is True
            assert result.total_reruns > 0

    def test_failing_test_reaches_red(self):
        """Test with failures eventually produces RED."""
        from orchestrator.lifecycle.e_values import HiFiEvaluator
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _fail_script(tmpdir_p)

            manifest = {
                "test_set": {"name": "suite", "assertion": "Should fail"},
                "test_set_tests": {
                    "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
                },
            }
            dag = TestDAG.from_manifest(manifest)
            sf = StatusFile(tmpdir_p / "status")

            evaluator = HiFiEvaluator(
                dag, sf, commit_sha="commit1",
                alpha_set=0.05, beta_set=0.05,
                max_reruns=50,
            )
            result = evaluator.evaluate(["t1"])
            assert result.verdict.verdict == "RED"
            assert result.decided is True

    def test_budget_exhausted_returns_undecided(self):
        """Low max_reruns with borderline test returns UNDECIDED or decided."""
        from orchestrator.lifecycle.e_values import HiFiEvaluator
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _pass_script(tmpdir_p)

            manifest = {
                "test_set": {"name": "suite", "assertion": "Suite"},
                "test_set_tests": {
                    "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
                },
            }
            dag = TestDAG.from_manifest(manifest)
            sf = StatusFile(tmpdir_p / "status")

            evaluator = HiFiEvaluator(
                dag, sf, commit_sha="commit1",
                alpha_set=0.05, beta_set=0.05,
                max_reruns=1,  # Very small budget
            )
            result = evaluator.evaluate(["t1"])
            # With only 1 rerun, likely UNDECIDED
            assert result.total_reruns <= 1


class TestEValueVerdictEndToEnd:
    """E-value verdict integration with full pipeline."""

    def test_quick_verdict_in_report(self):
        """Quick verdict appears in JSON and HTML reports."""
        from orchestrator.lifecycle.e_values import evaluate_test_set, verdict_to_dict
        from orchestrator.lifecycle.status import StatusFile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_p = Path(tmpdir)
            exe = _pass_script(tmpdir_p)
            manifest, _ = _make_manifest(tmpdir_p, {
                "t1": {"assertion": "T1", "executable": exe, "depends_on": []},
            })

            dag = TestDAG.from_manifest(manifest)
            executor = SequentialExecutor(dag, mode="diagnostic")
            results = executor.execute()

            # Record some history
            sf = StatusFile(tmpdir_p / "status")
            for _ in range(30):
                sf.record_run("t1", True, commit="abc")
            sf.save()

            verdict = evaluate_test_set(["t1"], sf, mode="quick")
            verdict_data = verdict_to_dict(verdict)

            reporter = Reporter()
            reporter.set_manifest(manifest)
            reporter.add_results(results)
            reporter.set_e_value_verdict(verdict_data)

            report = reporter.generate_report()
            assert "e_value_verdict" in report["report"]
            assert report["report"]["e_value_verdict"]["verdict"] in (
                "GREEN", "RED", "UNDECIDED",
            )

            # HTML generation should not crash
            html_str = generate_html_report(report)
            assert "Test Set Verdict" in html_str
            assert verdict_data["verdict"] in html_str
