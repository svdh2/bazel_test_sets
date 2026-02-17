# Flow: Burn-in Lifecycle

## Purpose

Manages test maturity through a state machine driven by SPRT (Sequential Probability Ratio Test). New tests must prove their reliability before participating in detection mode and regression selection. Flaky tests are quarantined until fixed.

## State Machine

```
                 CI tool: burn-in
    new  --------------------------->  burning_in
     ^                                    |
     |                        +-----------+-----------+
     |                        |                       |
     |                  SPRT accept              SPRT reject
     |                        |                       |
     |                        v                       v
     |                     stable                   flaky
     |                        |                       |
     |               failure + SPRT              CI tool: deflake
     |               evaluation                       |
     |                        |                       |
     |              +---------+---------+             |
     |              |         |         |             |
     |           demote   inconclusive retain         |
     |              |         |         |             |
     |              v         v      (no change)      |
     |            flaky   burning_in                   |
     |              ^     (suspicious,                 |
     |              |      counters                    |
     |              |      preserved)                  |
     |              +----------------------------------+
     |                                       (counters reset)
     |
     |     BUILD: disabled=True removed (sync)
 disabled  <-----  any state
               BUILD: disabled=True set (sync)
```

## States

| State | Description | Participates In |
|-------|-------------|-----------------|
| `new` | Test has been added but not yet evaluated | Diagnostic only |
| `burning_in` | Test is being repeatedly run for SPRT evaluation | Diagnostic only |
| `stable` | SPRT accepted the test as reliable | All modes |
| `flaky` | SPRT rejected the test or demotion occurred | Excluded from detection/regression |
| `disabled` | Test is disabled via `disabled=True` in BUILD file | Excluded from all execution |

## Flow: New Test Entering Burn-in

### 1. Developer Adds Test

A new `test_set_test` target is added to a BUILD file.

### 2. CI Tool: Transition to burning_in

```bash
bazel run //ci_tool:main -- burn-in --status-file .tests/status //path/to:new_test
```

The CI tool sets the test state to `burning_in` with `runs=0, passes=0`.

**Components**: CI Tool (`cmd_burn_in`), Status File

### 3. Burn-in Sweep

The orchestrator (or a dedicated CI job) runs the burn-in sweep:

```
For each burning_in test:
    1. Execute the test
    2. Record pass/fail in status file
    3. Evaluate SPRT(runs, passes, min_reliability=0.99, significance=0.95)
    4. If "accept": transition to stable
       If "reject": transition to flaky
       If "continue": keep in burning_in, repeat next iteration
```

The sweep repeats up to `max_iterations` (default 200) until all tests are decided or the limit is reached.

**Components**: Burn-in (`BurnInSweep`), SPRT, Status File

### 4. Outcome

- **Stable**: Test reliably passes (e.g., ~28 consecutive passes -> SPRT accept). It now participates in detection mode and regression selection.
- **Flaky**: Test shows unreliable behavior (e.g., 15/20 runs -> SPRT reject). It is excluded from detection and regression selection.
- **Undecided**: Max iterations reached without SPRT decision. Test remains `burning_in`.

## Flow: Stable Test Demotion

There are two paths for demotion evaluation:

### Path A: Via Orchestrator (process_results)

When the orchestrator runs with `--status-file`, each result is processed without re-execution:

```
1. Stable test fails during orchestrator run
2. Record failure in status file via record_run(passed=False, commit)
3. Read full persisted history from status file (newest-first)
4. Evaluate demotion_evaluate(full_history, min_reliability, significance)
5. If "demote": transition to flaky
   If "inconclusive": transition to burning_in (suspicious, counters preserved)
   If "retain": stays stable (one-off failure)
```

This is the primary integration path. Cross-run demotion works because failures accumulate in the persisted history across separate CI invocations.

**Components**: Burn-in (`process_results`), SPRT (`demotion_evaluate`), Status File

### Path B: Via handle_stable_failure (immediate re-runs)

For dedicated demotion evaluation with re-runs:

```
For up to max_reruns (20):
    1. Re-run the test
    2. Record result with commit SHA via record_run(passed, commit)
    3. Read full persisted history from status file (newest-first)
    4. Evaluate demotion_evaluate(full_history, min_reliability, significance)
    5. If "demote": transition to flaky
       If "retain": stays stable (likely a one-off failure)
       If exhausted: inconclusive
```

**Components**: Burn-in (`handle_stable_failure`), SPRT (`demotion_evaluate`), Status File

### Outcome

- **Demote**: Persisted history shows the test has become unreliable. Transitioned to `flaky`. Commit SHAs in the history enable identifying the commit range that caused the reliability change.
- **Suspicious (inconclusive)**: Not enough evidence to demote, but the failure is concerning. Transitioned to `burning_in` for closer monitoring, with counters and history preserved.
- **Retain**: Failure was transient. Test stays `stable`.

## Flow: Deflaking

### 1. Fix Applied

Developer fixes the underlying cause of flakiness.

### 2. CI Tool: Deflake

```bash
bazel run //ci_tool:main -- deflake --status-file .tests/status //path/to:flaky_test
```

Transitions from `flaky` to `burning_in` with counters reset to `runs=0, passes=0`.

**Components**: CI Tool (`cmd_deflake`), Status File

### 3. Re-enter Burn-in Sweep

The test goes through the normal burn-in process again.

## Flow: Disabling and Re-enabling Tests

Tests can be disabled by setting `disabled = True` on their `test_set_test` target in the BUILD file. The orchestrator synchronizes this flag with the status file at startup.

### Disabling

When the orchestrator starts with `--status-file`, `sync_disabled_state` compares the DAG (from manifest) with the status file:
- Tests with `disabled=True` in the manifest that are not in "disabled" state are transitioned to "disabled" (counters and history reset).
- Disabled tests are then removed from the DAG so they are excluded from all execution.

### Re-enabling

When `disabled=True` is removed from the BUILD file:
- The manifest no longer marks the test as disabled.
- `sync_disabled_state` detects the test is in "disabled" state but not disabled in the DAG.
- The test transitions to "new" (counters reset) and must go through burn-in again.

**Components**: Burn-in (`sync_disabled_state`), DAG (`remove_disabled`), Status File

## Flow: Orchestrator Integration

When the orchestrator is invoked with `--status-file`, it auto-detects the HEAD commit SHA from git, verifies the working tree is clean (no uncommitted changes), and records all test results with the commit SHA. Use `--allow-dirty` to bypass the clean-tree check.

```
orchestrator main(--status-file path [--allow-dirty])
    |
    +---> _resolve_git_context(): verify clean tree, get HEAD SHA
    +---> sync_disabled_state(dag, status_file): sync disabled flags
    +---> dag.remove_disabled(): exclude disabled tests from DAG
    +---> execute tests (DAG order)
    |
    +---> process_results(results, status_file, commit_sha)
              |
              for each result (skip dependencies_failed):
              |
              +---> record_run(passed, commit)
              +---> save()  (incremental, crash recovery)
              |
              +---> if burning_in:
              |         sprt_evaluate(runs, passes) -> accept/reject/continue
              |
              +---> if stable + failed:
              |         demotion_evaluate(history) -> demote/retain/inconclusive
              |
              +---> if flaky/new: no evaluation (just recorded)
              |
              +---> return lifecycle events
```

This also applies to the regression path (`--regression --status-file`).

**Components**: Orchestrator Main, Burn-in (`process_results`), SPRT, Status File

## Data Flow

```
.tests/status (JSON)
    |
    v
StatusFile (in-memory)
    |
    +---> process_results(results, commit_sha)    [orchestrator integration]
    |         |
    |         for each result:
    |         +---> record_run(passed, commit=commit_sha)
    |         +---> save()  (incremental)
    |         |
    |         +---> burning_in: sprt_evaluate(runs, passes)
    |         |         +---> "accept" -> set_test_state("stable")
    |         |         +---> "reject" -> set_test_state("flaky")
    |         |         +---> "continue" -> no change
    |         |
    |         +---> stable + failed: demotion_evaluate(history)
    |                   +---> "demote" -> set_test_state("flaky")
    |                   +---> "inconclusive" -> set_test_state("burning_in")
    |                   +---> "retain" -> no change
    |
    +---> BurnInSweep.run(commit_sha)             [dedicated burn-in]
    |         |
    |         +---> execute test (subprocess)
    |         +---> record_run(passed, commit=commit_sha)
    |         +---> sprt_evaluate(runs, passes, ...)
    |         |         |
    |         |         +---> "accept" -> set_test_state("stable")
    |         |         +---> "reject" -> set_test_state("flaky")
    |         |         +---> "continue" -> next iteration
    |         |
    |         +---> save() after each run (crash recovery)
    |
    +---> handle_stable_failure(commit_sha)        [dedicated demotion]
              |
              +---> re-run test
              +---> record_run(passed, commit=commit_sha)
              +---> get_test_history() -> full persisted history
              +---> demotion_evaluate(history)
              |         |
              |         +---> "demote" -> set_test_state("flaky")
              |         +---> "retain" -> no change
              |
              +---> save()
```

## SPRT Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `min_reliability` | 0.99 | Minimum acceptable pass rate |
| `statistical_significance` | 0.95 | Confidence level for SPRT decisions |
| `margin` | 0.10 | Separation between H0 (0.99) and H1 (0.89) |

With these defaults:
- A test passing ~28 consecutive runs triggers "accept" (stable)
- A test failing 3/20 runs typically triggers "reject" (flaky)
- Borderline cases may need 50-100 runs before a decision
