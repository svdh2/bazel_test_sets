# Agent Instructions for test_sets_bazel_rules

## Architecture Documentation

Use `architecture/current/README.md` as the entry point to understand the
structure of this repository and navigate its components and flows. Always
consult the architecture docs before making changes to unfamiliar areas.

After any code changes, update the relevant architecture documents in
`architecture/current/` so they remain in sync with the source code. This
includes component docs, flow docs, and the main README.md (e.g. updating the
Git Sync section commit hash).

## Build, Test, and Validation Rules

ALL builds, tests, type checks, queries, and validation commands MUST be run
inside the Docker container via the `ci` script. Do NOT run commands directly
via `docker run`, `python3 -m pytest`, `bazel`, or any other direct invocation.

### Available CI Commands

From the `test_sets_bazel_rules/` directory:

```bash
# Build all Bazel targets
./ci build

# Run all tests (Bazel + pytest)
./ci test

# Run type checks (mypy)
./ci check

# Query Bazel targets
./ci query '//macros/examples:*'
./ci query 'kind(test, //...)'

# Interactive shell (for debugging only)
./ci shell

# Clean generated files
./ci clean

# Rebuild Docker image (after Dockerfile changes)
./ci rebuild-image
```

### Important Notes

1. The `ci` script handles Docker image management automatically
2. All commands run inside the Docker container with the workspace mounted
3. Docker container output may not be visible through automated tools - rely on exit codes (0 = success)
4. Never bypass the `ci` script for build/test/validation operations
5. The working directory for `./ci` commands must be `test_sets_bazel_rules/`
