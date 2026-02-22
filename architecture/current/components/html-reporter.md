# Component: HTML Reporter

## Location

`orchestrator/reporting/html_reporter.py`

## Purpose

Generates self-contained HTML reports from the report data structure produced by the Reporter. Features color-coded status badges, expandable log sections, measurement tables, burn-in progress indicators, regression selection details, and an interactive DAG visualization.

## Interface

```python
def generate_html_report(report_data: dict) -> str
def generate_html_from_file(report_path: Path) -> str
def write_html_report(report_data: dict, output_path: Path)
```

### Visual Elements

| Element | Description |
|---------|-------------|
| **Header** | Report title, generation timestamp, commit hash, summary badges |
| **Summary badges** | Color-coded counts: total (grey), passed (green), failed (pink), deps failed (light grey), not run (steel blue) |
| **DAG visualization** | Interactive graph of test_set groups and test_set_test nodes with `depends_on` edges. Uses Cytoscape.js with dagre layout. Supports zoom/pan, click-to-inspect detail pane, search box, and "Show all workspace tests" checkbox to toggle visibility of `not_run` test nodes. Clicking a test node shows its full entry; clicking a group (set) node shows name, status badge, assertion, lifecycle summary, and threshold. Test nodes with `parameters` display multi-line labels: the test name on the first line followed by indented `key: value` pairs on subsequent lines. |
| **Search box** | Toolbar search input with keyword-based filtering across test/set fields: name, assertion, parameter, metric, check, feature, log. Supports field-scoped queries (e.g. `name:(email) check:(SMTP)`). CamelCase and snake_case identifiers are decomposed into words for matching. Results appear in a dropdown with keyboard navigation (arrows, Enter, Escape). Selecting a result focuses the Cytoscape node and opens the detail pane. Search index is pre-built in Python and embedded as `SEARCH_INDEX` JSON. |
| **Test set section** | Hierarchical: set name, aggregated status badge, nested test entries |
| **Test entry** | Name, status badge, assertion, duration, exit code, color-coded border. When `parameters` are present, a two-column table (Parameter / Value) appears at the top of the entry before the history timeline. |
| **Logs** | Expandable `<details>` with stdout (dark theme pre) and stderr (pink border) |
| **Structured log** | Expandable section with block sequence, measurement table, results, errors |
| **Step segments** | Nested collapsible `<details>` within blocks showing step hierarchy with status badges, measurements, assertions, errors, and raw logs. Passed steps collapsed; failed/warning steps and their ancestors expanded. Steps with sub-steps show a count indicator. |
| **Block summaries** | Block-level measurements and assertions are split into direct items (shown inline) and step-qualified items (collapsed by default in `<details>` sections). Rigging block features are rendered as individual list items rather than comma-separated. |
| **History timeline** | Compact horizontal bar of colored boxes showing pass/fail history; hover reveals commit hash |
| **Burn-in info** | Blue info box with runs, passes, SPRT status |
| **Lifecycle badge** | Per-test badge showing lifecycle state (stable/burning_in/flaky/new/disabled) with reliability rate |
| **Lifecycle summary** | Per-test-set summary showing state counts and aggregate reliability with config thresholds |
| **Regression section** | Changed files list, test scores table |
| **Source links** | Structured log events with `_file`/`_line` metadata render clickable source links. When `source_link_base` is present in the report JSON, links point to GitHub (`<a>` tags); otherwise display local `path:line` format (`<span>` tags). Uses `render_source_link()` from `source_links.py` |

### Status Colors

| Status | Color |
|--------|-------|
| `passed` | `#90EE90` (light green) |
| `failed` | `#FFB6C1` (light pink) |
| `dependencies_failed` | `#D3D3D3` (light grey) |
| `passed+dependencies_failed` | `#FFFFAD` (light yellow) |
| `failed+dependencies_failed` | `#FFB6C1` (light pink) |
| `not_run` | `#B0C4DE` (light steel blue) |

### Step Status Colors

| Status | Color |
|--------|-------|
| `passed` | `#90EE90` (light green) |
| `failed` | `#FFB6C1` (light pink) |
| `warning` | `#FFFFAD` (light yellow) |

### Lifecycle State Colors

| State | Color |
|-------|-------|
| `new` | `#87CEEB` (light blue) |
| `burning_in` | `#FFD700` (gold) |
| `stable` | `#90EE90` (light green) |
| `flaky` | `#FFB6C1` (light pink) |
| `disabled` | `#D3D3D3` (grey) |

## Dependencies

- Standard library: `json` (loading reports, embedding graph data), `html` (escaping), `re` (identifier decomposition)
- **Log Parser** (`orchestrator.analysis.log_parser`): `BlockSegment`, `StepSegment`, `TextSegment`, `parse_stdout_segments` for structured log parsing
- **Source Links** (`orchestrator.reporting.source_links`): `render_source_link()` for building HTML source code links
- **CDN (runtime)**: Cytoscape.js 3.30.4, dagre 0.8.5, cytoscape-dagre 2.5.0 from unpkg.com (loaded by the browser when viewing the report; requires internet access)

## Dependents

- **Orchestrator Main**: Writes HTML report alongside JSON when `--output` is specified

## Key Design Decisions

1. **Self-contained HTML with CDN exception**: All CSS is embedded inline in `<style>` tags. The DAG visualization loads Cytoscape.js from CDN (~300KB) since inlining a full graph library would be impractical. All other elements (history timeline, badges, etc.) remain pure CSS/HTML with no external dependencies.

2. **DAG graph data embedding**: The test_set hierarchy and `depends_on` edges are serialized as `GRAPH_DATA` and `TEST_DATA` JSON variables in `<script>` tags. Cytoscape.js reads these on page load. Test set nodes become compound (parent) nodes; test nodes are children with directed edges for dependencies. Each test node's graph data includes a `parameters` dict (empty when no parameters are set) so the client-side label function can render multi-line labels. A `SEARCH_INDEX` JSON variable provides per-node field-level searchable text with decomposed identifiers.

3. **DOM-cloning detail pane**: Clicking a test node clones its rendered `data-test-name` entry into the detail pane. Clicking a group (set) node clones a hidden `data-set-name` summary card containing the set's header, assertion, lifecycle summary, and config threshold. Both use `outerHTML` cloning so the detail pane mirrors the lower-panel styling.

4. **Expandable sections**: Logs and structured data use HTML `<details>/<summary>` elements for progressive disclosure. Reports with many tests remain scannable.

5. **XSS-safe**: All dynamic content is escaped with `html.escape()` before insertion into the HTML string. The JavaScript detail pane uses `textContent`-based escaping.

6. **Manifest-sourced test set**: The report includes all tests from the manifest's `test_set_tests`, not just tests with execution results. Tests without results receive `not_run` status, making it possible to detect orchestrator bugs that silently skip tests. The `not_run` status does not affect parent test-set aggregation. A "Show all workspace tests" checkbox in the DAG toolbar toggles `not_run` node visibility.
