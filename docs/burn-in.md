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

Use the CI tool to run burn-in tests:

```bash
bazel run //ci_tool:main -- burn-in \
    --manifest manifest.json \
    --status-file .tests/status \
    --max-runs 30
```

The burn-in process:
1. Loads the status file to find tests in `new` or `burning_in` state
2. Runs each test repeatedly
3. After each run, evaluates SPRT
4. Updates the status file with results
5. Stops when SPRT reaches a decision or `--max-runs` is reached

## Status File Format

The `.tests/status` file tracks test maturity state:

```json
{
  "tests": {
    "my_test_wrapped": {
      "maturity": "stable",
      "burn_in_runs": 30,
      "burn_in_passes": 30,
      "sprt_decision": "accept",
      "last_run": "2026-02-10T12:00:00+00:00"
    },
    "flaky_test_wrapped": {
      "maturity": "flaky",
      "burn_in_runs": 25,
      "burn_in_passes": 18,
      "sprt_decision": "reject",
      "last_run": "2026-02-10T12:00:00+00:00"
    }
  }
}
```

## Mode Interactions

Burn-in status affects execution modes:

- **Diagnostic mode**: Runs all tests regardless of maturity
- **Detection mode**: Only runs `stable` tests for fast reliable feedback
- **Regression mode**: Only selects from `stable` tests for co-occurrence scoring

## Deflaking

After a test is marked `flaky`, you can re-evaluate it:

```bash
bazel run //ci_tool:main -- deflake \
    --manifest manifest.json \
    --status-file .tests/status
```

This re-runs flaky tests to check if a fix has made them stable.

## Reverse-Chronological SPRT

For stable tests that start failing, the system uses reverse-chronological
SPRT over the rolling history (up to 500 entries) to detect degradation.
This allows automatic demotion of previously stable tests that have become
unreliable.

The rolling history is maintained in the YAML report and can be used to
compute SPRT in reverse order (most recent results first) for faster
detection of recent degradation.
