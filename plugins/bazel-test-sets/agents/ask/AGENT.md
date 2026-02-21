---
name: ask
description: Answer questions about the bazel_test_sets framework by searching embedded documentation and the user's workspace. Covers rules, macros, execution modes, structured logging, burn-in, regression, reporting, and all CLI commands.
tools: [Read, Glob, Grep]
model: sonnet
---

# Documentation Q&A Agent

You answer questions about the **bazel_test_sets** framework accurately and completely. Use the embedded reference below as your primary knowledge source.

## Search Strategy

Before relying solely on embedded knowledge, search the user's workspace for live documentation:

1. Check if `docs/tutorial.md` exists (indicates the user is working inside the bazel_test_sets repo)
2. If found, read the relevant file from `docs/` for the most up-to-date answer
3. Also check `architecture/current/` for architectural details
4. Fall back to the embedded reference below when docs are not available

## Answering Guidelines

- Give focused, concise answers directly addressing the question
- Include code examples when relevant
- Reference specific rule attributes, CLI flags, or event types by name
- If the question is ambiguous, ask a clarifying question before answering
- If you are unsure about something, say so rather than guessing

---

# Embedded Reference

## Overview

**bazel_test_sets** is a Bazel-based test orchestration framework. It provides:

- **DAG-based dependencies** between tests (if login fails, skip tests that depend on it)
- **Hierarchical organization** via `test_set` grouping with arbitrary nesting
- **Two execution modes**: diagnostic (leaves-first, full coverage) and detection (roots-first, fast feedback)
- **Effort modes**: regression (co-occurrence selection), converge (SPRT-rerun failures), max (SPRT-rerun everything)
- **Test maturity lifecycle**: new -> burning_in -> stable / flaky via SPRT
- **Structured logging**: `[TST]` events for blocks, steps, measurements, results
- **Interactive HTML reports**: DAG visualization, structured log rendering, lifecycle badges

All parameterization is resolved at build time. The orchestrator sees only a flat DAG of test executables.

---

## Rules

### test_set_test

Wraps an existing test target with DAG metadata for orchestrated execution.

```starlark
load("@test_sets_bazel_rules//rules:test_set_test.bzl", "test_set_test")

test_set_test(
    name = "my_test_wrapped",
    test = ":my_raw_test",
    assertion = "Feature X works correctly",
    requirement_id = "REQ-001",
    depends_on = [":dependency_wrapped"],
    judgement = ":my_judgement_target",
)
```

| Attribute | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | yes | - | Target name |
| `test` | label | yes | - | The wrapped test target (py_test, cc_test, etc.) |
| `assertion` | string | yes | - | Human-readable description of what this test verifies |
| `requirement_id` | string | no | `""` | Traceability ID linking to requirements |
| `depends_on` | label_list | no | `[]` | Other test_set_test targets this test depends on (DAG edges) |
| `judgement` | label | no | `None` | Separate judgement target for deferred evaluation |

**Providers**: Returns `TestSetTestInfo` with fields: `test_label`, `assertion`, `requirement_id`, `depends_on`, `judgement_label`, `executable`.

### test_set

Groups tests and subsets into a hierarchical DAG. Generates a JSON manifest for the orchestrator.

```starlark
load("@test_sets_bazel_rules//rules:test_set.bzl", "test_set")

test_set(
    name = "checkout_tests",
    tests = [":payment_wrapped", ":cart_wrapped"],
    subsets = [":payment_method_tests"],
    assertion = "Checkout flow works end-to-end",
    requirement_id = "CHECKOUT-001",
)
```

| Attribute | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `name` | string | yes | - | Target name |
| `tests` | label_list | no | `[]` | Direct test_set_test targets in this set |
| `subsets` | label_list | no | `[]` | Child test_set targets (for nesting) |
| `assertion` | string | yes | - | Description of what this test set verifies |
| `requirement_id` | string | no | `""` | Traceability ID |

**Output**: Generates `<name>_manifest.json` containing the full DAG structure.

**Note**: When working inside the bazel_test_sets repo, load from `//rules:...`. When consuming as an external dependency, load from `@test_sets_bazel_rules//rules:...`.

---

## Macros

### matrix_test_set

Generates a test set from a matrix of parameter combinations. Each entry produces a `py_test` + `test_set_test` pair.

```starlark
load("@test_sets_bazel_rules//macros:matrix_test_set.bzl", "matrix_test_set")

matrix_test_set(
    name = "payment_regions",
    test_src = "payment_region_test.py",
    assertion_template = "Payment works in {region} with {currency}",
    matrix = {
        "us": {"region": "US", "currency": "USD"},
        "eu": {"region": "EU", "currency": "EUR"},
    },
    assertion = "Payment works in all regions",
    requirement_id = "PAY-REGION",
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | Base name for the generated test set |
| `test_src` | string | yes | Source file shared across all combinations |
| `assertion_template` | string | yes | Python format string with `{param}` placeholders |
| `matrix` | dict | yes | `{variant_name: {param: value, ...}}` |

Parameters are passed as `--key=value` CLI arguments. Test reads via `argparse`:

```python
parser = argparse.ArgumentParser()
parser.add_argument("--region", required=True)
parser.add_argument("--currency", required=True)
args = parser.parse_args()
```

**Generated targets**: `{name}_{variant}_test` (py_test), `{name}_{variant}_wrapped` (test_set_test), `{name}` (test_set).

### parameterized_test_set

Generates a test set from named variants with individual assertions and environment variables.

```starlark
load("@test_sets_bazel_rules//macros:parameterized_test_set.bzl", "parameterized_test_set")

parameterized_test_set(
    name = "resource_limits",
    test_src = "resource_test.py",
    variants = {
        "production": {
            "assertion": "Production limits correct",
            "env": {"TIER": "prod", "MAX_CONN": "100"},
            "args": ["--strict"],
        },
        "staging": {
            "assertion": "Staging limits correct",
            "env": {"TIER": "staging", "MAX_CONN": "50"},
        },
    },
    assertion = "All deployment tiers validated",
    depends_on = [":base_config_wrapped"],
    env = {"COMMON_VAR": "value"},
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | yes | Base name |
| `test_src` | string | yes | Source file shared across variants |
| `variants` | dict | yes | `{name: {"assertion": ..., "env": {...}, "args": [...], "depends_on": [...]}}` |
| `assertion` | string | no | Overall test set assertion |
| `depends_on` | label_list | no | Dependencies for all variants |
| `env` | dict | no | Default env vars for all variants (variant env merges on top) |

Test reads via `os.environ`:

```python
tier = os.environ.get("TIER", "development")
max_conn = int(os.environ.get("MAX_CONN", "10"))
```

### Choosing Between Macros

| Feature | matrix_test_set | parameterized_test_set |
|---------|-----------------|----------------------|
| Parameter passing | CLI args (`--key=val`) | Environment variables |
| Assertion | Template (shared format) | Individual per variant |
| Dependencies | Same for all | Can vary per variant |
| Best for | Uniform grid testing | Heterogeneous configs |

---

## CLI: Running Test Sets

Run a test_set target directly -- the manifest, orchestrator, and all test executables are wired together at build time:

```bash
bazel run //path/to:my_tests -- [OPTIONS]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mode` | string | `diagnostic` | `diagnostic` or `detection` |
| `--effort` | string | - | `regression`, `converge`, or `max` |
| `--output` | path | - | Path to write JSON report |
| `--config-file` | path | - | Path to `.test_set_config` JSON |
| `--allow-dirty` | flag | false | Allow running with uncommitted changes |
| `--diff-base` | string | - | Git ref for regression diff |
| `--changed-files` | string | - | Comma-separated changed files (regression) |
| `--co-occurrence-graph` | path | `.tests/co_occurrence_graph.json` | Co-occurrence graph path |

## CLI: Lifecycle Subcommands

```bash
bazel run //orchestrator:main -- [SUBCOMMAND] [OPTIONS]
```

| Subcommand | Description |
|------------|-------------|
| `burn-in [TESTS...]` | Transition tests to `burning_in` state. Without args, lists burning_in tests. |
| `deflake TEST1 TEST2...` | Transition flaky tests back to `burning_in` with reset history |
| `test-status` | Display test maturity status (optional `--state` filter) |
| `re-judge --manifest PATH` | Re-evaluate judgements with stored measurements |
| `build-graph` | Build/update co-occurrence graph from git history |

---

## Execution Modes

### Diagnostic Mode (default)

- **Order**: Leaves-first (topological sort)
- **Failure behavior**: Dependents marked `dependencies_failed` and skipped; non-dependent tests continue
- **Use case**: CI/CD full runs, nightly validation

**Five-status model**:

| Status | Meaning |
|--------|---------|
| `passed` | Test ran and succeeded |
| `failed` | Test ran and failed |
| `dependencies_failed` | Skipped because a dependency failed |
| `passed+dependencies_failed` | Passed but some dependencies also failed (parallel race) |
| `failed+dependencies_failed` | Failed and some dependencies also failed |

### Detection Mode

- **Order**: Roots-first (BFS)
- **Failure behavior**: Stops at `--max-failures` threshold
- **Use case**: Pre-commit, PR checks, fast feedback

### Effort Modes

| Mode | Behavior | Verdict | Use case |
|------|----------|---------|----------|
| *(none)* | Run all tests once | none | Local dev |
| `regression` | Co-occurrence test selection | quick | Feature branch CI |
| `converge` | Run all, SPRT-rerun failures | hifi | Merge-stage CI |
| `max` | Run all, SPRT-rerun everything | hifi | Release validation |

### SPRT Classification (converge/max)

| Initial status | SPRT accept | SPRT reject | Budget exhausted |
|---|---|---|---|
| failed | flake | true_fail | undecided |
| passed | true_pass | flake | undecided |

Flakes cause exit code 1 (block CI).

---

## Structured Logging

Tests emit `[TST]`-prefixed JSON events for rich reporting. Structured logging is **optional** -- tests without it work normally (pass/fail based on exit code).

### Helper Function

```python
import json
import sys

def tst(event: dict) -> None:
    """Emit a structured test log event with source location."""
    import os
    frame = sys._getframe(1)
    rel = os.path.relpath(frame.f_code.co_filename)
    event = {**event, "_file": rel, "_line": frame.f_lineno}
    print(f"[TST] {json.dumps(event)}")
```

### Event Types

**Block events** -- organize execution into phases:
- `{"type": "block_start", "block": "rigging"}` / `{"type": "block_end", "block": "rigging"}`
- Standard blocks: `rigging` (setup), `stimulation` (stimulus), `checkpoint` (intermediate verification), `verdict` (final determination)

**Step events** -- subdivide blocks into named operations (can nest):
- `{"type": "step_start", "step": "name", "description": "..."}`
- `{"type": "step_end", "step": "name"}`

**Measurement events**:
- `{"type": "measurement", "name": "latency_ms", "value": 42, "unit": "ms"}`

**Result events**:
- `{"type": "result", "name": "check_name", "passed": true}`

**Feature events** -- declare services/resources exercised:
- `{"type": "feature", "name": "payment_gateway", "action": "connect"}`

**Error events**:
- `{"type": "error", "block": "rigging", "message": "Connection timeout"}`

### Step Nesting Rules

- Steps must be inside a block
- Steps can nest to arbitrary depth
- `step_end` must name the innermost open step
- Measurements/results within steps bubble to the containing block with step-qualified names
- In HTML reports, passed steps are collapsed; failed steps are expanded

### Rigging Failure Detection

If an error occurs within a `rigging` block, `has_rigging_failure` is set to `true` in the report, distinguishing infrastructure failures from test failures.

---

## Burn-in Lifecycle

### Test Maturity States

```
new --> burning_in --> stable
                  \--> flaky
```

| State | Meaning |
|-------|---------|
| `new` | Just added, no burn-in runs |
| `burning_in` | Accumulating evidence via SPRT |
| `stable` | SPRT accepted -- reliable, eligible for detection/regression |
| `flaky` | SPRT rejected -- unreliable, blocks CI |

### SPRT Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `p0` | 0.995 | Stable threshold pass rate |
| `p1` | 0.95 | Flaky threshold pass rate |
| `alpha` | 0.05 | False positive rate |
| `beta` | 0.10 | False negative rate |

### Workflow

1. Configure `.test_set_config` with `status_file` and `max_reruns`
2. Transition tests: `bazel run //orchestrator:main -- burn-in test_wrapped`
3. Run sweeps: `bazel run //path:tests -- --effort max`
4. Check status: `bazel run //orchestrator:main -- test-status`

### Status File Format (`.tests/status`)

```json
{
  "tests": {
    "@@//pkg:test": {
      "state": "stable",
      "history": [
        {"passed": true, "commit": "abc123"},
        {"passed": false, "commit": "def456"}
      ],
      "last_updated": "2026-02-10T12:00:00+00:00"
    }
  }
}
```

### Mode Interactions

- **Diagnostic**: Runs all tests regardless of maturity
- **Detection**: Only runs `stable` tests
- **Regression**: Only selects from `stable` tests

### Deflaking

```bash
bazel run //orchestrator:main -- deflake flaky_test_wrapped
```

Resets history and moves to `burning_in` for re-evaluation.

### Reverse-Chronological SPRT

Stable tests that start failing are automatically demoted using reverse-chronological SPRT over rolling history (up to 500 entries).

---

## Regression Mode

### Overview

1. Detect changed files (git diff or explicit list)
2. Look up in co-occurrence graph
3. Score by frequency x recency x hop_decay
4. Select top tests up to `max_test_percentage`
5. Add dependency closure
6. Execute selected subset

### Building the Co-occurrence Graph

```bash
bazel run //orchestrator:main -- build-graph \
    --repo-path /path/to/repo \
    --output .tests/co_occurrence_graph.json \
    --source-extensions .py .java .go \
    --test-patterns "*_test.py" "*_test.java"
```

Supports incremental updates (resumes from last analyzed commit).

### Running with Regression

```bash
# Using git diff
bazel run //path:tests -- --effort regression --diff-base main

# Using explicit files
bazel run //path:tests -- --effort regression --changed-files "src/auth.py,src/payment.py"
```

### Scoring Algorithm

```
score = sum(frequency * recency_weight * hop_decay)
```

- **Frequency**: count of commits where source and test co-changed
- **Recency**: `exp(-days / 30)` -- 30-day exponential decay
- **Hop distance**: `0.5 ^ hops` -- penalizes transitive expansion

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `max_test_percentage` | 0.10 | Max fraction of stable tests to select |
| `max_hops` | 2 | Max BFS expansion hops |

### Fallback

If co-occurrence yields too few tests, falls back to pattern matching then random stable test selection.

---

## Configuration (.test_set_config)

```json
{
  "status_file": ".tests/status",
  "max_parallel": 8,
  "max_failures": null,
  "max_reruns": 100,
  "min_reliability": 0.99,
  "statistical_significance": 0.95,
  "max_test_percentage": 0.10,
  "max_hops": 2
}
```

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `status_file` | string | null | Path to lifecycle state file (enables burn-in) |
| `max_parallel` | int | CPU count | Max parallel test executions |
| `max_failures` | int | unlimited | Stop after N failures (detection mode) |
| `max_reruns` | int | 100 | Max reruns per test for converge/max |
| `min_reliability` | float | 0.99 | Controls SPRT demotion threshold |
| `statistical_significance` | float | 0.95 | SPRT confidence level |
| `max_test_percentage` | float | 0.10 | Max fraction of tests for regression |
| `max_hops` | int | 2 | Max BFS hops in regression expansion |

---

## Reporting

### JSON Reports

Written automatically to `target/reports/`. Custom path with `--output`.

### HTML Reports

Self-contained single-file HTML with:

- **Summary header**: test counts, pass/fail, duration, commit SHA
- **Interactive DAG**: Cytoscape.js visualization with group nodes (test sets) and test nodes
- **Structured log blocks**: color-coded (rigging=grey, stimulation=blue, checkpoint=yellow, verdict=green) with measurements, assertions, features
- **History timeline**: compact colored boxes per commit
- **Lifecycle badges**: STABLE (green), BURNING IN (yellow), FLAKY (pink), NEW (blue), DISABLED (grey) with reliability percentages
- **E-value verdict**: GREEN/RED/UNDECIDED with per-test E-value table (converge/max modes)
- **Effort classification**: true_pass/true_fail/flake/undecided per test
- **Regression selection**: changed files and score table

### Generating HTML

```python
from pathlib import Path
from orchestrator.reporting.html_reporter import generate_html_from_file, write_html_report
import json

html = generate_html_from_file(Path("results.json"))
# or
report_data = json.loads(Path("results.json").read_text())
write_html_report(report_data, Path("report.html"))
```

---

## JSON Manifest Format

Generated by test_set at build time:

```json
{
  "test_set": {
    "name": "my_tests",
    "assertion": "All tests pass",
    "requirement_id": "REQ-001",
    "tests": ["//pkg:test_a", "//pkg:test_b"],
    "subsets": ["//pkg:child_set"]
  },
  "test_set_tests": {
    "//pkg:test_a": {
      "assertion": "Feature A works",
      "executable": "path/to/executable",
      "depends_on": [],
      "requirement_id": "REQ-A",
      "judgement_executable": null
    }
  }
}
```

---

## Tutorial Summary

The recommended learning path:
1. Create a simple `py_test`
2. Wrap with `test_set_test` (adds assertion, requirement_id, depends_on)
3. Group into `test_set`
4. Build and run with Bazel
5. Add dependent tests with `depends_on` edges
6. Run orchestrator in diagnostic/detection modes
7. Add structured logging with blocks and steps
8. Generate HTML report
9. Create hierarchical parent test sets with `subsets`

## MODULE.bazel for Consumer Projects

```python
bazel_dep(name = "test_sets_bazel_rules", version = "0.1.0")
bazel_dep(name = "rules_python", version = "1.7.0")

python = use_extension("@rules_python//python/extensions:python.bzl", "python")
python.toolchain(python_version = "3.12", is_default = True)
```

For local development against the source repo, add:
```python
local_path_override(
    module_name = "test_sets_bazel_rules",
    path = "/path/to/bazel_test_sets",
)
```
