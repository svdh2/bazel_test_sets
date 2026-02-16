# Component: Co-occurrence Graph

## Location

`orchestrator/regression/co_occurrence.py`

## Purpose

Builds a bidirectional index of file-commit relationships by analyzing git log output. The graph maps files to commits and commits to files, classifying each file as source, test, or ignored. This data structure powers the regression test selector by identifying which tests historically change alongside which source files.

## Interface

### File Classification

```python
def classify_file(filepath, source_extensions=None, test_patterns=None) -> str
    # Returns "source", "test", or "ignored"
```

Default source extensions: `.py`, `.java`, `.cc`, `.go`, `.rs`, `.ts`, `.js`, `.bzl`
Default test patterns: `*_test.*`, `test_*.*`, `*_spec.*`

### Git Log Parsing

```python
def parse_git_log(git_output: str) -> list[dict]
    # Expects format: git log --format='COMMIT %H %aI' --name-only

def run_git_log(repo_root, max_history=10000, since_commit=None) -> str
    # Executes git log and returns raw output
```

### Graph Construction

```python
def build_co_occurrence_graph(
    commits, source_extensions=None, test_patterns=None, existing_graph=None
) -> dict
```

### Graph Schema

```json
{
  "metadata": {
    "built_at": "ISO timestamp",
    "last_commit": "abc123",
    "total_commits_analyzed": 500,
    "source_extensions": [".py", ...],
    "test_patterns": ["*_test.*", ...]
  },
  "file_commits": {
    "path/to/file.py": [
      {"commit": "abc123", "timestamp": "2026-01-15T..."}
    ]
  },
  "commit_files": {
    "abc123": {
      "timestamp": "2026-01-15T...",
      "source_files": ["path/to/file.py"],
      "test_files": ["path/to/file_test.py"]
    }
  }
}
```

### Persistence

```python
def save_graph(graph, output_path)
def load_graph(input_path) -> dict | None
```

## Dependencies

- **git** (external): Subprocess calls to `git log` for commit history
- Standard library: `json`, `subprocess`, `fnmatch`, `pathlib`

## Dependents

- **Regression Selector**: Reads the graph to score tests by co-occurrence with changed files
- **CI Tool** (`build-graph` subcommand): Invokes graph construction and persistence
- **Orchestrator Main**: Loads graph when `--regression` is used

## Key Design Decisions

1. **Incremental updates**: The graph tracks `last_commit` in metadata. Subsequent builds pass `since_commit` to `run_git_log`, analyzing only new commits. Already-processed commit hashes are skipped during construction.

2. **Test pattern priority**: File classification checks test patterns before source extensions. A file like `auth_test.py` is classified as "test" even though `.py` is a source extension.

3. **Commits without source files skipped**: Commits that only touch test files or ignored files are excluded from the graph, as they provide no co-occurrence signal between source and test files.

4. **Error-tolerant loading**: `load_graph` returns `None` for missing or corrupted files rather than raising exceptions, enabling graceful fallback.
