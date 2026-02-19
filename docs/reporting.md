# Reporting Guide

The orchestrator produces JSON reports that can be converted to self-contained
HTML files. HTML reports include an interactive DAG visualization, structured
log rendering, and lifecycle metadata.

## Generating Reports

### JSON output

Every test set run writes a JSON report to `target/reports/` automatically.
Specify a custom path with `--output`:

```bash
bazel run //path/to:my_tests -- --output results.json
```

### HTML output

Convert any JSON report to a single-file HTML page:

```python
from pathlib import Path
from orchestrator.reporting.html_reporter import (
    generate_html_from_file,
    write_html_report,
)

# One-step: read JSON and get HTML string
html = generate_html_from_file(Path("results.json"))

# Or write directly to a file
import json
report_data = json.loads(Path("results.json").read_text())
write_html_report(report_data, Path("report.html"))
```

Open `report.html` in any browser -- no server required.

## Report Layout

### Summary Header

The header shows:

- **Generated timestamp** and **commit SHA**
- **Summary badges** -- total tests, passed, failed, dependencies failed, and
  total duration

### Interactive DAG

Hierarchical test sets are rendered as an interactive graph using
[Cytoscape.js](https://js.cytoscape.org/) with a top-to-bottom dagre layout.

- **Group nodes** (double-bordered) represent test sets. Their background
  color aggregates from member tests: green if all pass, red if any fail,
  grey if all are skipped.
- **Test nodes** represent individual tests. Color reflects the five-status
  model (passed, failed, dependencies\_failed, etc.).
- **Solid edges** show membership (test set contains test).
- **Dashed edges** show dependency relationships between tests.
- **Lifecycle indicators** -- tests in `burning_in`, `flaky`, or `disabled`
  states show an icon prefix and a colored border.

#### Toolbar

| Button | Action |
|--------|--------|
| **+** | Zoom in |
| **&minus;** | Zoom out |
| **Fit** | Fit the entire graph into view |

The graph also supports mouse-wheel zoom and click-drag panning.

#### Detail Pane

Click any node to open a detail pane on the right:

**For test nodes** the pane shows:

- Name, status badge, and lifecycle badge with reliability percentage
- History timeline (compact colored boxes grouped by commit)
- Assertion text, duration, and exit code
- Structured log blocks (see below)
- Burn-in progress and inferred dependencies (when present)

**For test set (group) nodes** the pane shows:

- Name, aggregated status badge
- Assertion text
- Lifecycle summary (count of stable, burning-in, flaky tests)
- Aggregate reliability percentage
- Aggregated history timeline

Click the **&times;** button or click the graph background to close the pane.

Selecting a node highlights its connected edges in blue so you can trace
membership and dependency relationships.

### Structured Log Blocks

When tests emit `[TST]` structured events, the report renders them as
color-coded block cards:

| Block | Color | Purpose |
|-------|-------|---------|
| **Rigging** | Grey | Test fixture setup |
| **Stimulation** | Blue | Applying the test stimulus |
| **Checkpoint** | Yellow | Intermediate verification |
| **Verdict** | Green | Final pass/fail determination |

Each block card can contain:

- **Features** -- services or resources the block exercises, with source-file
  links when `source_link_base` is configured
- **Measurement table** -- name, value, unit, and source location
- **Assertion checklist** -- pass/fail assertions with checkmark/cross icons
- **Raw logs** -- collapsible raw log output from the block
- **Errors** -- highlighted error messages

### History Timeline

A compact horizontal timeline of colored boxes represents the test's
pass/fail history across recent commits. Boxes are grouped by commit with
alternating backgrounds so commit boundaries are visible. Hover over a box
to see the commit SHA.

For test set nodes the timeline is computed by aggregating the histories of
all member tests: red if any test failed, grey if all were skipped, green
otherwise.

### Lifecycle Badges

When a status file is configured, each test displays its lifecycle state:

| Badge | Meaning |
|-------|---------|
| **STABLE** (green) | SPRT accepted -- test is reliable |
| **BURNING IN** (yellow) | Accumulating evidence |
| **FLAKY** (pink) | SPRT rejected -- test is unreliable |
| **NEW** (blue) | No burn-in runs yet |
| **DISABLED** (grey) | Manually disabled |

The badge is followed by a reliability percentage and a runs fraction
(e.g. `100.0% (50/50)`).

Test set nodes show a **lifecycle summary** counting how many member tests
are in each state, plus the aggregate reliability.

### E-value Verdict

When running with `--effort converge` or `--effort max`, the report includes
an E-value verdict section showing:

- Overall **GREEN** / **RED** / **UNDECIDED** verdict badge
- E-set (aggregate red evidence) vs the red threshold
- min(S_i) (green evidence) vs the green threshold
- Weakest test name and total HiFi reruns
- Expandable per-test E-value table

### Effort Classification

When using effort modes, a classification table shows each test's SPRT
result:

| Classification | Meaning |
|----------------|---------|
| **true_pass** (green) | Test reliably passes |
| **true_fail** (red) | Test genuinely fails |
| **flake** (yellow) | Intermittent -- blocks CI |
| **undecided** (grey) | SPRT budget exhausted without decision |

### Regression Selection

When using `--effort regression`, the report includes:

- Expandable list of changed files that triggered test selection
- Score table showing each selected test and its co-occurrence score

## Source Links

Configure `source_link_base` in the report data to make source references
in structured log blocks clickable. When set, measurements, features, and
assertions that include file/line metadata render as links.
