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
| **Summary badges** | Color-coded counts: total (grey), passed (green), failed (pink), deps failed (light grey) |
| **DAG visualization** | Interactive graph of test_set groups (compound nodes) and test_set_test nodes with `depends_on` edges. Uses Cytoscape.js with dagre layout. Supports zoom/pan, collapse/expand of groups, and click-to-inspect detail pane via `srcdoc` iframe. |
| **Test set section** | Hierarchical: set name, aggregated status badge, nested test entries |
| **Test entry** | Name, status badge, assertion, duration, exit code, color-coded border |
| **Logs** | Expandable `<details>` with stdout (dark theme pre) and stderr (pink border) |
| **Structured log** | Expandable section with block sequence, measurement table, results, errors |
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

### Lifecycle State Colors

| State | Color |
|-------|-------|
| `new` | `#87CEEB` (light blue) |
| `burning_in` | `#FFD700` (gold) |
| `stable` | `#90EE90` (light green) |
| `flaky` | `#FFB6C1` (light pink) |
| `disabled` | `#D3D3D3` (grey) |

## Dependencies

- Standard library: `json` (loading reports, embedding graph data), `html` (escaping)
- **Source Links** (`orchestrator.reporting.source_links`): `render_source_link()` for building HTML source code links
- **CDN (runtime)**: Cytoscape.js 3.30.4, dagre 0.8.5, cytoscape-dagre 2.5.0 from unpkg.com (loaded by the browser when viewing the report; requires internet access)

## Dependents

- **Orchestrator Main**: Writes HTML report alongside JSON when `--output` is specified

## Key Design Decisions

1. **Self-contained HTML with CDN exception**: All CSS is embedded inline in `<style>` tags. The DAG visualization loads Cytoscape.js from CDN (~300KB) since inlining a full graph library would be impractical. All other elements (history timeline, badges, etc.) remain pure CSS/HTML with no external dependencies.

2. **DAG graph data embedding**: The test_set hierarchy and `depends_on` edges are serialized as `GRAPH_DATA` and `TEST_DATA` JSON variables in `<script>` tags. Cytoscape.js reads these on page load. Test set nodes become compound (parent) nodes; test nodes are children with directed edges for dependencies.

3. **`srcdoc` iframe for detail pane**: Clicking a test node renders its details (status, assertion, stdout/stderr) into an iframe via the `srcdoc` attribute. This works from `file://` (no cross-origin issues) and provides scroll isolation from the main page.

4. **Expandable sections**: Logs and structured data use HTML `<details>/<summary>` elements for progressive disclosure. Reports with many tests remain scannable.

5. **XSS-safe**: All dynamic content is escaped with `html.escape()` before insertion into the HTML string. The JavaScript detail pane uses `textContent`-based escaping.
