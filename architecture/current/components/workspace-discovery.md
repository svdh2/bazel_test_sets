# Component: Workspace Discovery

## Location

`orchestrator/discovery/workspace.py`

## Purpose

Discovers all `test_set_test`, `_test_set_rule_test`, and `_ci_gate_rule_test` targets in a Bazel workspace via `bazel query --output=xml` and merges them into a manifest copy for reporting. This allows the HTML report to show the full workspace DAG structure — all test sets, ci_gates, and their tests — not just those in the executing `test_set`. Tests not in the current `test_set` appear as `not_run` in the report, organized under their declared test_set hierarchy. CI gate nodes wrap their backing test_set as parent nodes in the DAG; the executing gate inherits the test_set's status while others show `not_run`.

## Interface

```python
def normalize_label(label: str) -> str
def parse_query_xml(xml_content: str) -> list[dict]
def parse_test_sets_xml(xml_content: str) -> list[dict]
def parse_ci_gates_xml(xml_content: str) -> list[dict]
def resolve_depends_on(discovered: list[dict]) -> None
def build_workspace_trees(test_sets: list[dict], tests: list[dict],
                          existing_names: set[str]) -> list[dict]
def discover_workspace_tests(workspace_dir=None, timeout=60) -> dict | None
def merge_discovered_tests(manifest: dict, discovery: dict,
                           ci_gate_name: str | None = None) -> dict

CI_GATE_DEFAULTS: dict  # Default values for all ci_gate rule parameters
```

### `discover_workspace_tests()`

Runs `bazel query --output=xml 'kind("test_set_test", //...) + kind("_test_set_rule_test", //...) + kind("_ci_gate_rule_test", //...)'` from the workspace directory. Returns a dict with `tests` (list of test dicts), `test_sets` (list of test_set dicts), and `ci_gates` (list of ci_gate dicts), or `None` if discovery is unavailable. Each test dict includes a `parameters` string_dict (present when non-empty) extracted from the XML `<dict name="parameters">` element.

### `parse_ci_gates_xml()`

Parses `_ci_gate_rule_test` rules from the XML response. Returns a list of dicts with: `label`, `name`, `test_set` (label of the backing test_set), and `params` (dict of parameter name → `{"value": X, "is_default": bool}`). Parameters include all ci_gate execution attributes (mode, effort, max_reruns, etc.). Default comparison uses `CI_GATE_DEFAULTS`.

### `parse_test_sets_xml()`

Parses `_test_set_rule_test` rules from the XML response. Returns a list of dicts with: `label`, `name`, `assertion`, `requirement_id`, `tests_raw` (list of test_set_test labels), and `subsets_raw` (list of subset labels, which may be aliases).

### `build_workspace_trees()`

Identifies root test_sets (not referenced as a subset by any other), filters out those already present in the manifest (matched by name), and builds recursive tree dicts matching the manifest tree format. Handles alias resolution for subset references.

### `merge_discovered_tests()`

Deep-copies the manifest, adds discovered tests not already present (matched by normalized label) -- including their `parameters` when present -- and wraps everything under a synthetic *Workspace* root. The manifest root and all other discovered root test_sets become peer subsets of this workspace root. This ensures every report shows the same DAG shape regardless of which test_set is executed — only test statuses differ. Any new tests not placed in a discovered tree are grouped under an "Other workspace tests" catch-all subset. Returns the modified copy; the original manifest is unchanged.

When `ci_gate_name` is provided, discovered ci_gates are inserted as parent wrapper nodes around their backing test_sets. The executing gate (matched by name) inherits the test_set's aggregated status; all other gates show `not_run`. Each ci_gate node carries a `ci_gate_params` dict with execution parameters and default indicators.

### Label Normalization

Manifest labels use `@@//pkg:target` (Starlark bzlmod format), while `bazel query` produces `//pkg:target`. The `normalize_label()` function strips the leading `@@` for comparison. New tests are added using the query format (`//pkg:target`).

### Alias Resolution

The `test_set` macro creates a rule named `//pkg:foo_test` and an alias `//pkg:foo` when the user-given name doesn't end with `_test` or `_tests`. Subset references in the XML may use alias labels. `build_workspace_trees()` registers both the rule label and the potential alias (label without `_test` suffix) in the lookup table.

### Dependency Resolution

In `bazel query` XML, `depends_on` lists `test_set_test` target labels (e.g., `//pkg:cart_wrapped`), not wrapped test labels (e.g., `//pkg:cart_raw_test`). `resolve_depends_on()` builds a mapping from `test_set_test` label to wrapped test label and translates all `depends_on` entries. Unknown references are dropped.

## Dependencies

- Standard library: `xml.etree.ElementTree` (XML parsing), `subprocess` (bazel query), `copy` (manifest deep copy)
- Environment: `BUILD_WORKSPACE_DIRECTORY` (set by `bazel run`)

## Dependents

- **Orchestrator Main**: Calls `discover_workspace_tests()` and `merge_discovered_tests()` when `--discover-workspace-tests` is passed (lazy import)

## Key Design Decisions

1. **Reporting-only**: Discovery enriches the manifest only for the reporter. The execution DAG is built from the original manifest before discovery runs, so discovered tests are never executed.

2. **Graceful degradation**: If `BUILD_WORKSPACE_DIRECTORY` is not set (e.g., `bazel test` sandbox), `bazel` is not in PATH, or the query fails/times out, `discover_workspace_tests()` returns `None` and the report is generated without workspace tests.

3. **Workspace root with peer test_sets**: A synthetic *Workspace* root wraps the manifest's test_set and all discovered root test_sets as peer subsets. This gives every report the same DAG shape — the full workspace hierarchy as defined in the BUILD files. Only the test statuses differ between reports. Tests not placed in any discovered tree fall back to a catch-all "Other workspace tests" subset.

4. **Cross-reference depends_on**: When a discovered test depends on a test already in the manifest, the dependency uses the manifest's original label key (with `@@` prefix if applicable). This ensures the DAG visualization draws correct edges between existing and discovered tests.

5. **Root identification**: A test_set is a "root" if no other test_set references it as a subset. Only roots are added to the manifest tree; their children are included recursively within the root's tree structure.

6. **Name-based deduplication**: Discovered test_sets whose `name` matches a name already in the manifest tree are skipped, preventing the current test_set (and its subsets) from being duplicated.
