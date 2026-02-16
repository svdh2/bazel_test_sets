# Component: Judgement

## Location

`orchestrator/analysis/judgement.py`

## Purpose

Executes judgement targets with stored measurement files, enabling retroactive re-evaluation of test results without re-running the full test. A judgement target is a separate executable that receives a measurement file path as its argument and emits structured log events to indicate pass/fail.

## Interface

### JudgementResult (dataclass)

```python
@dataclass
class JudgementResult:
    status: str                 # "passed", "failed", "skipped", "judgement_error"
    reason: str                 # Human-readable reason
    measurements_file: str | None
    judgement_output: dict | None   # Parsed structured log output
    exit_code: int | None
```

### execute_judgement

```python
def execute_judgement(
    test_name: str,
    manifest: dict,
    measurements_dir: str | Path,
    timeout: float = 60.0,
) -> JudgementResult
```

Skips (with reason) if: test not in manifest, no judgement_executable configured, or no stored measurements available.

### find_rejudgeable_tests

```python
def find_rejudgeable_tests(manifest: dict, measurements_dir) -> list[str]
```

Returns tests that have both a judgement_executable and stored measurements.

## Dependencies

- **Log Parser** (`orchestrator.analysis.log_parser`): Parses structured output from judgement executables
- **Measurements** (`orchestrator.analysis.measurements`): Loads stored measurement files

## Dependents

- **CI Tool** (`re-judge` subcommand): Invokes `execute_judgement` and `find_rejudgeable_tests`

## Key Design Decisions

1. **Separation of execution and judgement**: Tests produce measurements during execution. Judgement targets consume those measurements to produce verdicts. This allows changing pass/fail criteria without re-running expensive tests.

2. **Graceful skip semantics**: Missing manifest entries, unconfigured judgement targets, and missing measurements all produce `"skipped"` results with descriptive reasons rather than errors.

3. **Timeout enforcement**: Judgement targets have a configurable timeout (default 60s), much shorter than test execution timeout (300s), because judgement should be a fast evaluation step.
