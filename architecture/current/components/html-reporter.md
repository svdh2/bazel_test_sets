# Component: HTML Reporter

## Location

`orchestrator/reporting/html_reporter.py`

## Purpose

Generates self-contained HTML reports from the report data structure produced by the Reporter. Features color-coded status badges, expandable log sections, measurement tables, burn-in progress indicators, and regression selection details.

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
| **Test set section** | Hierarchical: set name, aggregated status badge, nested test entries |
| **Test entry** | Name, status badge, assertion, duration, exit code, color-coded border |
| **Logs** | Expandable `<details>` with stdout (dark theme pre) and stderr (pink border) |
| **Structured log** | Expandable section with block sequence, measurement table, results, errors |
| **Burn-in info** | Blue info box with runs, passes, SPRT status |
| **Regression section** | Changed files list, test scores table |

### Status Colors

| Status | Color |
|--------|-------|
| `passed` | `#90EE90` (light green) |
| `failed` | `#FFB6C1` (light pink) |
| `dependencies_failed` | `#D3D3D3` (light grey) |
| `passed+dependencies_failed` | `#FFFFAD` (light yellow) |
| `failed+dependencies_failed` | `#FFB6C1` (light pink) |

## Dependencies

- Standard library: `json` (loading reports), `html` (escaping)

## Dependents

- **Orchestrator Main**: Writes HTML report alongside JSON when `--output` is specified

## Key Design Decisions

1. **Self-contained HTML**: All CSS is embedded inline in a `<style>` tag. No external dependencies, JavaScript frameworks, or CDN links. The report is a single file that can be opened in any browser.

2. **Expandable sections**: Logs and structured data use HTML `<details>/<summary>` elements for progressive disclosure. Reports with many tests remain scannable.

3. **XSS-safe**: All dynamic content is escaped with `html.escape()` before insertion into the HTML string.
