# Component: Regression Selector

## Location

`orchestrator/regression/regression_selector.py`

## Purpose

Selects a subset of stable tests most likely to catch regressions for a given code change. Uses co-occurrence graph analysis with bounded BFS expansion, hop-aware scoring, recency weighting, dependency closure, and fallback logic when co-occurrence data is insufficient.

## Interface

### RegressionConfig (dataclass)

```python
@dataclass
class RegressionConfig:
    max_test_percentage: float = 0.10      # Max fraction of stable tests to select
    max_hops: int = 2                       # Max BFS hops in co-occurrence expansion
    decay_per_hop: float = 0.5              # Score multiplier per hop
    recency_half_life_days: float = 180.0   # Exponential decay half-life
    min_tests: int = 3                      # Minimum tests before fallback triggers
    source_extensions: list[str]            # File extensions to consider as source
```

### SelectionResult (dataclass)

```python
@dataclass
class SelectionResult:
    selected_tests: list[str]
    scores: dict[str, float]
    selection_reason: dict[str, str]   # test -> "co-occurrence hop 0", "dependency closure", etc.
    fallback_used: bool
    total_stable_tests: int
    changed_files: list[str]
```

### Primary Function

```python
def select_regression_tests(
    changed_files, graph, manifest, config=None
) -> SelectionResult
```

### Supporting Functions

```python
def resolve_test_label(test_file_path, manifest) -> str | None
def add_dependency_closure(selected_tests, manifest) -> list[str]
```

## Algorithm

1. **Seed**: Extract changed source files (by extension) as the initial frontier.
2. **BFS expansion** (up to `max_hops` iterations):
   - Find all commits touching frontier files
   - Score co-occurring test files: `score += hop_decay^hop * recency_weight(commit_age)`
   - Discover new source files from those commits for the next hop
   - Stop early if enough candidates are found or no new files discovered
3. **Rank and select**: Sort candidates by score, take top N (where N = `max_test_percentage * total_stable_tests`).
4. **Dependency closure**: BFS through depends_on edges to include all transitive dependencies.
5. **Fallback**: If fewer than `min_tests` selected, include all stable tests.

## Dependencies

- **Co-occurrence Graph** (`orchestrator.regression.co_occurrence`): Provides the file-commit index
- Standard library: `math`, `collections.deque`, `os`, `datetime`

## Dependents

- **Orchestrator Main**: Calls `select_regression_tests` when `--regression` is used

## Key Design Decisions

1. **Hop-aware decay**: Each BFS hop multiplies the score by `decay_per_hop` (default 0.5). Direct co-occurrences (hop 0) are valued most; transitive associations diminish exponentially.

2. **Recency weighting**: Older commits contribute less via exponential decay with a configurable half-life (default 180 days). A 6-month-old co-occurrence contributes half the score of a present-day one.

3. **Test label resolution**: Maps file paths from git history to manifest test labels using two strategies: (1) executable path matching, (2) convention-based basename matching. This bridges the gap between git-level file paths and Bazel-level labels.

4. **Dependency closure for correctness**: Selected tests may depend on tests not initially selected. The closure step ensures the filtered DAG is self-contained.

5. **Conservative fallback**: When co-occurrence data is sparse (fewer than `min_tests` candidates), all stable tests are included rather than running a potentially incomplete subset.
