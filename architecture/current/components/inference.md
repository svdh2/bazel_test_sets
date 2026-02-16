# Component: Inference

## Location

`orchestrator/analysis/inference.py`

## Purpose

Infers DAG dependency edges from rigging feature events in structured test logs. When a test exercises a feature during its rigging phase, this module finds other tests that verify that feature, suggesting undeclared dependencies. Inferred dependencies are advisory (flagged as `inferred: True`).

## Interface

### find_tests_verifying_feature

```python
def find_tests_verifying_feature(
    feature_name: str,
    manifest: dict,
    feature_map: dict[str, list[str]] | None = None,
) -> list[str]
```

Two matching strategies:
1. **Explicit mapping**: If `feature_map` contains the feature name, return mapped test labels
2. **Convention-based**: Case-insensitive substring match against test target names and assertion text

### infer_rigging_dependencies

```python
def infer_rigging_dependencies(
    test_name: str,
    rigging_features: list[str],
    manifest: dict,
    feature_map: dict[str, list[str]] | None = None,
) -> list[dict]
```

Returns list of:
```python
{"target": "//test:b", "feature": "auth", "inferred": True}
```

Excludes self-references and already-declared depends_on edges.

### load_feature_map

```python
def load_feature_map(path) -> dict[str, list[str]] | None
```

Loads optional explicit feature-to-test mapping from JSON.

## Dependencies

- **Log Parser** (indirectly): The `rigging_features` input comes from `get_rigging_features()`
- Standard library: `json`, `pathlib`

## Dependents

- **Reporter**: Includes inferred dependencies in the report via `add_inferred_dependencies`

## Key Design Decisions

1. **Advisory only**: Inferred dependencies are flagged and reported but do not modify the actual DAG. They serve as suggestions for developers to add explicit `depends_on` edges.

2. **Convention-based fallback**: Without an explicit feature map, the module uses substring matching. This works well when feature names align with test names or assertion text (e.g., feature "auth" matches test "auth_test" or assertion "User can authenticate").

3. **Deduplication**: Each target appears at most once in the output, even if multiple features point to it.
