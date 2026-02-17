# Flow: Effort Execution

## Overview

The effort execution flow handles `--effort converge` and `--effort max` modes. It runs all tests once (initial phase), then reruns target tests using SPRT until each is classified as true_pass, true_fail, flake, or undecided. Finally, it computes a hifi E-value verdict.

## Trigger

User invokes the orchestrator with `--effort converge` or `--effort max`.

## Steps

```
1. Validate prerequisites
   - Require --status-file
   - Resolve git context (commit SHA)

2. Initial execution (phase 1)
   - Run all tests in DAG order via diagnostic/detection executor
   - Record initial results in status file

3. SPRT rerun loop (phase 2) — EffortRunner.run()
   ├── Build session state from initial results
   ├── Determine targets:
   │   ├── converge: only initially-failed tests
   │   └── max: all tests
   ├── For each target, round-robin:
   │   ├── Execute test via subprocess
   │   ├── Record result in status file
   │   ├── Update session runs/passes
   │   ├── Evaluate SPRT(runs, passes, min_reliability, significance)
   │   └── If SPRT decides: classify and remove from targets
   └── Stop when all targets classified or per-test budget exhausted

4. Classification
   ├── SPRT-evaluated tests: classified by initial_status × SPRT decision
   ├── Non-targeted tests (converge mode): true_pass with not_evaluated
   └── Budget-exhausted tests: undecided

5. Hifi verdict computation
   - Compute E-value verdict using existing infrastructure
   - Include in report

6. Output
   - Print per-test SPRT classifications
   - Write JSON + HTML reports (if --output specified)
   - Exit code: 1 if any true_fail or flake, 0 otherwise
```

## Components Involved

| Component | Role |
|-----------|------|
| [Orchestrator Main](../components/orchestrator-main.md) | CLI dispatch, `_run_effort()` |
| [Effort Runner](../components/effort.md) | SPRT rerun loop and classification |
| [Executor](../components/executor.md) | Initial test execution |
| [SPRT](../components/sprt.md) | Statistical decision function |
| [Status File](../components/status-file.md) | Records all reruns |
| [E-values](../components/e-values.md) | Hifi verdict computation |
| [Reporter](../components/reporter.md) | Report generation with effort data |

## Data Flow

```
manifest.json → TestDAG → Executor → initial TestResults
                                          ↓
                              EffortRunner(initial_results)
                                          ↓
                              session_runs/passes per test
                                          ↓
                              sprt_evaluate() → accept/reject/continue
                                          ↓
                              EffortClassification per test
                                          ↓
                              EffortResult → report + exit code
```

## Related Flows

- [Diagnostic Execution](diagnostic-execution.md): Used for initial phase 1 execution
- [E-value Verdict](e-value-verdict.md): Hifi verdict computed after effort reruns
- [Burn-in Lifecycle](burn-in-lifecycle.md): Uses same SPRT infrastructure
