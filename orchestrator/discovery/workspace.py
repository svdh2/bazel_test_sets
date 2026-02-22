"""Discover all test_set_test and test_set targets in a Bazel workspace.

Runs ``bazel query --output=xml`` to discover both individual tests
(``test_set_test``) and their hierarchical groupings (``_test_set_rule_test``).
Discovered tests are merged into a manifest copy so the reporter can show
the full workspace DAG structure, with undiscovered tests marked ``not_run``.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any


def normalize_label(label: str) -> str:
    """Normalize a Bazel label for comparison.

    Strips the leading ``@@`` prefix that Starlark's ``str(label)``
    adds under bzlmod, so that manifest keys (``@@//pkg:target``) can
    be compared with ``bazel query`` output (``//pkg:target``).
    """
    if label.startswith("@@"):
        return label[2:]
    return label


def parse_query_xml(xml_content: str) -> list[dict[str, Any]]:
    """Parse ``bazel query --output=xml`` output for test_set_test rules.

    Returns a list of dicts, one per discovered rule, with keys:
        test_set_test_label, test_label, assertion, requirement_id,
        disabled, depends_on_raw (list of test_set_test target labels).
    """
    root = ET.fromstring(xml_content)
    results: list[dict[str, Any]] = []

    for rule in root.findall("rule"):
        if rule.get("class") != "test_set_test":
            continue

        test_set_test_label = rule.get("name", "")

        # Extract wrapped test label (required)
        test_label_elem = rule.find("label[@name='test']")
        if test_label_elem is None:
            continue  # skip malformed rules
        test_label = test_label_elem.get("value", "")

        # Extract optional string attributes
        assertion = ""
        assertion_elem = rule.find("string[@name='assertion']")
        if assertion_elem is not None:
            assertion = assertion_elem.get("value", "")

        requirement_id = ""
        req_elem = rule.find("string[@name='requirement_id']")
        if req_elem is not None:
            requirement_id = req_elem.get("value", "")

        # Extract disabled boolean (only present when True)
        disabled = False
        disabled_elem = rule.find("boolean[@name='disabled']")
        if disabled_elem is not None:
            disabled = disabled_elem.get("value", "").lower() == "true"

        # Extract depends_on list (test_set_test target labels)
        depends_on_raw: list[str] = []
        depends_on_list = rule.find("list[@name='depends_on']")
        if depends_on_list is not None:
            for label_elem in depends_on_list.findall("label"):
                dep_val = label_elem.get("value", "")
                if dep_val:
                    depends_on_raw.append(dep_val)

        results.append({
            "test_set_test_label": test_set_test_label,
            "test_label": test_label,
            "assertion": assertion,
            "requirement_id": requirement_id,
            "disabled": disabled,
            "depends_on_raw": depends_on_raw,
        })

    return results


def parse_test_sets_xml(xml_content: str) -> list[dict[str, Any]]:
    """Parse ``bazel query --output=xml`` output for _test_set_rule_test rules.

    Returns a list of dicts, one per discovered test_set, with keys:
        label, name, assertion, requirement_id, tests_raw (list of
        test_set_test labels), subsets_raw (list of test_set labels,
        which may be aliases).
    """
    root = ET.fromstring(xml_content)
    results: list[dict[str, Any]] = []

    for rule in root.findall("rule"):
        if rule.get("class") != "_test_set_rule_test":
            continue

        label = rule.get("name", "")

        name = ""
        name_elem = rule.find("string[@name='name']")
        if name_elem is not None:
            name = name_elem.get("value", "")

        assertion = ""
        assertion_elem = rule.find("string[@name='assertion']")
        if assertion_elem is not None:
            assertion = assertion_elem.get("value", "")

        requirement_id = ""
        req_elem = rule.find("string[@name='requirement_id']")
        if req_elem is not None:
            requirement_id = req_elem.get("value", "")

        tests_raw: list[str] = []
        tests_list = rule.find("list[@name='tests']")
        if tests_list is not None:
            for label_elem in tests_list.findall("label"):
                val = label_elem.get("value", "")
                if val:
                    tests_raw.append(val)

        subsets_raw: list[str] = []
        subsets_list = rule.find("list[@name='subsets']")
        if subsets_list is not None:
            for label_elem in subsets_list.findall("label"):
                val = label_elem.get("value", "")
                if val:
                    subsets_raw.append(val)

        results.append({
            "label": label,
            "name": name,
            "assertion": assertion,
            "requirement_id": requirement_id,
            "tests_raw": tests_raw,
            "subsets_raw": subsets_raw,
        })

    return results


def resolve_depends_on(discovered: list[dict[str, Any]]) -> None:
    """Resolve depends_on from test_set_test labels to wrapped test labels.

    Modifies *discovered* in place: replaces ``depends_on_raw`` (list of
    test_set_test target labels like ``//pkg:cart_wrapped``) with
    ``depends_on`` (list of wrapped test labels like ``//pkg:cart_raw_test``).
    Unknown references are silently dropped.
    """
    # Build mapping: test_set_test_label -> wrapped test_label
    mapping: dict[str, str] = {}
    for entry in discovered:
        mapping[entry["test_set_test_label"]] = entry["test_label"]

    for entry in discovered:
        resolved: list[str] = []
        for raw_dep in entry.get("depends_on_raw", []):
            wrapped = mapping.get(raw_dep)
            if wrapped is not None:
                resolved.append(wrapped)
        entry["depends_on"] = resolved


def _collect_tree_names(tree: dict[str, Any]) -> set[str]:
    """Collect all test_set names from a manifest tree recursively."""
    names: set[str] = set()
    name = tree.get("name", "")
    if name:
        names.add(name)
    for subset in tree.get("subsets", []):
        names.update(_collect_tree_names(subset))
    return names


def _collect_test_labels(tree: dict[str, Any]) -> set[str]:
    """Collect all test labels from a tree recursively."""
    labels: set[str] = set()
    for label in tree.get("tests", []):
        labels.add(label)
    for subset in tree.get("subsets", []):
        labels.update(_collect_test_labels(subset))
    return labels


def _build_tree(
    ts: dict[str, Any],
    ts_lookup: dict[str, dict[str, Any]],
    test_mapping: dict[str, str],
    visited: set[str],
) -> dict[str, Any]:
    """Recursively build a tree dict from a test_set definition.

    Args:
        ts: A parsed test_set dict from ``parse_test_sets_xml()``.
        ts_lookup: Lookup mapping test_set labels (and aliases) to dicts.
        test_mapping: Mapping from test_set_test label to wrapped test label.
        visited: Set of already-visited test_set labels (cycle protection).
    """
    if ts["label"] in visited:
        # Prevent infinite recursion on circular references
        return {
            "name": ts["name"],
            "assertion": ts["assertion"],
            "tests": [],
            "subsets": [],
        }
    visited.add(ts["label"])

    # Resolve test_set_test labels to wrapped test labels
    resolved_tests: list[str] = []
    for tst_label in ts["tests_raw"]:
        test_label = test_mapping.get(tst_label)
        if test_label is not None:
            resolved_tests.append(test_label)

    # Recurse into subsets
    child_trees: list[dict[str, Any]] = []
    for sub_ref in ts["subsets_raw"]:
        sub_ts = ts_lookup.get(sub_ref)
        if sub_ts is not None:
            child_trees.append(
                _build_tree(sub_ts, ts_lookup, test_mapping, visited)
            )

    tree: dict[str, Any] = {
        "name": ts["name"],
        "assertion": ts["assertion"],
        "tests": resolved_tests,
        "subsets": child_trees,
    }
    if ts.get("requirement_id"):
        tree["requirement_id"] = ts["requirement_id"]

    return tree


def build_workspace_trees(
    test_sets: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    existing_names: set[str],
) -> list[dict[str, Any]]:
    """Build hierarchical trees for root test_sets not in the manifest.

    Identifies root test_sets (not referenced as a subset by any other),
    filters out those already present in the manifest (by name), and
    builds recursive tree dicts matching the manifest tree format.

    Args:
        test_sets: Parsed test_set dicts from ``parse_test_sets_xml()``.
        tests: Parsed test dicts from ``parse_query_xml()``.
        existing_names: Set of test_set names already in the manifest tree.

    Returns:
        List of tree dicts for discovered root test_sets.
    """
    if not test_sets:
        return []

    # Build test_set lookup by label (including potential aliases).
    # The test_set macro creates rule //pkg:foo_test and alias //pkg:foo
    # when the user-given name doesn't end with _test or _tests.
    ts_lookup: dict[str, dict[str, Any]] = {}
    for ts in test_sets:
        ts_lookup[ts["label"]] = ts
        # Register alias: if label ends with _test (not _tests),
        # the alias is the label without _test suffix.
        label = ts["label"]
        if label.endswith("_test") and not label.endswith("_tests"):
            alias = label[:-5]
            if alias not in ts_lookup:
                ts_lookup[alias] = ts

    # Build test_set_test -> test_label mapping
    test_mapping: dict[str, str] = {}
    for t in tests:
        test_mapping[t["test_set_test_label"]] = t["test_label"]

    # Find root test_sets (not referenced as subset by any other)
    referenced_labels: set[str] = set()
    for ts in test_sets:
        for sub_ref in ts["subsets_raw"]:
            resolved = ts_lookup.get(sub_ref)
            if resolved is not None:
                referenced_labels.add(resolved["label"])

    roots = [ts for ts in test_sets if ts["label"] not in referenced_labels]

    # Build trees for roots not already in the manifest
    trees: list[dict[str, Any]] = []
    for root_ts in roots:
        if root_ts["name"] in existing_names:
            continue
        tree = _build_tree(root_ts, ts_lookup, test_mapping, set())
        trees.append(tree)

    return trees


def discover_workspace_tests(
    workspace_dir: str | None = None,
    timeout: int = 60,
) -> dict[str, Any] | None:
    """Discover all test_set_test and test_set targets in the Bazel workspace.

    Runs ``bazel query --output=xml`` to find both ``test_set_test`` rules
    (individual tests) and ``_test_set_rule_test`` rules (test set groupings).

    Args:
        workspace_dir: Path to Bazel workspace root.  If *None*, reads
            ``BUILD_WORKSPACE_DIRECTORY`` from the environment.
        timeout: Timeout in seconds for the bazel query command.

    Returns:
        Dict with ``tests`` and ``test_sets`` lists, or *None* if
        discovery is unavailable (no workspace dir, bazel not found,
        query failed, etc.).
    """
    if workspace_dir is None:
        workspace_dir = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    if not workspace_dir:
        return None

    try:
        result = subprocess.run(
            [
                "bazel", "query", "--output=xml",
                'kind("test_set_test", //...)'
                ' + kind("_test_set_rule_test", //...)',
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace_dir,
        )
    except FileNotFoundError:
        print(
            "Workspace discovery: bazel not found in PATH, skipping",
            file=sys.stderr,
        )
        return None
    except subprocess.TimeoutExpired:
        print(
            f"Workspace discovery: bazel query timed out after {timeout}s, "
            "skipping",
            file=sys.stderr,
        )
        return None

    if result.returncode != 0:
        print(
            "Workspace discovery: bazel query failed "
            f"(exit {result.returncode}), skipping",
            file=sys.stderr,
        )
        return None

    if not result.stdout.strip():
        return {"tests": [], "test_sets": []}

    tests = parse_query_xml(result.stdout)
    test_sets = parse_test_sets_xml(result.stdout)
    resolve_depends_on(tests)
    return {"tests": tests, "test_sets": test_sets}


def merge_discovered_tests(
    manifest: dict[str, Any],
    discovery: dict[str, Any],
) -> dict[str, Any]:
    """Merge discovered workspace tests into a manifest copy for reporting.

    Uses the hierarchical test_set structure from discovery to reconstruct
    the full workspace DAG.  All root test_sets (the executing one from
    the manifest and any others from discovery) become peer subsets of a
    synthetic *Workspace* root.  This ensures every report shows the same
    DAG shape regardless of which test_set is executed — only test
    statuses differ.

    Tests already in the manifest (matched by normalized label) are
    skipped.  Any new tests not placed in a discovered tree are grouped
    under an *Other workspace tests* catch-all subset.

    The original manifest is **not** modified; a deep copy is returned.
    """
    tests = discovery["tests"]
    test_sets = discovery["test_sets"]

    merged = copy.deepcopy(manifest)
    test_set_tests = merged.setdefault("test_set_tests", {})

    # Build normalized lookup of existing manifest labels
    existing_normalized: dict[str, str] = {}
    for key in test_set_tests:
        existing_normalized[normalize_label(key)] = key

    # Also collect all normalized labels (existing + new) for depends_on
    # filtering later.
    all_normalized: set[str] = set(existing_normalized.keys())

    new_labels: list[str] = []

    for entry in tests:
        norm = normalize_label(entry["test_label"])
        if norm in existing_normalized:
            continue  # already in manifest

        # Use the query-format label (//pkg:target)
        label = entry["test_label"]
        all_normalized.add(norm)
        new_labels.append(label)

        test_set_tests[label] = {
            "assertion": entry["assertion"],
            "requirement_id": entry.get("requirement_id", ""),
            "depends_on": [],  # populated below after all labels are known
            "executable": "",  # not executed
        }
        if entry.get("disabled"):
            test_set_tests[label]["disabled"] = True

    if not new_labels:
        return merged

    # Now populate depends_on for new entries, filtered to known labels.
    discovered_by_label: dict[str, dict[str, Any]] = {
        entry["test_label"]: entry for entry in tests
    }
    for label in new_labels:
        disc_entry = discovered_by_label.get(label)
        if disc_entry is None:
            continue
        filtered_deps: list[str] = []
        for dep in disc_entry.get("depends_on", []):
            dep_norm = normalize_label(dep)
            if dep_norm in all_normalized:
                # Use the existing manifest key if it exists, otherwise
                # use the query-format label.
                filtered_deps.append(
                    existing_normalized.get(dep_norm, dep)
                )
        test_set_tests[label]["depends_on"] = filtered_deps

    # Build hierarchical trees for discovered root test_sets.
    # Exclude the manifest root so we don't duplicate it.
    manifest_root_name = merged.get("test_set", {}).get("name", "")
    other_trees = build_workspace_trees(
        test_sets, tests, {manifest_root_name},
    )

    # Find tests not placed in any discovered tree (orphans)
    placed: set[str] = set()
    for tree in other_trees:
        placed.update(_collect_test_labels(tree))

    unplaced = [label for label in new_labels if label not in placed]

    # Build the list of peer subsets for the workspace root
    peer_subsets: list[dict[str, Any]] = other_trees[:]
    if unplaced:
        peer_subsets.append({
            "name": "Other workspace tests",
            "assertion": "Tests discovered in workspace but not in "
                         "any test set",
            "tests": unplaced,
            "subsets": [],
        })

    if peer_subsets:
        # Wrap manifest root and discovered roots as peers under a
        # synthetic workspace root.  This ensures every report shows the
        # same DAG shape regardless of which test_set is executed.
        merged["test_set"] = {
            "name": "Workspace",
            "assertion": "All test sets in workspace",
            "tests": [],
            "subsets": [merged["test_set"]] + peer_subsets,
        }

    return merged
