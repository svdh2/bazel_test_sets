# Component: Test Set Config

## Location

`orchestrator/lifecycle/config.py`

## Purpose

Manages the `.test_set_config` JSON file that stores reliability thresholds and statistical parameters for the burn-in lifecycle. This file lives at the Bazel workspace root, separate from the mutable `.tests/status` lifecycle state.

## Interface

### TestSetConfig

```python
class TestSetConfig:
    def __init__(self, path: Path | None = None)

    # Properties
    min_reliability: float           # Default 0.99
    statistical_significance: float  # Default 0.95
    max_test_percentage: float       # Default 0.10
    max_hops: int                    # Default 2
    max_reruns: int                  # Default 100
    max_failures: int | None         # Default None (unlimited)
    max_parallel: int | None         # Default None (CPU count)
    status_file: Path | None         # Default None (lifecycle tracking disabled)
    config: dict[str, Any]           # Full config dict (copy)

    # Mutation
    def set_config(min_reliability=None, statistical_significance=None)
    def save()
```

### File Format

`.test_set_config` at the Bazel workspace root:

```json
{
  "min_reliability": 0.99,
  "statistical_significance": 0.95,
  "status_file": ".tests/status",
  "max_parallel": null,
  "max_failures": null,
  "max_reruns": 100,
  "max_test_percentage": 0.10,
  "max_hops": 2
}
```

### Path Resolution

The config file path is passed to the orchestrator via `--config-file`. The Starlark runner script (`rules/test_set.bzl`) resolves it from `BUILD_WORKSPACE_DIRECTORY`:

```bash
CONFIG="${BUILD_WORKSPACE_DIRECTORY:-.}/.test_set_config"
```

When no config file exists or no path is provided, `DEFAULT_CONFIG` values are used.

## Dependencies

- None (pure Python with standard library JSON and pathlib)

## Dependents

- **Status File** (`orchestrator/lifecycle/status.py`): `StatusFile` delegates config access to a `TestSetConfig` instance
- **Burn-in** (`orchestrator.lifecycle.burnin`): Reads `min_reliability` and `statistical_significance` via StatusFile
- **E-values** (`orchestrator.lifecycle.e_values`): Reads `min_reliability` via StatusFile
- **Orchestrator Main** (`orchestrator.main`): Passes `--config-file` to StatusFile; reads config for reporter

## Key Design Decisions

1. **Separate from status file**: Configuration is a workspace-level concern (how reliable tests should be) while status is mutable per-test state (current reliability evidence). Separating them prevents config from being accidentally modified by lifecycle operations.

2. **Workspace root location**: The config file lives at the Bazel workspace root (alongside `MODULE.bazel`), not inside `.tests/`. This reflects its role as a project-wide setting rather than per-test-set state.

3. **Defaults without file**: If no `.test_set_config` file exists, `DEFAULT_CONFIG` values are used silently. This supports environments (tests, CI tool) where config is not relevant.
