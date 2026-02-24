# Flow: Effort Execution

## Overview

The effort execution flow handles `--effort converge` and `--effort max` modes. It computes target hashes for content-based filtering, runs all tests once (initial phase), then reruns target tests using SPRT with cross-session evidence pooling until each is classified as true_pass, true_fail, flake, or undecided. After classification, a burn-in sweep runs any burning_in tests, and finally a hifi E-value verdict is computed.

## Trigger

User invokes the orchestrator with `--effort converge` or `--effort max`.

## Steps

```
1. Validate prerequisites
   - Require --status-file
   - Resolve git context (commit SHA)

2. Hash-based filtering (phase 0)
   - Compute target hashes via bazel aquery
   - Compare each hash against stored value in status file
   - For changed hashes: invalidate evidence, update stored hash
   - For unchanged hashes with conclusive state: mark as skippable
   - When --skip-unchanged: remove skippable tests from DAG
   - When --no-skip-unchanged: still compute hashes for evidence pooling
   - Report hash filter data (changed/unchanged/skipped counts)

3. Initial execution (phase 1)
   - Run all (non-skipped) tests in DAG order via diagnostic/detection executor
   - Record initial results in status file with target hashes

4. SPRT rerun loop (phase 2) -- EffortRunner.run()
   |-- Load prior same-hash evidence for each target test
   |-- Build session state from initial results + prior evidence
   |-- Determine targets:
   |   |-- converge: only initially-failed tests
   |   +-- max: all tests
   |-- For each target, round-robin:
   |   |-- Execute test via subprocess
   |   |-- Record result in status file (with target_hash)
   |   |-- Update session runs/passes
   |   |-- Evaluate SPRT(runs + prior_runs, passes + prior_passes,
   |   |                  min_reliability, significance)
   |   +-- If SPRT decides: classify and remove from targets
   +-- Stop when all targets classified or per-test budget exhausted

5. Burn-in sweep (phase 3) -- BurnInSweep
   |-- Find burning_in tests remaining in DAG
   |-- Run sweep loop with target_hashes for evidence pooling
   |-- Transitions: burning_in -> stable (SPRT accept)
   |                burning_in -> flaky (SPRT reject)
   +-- Report decided/undecided counts

6. Classification
   |-- SPRT-evaluated tests: classified by initial_status x SPRT decision
   |-- Non-targeted tests (converge mode): true_pass with not_evaluated
   +-- Budget-exhausted tests: undecided

7. Hifi verdict computation (phase 4)
   - Compute E-value verdict using existing infrastructure
   - Include in report

8. Output
   - Print per-test SPRT classifications
   - Print burn-in sweep results (decided tests, undecided count)
   - Print hash filter summary (changed/unchanged/skipped)
   - Write JSON + HTML reports (if --output specified)
   - Exit code: 1 if any true_fail or flake, 0 otherwise
```

## Cross-Session Evidence Pooling

When `--status-file` is configured and target hashes are available:

1. The orchestrator computes content hashes for all test targets via `bazel aquery`
2. Each test run is recorded with its target hash in the status file history
3. EffortRunner loads prior evidence from the status file: history entries whose `target_hash` matches the current hash
4. SPRT evaluation uses the combined evidence (prior + current session), allowing tests to reach decisions faster by reusing data from previous runs of the same code
5. When a target hash changes, all prior evidence is invalidated via `invalidate_evidence()`, and the test re-enters `burning_in` state

This enables incremental CI: unchanged tests with sufficient evidence can skip reruns entirely, while tests with changed inputs start fresh.

## Components Involved

| Component | Role |
|-----------|------|
| [Orchestrator Main](../components/orchestrator-main.md) | CLI dispatch, `_run_effort()` |
| [Effort Runner](../components/effort.md) | SPRT rerun loop and classification with evidence pooling |
| [Burn-in](../components/burnin.md) | BurnInSweep for phase 3 |
| [Executor](../components/executor.md) | Initial test execution |
| [SPRT](../components/sprt.md) | Statistical decision function |
| [Status File](../components/status-file.md) | Records all reruns with target hashes |
| [E-values](../components/e-values.md) | Hifi verdict computation |
| [Reporter](../components/reporter.md) | Report generation with effort data, hash filter data, and sweep results |

## Data Flow

```
manifest.json --> TestDAG --> _compute_and_filter_hashes()
                                        |
                                        v
                              target_hashes + hash_filter_data
                                        |
                                        v
                              Executor --> initial TestResults
                                              |
                                              v
                              EffortRunner(initial_results, target_hashes)
                                              |
                                  +-----------+-----------+
                                  |                       |
                                  v                       v
                         _load_prior_evidence()   session runs/passes
                                  |                       |
                                  v                       v
                              prior + session evidence combined
                                              |
                                              v
                              sprt_evaluate() --> accept/reject/continue
                                              |
                                              v
                              EffortClassification per test
                                              |
                                              v
                              BurnInSweep(target_hashes)
                                              |
                                              v
                              SweepResult (decided/undecided)
                                              |
                                              v
                              EffortResult + SweepResult + hash_filter_data
                                              |
                                              v
                                        report + exit code
```

## Related Flows

- [Diagnostic Execution](diagnostic-execution.md): Used for initial phase 1 execution
- [E-value Verdict](e-value-verdict.md): Hifi verdict computed after effort reruns
- [Burn-in Lifecycle](burn-in-lifecycle.md): Uses same SPRT infrastructure; BurnInSweep is phase 3 of effort execution
