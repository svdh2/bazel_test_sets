# Component: SPRT

## Location

`orchestrator/lifecycle/sprt.py`

## Purpose

Pure Python implementation of the Sequential Probability Ratio Test (SPRT) for two use cases: burn-in evaluation (accept/reject a test as stable/flaky) and demotion evaluation (determine if a stable test should be demoted to flaky based on recent history).

## Interface

### sprt_evaluate

```python
def sprt_evaluate(
    runs: int,
    passes: int,
    min_reliability: float,     # e.g., 0.99
    significance: float,        # e.g., 0.95
    margin: float = 0.10,
) -> str   # "accept", "reject", or "continue"
```

Tests H0 (reliability >= min_reliability) against H1 (reliability < min_reliability - margin). Returns:
- `"accept"`: Sufficient evidence the test is reliable (transition to stable)
- `"reject"`: Sufficient evidence the test is unreliable (transition to flaky)
- `"continue"`: More evidence needed

### demotion_evaluate

```python
def demotion_evaluate(
    test_history: list[bool],   # Newest-first pass/fail records
    min_reliability: float,
    significance: float,
    margin: float = 0.10,
) -> str   # "demote", "retain", or "inconclusive"
```

Processes history from newest to oldest. When SPRT reaches sufficient confidence, compares empirical reliability against the threshold:
- `"demote"`: Observed reliability below min_reliability (transition to flaky)
- `"retain"`: Observed reliability meets threshold (stays stable)
- `"inconclusive"`: Exhausted history without reaching confidence

## Dependencies

- **math** (standard library): `math.log`, `math.exp` for log-likelihood ratio computation

## Dependents

- **Burn-in** (`orchestrator.lifecycle.burnin`): Calls `sprt_evaluate` after each test run in the sweep loop; calls `demotion_evaluate` for stable test failure handling

## Key Design Decisions

1. **Symmetric error rates**: Both Type I (alpha) and Type II (beta) error rates are set to `1 - significance`, making the test symmetric. With significance=0.95, both alpha and beta are 0.05.

2. **Configurable margin**: The margin parameter (default 0.10) defines the separation between H0 and H1 hypotheses. A min_reliability of 0.99 with margin 0.10 tests 0.99 vs 0.89, creating a meaningful gap for the SPRT to discriminate.

3. **Numerical safety**: All probability values are clamped to avoid `log(0)` errors. When significance is exactly 1.0, alpha/beta are set to 1e-10.

4. **Reverse-chronological for demotion**: The demotion function processes history newest-first, giving more weight to recent behavior. SPRT naturally stops early if recent results strongly indicate flakiness or stability.
