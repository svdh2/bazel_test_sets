# E-value Verdict Flow

Computes the aggregate test set verdict (GREEN/RED/UNDECIDED) using E-values with controlled Type II error.

## Trigger

Orchestrator `main()` when `--effort` implies a verdict mode and `status_file` is configured in `.test_set_config`.

## Quick Mode Flow

```
main() / _run_regression()
  |
  v
_compute_verdict(args, dag, commit_sha)
  |
  +--> StatusFile.get_test_history(name)  [for each test]
  |
  +--> compute_test_e_value_quick(name, history, min_reliability, margin)
  |      Groups history by commit SHA
  |      Computes per-commit log(L(H0)/L(H1))
  |      Sums log-ratios (= product of per-commit E-values)
  |      Returns TestEValue
  |
  +--> compute_test_set_verdict(test_e_values, alpha_set, beta_set)
  |      RED:   (1/N) * sum(E_i) > 1/alpha_set
  |      GREEN: min(S_i) > N/beta_set
  |      Otherwise: UNDECIDED
  |
  +--> verdict_to_dict(verdict) --> reporter.set_e_value_verdict()
```

## High-Fidelity Mode Flow

```
main() / _run_regression()
  |
  v
_compute_verdict(args, dag, commit_sha)
  |
  +--> HiFiEvaluator(dag, status_file, commit_sha, alpha_set, beta_set, max_reruns)
  |
  +--> evaluator.evaluate(test_names)
         |
         +--> LOOP (up to max_reruns):
         |      |
         |      +--> evaluate_test_set(mode="hifi", current_commit=commit_sha)
         |      |      Filters history to current commit
         |      |      Computes E-values from current-commit runs only
         |      |
         |      +--> If GREEN or RED: return immediately
         |      |
         |      +--> For each test: _execute_test() --> subprocess
         |      |    Record result: status_file.record_run()
         |      |
         |      +--> status_file.save()
         |
         +--> Return HiFiResult(verdict, total_reruns, decided)
```

## Integration Points

- **CLI**: `--verdict quick|hifi|off`, `--alpha-set`, `--beta-set`, `--max-reruns`
- **Report**: Verdict data included as `report.e_value_verdict` in JSON output
- **HTML**: Rendered as verdict badge with per-test E-value table
- **Exit code**: Currently informational only; does not override the execution-based exit code
