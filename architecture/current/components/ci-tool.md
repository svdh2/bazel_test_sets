# Component: CI Tool

## Location

`ci_tool/main.py`

## Purpose

Command-line interface for test lifecycle management. Provides subcommands for burn-in state transitions, deflaking, status reporting, judgement re-evaluation, and co-occurrence graph construction. Acts as the human-facing CLI for operations that are not part of normal test execution.

## Interface

### Subcommands

| Command | Description | Key Flags |
|---------|-------------|-----------|
| `burn-in` | Transition tests from `new` to `burning_in` | `--status-file`, positional test names |
| `deflake` | Transition `flaky` tests back to `burning_in` with reset counters | `--status-file`, positional test names (required) |
| `test-status` | Display tabular status of all tests | `--status-file`, `--state` filter |
| `re-judge` | Re-evaluate judgements using stored measurements | `--manifest`, `--measurements-dir`, positional test names |
| `build-graph` | Build/update co-occurrence graph from git history | `--output`, `--source-extensions`, `--test-patterns`, `--max-history`, `--repo-root` |

### Usage Examples

```bash
# Transition tests to burning_in
bazel run //ci_tool:main -- burn-in --status-file .tests/status //test:a //test:b

# Deflake a flaky test
bazel run //ci_tool:main -- deflake --status-file .tests/status //test:flaky_one

# Show all stable tests
bazel run //ci_tool:main -- test-status --state stable

# Re-judge all eligible tests
bazel run //ci_tool:main -- re-judge --manifest bazel-bin/path/manifest.json

# Build co-occurrence graph
bazel run //ci_tool:main -- build-graph --output .tests/co_occurrence_graph.json
```

## Dependencies

- **Status File** (`orchestrator.lifecycle.status.StatusFile`): Used by burn-in, deflake, and test-status subcommands
- **Judgement** (`orchestrator.analysis.judgement`): Used by re-judge subcommand (lazy import)
- **Co-occurrence** (`orchestrator.regression.co_occurrence`): Used by build-graph subcommand (lazy import)

## Dependents

- **Bazel**: Invoked via `bazel run //ci_tool:main -- <subcommand>` (Bazel is triggered by the CI Script inside the container)
- **Users**: Direct CLI invocation for lifecycle management

## Bazel Target

```starlark
# ci_tool/BUILD.bazel
py_binary(
    name = "main",
    srcs = ["main.py"],
    deps = [":ci_tool_lib"],   # which depends on //orchestrator:orchestrator_lib
)
```

## Key Design Decisions

1. **Lazy imports**: The `re-judge` and `build-graph` subcommands use lazy imports to avoid loading heavy modules (judgement, co_occurrence) when they are not needed. The burn-in and deflake subcommands only need StatusFile.

2. **State machine enforcement**: The `burn-in` command only transitions from `new` (or absent) to `burning_in`. Tests in `stable` or `flaky` state cannot be burned in directly -- `flaky` tests must use `deflake` first. This enforces the state machine transitions.

3. **Tabular status display**: The `test-status` command formats output as an aligned table with state, run counts, pass counts, and last-updated dates. Supports filtering by state.

4. **Incremental graph building**: The `build-graph` command loads an existing graph and passes it to `build_co_occurrence_graph` for incremental update, avoiding re-analysis of already-processed commits.
