# Component: CI Script

## Location

`./ci` (project root)

## Purpose

The outermost entry point for all build, test, and validation operations. A Python Click CLI script that, when run on the host, builds the Docker image and re-invokes itself inside the container via `_delegate_to_container`. Inside the container, it executes Bazel commands (build, test, run) directly. This dual-mode design means the same script serves as both the host-side launcher and the container-side command dispatcher.

## Interface

### Commands

| Command | Host | Container | Description |
|---------|------|-----------|-------------|
| `build` | Delegates | `bazel build //...` | Build all Bazel targets |
| `test` | Delegates | `bazel test //...` + `pytest` | Run all Bazel tests and Python unit tests |
| `check` | Delegates | `mypy` | Run type checks |
| `test-examples` | Delegates | `bazel test //...` in examples/ + ci_gate lifecycle demo | Test the examples module via ci_gate targets |
| `query <pattern>` | Delegates | `bazel query` | Query Bazel targets |
| `shell` | Host only | N/A | Interactive bash in container |
| `clean` | Host only | N/A | Remove target/ directory |
| `rebuild-image` | Host only | N/A | Force rebuild Docker image (no cache) |

### Environment Detection

The script uses the `IN_CONTAINER` environment variable (set to `"1"` in the Dockerfile) to determine whether it is running on the host or inside the container.

### Docker Image

- **Name**: `test-sets-bazel-devenv`
- **Base**: Ubuntu 22.04
- **Contents**: Bazel 9.0.0 (via Bazelisk), Python 3.12 (via uv), git, gcc/g++
- **Build args**: Host UID/GID/username/group for file permission matching
- **Workspace mount**: `/workspace:rw` (read-write bind mount of project root)

### Concurrency Control

The script acquires an exclusive file lock (`target/.ci.lock`) before running a container command. Only one `./ci` invocation can run at a time.

## Dependencies

- **Docker**: Container runtime
- **uv**: Python script runner (the `ci` script itself runs via `uv run --script`)
- **click**: CLI framework
- **rich**: Console output formatting

## Dependents

- **Developers**: Primary interface for all build/test operations (outermost entry point)
- **CI systems**: Would invoke `./ci test`, `./ci check`, etc.

## Triggers

Inside the container, this script invokes:
- **Bazel**: `bazel build`, `bazel test`, `bazel run`, `bazel query`
- **pytest**: Python unit tests for orchestrator
- **mypy**: Type checking

Bazel in turn triggers the **Orchestrator** (for test_set targets and lifecycle subcommands via `bazel run //orchestrator:main`).

## Key Design Decisions

1. **Dual-mode execution**: The same script works both on the host and inside the container. Host invocations auto-build the image and delegate; container invocations execute directly. This avoids needing separate scripts.

2. **Stale symlink cleanup**: Before Bazel operations, the script removes stale `bazel-*` symlinks that may point to inaccessible locations from previous container runs.

3. **Pytest alongside Bazel test**: The `test` command runs both `bazel test //...` (Starlark analysis, integration tests) and `pytest` (Python unit tests). This covers both the Bazel rule tests and the orchestrator unit tests.

4. **Report generation via bazel run**: The `test-examples` command uses `bazel run` (not `bazel test`) for ci_gate targets, because `bazel test` sandboxes output away from the workspace.

5. **CI gates drive test-examples**: The lifecycle demo (burn-in, degradation, merge gate) invokes `//ci:pr` and `//ci:merge` ci_gate targets rather than bare test_set targets. Execution parameters (mode, effort, status_file) are baked into the ci_gate BUILD targets.
