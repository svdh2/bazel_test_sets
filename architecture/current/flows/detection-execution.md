# Flow: Detection Execution

## Purpose

Fast-feedback test run in roots-first BFS order without dependency gating. Designed for quick detection of high-level failures, with optional early termination via `--max-failures`.

## Trigger

```bash
bazel run //path/to:my_test_set -- --mode detection --max-failures 3
```

## Differences from Diagnostic Mode

| Aspect | Diagnostic | Detection |
|--------|-----------|-----------|
| **Ordering** | Leaves first (topological sort) | Roots first (BFS) |
| **Dependency gating** | Tests skip if dependency failed | Tests run regardless of dependency status |
| **Primary use** | Full CI run, thorough analysis | Fast feedback, find failures quickly |
| **Failure propagation** | `dependencies_failed` status used | No `dependencies_failed` status |

## Steps

### 1-2. Runner and Manifest Loading

Same as [Diagnostic Execution](diagnostic-execution.md).

### 3. DAG Construction

Same as diagnostic. Cycle detection still applies.

### 4. BFS Roots-First Ordering

`dag.bfs_roots_first()` starts from root nodes (tests that no other test depends on) and explores dependencies via BFS.

Example ordering for the same e-commerce DAG:
```
shipping -> email_notification -> order_placement -> user_profile ->
discount -> cart -> inventory -> credit_card -> user_login ->
user_registration -> paypal
```

This hits the high-level integration tests first, providing fast feedback on whether the overall system is broken.

### 5. Test Execution

For each test in BFS order:

1. **Check max_failures**: If threshold reached, stop execution.
2. **Execute**: Run the test (no dependency check -- tests run regardless).
3. **Record result**: Store `TestResult` with pass/fail status.

In parallel mode, tests are dispatched when their dependencies have completed (not necessarily passed), maintaining execution ordering constraints while not gating on pass/fail.

### 6-7. Summary and Reports

Same as diagnostic mode. The mode label shows "detection" in output.

## When to Use

- **Pre-merge checks**: Run detection mode with `--max-failures 1` to get the fastest possible signal on whether the change breaks anything.
- **Developer iteration**: Quick check before pushing, where knowing "something is broken" is more important than knowing "everything about what is broken."
- **Triage**: When a CI run fails in diagnostic mode, re-run in detection mode to quickly verify specific failures.

## Data Flow

```
manifest.json
    |
    v
TestDAG.from_manifest()
    |
    v
dag.bfs_roots_first()
    |
    v
[root1, root2, dep1, dep2, leaf1, ...]  (BFS order)
    |
    v
Executor.execute()  (no dependency gating)
    |
    v
list[TestResult]  (may stop early at max_failures)
```
