# Flow: Diagnostic Execution

## Purpose

Full CI test run in leaves-first topological order with dependency failure propagation. Designed for thorough testing where all tests run and dependency relationships are enforced.

## Trigger

```bash
bazel test //path/to:my_test_set
# or
bazel run //path/to:my_test_set -- --mode diagnostic
```

`diagnostic` is the default mode.

## Steps

### 1. Runner Script Invoked

The Bazel-generated `{name}_runner.sh` starts the orchestrator:

```
orchestrator/main.py --manifest {manifest_path} --output {report_path}
```

### 2. Manifest Loading

`main.py` reads the JSON manifest from disk and validates its structure.

**Components**: Orchestrator Main

### 3. DAG Construction

`TestDAG.from_manifest()` creates `TestNode` objects from the manifest, computes reverse edges (dependents), and validates the graph is acyclic.

**Components**: DAG

### 4. Topological Sort (Leaves First)

`dag.topological_sort_leaves_first()` produces execution order using Kahn's algorithm on the dependency graph. Nodes with no dependencies appear first.

Example ordering for an e-commerce DAG:
```
credit_card -> paypal -> cart -> user_registration -> inventory ->
discount -> user_login -> order_placement -> user_profile ->
shipping -> email_notification
```

**Components**: DAG

### 5. Test Execution

For each test in order:

1. **Check max_failures**: If the failure threshold is reached, stop execution.
2. **Check dependency failures**: If any dependency has status `failed` or `dependencies_failed`, mark this test as `dependencies_failed` and skip it.
3. **Execute**: Run the test executable via `subprocess.run`, capturing stdout/stderr.
4. **Record result**: Create a `TestResult` with status, duration, and output.

For parallel execution (`--max-parallel > 1`), the AsyncExecutor uses a semaphore-based sliding window:
- Tests are dispatched as soon as all their dependencies complete successfully
- Failed dependencies cause immediate `dependencies_failed` marking
- Race conditions (dependency fails while dependent is running) produce combined statuses

**Components**: Executor (SequentialExecutor or AsyncExecutor)

### 6. Result Summary

Print a summary to stdout:
```
Mode: diagnostic
Tests executed: 11

  [PASS] credit_card - Credit card payments authorized (0.42s)
  [PASS] cart - Shopping cart calculates totals (0.38s)
  [FAIL] inventory - Inventory queries accurate (0.51s)
         AssertionError: stock mismatch
  [SKIP] order_placement - Full order completes (0.00s)

Results: 9 passed, 1 failed, 1 skipped
```

**Components**: Orchestrator Main

### 7. Report Generation (if --output)

If `--output` is specified:
- Generate JSON report with summary, test results, and optional structured log data
- Generate HTML report at the same path with `.html` extension

**Components**: Reporter, HTML Reporter

## Data Flow

```
manifest.json
    |
    v
TestDAG.from_manifest()
    |
    v
dag.topological_sort_leaves_first()
    |
    v
[name1, name2, name3, ...]  (execution order)
    |
    v
Executor.execute()
    |
    +---> subprocess.run(test_executable)
    |         |
    |         v
    |     TestResult(status, duration, stdout, stderr)
    |
    v
list[TestResult]
    |
    +---> stdout summary
    +---> Reporter.write_report()
    +---> write_html_report()
```

## Error Handling

| Error | Behavior |
|-------|----------|
| Manifest not found | Exit code 1, error to stderr |
| Invalid JSON | Exit code 1, error to stderr |
| DAG cycle detected | Exit code 1, cycle path in error message |
| Test timeout | `TestResult(status="failed", stderr="timed out after Ns")` |
| Executable not found | `TestResult(status="failed", stderr="Executable not found: ...")` |
| Dependency failed | `TestResult(status="dependencies_failed")`, test not executed |

## Exit Code

- **0**: All executed tests passed
- **1**: At least one test failed (or an error occurred during setup)
