# Flow: Burn-in Lifecycle

## Purpose

Manages test maturity through a state machine driven by SPRT (Sequential Probability Ratio Test). New tests must prove their reliability before participating in detection mode and regression selection. Flaky tests are quarantined until fixed.

## State Machine

```
                 CI tool: burn-in
    new  --------------------------->  burning_in
                                          |
                              +-----------+-----------+
                              |                       |
                        SPRT accept              SPRT reject
                              |                       |
                              v                       v
                           stable                   flaky
                              |                       |
                     demotion (repeated           CI tool: deflake
                      failure + SPRT)                 |
                              |                       |
                              +----------> flaky <----+
                                             |
                                     CI tool: deflake
                                             |
                                             v
                                         burning_in
                                       (counters reset)
```

## States

| State | Description | Participates In |
|-------|-------------|-----------------|
| `new` | Test has been added but not yet evaluated | Diagnostic only |
| `burning_in` | Test is being repeatedly run for SPRT evaluation | Diagnostic only |
| `stable` | SPRT accepted the test as reliable | All modes |
| `flaky` | SPRT rejected the test or demotion occurred | Excluded from detection/regression |

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

- **Stable**: Test reliably passes (e.g., 20/20 runs -> SPRT accept). It now participates in detection mode and regression selection.
- **Flaky**: Test shows unreliable behavior (e.g., 15/20 runs -> SPRT reject). It is excluded from detection and regression selection.
- **Undecided**: Max iterations reached without SPRT decision. Test remains `burning_in`.

## Flow: Stable Test Demotion

### 1. Stable Test Fails

During normal execution, a test with `stable` state fails.

### 2. Demotion Evaluation

```
For up to max_reruns (20):
    1. Re-run the test
    2. Record result
    3. Evaluate demotion_evaluate(recent_history, min_reliability, significance)
    4. If "demote": transition to flaky
       If "retain": stays stable (likely a one-off failure)
       If exhausted: inconclusive
```

**Components**: Burn-in (`handle_stable_failure`), SPRT (`demotion_evaluate`), Status File

### 3. Outcome

- **Demote**: Recent history shows the test has become unreliable. Transitioned to `flaky`.
- **Retain**: Re-runs show the failure was transient. Test stays `stable`.

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

## Data Flow

```
.tests/status (JSON)
    |
    v
StatusFile (in-memory)
    |
    +---> BurnInSweep.run()
    |         |
    |         +---> execute test (subprocess)
    |         +---> record_run(passed)
    |         +---> sprt_evaluate(runs, passes, ...)
    |         |         |
    |         |         +---> "accept" -> set_test_state("stable")
    |         |         +---> "reject" -> set_test_state("flaky")
    |         |         +---> "continue" -> next iteration
    |         |
    |         +---> save() after each run (crash recovery)
    |
    +---> handle_stable_failure()
              |
              +---> re-run test
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
- A test passing 20/20 runs typically triggers "accept" (stable)
- A test failing 3/20 runs typically triggers "reject" (flaky)
- Borderline cases may need 50-100 runs before a decision
