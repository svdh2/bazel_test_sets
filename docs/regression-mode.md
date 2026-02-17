# Regression Guide

The `--regression` flag narrows any execution mode to a subset of tests most
likely to catch regressions based on which source files changed. It uses a
co-occurrence graph built from git history to identify tests historically
associated with changed code.

## Overview

1. Detect changed files (git diff or explicit list)
2. Look up changed files in the co-occurrence graph
3. Find tests that historically changed alongside those files
4. Score and rank tests by relevance
5. Select top tests up to a configurable percentage
6. Add dependency closure for DAG self-containment
7. Execute selected tests in diagnostic order

## Co-occurrence Graph

The co-occurrence graph is a bidirectional index linking source files to
tests through shared commits.

### Building the Graph

```bash
bazel run //ci_tool:main -- build-graph \
    --repo-path /path/to/repo \
    --output .tests/co_occurrence_graph.json \
    --source-extensions .py .java .go \
    --test-patterns "*_test.py" "*_test.java"
```

The graph is built by analyzing git log to find commits where source files
and test files changed together. This creates the co-occurrence relationship:
"when source file X changed, test file Y also changed."

### Graph Structure

```json
{
  "metadata": {
    "last_commit": "abc123",
    "total_commits_analyzed": 500,
    "source_extensions": [".py"],
    "test_patterns": ["*_test.py"]
  },
  "file_commits": {
    "src/auth.py": [
      {"commit": "c1", "timestamp": "2026-01-01T..."},
      {"commit": "c5", "timestamp": "2026-01-05T..."}
    ]
  },
  "commit_files": {
    "c1": {
      "timestamp": "2026-01-01T...",
      "source_files": ["src/auth.py"],
      "test_files": ["tests/auth_test.py"]
    }
  }
}
```

### Incremental Updates

The graph supports incremental updates. When you re-run `build-graph`,
it picks up from the last analyzed commit:

```bash
# First build (analyzes entire history)
bazel run //ci_tool:main -- build-graph --output .tests/co_occurrence_graph.json

# Later (only analyzes new commits)
bazel run //ci_tool:main -- build-graph --output .tests/co_occurrence_graph.json
```

## Running with Regression

### Using git diff

```bash
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode diagnostic \
    --regression \
    --diff-base main \
    --co-occurrence-graph .tests/co_occurrence_graph.json
```

### Using explicit file list

```bash
bazel run //orchestrator:main -- \
    --manifest manifest.json \
    --mode detection \
    --regression \
    --changed-files "src/auth.py,src/payment.py"
```

## Scoring Algorithm

Tests are scored based on three factors:

### 1. Frequency

How often the test co-occurred with the changed source file:

```
frequency = count(commits where both changed)
```

### 2. Recency

More recent co-occurrences are weighted higher using exponential decay:

```
recency_weight = exp(-days_since_commit / 30)
```

Commits from 30 days ago get ~37% weight, 60 days ~14%, 90 days ~5%.

### 3. Hop Distance

Tests found through transitive expansion (multi-hop BFS) are penalized:

```
hop_decay = 0.5 ^ hop_distance
```

- Hop 0 (direct co-occurrence): 1.0x weight
- Hop 1 (one file away): 0.5x weight
- Hop 2 (two files away): 0.25x weight

### Combined Score

```
score = sum(frequency * recency_weight * hop_decay)
```

## Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--max-test-percentage` | 0.10 | Maximum fraction of stable tests to select |
| `--max-hops` | 2 | Maximum BFS expansion hops |
| `--co-occurrence-graph` | `.tests/co_occurrence_graph.json` | Graph file path |

### Tuning max-test-percentage

- **0.05** (5%): Very selective, fast CI, may miss some regressions
- **0.10** (10%): Balanced default
- **0.25** (25%): More thorough, slower CI
- **0.50** (50%): Nearly full regression suite

### Tuning max-hops

- **1**: Only direct co-occurrences (most precise)
- **2**: One level of transitive expansion (recommended)
- **3+**: Broader coverage but more noise

## Dependency Closure

After scoring, the selected tests may have dependencies on unselected tests.
The system automatically adds these dependencies to ensure the subset is
self-contained:

```
Selected: [B, C]
B depends on A (not selected)
--> Automatically adds A

Final selection: [A, B, C]
```

## Fallback Behavior

If co-occurrence yields fewer tests than expected (e.g., changed files are
not in the graph), the system falls back to:

1. Testing all tests that match changed file patterns
2. If still too few, selecting random stable tests up to the percentage limit

The fallback is reported in the output:
```
Regression: 5 tests selected from 100 stable tests
  (fallback: co-occurrence yielded too few tests)
```

## Integration with Burn-in

Only `stable` tests are candidates for regression selection. Tests in
`new`, `burning_in`, or `flaky` states are excluded from the pool.

## Report Integration

When using the `--regression` flag, the JSON report includes a
`regression_selection` section:

```json
{
  "report": {
    "regression_selection": {
      "changed_files": ["src/auth.py", "src/payment.py"],
      "scores": {
        "auth_test": 0.85,
        "payment_test": 0.72
      },
      "total_stable_tests": 100,
      "selected_count": 5,
      "fallback_used": false
    }
  }
}
```
