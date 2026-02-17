# E-values

**Path**: `orchestrator/lifecycle/e_values.py`

E-value computation for test set verdicts with aggregate Type II error control via safe testing.

## Purpose

Defines when a test set is GREEN (all tests reliable), RED (at least one test unreliable), or UNDECIDED (insufficient evidence). Controls the aggregate Type II error across N tests using the union bound on E-values, preventing the error compounding that occurs when aggregating binary per-test SPRT decisions.

## Mathematical Foundation

The SPRT log-likelihood ratio `log(L(H0)/L(H1))` is already computed in `sprt.py`. The E-value is its reciprocal:

- `S_i = exp(log_ratio)` — evidence FOR reliability of test i
- `E_i = 1/S_i = exp(-log_ratio)` — evidence AGAINST reliability (the E-value)
- Under H0 (reliable): `E[E_i] <= 1` (supermartingale property)
- Under H1 (unreliable): `E_i` grows exponentially at rate `KL(p1 || p0)`

### Test set combination

- **RED**: `E_set = (1/N) * sum(E_i) > 1/alpha_set` — at least one test unreliable (Markov on average E-value)
- **GREEN**: `min(S_i) > N/beta_set` for all tests — all tests reliable (union bound: each test contributes at most `beta_set/N` to the aggregate Type II error)
- **UNDECIDED**: neither threshold crossed

### Two modes for non-stationarity

Code changes across commits can shift the true reliability of a test. Two modes address this:

- **Quick mode**: Groups history by commit SHA, computes per-commit log ratios, sums them. This is equivalent to the product of per-commit E-values, testing the intersection "reliable at all commits".
- **High-fidelity mode**: Filters history to the current commit only. Reruns tests until thresholds are met. No stationarity assumption — all data comes from the same code.

## Data Classes

- `TestEValue` — Per-test result: `test_name`, `e_value`, `s_value`, `log_e_value`, `runs`, `passes`, `commits_included`
- `TestSetVerdict` — Aggregate result: `verdict` (GREEN/RED/UNDECIDED), `e_set`, `min_s_value`, thresholds, `per_test`, `weakest_test`
- `HiFiResult` — High-fidelity evaluation result: `verdict`, `total_reruns`, `decided`

## Public Functions

| Function | Description |
|----------|-------------|
| `compute_log_ratio(runs, passes, min_reliability, margin)` | Per-batch log(L(H0)/L(H1)); same math as `sprt.py` |
| `compute_test_e_value_quick(name, history, min_reliability, margin)` | Quick mode: pool across commits |
| `compute_test_e_value_hifi(name, history, current_commit, min_reliability, margin)` | High-fidelity: current commit only |
| `compute_test_set_verdict(test_e_values, alpha_set, beta_set)` | Apply RED/GREEN/UNDECIDED thresholds |
| `evaluate_test_set(test_names, status_file, mode, ...)` | Convenience: read history from StatusFile and compute verdict |
| `verdict_to_dict(verdict)` | JSON serialization |

## HiFiEvaluator Class

Follows the `BurnInSweep` pattern (iterative execute-record-evaluate loop):

1. Evaluate current verdict from status file history
2. If decided (GREEN or RED), return
3. Otherwise, rerun all tests once and record results
4. Repeat until decided or `max_reruns` budget exhausted

## Dependencies

- `orchestrator.lifecycle.status.StatusFile` — reads per-test history
- `orchestrator.execution.dag.TestDAG` — node lookup for HiFi test execution
- `orchestrator.execution.executor.TestResult` — result data class
