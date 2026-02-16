# Component: Measurements

## Location

`orchestrator/measurements.py`

## Purpose

Stores and retrieves structured measurement data captured from `[TST]` log events. Each test label gets a JSON file in a measurements directory, keyed by a sanitized version of the Bazel label. This enables retroactive re-evaluation of test verdicts via the judgement system.

## Interface

```python
def store_measurements(test_label: str, measurements: list[dict], output_dir) -> Path
def load_measurements(test_label: str, output_dir) -> dict | None
```

### Storage Format

```json
{
  "test_label": "//test:a",
  "measurements": [
    {"name": "latency_ms", "value": 42, "block": "execution"},
    {"name": "throughput", "value": 1000, "block": "execution"}
  ]
}
```

### Label to Filename

```python
def _label_to_filename(test_label: str) -> str
    # "//test:a" -> "test_a"
    # Replaces non-alphanumeric chars (except - and _) with underscores
```

## Dependencies

- Standard library: `json`, `re`, `pathlib`

## Dependents

- **Judgement** (`orchestrator.judgement`): Loads measurements for re-evaluation; uses `_label_to_filename` for file path construction
- **Reporter**: Could store measurements alongside reports (integration point)

## Key Design Decisions

1. **One file per test**: Each test label produces a separate JSON file, enabling individual test re-evaluation without loading all measurements.

2. **Safe filename conversion**: Bazel labels contain characters (`/`, `:`) that are invalid in filenames. The sanitization replaces these with underscores.

3. **Overwrite semantics**: `store_measurements` overwrites any existing file for the same label, keeping only the most recent measurements.
