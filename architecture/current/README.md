# Architecture: test_sets_bazel_rules

Bazel rules for large-scale test orchestration with DAG-based dependency management, intelligent test selection, and test maturity tracking.

## Tech Stack

- **Build system**: Bazel 9.x with Bzlmod
- **Language (rules/macros)**: Starlark
- **Language (orchestrator/ci_tool)**: Python 3.12
- **Testing**: pytest (unit), Bazel test (integration)
- **Type checking**: mypy
- **Container tooling**: Docker (Ubuntu 22.04 base image)
- **Dependencies**: click + rich (CI script)

## System Overview

The repository provides **reusable Bazel rules** (rules/, macros/, orchestrator/) that any project can depend on. The ci_tool/ and examples/ directories are **consumers** that exist only for testing and demonstrating the rules within this repo.

```
REUSABLE LIBRARY
+-------------------------------------------------------------------+
|                                                                   |
|  BUILD TIME (Bazel analysis phase):                               |
|  +-------------------+                                            |
|  |  Bazel Rules      |  manifest                                  |
|  |  (Starlark)       | ---------> .json file consumed by          |
|  |  rules/  macros/  |           Orchestrator at run time         |
|  +-------------------+                                            |
|                                                                   |
|  RUN TIME:                                                        |
|  +-----------------+                                              |
|  |  Orchestrator   |  Executes tests in DAG order, manages        |
|  |  (Python)       |  burn-in lifecycle, regression selection     |
|  |  orchestrator/  |                                              |
|  +-----------------+                                              |
|                                                                   |
+-------------------------------------------------------------------+

CONSUMERS (testing / examples within this repo)
+-------------------------------------------------------------------+
|                                                                   |
|  +-----------------+  +-----------------+                         |
|  |  CI Tool        |  |  Examples       |                         |
|  |  (Python CLI)   |  |  (Bazel module) |                         |
|  |  ci_tool/       |  |  examples/      |                         |
|  +-----------------+  +-----------------+                         |
|                                                                   |
+-------------------------------------------------------------------+

DEVELOPMENT INFRASTRUCTURE
+-------------------------------------------------------------------+
|                                                                   |
|  HOST                              DOCKER CONTAINER               |
|  +------------------+   docker    +---------------------------+   |
|  |  ./ci script     |  -------->  |  ./ci script              |   |
|  |  (host mode)     |  re-invokes |  (container mode)         |   |
|  +------------------+   itself    +---------------------------+   |
|                                            |                      |
|                                            | invokes              |
|                                            v                      |
|                                   +---------------------------+   |
|                                   |  Bazel                    |   |
|                                   |  (build / test / run)     |   |
|                                   +---------------------------+   |
|                                                                   |
+-------------------------------------------------------------------+
```

The **reusable library** is what external projects consume: Starlark rules and macros produce a JSON manifest at build time, and the Python orchestrator reads that manifest at run time to execute tests in DAG order. The **CI tool** (`ci_tool/`) is a CLI wrapper around orchestrator internals (burn-in, deflake, re-judge, build-graph) that exists only for testing and operating this repo's own test sets. The **examples** directory (`examples/`) is a separate Bazel module that demonstrates rule usage. Both are consumers of the library, not part of it.

The `./ci` script is development infrastructure: it launches itself inside a Docker container and invokes Bazel commands (build, test, run) there.

## Component Index

### Reusable Library

| Component | Directory | Description |
|-----------|-----------|-------------|
| [test_set_test rule](components/test-set-test-rule.md) | `rules/` | Wraps an existing test target with DAG metadata (depends_on, assertion, judgement) |
| [test_set rule](components/test-set-rule.md) | `rules/` | Groups test_set_test targets and subsets into hierarchical sets; generates JSON manifest |
| [matrix_test_set macro](components/matrix-test-set-macro.md) | `macros/` | Generates parameterized tests from a matrix of parameter combinations |
| [parameterized_test_set macro](components/parameterized-test-set-macro.md) | `macros/` | Generates variant-specific tests with environment variables and args |
| [Orchestrator Main](components/orchestrator-main.md) | `orchestrator/` | CLI entry point; parses args, loads manifest, dispatches to executor or regression flow |
| [DAG](components/dag.md) | `orchestrator/execution/` | TestNode/TestDAG data structures with topological sort and BFS ordering |
| [Executor](components/executor.md) | `orchestrator/execution/` | Sequential and async parallel test execution with dependency gating |
| [Burn-in](components/burnin.md) | `orchestrator/lifecycle/` | Sweep loop for burning_in tests and stable demotion logic |
| [SPRT](components/sprt.md) | `orchestrator/lifecycle/` | Sequential Probability Ratio Test for burn-in accept/reject and demotion decisions |
| [E-values](components/e-values.md) | `orchestrator/lifecycle/` | E-value based test set verdicts with aggregate Type II error control |
| [Status File](components/status-file.md) | `orchestrator/lifecycle/` | JSON state file manager for test maturity lifecycle (new/burning_in/stable/flaky) |
| [Co-occurrence Graph](components/co-occurrence.md) | `orchestrator/regression/` | Git history analysis to build file-commit bidirectional index |
| [Regression Selector](components/regression-selector.md) | `orchestrator/regression/` | Selects tests by co-occurrence scoring with hop decay and dependency closure |
| [Log Parser](components/log-parser.md) | `orchestrator/analysis/` | Parses [TST] structured log events from test stdout |
| [Reporter](components/reporter.md) | `orchestrator/reporting/` | Generates JSON reports with hierarchical DAG structure and rolling history |
| [HTML Reporter](components/html-reporter.md) | `orchestrator/reporting/` | Generates self-contained HTML reports from report data |
| [Measurements](components/measurements.md) | `orchestrator/analysis/` | Stores and retrieves structured measurement data per test label |
| [Inference](components/inference.md) | `orchestrator/analysis/` | Infers DAG dependencies from rigging feature events in structured logs |
| [Judgement](components/judgement.md) | `orchestrator/analysis/` | Executes judgement targets with stored measurements for verdict re-evaluation |

### Consumers / Development Infrastructure

| Component | Directory | Description |
|-----------|-----------|-------------|
| [CI Tool](components/ci-tool.md) | `ci_tool/` | CLI with burn-in, deflake, test-status, re-judge, and build-graph subcommands |
| [CI Script](components/ci-script.md) | `./ci` | Docker-delegating build/test/check runner |

## Flow Index

| Flow | Description |
|------|-------------|
| [Diagnostic Execution](flows/diagnostic-execution.md) | Full test run in leaves-first topological order with dependency failure propagation |
| [Detection Execution](flows/detection-execution.md) | Fast-feedback run in roots-first BFS order with max-failures threshold |
| [Regression Selection](flows/regression-selection.md) | Co-occurrence-based test selection for feature branch CI |
| [Burn-in Lifecycle](flows/burn-in-lifecycle.md) | SPRT-driven test maturity progression from new to stable or flaky |
| [Manifest Generation](flows/manifest-generation.md) | Build-time flow from Starlark rules to JSON manifest to orchestrator |
| [Report Generation](flows/report-generation.md) | Post-execution JSON and HTML report generation with structured log integration |
| [E-value Verdict](flows/e-value-verdict.md) | E-value based test set verdict computation with quick and high-fidelity modes |

## Git Sync

- **Commit**: `78a670e655e18f17aa4dea7f934cbee770756d81`
- **Last updated**: 2026-02-16
