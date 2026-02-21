# Burn-in Guide

The burn-in system manages test maturity through a lifecycle that
distinguishes new, unreliable tests from proven stable ones.

## Test Maturity Lifecycle

```
new --> burning_in --> stable
                  \--> flaky
```

| State | Meaning |
|-------|---------|
| `new` | Just added, no burn-in runs yet |
| `burning_in` | Currently accumulating evidence via repeated runs |
| `stable` | SPRT accepted -- test is reliable enough for detection/regression |
| `flaky` | SPRT rejected -- test fails too often to be trusted |

## SPRT (Sequential Probability Ratio Test)

The burn-in process uses SPRT to decide whether a test is stable or flaky
without requiring a fixed number of runs. SPRT evaluates the pass rate
against two hypotheses:

- **H0** (stable): True pass rate >= `p0` (default: 0.995)
- **H1** (flaky): True pass rate <= `p1` (default: 0.95)

After each run, SPRT computes a likelihood ratio and compares it against
bounds derived from the desired error rates (alpha, beta):

- **Accept H0** (stable): Likelihood ratio exceeds upper bound
- **Reject H0** (flaky): Likelihood ratio falls below lower bound
- **Continue**: More data needed

### Default Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `p0` | 0.995 | Null hypothesis pass rate (stable threshold) |
| `p1` | 0.95 | Alternative hypothesis pass rate (flaky threshold) |
| `alpha` | 0.05 | False positive rate (marking flaky as stable) |
| `beta` | 0.10 | False negative rate (marking stable as flaky) |

## Running Burn-in

Burn-in is a two-step process: first transition tests into the `burning_in`
state, then run the orchestrator with an effort mode that evaluates SPRT.

### 1. Configure `.test_set_config`

Set `status_file` and `max_reruns` in `.test_set_config`:

```json
{
  "status_file": ".tests/status",
  "max_reruns": 100,
  "min_reliability": 0.99,
  "statistical_significance": 0.95
}
```

### 2. Transition tests to burning\_in

Use the orchestrator to move tests from `new` to `burning_in`:

```bash
bazel run //orchestrator:main -- burn-in my_test_wrapped
```

Without test names, it lists all tests currently in `burning_in` state:

```bash
bazel run //orchestrator:main -- burn-in
```

### 3. Run the orchestrator with effort mode

The orchestrator performs the actual repeated execution with SPRT evaluation.
Use `--effort converge` (reruns failures) or `--effort max` (reruns all):

```bash
bazel run //path/to:my_tests -- --effort max
```

The burn-in process:
1. Loads the status file to find tests in `burning_in` state
2. Runs each test, then reruns using SPRT up to `max_reruns`
3. After each rerun, evaluates SPRT against the configured thresholds
4. Updates the status file with results
5. Stops when SPRT reaches a decision or the rerun budget is exhausted

## Status File Format

The `.tests/status` file tracks test maturity state. Each test entry has a
`state`, a rolling `history` of pass/fail results per commit, and a
`last_updated` timestamp:

```json
{
  "tests": {
    "@@//pkg:my_test": {
      "state": "stable",
      "history": [
        {"passed": true, "commit": "abc123"},
        {"passed": true, "commit": "abc123"},
        {"passed": true, "commit": "def456"}
      ],
      "last_updated": "2026-02-10T12:00:00+00:00"
    },
    "@@//pkg:flaky_test": {
      "state": "flaky",
      "history": [
        {"passed": true, "commit": "abc123"},
        {"passed": false, "commit": "abc123"},
        {"passed": true, "commit": "def456"}
      ],
      "last_updated": "2026-02-10T12:00:00+00:00"
    }
  }
}
```

Runs and passes are derived from the `history` array (count of entries and
count where `passed` is true). SPRT decisions are computed on the fly from
this history against the thresholds in `.test_set_config`.

## Mode Interactions

Burn-in status affects execution modes:

- **Diagnostic mode**: Runs all tests regardless of maturity
- **Detection mode**: Only runs `stable` tests for fast reliable feedback
- **Regression flag**: Only selects from `stable` tests for co-occurrence scoring

## Deflaking

After a test is marked `flaky`, transition it back to `burning_in` so the
next orchestrator run re-evaluates it with SPRT:

```bash
bazel run //orchestrator:main -- deflake flaky_test_wrapped
```

This resets the test's history and moves it to `burning_in`. Run the
orchestrator again with `--effort converge` or `--effort max` to re-evaluate.

## Reverse-Chronological SPRT

For stable tests that start failing, the system uses reverse-chronological
SPRT over the rolling history (up to 500 entries) to detect degradation.
This allows automatic demotion of previously stable tests that have become
unreliable.

The rolling history is maintained in the JSON report and can be used to
compute SPRT in reverse order (most recent results first) for faster
detection of recent degradation.
