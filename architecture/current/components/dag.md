# Component: DAG

## Location

`orchestrator/execution/dag.py`

## Purpose

Provides the core data structures and graph algorithms for test orchestration. `TestNode` represents a single test with its metadata and graph edges. `TestDAG` holds the complete graph and implements the two ordering algorithms needed by the execution modes.

## Interface

### TestNode (dataclass)

```python
@dataclass
class TestNode:
    name: str
    assertion: str
    executable: str
    depends_on: list[str]       # Forward edges (this test depends on these)
    requirement_id: str
    judgement_executable: str | None
    dependents: list[str]       # Reverse edges (these tests depend on this one)
```

### TestDAG

```python
class TestDAG:
    nodes: dict[str, TestNode]

    @classmethod
    def from_manifest(cls, manifest: dict) -> TestDAG

    def topological_sort_leaves_first(self) -> list[str]
    def bfs_roots_first(self) -> list[str]
    def get_dependencies(self, name: str) -> list[str]
    def get_dependents(self, name: str) -> list[str]
```

### Ordering Algorithms

| Method | Order | Used By | Description |
|--------|-------|---------|-------------|
| `topological_sort_leaves_first()` | Leaves first | Diagnostic mode | Kahn's algorithm on the dependency graph. Nodes with no dependencies are emitted first, then nodes whose deps are all resolved. |
| `bfs_roots_first()` | Roots first | Detection mode | BFS starting from root nodes (no dependents). Explores the graph top-down for fast feedback. |

Both methods detect cycles via DFS with three-color marking and raise `ValueError` with the cycle path.

## Dependencies

- None (pure data structure module)

## Dependents

- **Executor**: Uses DAG ordering and dependency queries during execution
- **Orchestrator Main**: Constructs DAG from manifest, passes to executor
- **Burn-in**: Uses DAG for test execution within the sweep loop
- **Regression Selector**: Filtered manifests are used to construct new DAGs

## Key Design Decisions

1. **Bidirectional edges**: Both `depends_on` (forward) and `dependents` (reverse) are maintained. Forward edges come from the manifest; reverse edges are computed during `from_manifest()`.

2. **Cycle detection with path reporting**: When a cycle is found, the full cycle path is included in the error message (e.g., "A -> B -> C -> A") to aid debugging.

3. **Leaves-first vs roots-first**: The diagnostic mode uses leaves-first to ensure all dependencies run before dependents (thorough). The detection mode uses roots-first to hit high-level failures fast (quick feedback).

4. **Disconnected subgraph handling**: The BFS roots-first method appends any unvisited nodes at the end, ensuring disconnected subgraphs are not silently dropped.
