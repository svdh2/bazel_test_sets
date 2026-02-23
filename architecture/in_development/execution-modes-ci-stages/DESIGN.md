# Execution Modes and CI Stage Integration Design Document

## Glossary

| Term | Definition |
|------|-----------|
| **Effort mode** | CLI flag (`--effort`) that controls test execution thoroughness: how many reruns, which tests get rerun, and what verdict is computed. One of `none`, `regression`, `converge`, or `max`. |
| **Execution mode** | CLI flag (`--mode`) that controls DAG traversal order and failure propagation behavior. Either `diagnostic` (leaves-first, dependency gating) or `detection` (roots-first, no gating). |
| **Mini-converge** | A budget-capped SPRT rerun loop applied only to initially-failed tests during `regression` effort mode. Uses a tight rerun budget (3-5 reruns per test) to discriminate flakes from real failures. |
| **Burn-in sweep** | The `BurnInSweep` loop that repeatedly runs `burning_in` tests until SPRT classifies each as stable or flaky. Currently a separate subcommand; this design integrates it as a phase within effort modes. |
| **SPRT** | Sequential Probability Ratio Test. Statistical test that classifies a test as reliable (accept H0) or unreliable (reject H0) based on accumulated pass/fail evidence. |
| **E-value verdict** | Aggregate test set verdict (GREEN/RED/UNDECIDED) computed from per-test E-values with controlled Type II error. Only same-hash evidence is used for gating decisions. Cross-hash pooling is not used because different hashes mean different experimental conditions (different code, tools, or dependencies). |
| **Test set target** | A Bazel `test_set` rule target that groups `test_set_test` targets into a DAG. Each CI stage invokes its own test set target. |
| **Rerun budget** | Maximum number of SPRT reruns allowed per test within a single session, controlled by `--max-reruns`. |
| **Target hash** | The content hash of a Bazel test target, computed from the test binary, all transitive runtime dependencies, testing tools, and build configuration. Two executions of the same test with the same target hash have identical inputs, making them valid samples under the same experimental conditions for SPRT. Obtained from Bazel's action graph (e.g., via `bazel aquery`). |
| **Hash-based filtering** | Pre-execution step that compares current target hashes against stored hashes from previous sessions. Tests with unchanged hashes can be skipped (for regression detection) or can pool evidence from prior sessions (for SPRT accumulation and burn-in). |
| **Co-occurrence narrowing** | The regression selection algorithm that scores tests by co-occurrence with changed files in git history, selecting only the most relevant subset. |
| **ci_gate rule** | A Starlark rule that bundles a `test_set` reference with execution policy (mode, effort, max-reruns, etc.) and statistical/lifecycle parameters (min-reliability, statistical-significance, flaky-deadline-days) into a single runnable Bazel target. Each CI stage is a `ci_gate` target. Invoking `bazel run //ci:pr_gate_tests` runs the referenced test set with the baked-in execution parameters. |

## Overview

This design redefines how execution modes (diagnostic/detection) and effort modes (none/regression/converge/max) compose together to serve multiple CI stages with appropriate flake handling at each stage. The core principle is **separation of test selection from rerun strategy**: each CI stage is defined by (1) which `test_set` target to invoke, (2) execution mode, and (3) effort mode. Test tiering is done via test sets -- each CI stage has its own `test_set` target in BUILD files.

The primary changes are:
1. A new `ci_gate` Starlark rule that bundles a test set with execution policy (mode, effort, max-reruns, etc.) and statistical/lifecycle parameters (min-reliability, statistical-significance, flaky-deadline-days) into a single runnable target. Each CI stage becomes `bazel run //ci:{stage}_tests` with no CLI flags needed.
2. The `regression` effort mode gains mini-converge (budget-capped SPRT on failures) and burn-in sweep integration.
3. The `converge` and `max` effort modes gain burn-in sweep integration (currently a separate subcommand).
4. The `.test_set_config` file is eliminated entirely. All execution, statistical, and lifecycle parameters are `ci_gate` target attributes.

### Same-Hash Stationarity Principle

SPRT requires that all evidence comes from the same experimental conditions (stationarity). **Target hashes -- not commit boundaries -- are the correct stationarity marker.** Two test executions have the same experimental conditions if and only if they share the same target hash (same test binary, same dependencies, same tools, same build configuration).

Commit-level boundaries are too blunt: a commit may change files unrelated to a given test, leaving its target hash unchanged. Conversely, a single commit can change multiple test targets. Target hashes capture exactly what matters -- whether the test's inputs changed.

This means:
- **Evidence from different sessions with the same target hash CAN be pooled** for SPRT decisions. A burn-in test that ran 5 times at hash `abc123` yesterday and 5 times at hash `abc123` today has 10 valid samples.
- **When a target hash changes, all prior evidence is invalidated** and SPRT restarts from scratch. The status file records the hash alongside SPRT state; a hash mismatch triggers a reset.
- **Tests with unchanged hashes can be skipped** for regression detection (no new code → no new regression possible) or can contribute accumulated evidence for SPRT classification and burn-in.
- **E-value verdicts** use same-hash evidence exclusively. The quick verdict mode (cross-commit pooling without hash checks) is removed from all gating paths.
- **Hash-based filtering composes with regression selection**: at PR gate, the tests to run are the intersection of hash-changed tests and co-occurrence-selected tests, giving the tightest possible selection.

## Motivation

### Problem

The current system has two gaps that prevent effective multi-stage CI:

1. **Regression mode has no flake discrimination.** When `--effort regression` selects and runs tests, any failure is treated as a real failure. In PR CI, this causes flaky tests to block merges unnecessarily. There is no mechanism to re-run failures to distinguish flakes from genuine regressions.

2. **Burn-in is disconnected from effort modes.** The burn-in sweep (`BurnInSweep`) is a separate subcommand that must be invoked independently. When new tests are added in a PR, they run in CI but do not enter the burn-in lifecycle automatically. The `converge` and `max` effort modes do not integrate burn-in, so new tests in those stages get classified but never transition through the maturity state machine.

### Desired Outcome

After this change:
- PR CI (`regression` effort) can distinguish flakes from real failures using a tight rerun budget, reducing false merge blocks.
- New tests entering CI through any effort mode (`regression`, `converge`, `max`) automatically undergo burn-in evaluation.
- Each CI stage has a clear, documented configuration pattern.
- The `--max-reruns` flag allows fine-tuning the rerun budget per stage without changing the effort mode.

## Requirements

### Functional Requirements

1. **FR-1: Mini-converge in regression mode.** When `--effort regression` is used, initially-failed tests must be re-run using SPRT with a configurable budget (`--max-reruns`, default 5). If SPRT classifies a failure as a flake, the test is reported as `flake` and does not cause exit code 1. If SPRT confirms a true failure, the test is reported as `true_fail` and causes exit code 1. Initially-passing tests are not re-run.

2. **FR-2: Burn-in integration in regression mode.** When `--effort regression` is used with a configured `status_file`, tests in `new` or `burning_in` state must be included in execution (they are currently excluded from regression selection). After execution, their results must be processed through the burn-in lifecycle (`process_results`), potentially transitioning them to `stable` or `flaky`.

3. **FR-3: Burn-in integration in converge/max modes.** When `--effort converge` or `--effort max` is used with a configured `status_file`, after the SPRT rerun loop completes, a burn-in sweep phase must run on any `burning_in` tests. The sweep uses the runs already recorded during the SPRT phase as part of its evidence, avoiding redundant executions.

4. **FR-4: `ci_gate` Starlark rule.** A new Starlark rule that bundles a `test_set` reference with execution policy and statistical/lifecycle parameters into a single runnable target. Attributes include: `test_set` (label), `mode` (diagnostic/detection), `effort` (none/regression/converge/max), `max_reruns` (int), `max_failures` (int, optional), `max_parallel` (int, optional), `status_file` (string, optional), `diff_base` (string, optional), `co_occurrence_graph` (string, optional), `skip_unchanged` (bool, default true), `min_reliability` (float, default 0.99), `statistical_significance` (float, default 0.95), `flaky_deadline_days` (int, default 14). The rule generates a runner script that invokes the orchestrator with the configured flags, passing through any additional CLI args for overrides. Each CI stage is a `ci_gate` target: `bazel run //ci:pr_gate_tests`.

5. **FR-5: Eliminate `.test_set_config`.** The `.test_set_config` file is eliminated entirely. All fields (`max_test_percentage`, `max_hops`, `max_reruns`, `max_failures`, `max_parallel`, `status_file`, `skip_unchanged`, `min_reliability`, `statistical_significance`, `flaky_deadline_days`) become attributes of `ci_gate` targets. All configuration lives in BUILD files.

6. **FR-6: Regression flake exit code.** When mini-converge classifies a test as `flake`, exit code must be 0 (do not block). When a test is `true_fail` or `undecided`, exit code must be 1 (block). This differs from the current `converge`/`max` behavior where flakes cause exit code 1.

7. **FR-7: Known-flaky tests are non-blocking at PR and merge.** Tests in `flaky` lifecycle state (in the status file) must not cause exit code 1 at PR or merge stages, regardless of their session result. Tests in `new` or `burning_in` state are also non-blocking. Only `stable` tests that fail with `true_fail` or `undecided` classification cause exit code 1. This enables independent fix PRs for flaky tests without disabling them.

8. **FR-8: Flaky deadline with auto-disable.** A configurable `flaky_deadline_days` attribute (default 14) on `ci_gate` targets controls how long a test may remain in `flaky` state. When the deadline expires, the test transitions to `disabled` state (excluded from execution at all stages) with a warning in the report. The `last_updated` field in the status file serves as the `flaky_since` timestamp.

9. **FR-9: Flaky-to-stable re-promotion.** When a test owner transitions a `flaky` test to `burning_in` (via `test-status` subcommand) as part of a fix PR, the test must be included in burn-in execution at PR and post-merge stages. If the burn-in sweep classifies the test as reliable, it promotes to `stable`. If unreliable, it demotes back to `flaky`. This provides the same vetting path as new tests.

10. **FR-10: Hash-based test filtering.** Before execution, the orchestrator must compute target hashes for all tests in the DAG (via Bazel's action graph) and compare them against stored hashes in the status file. The `skip_unchanged` attribute on `ci_gate` (default `true`) controls whether hash-unchanged tests with conclusive SPRT decisions are skipped:
    - **`skip_unchanged = true` (default)**: Hash-unchanged tests with a conclusive SPRT decision at their current hash are skipped. In regression mode, this intersects with co-occurrence selection (no new code → no new regression). In converge/max modes, tests that still need evidence (e.g., `burning_in`) are re-run even if the hash is unchanged. `new`/`burning_in` tests are always included regardless of hash.
    - **`skip_unchanged = false`**: All tests in the DAG are executed regardless of hash changes. Evidence from prior sessions with the same hash is still pooled (hashes are always computed and stored), but no test is skipped. This is the correct setting for post-merge and release stages whose purpose is to validate ongoing test health, not just detect regressions — a test with an unchanged hash can still be flaky.
    - **None mode (local dev)**: No hash filtering. All tests executed, no hash computation.

11. **FR-11: Hash-based SPRT evidence management.** The status file must store the target hash alongside each test's SPRT state. When a test's target hash changes, its SPRT evidence must be invalidated (reset to empty) and its lifecycle state must transition to `burning_in` (the code changed, so reliability must be re-established). Evidence from sessions with the same target hash is valid for pooling.

### Non-Functional Requirements

1. **NFR-1: Performance.** Mini-converge must add minimal latency to PR CI. With a budget of 5 reruns per failed test, the worst case is 5 additional executions per failure. The burn-in sweep phase in converge/max modes must reuse SPRT evidence from the rerun phase to avoid redundant test executions. Hash-based filtering must reduce execution time in steady state (few tests changing per commit).

2. **NFR-2: Observability.** The JSON and HTML reports must include mini-converge classifications, burn-in phase results, and the effective `--max-reruns` budget used. The regression selection summary in stdout must indicate when mini-converge was applied. Reports must show how many tests were skipped due to unchanged hashes.

3. **NFR-3: Hash computation performance.** Target hash computation (via `bazel aquery` or equivalent) must complete within seconds, not minutes. Bazel's action graph is cached after a build, so hash queries on a warm cache should be fast. The orchestrator should compute hashes for all tests in a single batch query, not per-test.

## Architecture

### Components

One new component is introduced (`ci_gate` rule). The following existing components are modified.

| Component | Change |
|-----------|--------|
| **ci_gate rule** (new, `rules/`) | New Starlark rule that bundles a `test_set` label with execution policy attributes (mode, effort, max_reruns, etc.) and statistical/lifecycle parameters (min_reliability, statistical_significance, flaky_deadline_days). Generates a runner script. |
| [test_set rule](../../current/components/test-set-rule.md) | No changes. Continues to define test groupings and generate manifests. |
| [Orchestrator Main](../../current/components/orchestrator-main.md) | Add hash computation + filtering phase before execution. Modify `_run_regression` to include hash intersection, mini-converge, and burn-in phases. Modify `_run_effort` to include hash-based evidence pooling and burn-in sweep phase. Add lifecycle-aware exit code logic. All execution flags now come from the runner script generated by `ci_gate`, with CLI overrides still supported. |
| [Effort Runner](../../current/components/effort.md) | Modify to pool SPRT evidence across sessions with matching target hashes. Mini-converge reuses `EffortRunner` with `effort_mode="converge"` and a tight `max_reruns` budget. |
| [Burn-in](../../current/components/burnin.md) | Modify to accumulate evidence across sessions with matching target hashes. The `process_results` function and `BurnInSweep` class are called from new integration points in orchestrator main. |
| [Regression Selector](../../current/components/regression-selector.md) | Modify to include `new` and `burning_in` tests alongside co-occurrence-selected stable tests (when `status_file` is configured). |
| [Test Set Config](../../current/components/test-set-config.md) | Eliminated. All fields move to `ci_gate` target attributes. The `TestSetConfig` class is removed from the orchestrator. |
| [Reporter](../../current/components/reporter.md) | Add mini-converge classification data to regression reports. Add burn-in phase results to effort reports. |

### Data Flow

#### Regression Mode (with mini-converge and burn-in)

```
CLI: bazel run //ci:standard -- --mode diagnostic --effort regression --diff-base main

1. Load manifest, build DAG
2. Compute target hashes for all tests in DAG (batch bazel aquery)
3. Load stored hashes from status file
4. Identify hash-changed tests (current hash ≠ stored hash)
   - Tests with changed hashes: invalidate SPRT evidence, transition to burning_in
   - Tests with unchanged hashes: retain SPRT evidence from prior sessions
5. Resolve git context (commit SHA, changed files)
6. Load co-occurrence graph
7. Regression selection:
   a. Select stable tests by co-occurrence scoring
   b. Intersect with hash-changed tests (skip unchanged — no new regression possible)
   c. If status_file configured:
      - Add all new/burning_in tests to selected set (regardless of hash)
      - Add dependency closure
8. Build filtered DAG from selected tests
9. Phase 1 - Initial execution:
   Execute selected tests in diagnostic order
   Record results + target hashes in status file (if configured)
10. Phase 2 - Mini-converge (if status_file configured):
    EffortRunner(effort_mode="converge", max_reruns=5, initial_results)
    - Rerun initially-failed tests via SPRT (budget: 5 per test)
    - Classify each as true_fail, flake, or undecided
    - Initially-passing tests: true_pass (not re-run)
11. Phase 3 - Burn-in processing (if status_file configured):
    process_results(all results including reruns, status_file)
    - burning_in tests: SPRT evaluate on same-hash evidence -> stable/flaky/continue
    - stable + failed: demotion evaluate -> demote/retain/inconclusive
12. Report and exit:
    - No aggregate verdict (per-test classifications determine exit code)
    - Print per-test classifications (including hash-skipped count)
    - Write JSON + HTML reports
    - Exit code: 1 if any true_fail or undecided; 0 if only flakes/passes
```

#### Converge/Max Mode (with burn-in integration)

```
CLI: bazel run //ci:integration -- --mode diagnostic --effort max

1. Load manifest, build DAG
2. Compute target hashes for all tests in DAG (batch bazel aquery)
3. Load stored hashes from status file
4. Hash-based processing:
   - Tests with changed hashes: invalidate SPRT evidence, transition to burning_in
   - If skip_unchanged=true (PR/merge gates):
     Tests with unchanged hashes + conclusive SPRT decision: skip
     Tests with unchanged hashes + inconclusive/burning_in: include
   - If skip_unchanged=false (post-merge, release):
     All tests included regardless of hash (validate test health)
     Same-hash evidence from prior sessions is still pooled
5. Resolve git context
6. Phase 1 - Initial execution:
   Execute included tests in diagnostic order
   Record results + target hashes in status file
7. Phase 2 - SPRT rerun loop:
   EffortRunner(effort_mode="max", max_reruns=100, initial_results)
   - converge: rerun failed tests only
   - max: rerun all tests
   - Classify each test (pooling same-hash evidence from prior sessions)
8. Phase 3 - Burn-in sweep (if burning_in tests exist):
   BurnInSweep.run(test_names=burning_in_tests)
   - Evaluate SPRT on accumulated same-hash evidence (prior sessions + current)
   - Only re-run tests where SPRT still says "continue"
   - Classify as stable, flaky, or undecided
9. Phase 4 - Hifi verdict:
   HiFiEvaluator.evaluate()
7. Report and exit:
    - Print per-test SPRT classifications
    - Print burn-in phase results
    - Write JSON + HTML reports
    - Exit code: 1 if any true_fail or flake
```

### Interfaces

#### CLI Changes

No new CLI flags are introduced. The `ci_gate` rule generates a runner script that passes the configured flags (`--mode`, `--effort`, `--max-reruns`, `--diff-base`, `--status-file`, etc.) to the orchestrator automatically. All existing CLI flags remain available for overrides when running a `ci_gate` target:

```bash
# Uses baked-in configuration
bazel run //ci:pr_gate_tests

# Override max-reruns for this invocation
bazel run //ci:pr_gate_tests -- --max-reruns 10
```

#### Parameter Resolution

When a `ci_gate` target is run, execution parameters are resolved from two sources:

1. **`ci_gate` attributes** (from the BUILD file) — the primary source
2. **CLI overrides** (passed after `--`) — highest precedence, for ad-hoc adjustments

There is no config file fallback for execution parameters. All execution policy is in the BUILD file.

#### EffortRunner Reuse for Mini-Converge

Mini-converge reuses the existing `EffortRunner` class without modification:

```python
# In _run_regression, after initial execution:
runner = EffortRunner(
    dag=filtered_dag,
    status_file=sf,
    commit_sha=commit_sha,
    max_reruns=args.max_reruns,  # From ci_gate attribute (e.g., 5)
    effort_mode="converge",      # Only rerun failures
    initial_results=initial_results,
)
effort_result = runner.run()
```

The key difference from full converge mode is:
- `max_reruns` is set by the `ci_gate` target (typically 5 for PR gate, not 100)
- Exit code treats flakes as non-blocking (handled in `_run_regression`, not in `EffortRunner`)

#### BurnInSweep Integration in Effort Modes

After the SPRT rerun loop, the burn-in sweep is invoked on `burning_in` tests:

```python
# In _run_effort, after EffortRunner.run():
from orchestrator.lifecycle.burnin import BurnInSweep, filter_tests_by_state

burning_in_tests = filter_tests_by_state(dag, sf, include_states=["burning_in"])
if burning_in_tests:
    sweep = BurnInSweep(
        dag=dag,
        status_file=sf,
        commit_sha=commit_sha,
        max_iterations=args.max_reruns,
    )
    sweep_result = sweep.run(test_names=burning_in_tests)
```

The `BurnInSweep` starts by evaluating SPRT on the existing history in the status file. Tests that already have enough evidence from the effort rerun loop may be classified immediately without additional executions. Only tests where SPRT says "continue" will be re-run.

#### Regression Selector Change

When `status_file` is configured, the regression selector's pool is expanded and then intersected with hash-changed tests:

```python
# Current behavior:
# - Only stable tests are candidates for co-occurrence selection
# - new/burning_in/flaky are excluded

# New behavior:
# - stable tests: candidates for co-occurrence selection (unchanged)
#   → then intersect with hash-changed tests (skip stable + hash-unchanged)
# - new tests: always included (they need initial execution + burn-in)
# - burning_in tests: always included (they need more SPRT evidence)
# - flaky tests: excluded (unchanged)
```

This is implemented in `_run_regression` in orchestrator main, not in the regression selector itself. After regression selection returns its results:
1. Intersect co-occurrence-selected stable tests with hash-changed tests
2. Add `new` and `burning_in` tests (regardless of hash — they need evidence)
3. Add dependency closure
4. Build the filtered DAG

## Design Details

### Mini-Converge Phase in Regression Mode

#### When Mini-Converge Activates

Mini-converge activates when all of these conditions are met:
- `--effort regression` is specified
- `--status-file` is configured in the `ci_gate` target
- At least one test fails in the initial execution

When `--status-file` is not configured, regression mode behaves exactly as it does today: run selected tests once, report pass/fail, exit code based on raw results.

#### Mini-Converge Classification

The classification rules are the same as full converge mode (reused via `EffortRunner`):

| Initial status | SPRT accept (reliable) | SPRT reject (unreliable) | Budget exhausted |
|---|---|---|---|
| failed | flake | true_fail | undecided |
| passed | true_pass (not re-run) | N/A | N/A |

#### Exit Code Logic for PR and Merge Stages

The exit code depends on both the test's **lifecycle state** (from the status file) and the **session classification** (from mini-converge/SPRT). The full interaction matrix is documented in the Flaky Test Resolution Workflow section.

The key rules:
- Tests in `flaky`, `new`, or `burning_in` state → **never block** (exit 0) regardless of session result
- Tests in `stable` state → session classification determines exit code:

| Classification | Exit Code | Rationale |
|---|---|---|
| true_pass | 0 | Test passed reliably |
| true_fail | 1 | Genuine regression detected |
| flake | 0 (warn) | Newly detected flake -- flag in report, post-merge will evaluate |
| undecided | 1 | Cannot confirm it's a flake; block to be safe |

This differs from `converge`/`max` modes in post-merge/release stages, where flakes cause exit code 1 because they indicate reliability degradation that needs attention. At PR and merge, the goal is to detect regressions and catch new flaky tests via burn-in, not to enforce flake-free status on existing tests.

#### Budget Sizing Rationale

The default budget of 5 reruns for regression mode is chosen to balance:
- **Speed**: PR CI should complete quickly. 5 reruns per failure adds at most a few seconds per flaky test.
- **Statistical power**: With `min_reliability=0.99` and `margin=0.10`, a truly flaky test (e.g., 90% pass rate) will be rejected by SPRT within 3-5 runs in most cases. A truly failing test (0% pass rate) will be rejected immediately.
- **Undecided handling**: Tests that are borderline flaky (e.g., 95% pass rate) may not converge within 5 runs. These are classified as `undecided` and block the PR, which is the safe default.

### Burn-in Integration

#### Integration in Regression Mode

After initial execution and mini-converge:

1. Call `process_results(all_results, status_file)` on the combined initial + rerun results.
2. This triggers the existing lifecycle state machine:
   - `burning_in` tests: SPRT evaluate on accumulated same-hash history -> `stable`/`flaky`/continue
   - `stable` + failed: demotion evaluate -> `demote`/`retain`/`inconclusive`
   - `new`/`flaky`: record only, no evaluation

The key point: `process_results` already handles all the burn-in lifecycle logic. The change is that regression mode now calls it (currently it does not call `process_results` after execution -- it calls `_update_status_file` which is a thin wrapper around `process_results`).

Review of current code shows `_update_status_file` already calls `process_results`. So the burn-in integration for regression mode is already partially in place. The missing piece is including `new`/`burning_in` tests in the regression selection.

With hash-based evidence pooling, burn-in tests accumulate evidence across CI sessions as long as their target hash is stable. A `burning_in` test that ran 5 times in yesterday's PR CI and 5 times in today's PR CI (same hash both times) has 10 valid samples for SPRT evaluation.

#### Integration in Converge/Max Modes

After the SPRT rerun loop:

1. Check for `burning_in` tests in the DAG.
2. If any exist, invoke `BurnInSweep.run(test_names=burning_in_tests)`.
3. The sweep evaluates SPRT on accumulated same-hash evidence (from status file, which includes runs from prior sessions with the same hash + runs recorded during the current effort phase).
4. Tests that already have a decision from accumulated evidence are classified immediately.
5. Tests that still need more evidence are re-run within the sweep loop.
6. Sweep results (stable/flaky/undecided) are included in the report.

This means a new test added to an existing test set will:
1. Run during the initial execution (phase 1)
2. Get SPRT-rerun during the effort phase if it fails (phase 2, converge mode) or always (phase 2, max mode)
3. Get evaluated by the burn-in sweep (phase 3) which sees all accumulated same-hash runs (current + prior sessions)

#### Avoiding Redundant Executions

Evidence reuse happens at two levels:

1. **Within a session**: The burn-in sweep reads history from the status file. All runs from phases 1 and 2 are already recorded (the effort runner records each rerun). The sweep evaluates SPRT on existing history first. If the evidence is already sufficient, the test is classified without re-execution.

2. **Across sessions**: When a test's target hash hasn't changed since the last session, all evidence from prior sessions is still valid. The status file retains this evidence keyed by target hash. A `burning_in` test may reach a conclusive SPRT decision after accumulating runs across 2-3 CI sessions without needing the full rerun budget in any single session.

Only if SPRT says "continue" after considering all accumulated same-hash evidence does the sweep re-run the test.

### `ci_gate` Rule Design

#### Starlark Interface

```starlark
ci_gate(
    name,                          # Target name (e.g., "pr_gate_tests")
    test_set,                      # Label of the test_set target
    mode = "diagnostic",           # "diagnostic" or "detection"
    effort = None,                 # "regression", "converge", "max", or None
    max_reruns = 100,              # Per-test SPRT rerun budget
    max_failures = None,           # Stop after N failures (detection mode)
    max_parallel = None,           # Parallel execution limit
    status_file = None,            # Path to status file (enables lifecycle features)
    diff_base = None,              # Git ref for regression diff (e.g., "main")
    co_occurrence_graph = None,    # Path to co-occurrence graph file
    max_test_percentage = 0.10,    # Regression: max fraction of tests to select
    max_hops = 2,                  # Regression: max BFS hops in co-occurrence graph
    skip_unchanged = True,         # Skip hash-unchanged tests with conclusive SPRT
    min_reliability = 0.99,        # SPRT: minimum reliability threshold (H0)
    statistical_significance = 0.95, # SPRT: significance level for decisions
    flaky_deadline_days = 14,      # Days before flaky tests auto-disable
)
```

#### Implementation

The `ci_gate` rule:
1. Depends on the referenced `test_set` target (gets the manifest via a provider)
2. Generates a runner script that invokes the orchestrator with baked-in flags
3. Passes through `"$@"` for CLI overrides

```bash
# Generated runner script (simplified)
exec python -m orchestrator.main \
    --manifest "$MANIFEST_PATH" \
    --mode diagnostic \
    --effort regression \
    --max-reruns 5 \
    --diff-base main \
    --status-file .tests/status \
    --co-occurrence-graph .tests/co_occurrence_graph.json \
    --skip-unchanged \
    --min-reliability 0.99 \
    --statistical-significance 0.95 \
    --flaky-deadline-days 14 \
    "$@"
```

#### `.test_set_config` Elimination

With all parameters moved to `ci_gate` attributes, the `.test_set_config` file is eliminated entirely. The `TestSetConfig` class is removed from the orchestrator. All configuration is in BUILD files:

- **Execution policy**: `mode`, `effort`, `max_reruns`, `max_failures`, `max_parallel`, `status_file`, `diff_base`, `co_occurrence_graph`, `max_test_percentage`, `max_hops`
- **Statistical parameters**: `min_reliability`, `statistical_significance`
- **Lifecycle parameters**: `flaky_deadline_days`

This means different CI stages can use different statistical thresholds. For example, release validation can require higher reliability (`min_reliability = 0.999`) than the PR gate (`min_reliability = 0.99`). The `ci_gate` runner script passes all parameters to the orchestrator via CLI flags.

### CI Stage Mapping

Each CI stage is a `ci_gate` target. Running a stage is a single command with no flags:

| Stage | Target | Cadence | Purpose |
|-------|--------|---------|---------|
| Local dev | `bazel run //ci:local_tests` | on-demand | Developer iteration, full dependency propagation |
| PR gate | `bazel run //ci:pr_gate_tests` | per-push | Detect regressions, discriminate flakes, burn-in new tests |
| Merge gate | `bazel run //ci:merge_gate_tests` | per-merge (serialized) | Fast go/no-go with flake discrimination |
| Post-merge (per-merge) | `bazel run //ci:post_merge_tests` | per merge to main | Full SPRT on integration tests |
| Post-merge (hourly) | `bazel run //ci:hourly_tests` | hourly | Full SPRT on extended test set |
| Post-merge (daily) | `bazel run //ci:nightly_tests` | daily | Full SPRT on complete test suite |
| Release scan | `bazel run //ci:release_scan_tests` | on release tag | Quickly detect product defects in the candidate |
| Release validation | `bazel run //ci:release_validation_tests` | on release tag | Thorough validation when candidate looks good |

#### Stage Rationale

**Local dev (diagnostic + none):** Developers should not be subjected to test failures unrelated to their code changes. They run whichever test set is relevant to their work. Diagnostic mode propagates dependency failures, showing the developer what broke and what was affected. No effort mode because developers want fast iteration -- they can re-run manually when suspicious.

**PR gate (diagnostic + regression):** The developer needs thorough validation of their changes so that when they attempt to merge, the probability of success is high (required for stacked merges and merge trains). Co-occurrence narrowing limits scope to tests related to the changed code, keeping the PR gate fast enough for iteration. Tests in `flaky` lifecycle state are non-blocking -- they still run for signal, but their failures do not block the PR because they are known issues being tracked for resolution (see Flaky Test Resolution Workflow). Mini-converge (limited SPRT, budget 5) discriminates newly-detected flakes from real failures for `stable` tests. Full burn-in runs for new tests introduced in the PR and for previously-flaky tests whose owner has transitioned them to `burning_in` after applying a fix, to minimize the risk of introducing or re-introducing flaky tests into the codebase. The goal is to catch regressions and vet new and recovering tests without subjecting the developer to the full burn-in budget on existing stable tests.

**Merge gate (detection + converge, budget 5):** Merging is serialized -- it cannot be parallelized and is the rate limiter for how fast changes land on main. The merge gate must be fast. The smoke test set is small and curated to contain only stable, fast tests that validate build health and core features other teams rely on for development. Detection mode provides the fastest feedback (roots-first, early termination). Converge effort with a tight budget (5 reruns) provides flake discrimination even at this stage. Full burn-in runs for any new or recovering (`burning_in`) tests in the smoke set, though this is rare since the smoke set changes infrequently.

**Post-merge stages (diagnostic + max, skip_unchanged=false):** Post-merge stages do not run on each PR. Instead they run in a loop with a maximum frequency and skip commits that have already been tested. This decoupling from the merge pipeline allows running time-expensive tests that are impractical during PR and merge stages. These tests validate features that do not block large swaths of development but are important for product quality. Each cadence (per-merge, hourly, daily) has its own test set scoped to its time budget. Max effort SPRT-reruns everything for full classification. `skip_unchanged=false` because the purpose is to validate ongoing test health — a test with an unchanged hash can still be flaky, and post-merge is where you catch that. Same-hash evidence from prior sessions is still pooled (hashes are always computed), so tests that already have conclusive SPRT decisions reach their verdict faster. Flake identification is critical at this stage because flakes have a heavy compounding cost: they erode signal quality across all future CI runs and waste developer attention.

**Release scan (detection + converge, skip_unchanged=false):** The first phase of release validation. The goal is to quickly detect product defects in the release candidate that need fixing. Detection mode (roots-first, early stop) surfaces failures fast. Converge effort SPRT-reruns failures to confirm they are real defects, not flakes. `skip_unchanged=false` because all tests must be validated for a release regardless of hash changes. This phase answers: "does this candidate have any obvious problems?"

**Release validation (diagnostic + max, skip_unchanged=false):** The second phase, run when the release scan is clean and the candidate looks good. Diagnostic mode for complete dependency analysis. Max effort SPRT-reruns everything aggressively to minimize the risk of bug escapes. `skip_unchanged=false` for the same reason as release scan — all tests must pass for a release. This is the least frequently run CI stage, so time budget is generous. This phase answers: "is this candidate ready for production?"

#### BUILD File Pattern

The BUILD file separates test grouping (`test_set`) from execution policy (`ci_gate`):

```starlark
# ci/BUILD

load("//rules:test_set.bzl", "test_set")
load("//rules:ci_gate.bzl", "ci_gate")

# ── Test groupings (what to test) ──────────────────────────────

test_set(
    name = "smoke",
    assertion = "Core smoke tests pass",
    tests = [
        "//tests/core:health_check",
        "//tests/core:auth_basic",
    ],
)

test_set(
    name = "standard",
    assertion = "Standard regression suite passes",
    subsets = [
        ":smoke",
        "//tests/api:api_tests",
        "//tests/integration:basic_integration",
    ],
)

test_set(
    name = "integration",
    assertion = "Full integration suite passes",
    subsets = [
        ":standard",
        "//tests/integration:advanced_integration",
        "//tests/e2e:e2e_tests",
    ],
)

test_set(
    name = "extended",
    assertion = "Extended test suite passes",
    subsets = [
        ":integration",
        "//tests/performance:perf_tests",
        "//tests/compatibility:compat_tests",
    ],
)

test_set(
    name = "nightly",
    assertion = "Complete nightly suite passes",
    subsets = [
        ":extended",
        "//tests/stress:stress_tests",
        "//tests/migration:migration_tests",
    ],
)

test_set(
    name = "release",
    assertion = "Release candidate is ready for deployment",
    subsets = [":nightly"],
)

# ── CI stage targets (how to test) ────────────────────────────

ci_gate(
    name = "local_tests",
    test_set = ":standard",
    mode = "diagnostic",
)

ci_gate(
    name = "pr_gate_tests",
    test_set = ":standard",
    mode = "diagnostic",
    effort = "regression",
    max_reruns = 5,
    diff_base = "main",
    status_file = ".tests/status",
    co_occurrence_graph = ".tests/co_occurrence_graph.json",
)

ci_gate(
    name = "merge_gate_tests",
    test_set = ":smoke",
    mode = "detection",
    effort = "converge",
    max_reruns = 5,
    max_failures = 1,
    status_file = ".tests/status",
)

ci_gate(
    name = "post_merge_tests",
    test_set = ":integration",
    mode = "diagnostic",
    effort = "max",
    skip_unchanged = False,        # Validate test health, not just regressions
    status_file = ".tests/status",
)

ci_gate(
    name = "hourly_tests",
    test_set = ":extended",
    mode = "diagnostic",
    effort = "max",
    skip_unchanged = False,        # Validate test health, not just regressions
    status_file = ".tests/status",
)

ci_gate(
    name = "nightly_tests",
    test_set = ":nightly",
    mode = "diagnostic",
    effort = "max",
    skip_unchanged = False,        # Validate test health, not just regressions
    status_file = ".tests/status",
)

ci_gate(
    name = "release_scan_tests",
    test_set = ":release",
    mode = "detection",
    effort = "converge",
    skip_unchanged = False,        # Must validate all tests for release
    status_file = ".tests/status",
)

ci_gate(
    name = "release_validation_tests",
    test_set = ":release",
    mode = "diagnostic",
    effort = "max",
    skip_unchanged = False,        # Must validate all tests for release
    status_file = ".tests/status",
    min_reliability = 0.999,       # Stricter threshold for release
)
```

Test sets define *what* to test (hierarchically composable). `ci_gate` targets define *how* to test (execution policy). Running a CI stage is a single command:

```bash
bazel run //ci:pr_gate_tests
bazel run //ci:merge_gate_tests
bazel run //ci:post_merge_tests
bazel run //ci:release_scan_tests
bazel run //ci:release_validation_tests
```

No CLI flags needed. All execution policy is encoded in the BUILD file. CLI args can still be passed for overrides (e.g., `bazel run //ci:pr_gate_tests -- --max-reruns 10`).

### Flake Handling Progression

| Stage | Flake Strategy | Mechanism | Rationale |
|-------|---------------|-----------|-----------|
| Local dev | Ignore | No effort mode, raw pass/fail | Fast iteration, developer can re-run manually |
| PR gate | Discriminate | Mini-converge (budget-capped SPRT, 3-5 reruns) + full burn-in for new tests | Distinguish flakes from regressions; vet new tests for flakiness before they enter the codebase |
| Merge gate | Discriminate | Converge (budget-capped SPRT, 5 reruns) + full burn-in for new smoke tests | Small curated set, but still needs flake discrimination to avoid stalling the serialized merge queue |
| Post-merge | Classify | Full SPRT (max) | Comprehensive classification; flakes have heavy compounding cost and must be identified |
| Release scan | Discriminate | Converge (SPRT on failures) | Confirm failures are real defects, not flakes, before flagging the candidate |
| Release validation | Classify | Full SPRT (max) | Maximum reruns, any unreliable test blocks release |

### Flaky Test Resolution Workflow

Flake detection alone is not enough. Flaky tests have a heavy compounding cost (noisy CI, wasted developer attention, eroded trust in the test suite) and must be actively resolved. This section defines the lifecycle from detection to resolution and the mechanisms that enable independent fix PRs without disabling tests.

#### The Problem with Disabling

When multiple flaky tests are discovered at once, the naive approach is to disable all but the one being fixed. This has two risks:
1. **Forgotten re-enable**: Disabled tests are out of sight and out of mind. Owners forget to re-enable them.
2. **Reduced quality window**: While tests are disabled, PRs can merge without the coverage those tests provide.

The mechanism below avoids disabling entirely. Flaky tests remain enabled and run at every CI stage. Their results are tracked but do not block developer workflow at PR and merge stages.

#### Lifecycle State and Exit Code Interaction

The exit code at PR and merge depends on the combination of the test's **lifecycle state** (from the status file) and the **session classification** (from mini-converge/SPRT):

| Lifecycle State | Session Classification | PR/Merge Exit Code | Rationale |
|----------------|----------------------|---------------------|-----------|
| `stable` | true_pass | 0 | All good |
| `stable` | true_fail | 1 | Regression introduced by this change |
| `stable` | flake | 0 (warn) | Borderline flaky -- flag in report, post-merge will evaluate |
| `stable` | undecided | 1 | Cannot confirm, block to be safe |
| `flaky` | true_fail | 0 | Known issue, not caused by this change |
| `flaky` | flake | 0 | Known issue, expected behavior |
| `flaky` | true_pass | 0 | May be fixed -- post-merge will verify |
| `flaky` | undecided | 0 | Known issue |
| `burning_in` | any | 0 | Still being evaluated, not gating |
| `new` | any | 0 | First runs, not gating |

Key property: **tests in `flaky` state never block PR or merge**, regardless of their session result. This is what enables independent fix PRs -- a developer fixing test X is not blocked by tests Y and Z which are also flaky.

#### Independent Fix PR Workflow

When post-merge CI demotes a test to `flaky`:

```
1. Post-merge detects flake
   stable test fails SPRT → demoted to `flaky` state
   Report flags the test with `flaky_since` timestamp

2. Owner creates fix PR
   Owner transitions the test from `flaky` to `burning_in` (via `test-status` subcommand)
   The fix touches code related to the flaky test
   PR gate: test is included as a `burning_in` test (always selected alongside co-occurrence tests)
   Full burn-in runs for this test to validate the fix under SPRT
   Other flaky tests (Y, Z) remain non-blocking — the developer only needs their test to pass

3. Fix merges
   Test is already in `burning_in` state (set by the owner in step 2)
   Post-merge stages run the test with full SPRT (max effort)
   If test passes reliably → promoted to `stable`
   If test still flaky → demoted back to `flaky`, owner continues working

4. Other flaky tests are unaffected
   Y and Z remain flaky and non-blocking throughout
   Their owners fix them independently through separate PRs
```

#### Flaky Deadline and Escalation

To prevent tests from staying `flaky` indefinitely, a configurable deadline mechanism escalates unresolved flakes:

- **`flaky_deadline_days`** on the `ci_gate` target (default: 14 days)
- When a test has been in `flaky` state longer than the deadline:
  - The test transitions to `disabled` state
  - `disabled` tests are excluded from execution at all stages
  - The report emits a loud warning: "Test X auto-disabled after N days in flaky state"
  - The warning includes the `flaky_since` date for traceability

The deadline creates urgency without requiring lock-step fixes. Each flaky test has its own independent clock. The status file already tracks `last_updated` which serves as the `flaky_since` timestamp when the state is `flaky`.

#### Re-promotion After Fix

When a flaky or disabled test's underlying issue is fixed:

1. **Flaky test fix PR**: Owner transitions the test from `flaky` to `burning_in` (via `test-status` subcommand) as part of the fix PR. The PR gate runs full burn-in on the test. After merge, post-merge stages continue burn-in evaluation → `stable` if reliable, back to `flaky` if not.
2. **Disabled test re-enabled**: Owner manually transitions to `new` (via `test-status` subcommand) → enters burn-in lifecycle normally.
3. Re-promotion requires the test to pass the same burn-in criteria as any new test — no shortcuts.

## Error Handling

### Mini-Converge Errors

| Error | Behavior |
|-------|----------|
| `status_file` not set in `ci_gate` with effort `regression` | Mini-converge is skipped. Regression mode runs tests once and reports raw pass/fail. No burn-in processing. |
| SPRT budget exhausted (undecided) | Test classified as `undecided`. Exit code 1 (blocks PR). Report shows the classification and rerun count. |
| Test executable fails during rerun | Counted as a failure for SPRT. If the test consistently fails, SPRT will reject (true_fail). |
| All tests pass in initial execution | Mini-converge has nothing to do. All tests classified as `true_pass` via `EffortRunner` (which sets `sprt_decision="not_evaluated"` for non-targeted tests). |

### Burn-in Integration Errors

| Error | Behavior |
|-------|----------|
| `BurnInSweep` encounters an executable-not-found error | Test result is recorded as failed. SPRT evaluation continues based on accumulated evidence. Reported in the burn-in phase results. |
| `status_file` becomes corrupted during sweep | `StatusFile.save()` writes atomically (write to temp file, rename). If the process crashes mid-sweep, the last successfully saved state is preserved. |
| `burning_in` tests have no executable in the DAG | `BurnInSweep` skips tests not found in the DAG. These remain `burning_in` in the status file. |

### `max_reruns` Validation

| Error | Behavior |
|-------|----------|
| `max_reruns` set with no `effort` | Value is ignored (no reruns happen without an effort mode). |
| `max_reruns = 0` | Effectively disables reruns. In regression mode: raw pass/fail results. In converge/max: initial execution only, all tests classified based on initial results. |
| Negative `max_reruns` | Starlark `attr.int` validation rejects negative values at build time. |

## Testing Strategy

### Unit Tests

1. **`ci_gate` rule generation:**
   - Runner script includes all configured flags.
   - Missing optional attributes (e.g., `diff_base`) are not included in runner script.
   - CLI overrides are passed through.

2. **Lifecycle-aware exit code logic:**
   - All stable + true_pass: exit 0.
   - Any stable + true_fail: exit 1.
   - Only stable + flake (no true_fail): exit 0 with warning.
   - Any stable + undecided: exit 1.
   - Flaky test + true_fail: exit 0 (known issue, non-blocking).
   - Mix of stable true_fail and flaky true_fail: exit 1 (stable failure dominates).
   - All new/burning_in: exit 0 regardless of result.

3. **Burn-in test inclusion in regression:**
   - Regression selection returns N tests; K new/burning_in tests exist; selected set has N+K tests.
   - Dependency closure includes dependencies of new/burning_in tests.
   - When status_file not configured: only co-occurrence-selected tests (current behavior).

### Integration Tests

1. **End-to-end regression + mini-converge:**
   - Create a test set with one flaky test and one stable test.
   - Run with `--effort regression`.
   - Verify the flaky test is classified and does not block (exit 0).
   - Verify report contains mini-converge classification data.

2. **End-to-end converge + burn-in:**
   - Create a test set with one `burning_in` test.
   - Run with `--effort converge`.
   - Verify the burn-in sweep runs after the SPRT loop.
   - Verify the test transitions to `stable` or `flaky` in the status file.

3. **`--max-reruns` override:**
   - Run `--effort regression --max-reruns 1`.
   - Verify at most 1 rerun per failed test.
   - Run `--effort max --max-reruns 3`.
   - Verify at most 3 reruns per test.

### Property Tests

1. **SPRT convergence within budget:** For tests with known reliability rates (e.g., 0.5, 0.9, 0.99), verify SPRT reaches a decision within the budget at the expected rate.

2. **Exit code consistency:** For any combination of classifications, the exit code is deterministic and matches the documented rules.

## Security Considerations

No new security concerns are introduced by this design. The changes are internal to the orchestrator's execution pipeline and do not affect:
- File system access patterns (same subprocess execution model)
- Network access (orchestrator remains offline)
- Authentication/authorization (none required)
- User input handling (new `--max-reruns` flag is type-validated by argparse)

## Performance Considerations

### Mini-Converge Overhead

With a budget of 5 reruns per test and a typical PR having 0-3 failures:
- Best case (no failures): 0 additional executions.
- Worst case (3 failures, all undecided): 15 additional executions.
- Typical case (1 flaky failure): 2-3 additional executions before SPRT decides.

For tests that take ~1 second each, mini-converge adds 2-15 seconds to PR CI in the worst case. This is acceptable for the value of avoiding false merge blocks.

### Hash-Based Filtering Savings

Hash-based filtering provides the largest performance benefit in steady state:
- **Regression mode**: In a typical PR touching 5-10 files, the vast majority of tests in the test set have unchanged target hashes. If the test set has 500 tests and co-occurrence selects 50, but only 20 of those have changed hashes, only 20 tests execute instead of 50. This is a 60% reduction on top of regression narrowing.
- **Converge/max modes**: In post-merge CI, most tests have unchanged hashes between adjacent commits. Tests with unchanged hashes and conclusive SPRT decisions from prior sessions are skipped entirely. Only tests with changed hashes (code updates) or inconclusive decisions (need more evidence) execute.
- **Hash computation cost**: A single batch `bazel aquery` on a warm cache typically completes in 1-5 seconds. This fixed cost is amortized against the savings from skipping unchanged tests.

### Burn-in Sweep Overhead in Effort Modes

The burn-in sweep after the SPRT loop adds overhead only for `burning_in` tests:
- If no `burning_in` tests exist: 0 additional executions.
- If `burning_in` tests have sufficient evidence from same-hash runs (current + prior sessions): 0 additional executions (SPRT evaluates immediately).
- If `burning_in` tests need more evidence: additional executions up to `max_iterations` (inherited from `max_reruns`).

In steady state (no new tests being added), the burn-in phase has zero overhead. With hash-based evidence pooling, burn-in tests reach conclusive decisions faster because evidence accumulates across sessions.

### Round-Robin Fair Scheduling

Both `EffortRunner` (for mini-converge and full SPRT) and `BurnInSweep` use round-robin scheduling: each target test gets one rerun per round. This ensures fair budget distribution. A test that converges quickly frees its budget slot for other tests.

## Dependencies

| Dependency | Type | Status |
|------------|------|--------|
| `test_set` rule | Internal | Exists. `ci_gate` depends on it for manifest generation. |
| `EffortRunner` class | Internal | Exists. Modified to pool same-hash SPRT evidence. Reused for mini-converge. |
| `BurnInSweep` class | Internal | Exists. Modified to accumulate same-hash evidence. Called from new integration point in `_run_effort`. |
| `process_results` function | Internal | Exists. Already called via `_update_status_file` in regression path. |
| `filter_tests_by_state` function | Internal | Exists. Used to find `burning_in` tests for sweep phase. |
| `StatusFile` class | Internal | Exists. Modified to store target hashes alongside SPRT state and to invalidate evidence on hash changes. |
| `SPRT` module | Internal | Exists. No changes required. |
| `E-values` module | Internal | Exists. Hifi verdict for converge/max only. Quick (cross-commit) mode not used for gating. |
| `TestSetConfig` class | Internal | Exists. **Removed** by this design. All its fields become `ci_gate` attributes and CLI flags. |
| `bazel aquery` | External (Bazel) | Exists. Used to compute target hashes for all tests in a single batch query. Requires a warm Bazel cache for fast execution. |
| `argparse` | Standard library | Exists. Orchestrator CLI accepts flags passed by `ci_gate` runner script, including new `--min-reliability`, `--statistical-significance`, `--flaky-deadline-days` flags. |

## References

- [Execution Modes (current docs)](../../../docs/execution-modes.md) -- Current execution and effort mode documentation
- [Regression Mode (current docs)](../../../docs/regression-mode.md) -- Current regression mode documentation
- [Architecture README](../../current/README.md) -- Architecture entry point
- [Effort Runner component](../../current/components/effort.md) -- SPRT rerun engine
- [Burn-in component](../../current/components/burnin.md) -- Burn-in lifecycle
- [E-values component](../../current/components/e-values.md) -- E-value verdict system
- [Effort Execution flow](../../current/flows/effort-execution.md) -- Effort execution flow
- [Regression Selection flow](../../current/flows/regression-selection.md) -- Regression selection flow
- [Test Set Config component](../../current/components/test-set-config.md) -- Config file manager (eliminated by this design)

## Required Architecture Changes

After implementation, the following architecture documents in `architecture/current/` must be updated:

1. **New `components/ci-gate-rule.md`**: Document the `ci_gate` Starlark rule, its attributes, generated runner script, and relationship to `test_set`.

2. **`components/orchestrator-main.md`**: Document hash computation + filtering phase. Document the multi-phase execution in regression mode (hash filter + initial + mini-converge + burn-in). Document the burn-in sweep phase in converge/max modes. Update exit code semantics with lifecycle-aware logic.

3. **`components/effort.md`**: Add a note that `EffortRunner` is now also used for mini-converge in regression mode with a tight budget. Document same-hash evidence pooling across sessions.

4. **`components/test-set-config.md`**: Remove this document. The `TestSetConfig` component no longer exists; all parameters are `ci_gate` attributes.

5. **`components/status-file.md`**: Document the `target_hash` field stored per test. Document hash-change-triggered evidence invalidation and lifecycle transition to `burning_in`.

6. **`flows/effort-execution.md`**: Add hash-based filtering as the first phase. Add burn-in sweep as a later phase (between SPRT rerun loop and verdict computation). Document cross-session evidence pooling with same-hash checks.

7. **`flows/regression-selection.md`**: Document the intersection of co-occurrence-selected tests with hash-changed tests. Document the inclusion of `new`/`burning_in` tests regardless of hash.

8. **`README.md`**: Add `ci_gate` rule to Component Index. Update Git Sync commit hash after implementation.

## Implementation Priorities

### Priority 1: `ci_gate` Starlark rule and config elimination

**Rationale:** This is the foundation for all CI stage targets. It establishes the declarative model where all configuration lives in BUILD files, not config files or CLI invocations.

**Scope:**
- Implement `ci_gate` rule in `rules/ci_gate.bzl` with all attributes (mode, effort, max_reruns, min_reliability, statistical_significance, flaky_deadline_days, etc.)
- Generate runner script that invokes orchestrator with baked-in flags + `"$@"` pass-through
- Eliminate `TestSetConfig` class entirely (all its fields are now `ci_gate` attributes passed via CLI)
- Add `--min-reliability`, `--statistical-significance`, `--flaky-deadline-days` CLI flags to orchestrator
- Update orchestrator to read statistical/lifecycle parameters from CLI flags instead of config file
- Remove `.test_set_config` file handling from orchestrator
- Update existing tests to not rely on config file
- Unit tests for rule generation and CLI changes

**Dependencies:** None.

### Priority 2: Hash-based test filtering and evidence management

**Rationale:** Hash-based filtering is foundational — it changes how the orchestrator decides what to run and how SPRT evidence is managed across sessions. It must be in place before mini-converge and burn-in integration can benefit from cross-session evidence pooling.

**Scope:**
- Implement target hash computation via batch `bazel aquery` (single query for all tests in DAG)
- Extend `StatusFile` to store `target_hash` per test alongside SPRT state
- Implement hash comparison logic: detect changed vs unchanged tests
- On hash change: invalidate SPRT evidence, transition test to `burning_in`
- On hash match: retain SPRT evidence from prior sessions for pooling
- Add `--skip-hash-filter` CLI flag for disabling (e.g., local dev `none` mode)
- Unit tests for hash comparison, evidence invalidation, and evidence pooling
- Performance benchmarks for `bazel aquery` batch queries

**Dependencies:** Priority 1 (orchestrator accepts `ci_gate`-provided flags, status file changes).

### Priority 3: Lifecycle-aware exit code logic

**Rationale:** This is a prerequisite for both mini-converge and the flaky resolution workflow. Exit code determination must account for the test's lifecycle state in the status file, not just the session classification.

**Scope:**
- Implement exit code function that checks lifecycle state (`flaky`/`new`/`burning_in` → non-blocking) before session classification
- Tests in `stable` state use session classification (true_fail/undecided → blocking, flake → non-blocking with warning)
- Unit tests for the full lifecycle × classification matrix

**Dependencies:** Priority 1 (orchestrator accepts `ci_gate`-provided flags).

### Priority 4: Mini-converge in regression mode

**Rationale:** This delivers the highest-value improvement (flake discrimination in PR CI) and exercises the `EffortRunner` reuse pattern.

**Scope:**
- Modify `_run_regression` to create an `EffortRunner` after initial execution (when `status_file` is configured and failures exist)
- Wire in lifecycle-aware exit code logic from Priority 3
- Integrate hash-based filtering: intersect co-occurrence-selected tests with hash-changed tests
- Add mini-converge classification data to regression reports (including hash-skipped count)
- Update stdout summary to show classifications
- Integration tests

**Dependencies:** Priority 2 (hash filtering), Priority 3 (exit code logic).

### Priority 5: Burn-in test inclusion in regression selection

**Rationale:** Ensures new tests entering the codebase via PRs get burn-in lifecycle progression during PR CI.

**Scope:**
- After regression selection and hash intersection, add `new`/`burning_in` tests to the selected set (regardless of hash — they need evidence)
- Add dependency closure for the added tests
- Verify `_update_status_file` (which calls `process_results`) handles the lifecycle transitions correctly with same-hash evidence pooling
- Integration tests

**Dependencies:** Priority 4 (regression mode changes).

### Priority 6: Burn-in sweep integration in converge/max modes

**Rationale:** Completes the burn-in lifecycle integration across all effort modes. With hash-based evidence pooling, burn-in tests accumulate evidence across sessions naturally, reaching conclusions faster.

**Scope:**
- Modify `_run_effort` to apply hash-based filtering (skip tests with unchanged hashes + conclusive decisions)
- Invoke `BurnInSweep.run()` after the SPRT rerun loop (when `burning_in` tests exist), pooling same-hash evidence from prior sessions
- Add burn-in sweep results to effort reports
- Integration tests

**Dependencies:** Priority 2 (hash filtering).

### Priority 7: Flaky deadline and auto-disable

**Rationale:** Prevents flaky tests from staying in quarantine indefinitely. Without a deadline, flaky tests accumulate and erode the test suite's value.

**Scope:**
- `flaky_deadline_days` is already a `ci_gate` attribute (from Priority 1); orchestrator reads it via `--flaky-deadline-days` CLI flag
- Before execution, check all `flaky` tests against their `last_updated` timestamp
- Tests exceeding the deadline transition to `disabled` with a report warning
- Add `disabled` tests to report summary (count and names)
- Add flaky-to-`burning_in` re-promotion logic in `process_results` (when a `flaky` test passes SPRT)
- Unit and integration tests

**Dependencies:** Priority 3 (lifecycle-aware exit code).

### Priority 8: CI stage documentation and BUILD file examples

**Rationale:** This is documentation, not code. It can be written at any time but is most useful after the code changes are complete so the examples are accurate.

**Scope:**
- Add CI stage guidance to `docs/execution-modes.md` or a new `docs/ci-stages.md`
- Provide example BUILD file patterns
- Document hash-based filtering, the flake handling progression, and the flaky resolution workflow

**Dependencies:** Priorities 1-7 (so documentation reflects implemented behavior). Includes `ci_gate` rule usage examples.

## Open Questions

1. ~~**Burn-in under single-commit SPRT.**~~ **Resolved by same-hash stationarity.** Burn-in CAN accumulate evidence across CI sessions as long as the test's target hash is unchanged. The status file stores SPRT evidence keyed by target hash. When the hash matches, evidence from prior sessions is valid and pools with current-session evidence. When the hash changes, evidence resets. This eliminates the need for artificially high per-session rerun budgets or multi-session promotion thresholds — burn-in naturally progresses as sessions accumulate same-hash evidence.

2. **Bazel aquery performance at scale.** Computing target hashes for all tests in a large DAG requires a `bazel aquery` call. On a warm Bazel cache (after build), this should be fast (seconds). On a cold cache, it may trigger analysis. Need to validate performance on a representative codebase and determine whether the hash query should be a separate build step or integrated into the orchestrator startup.

## Alternatives Considered

### Alternative 1: Separate mini-converge engine (rejected)

Instead of reusing `EffortRunner` for mini-converge, we considered building a dedicated lightweight rerun engine for regression mode.

**Rejected because:** `EffortRunner` already does exactly what mini-converge needs (SPRT rerun loop on failed tests). Reusing it with `effort_mode="converge"` and a tight `max_reruns` budget requires zero changes to the runner. A separate engine would duplicate logic.

### Alternative 2: Flakes block in regression mode (rejected)

We considered having flakes block the PR (exit code 1) in regression mode, matching the converge/max behavior.

**Rejected because:** The purpose of PR CI is to detect regressions, not to enforce flake-free status. A known flake that was flaky before the PR is not a regression. Blocking on flakes would negate the benefit of mini-converge and keep the current false-block problem. Flake enforcement is handled by post-merge stages where full SPRT classifies everything.

### Alternative 3: New effort mode `mini-converge` (rejected)

We considered adding a fifth effort mode called `mini-converge` instead of enhancing `regression`.

**Rejected because:** Mini-converge is not an independent concept -- it is specifically tied to regression mode's use case (PR CI, tight budget, flakes non-blocking). Adding a separate mode would increase the combinatorial complexity of mode x effort without adding real flexibility. The `--max-reruns` flag already allows tuning the budget within existing modes.

### Alternative 4: Automatic burn-in without explicit integration (rejected)

We considered making `process_results` automatically trigger a burn-in sweep when it encounters `burning_in` tests, rather than adding explicit sweep calls in `_run_effort`.

**Rejected because:** `process_results` is designed to operate on existing results without re-execution. Embedding re-execution logic (which is what the sweep does) inside `process_results` would violate its single responsibility. The explicit sweep call in `_run_effort` keeps the separation clean: `process_results` records and evaluates, `BurnInSweep` re-executes and records.

### Alternative 5: Disable flaky tests during fix window (rejected)

We considered disabling flaky tests while they are being fixed, so that only the test under active fix runs at PR/merge.

**Rejected because:** Disabling tests creates two risks: (1) owners forget to re-enable them, leaving permanent coverage gaps; (2) during the disabled window, PRs can merge without the quality checks those tests provide. Instead, flaky tests remain enabled at all CI stages. They are non-blocking at PR/merge (via lifecycle-aware exit code) but continue to run, providing ongoing signal. This enables independent fix PRs without disabling other flaky tests and without lock-step fixes.

### Alternative 6: Commit-based stationarity boundary (rejected)

We considered using the commit SHA as the stationarity boundary for SPRT evidence — all evidence must come from a single commit, and cross-commit pooling is never valid.

**Rejected because:** Commit boundaries are too blunt. A commit may change files completely unrelated to a given test, leaving its target hash (test binary, dependencies, tools) unchanged. Under commit-based stationarity, that test's prior evidence would be invalidated unnecessarily, forcing redundant re-execution. Conversely, two adjacent commits might not change any test targets at all, yet commit-based stationarity would prevent evidence pooling. Target hashes capture exactly what matters — whether the test's inputs changed. Same hash = same experimental conditions = valid to pool. Different hash = different conditions = must restart. This is strictly more precise than commit-level boundaries and directly enables cross-session burn-in evidence accumulation (resolving what was previously an open question).

### Alternative 6b: Cross-commit SPRT without hash checks (rejected)

We considered using the quick E-value mode (which pools SPRT evidence across multiple commits without checking target hashes) for test set verdicts.

**Rejected because:** Blindly pooling evidence across commits ignores that code changes between commits can change experimental conditions. Without hash verification, a test's reliability at commit A (where it was passing) could be mixed with evidence at commit B (where a dependency changed and broke it), producing invalid SPRT conclusions. Hash-based pooling (the adopted approach) achieves the benefits of cross-commit evidence accumulation while maintaining valid stationarity guarantees.

### Alternative 7: Merge gate uses no effort mode (rejected)

We considered using `detection + none` for the merge gate (run smoke tests once with no reruns).

**Rejected because:** Even though the smoke set is curated for stability, a flaky test failure at the merge gate stalls the serialized merge queue. With converge effort and a tight budget (5 reruns), flake discrimination adds negligible latency (seconds) while preventing merge queue stalls. Full burn-in for new smoke tests is also needed to prevent flaky tests from entering the smoke set.

### Alternative 8: Merge gate uses diagnostic mode (rejected)

We considered using diagnostic mode for the merge gate instead of detection.

**Rejected because:** The merge gate is a serialized bottleneck -- it must be as fast as possible. Detection mode (roots-first, early termination) provides the fastest feedback on a small curated smoke set. Dependency propagation adds no value at this stage -- either the smoke tests pass or they don't. Diagnostic mode is better suited for stages where understanding failure propagation matters (PR gate, post-merge).
