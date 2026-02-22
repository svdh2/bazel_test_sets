"""Tests for workspace-wide test discovery via bazel query."""

from __future__ import annotations

import copy
import os
import subprocess
from unittest.mock import patch

from orchestrator.discovery.workspace import (
    build_workspace_trees,
    discover_workspace_tests,
    merge_discovered_tests,
    normalize_label,
    parse_query_xml,
    parse_test_sets_xml,
    resolve_depends_on,
)

# ---------------------------------------------------------------------------
# Sample XML fragments for tests
# ---------------------------------------------------------------------------

SAMPLE_XML = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//ecommerce:cart_wrapped">
        <string name="name" value="cart_wrapped"/>
        <label name="test" value="//ecommerce:cart_raw_test"/>
        <string name="assertion" value="Shopping cart correctly calculates totals"/>
        <string name="requirement_id" value="CART-001"/>
        <rule-input name="//ecommerce:cart_raw_test"/>
    </rule>
    <rule class="test_set_test" name="//ecommerce:discount_wrapped">
        <string name="name" value="discount_wrapped"/>
        <label name="test" value="//ecommerce:discount_raw_test"/>
        <list name="depends_on">
            <label value="//ecommerce:cart_wrapped"/>
        </list>
        <string name="assertion" value="Discount codes apply correctly"/>
        <string name="requirement_id" value="CART-002"/>
        <rule-input name="//ecommerce:cart_wrapped"/>
        <rule-input name="//ecommerce:discount_raw_test"/>
    </rule>
</query>"""

DISABLED_XML = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//pkg:disabled_wrapped">
        <label name="test" value="//pkg:disabled_test"/>
        <string name="assertion" value="A disabled test"/>
        <boolean name="disabled" value="true"/>
    </rule>
</query>"""

EMPTY_XML = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
</query>"""

MIXED_XML = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//pkg:real_test">
        <label name="test" value="//pkg:real"/>
        <string name="assertion" value="Real test"/>
    </rule>
    <rule class="some_other_rule" name="//pkg:not_a_test">
        <string name="name" value="not_a_test"/>
    </rule>
</query>"""

# XML containing both test_set_test and _test_set_rule_test rules
COMBINED_XML = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//ecommerce:cart_wrapped">
        <string name="name" value="cart_wrapped"/>
        <label name="test" value="//ecommerce:cart_raw_test"/>
        <string name="assertion" value="Cart works"/>
    </rule>
    <rule class="test_set_test" name="//ecommerce:payment_wrapped">
        <string name="name" value="payment_wrapped"/>
        <label name="test" value="//ecommerce:payment_raw_test"/>
        <string name="assertion" value="Payment works"/>
        <list name="depends_on">
            <label value="//ecommerce:cart_wrapped"/>
        </list>
    </rule>
    <rule class="test_set_test" name="//other:alpha_wrapped">
        <string name="name" value="alpha_wrapped"/>
        <label name="test" value="//other:alpha_raw_test"/>
        <string name="assertion" value="Alpha test"/>
    </rule>
    <rule class="test_set_test" name="//other:beta_wrapped">
        <string name="name" value="beta_wrapped"/>
        <label name="test" value="//other:beta_raw_test"/>
        <string name="assertion" value="Beta test"/>
        <list name="depends_on">
            <label value="//other:alpha_wrapped"/>
        </list>
    </rule>
    <rule class="_test_set_rule_test" name="//ecommerce:ecommerce_tests">
        <string name="name" value="ecommerce_tests"/>
        <list name="tests">
            <label value="//ecommerce:cart_wrapped"/>
            <label value="//ecommerce:payment_wrapped"/>
        </list>
        <list name="subsets"/>
        <string name="assertion" value="E-commerce tests"/>
        <string name="requirement_id" value="ECOM-001"/>
    </rule>
    <rule class="_test_set_rule_test" name="//other:other_suite_test">
        <string name="name" value="other_suite_test"/>
        <list name="tests">
            <label value="//other:alpha_wrapped"/>
        </list>
        <list name="subsets"/>
        <string name="assertion" value="Other suite"/>
    </rule>
    <rule class="_test_set_rule_test" name="//other:root_tests">
        <string name="name" value="root_tests"/>
        <list name="tests">
            <label value="//other:beta_wrapped"/>
        </list>
        <list name="subsets">
            <label value="//other:other_suite"/>
        </list>
        <string name="assertion" value="Root test set"/>
        <string name="requirement_id" value="ROOT-001"/>
    </rule>
</query>"""


# ---------------------------------------------------------------------------
# normalize_label
# ---------------------------------------------------------------------------


class TestNormalizeLabel:
    def test_strips_double_at(self):
        assert normalize_label("@@//pkg:target") == "//pkg:target"

    def test_no_prefix_unchanged(self):
        assert normalize_label("//pkg:target") == "//pkg:target"

    def test_single_at_unchanged(self):
        assert normalize_label("@repo//pkg:target") == "@repo//pkg:target"

    def test_empty_string(self):
        assert normalize_label("") == ""


# ---------------------------------------------------------------------------
# parse_query_xml
# ---------------------------------------------------------------------------


class TestParseQueryXml:
    def test_basic_rule(self):
        results = parse_query_xml(SAMPLE_XML)
        assert len(results) == 2

        cart = results[0]
        assert cart["test_set_test_label"] == "//ecommerce:cart_wrapped"
        assert cart["test_label"] == "//ecommerce:cart_raw_test"
        assert cart["assertion"] == "Shopping cart correctly calculates totals"
        assert cart["requirement_id"] == "CART-001"
        assert cart["disabled"] is False
        assert cart["depends_on_raw"] == []

    def test_with_depends_on(self):
        results = parse_query_xml(SAMPLE_XML)
        discount = results[1]
        assert discount["depends_on_raw"] == ["//ecommerce:cart_wrapped"]

    def test_with_disabled(self):
        results = parse_query_xml(DISABLED_XML)
        assert len(results) == 1
        assert results[0]["disabled"] is True

    def test_missing_optional_attributes(self):
        """Missing requirement_id defaults to empty string."""
        xml = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//pkg:t">
        <label name="test" value="//pkg:raw"/>
        <string name="assertion" value="A test"/>
    </rule>
</query>"""
        results = parse_query_xml(xml)
        assert len(results) == 1
        assert results[0]["requirement_id"] == ""
        assert results[0]["disabled"] is False
        assert results[0]["depends_on_raw"] == []

    def test_empty_query_result(self):
        results = parse_query_xml(EMPTY_XML)
        assert results == []

    def test_non_test_set_test_rules_ignored(self):
        results = parse_query_xml(MIXED_XML)
        assert len(results) == 1
        assert results[0]["test_set_test_label"] == "//pkg:real_test"

    def test_multiple_depends_on(self):
        xml = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//pkg:multi">
        <label name="test" value="//pkg:multi_test"/>
        <string name="assertion" value="Multi deps"/>
        <list name="depends_on">
            <label value="//pkg:a_wrapped"/>
            <label value="//pkg:b_wrapped"/>
        </list>
    </rule>
</query>"""
        results = parse_query_xml(xml)
        assert results[0]["depends_on_raw"] == [
            "//pkg:a_wrapped", "//pkg:b_wrapped",
        ]

    def test_parameters_parsed(self):
        """Parameters string_dict is extracted from XML."""
        xml = """\
<?xml version="1.1" encoding="UTF-8" standalone="no"?>
<query version="2">
    <rule class="test_set_test" name="//pkg:mem_wrapped">
        <label name="test" value="//pkg:mem_test"/>
        <string name="assertion" value="Memory under limit"/>
        <dict name="parameters">
            <entry key="service" value="worker"/>
            <entry key="limit-gb" value="1.0"/>
        </dict>
    </rule>
</query>"""
        results = parse_query_xml(xml)
        assert len(results) == 1
        assert results[0]["parameters"] == {"service": "worker", "limit-gb": "1.0"}

    def test_parameters_absent_when_not_in_xml(self):
        """No parameters key when XML has no dict element."""
        results = parse_query_xml(SAMPLE_XML)
        assert "parameters" not in results[0]

    def test_ignores_test_set_rule_test(self):
        """parse_query_xml only returns test_set_test, not _test_set_rule_test."""
        results = parse_query_xml(COMBINED_XML)
        classes = {r.get("class") for r in results}
        # All returned entries are test_set_test (no class field in result,
        # but we verify by checking labels)
        labels = [r["test_set_test_label"] for r in results]
        assert "//ecommerce:ecommerce_tests" not in labels
        assert "//other:root_tests" not in labels


# ---------------------------------------------------------------------------
# parse_test_sets_xml
# ---------------------------------------------------------------------------


class TestParseTestSetsXml:
    def test_basic_test_set(self):
        results = parse_test_sets_xml(COMBINED_XML)
        assert len(results) == 3

        ecom = next(r for r in results if r["name"] == "ecommerce_tests")
        assert ecom["label"] == "//ecommerce:ecommerce_tests"
        assert ecom["assertion"] == "E-commerce tests"
        assert ecom["requirement_id"] == "ECOM-001"
        assert ecom["tests_raw"] == [
            "//ecommerce:cart_wrapped",
            "//ecommerce:payment_wrapped",
        ]
        assert ecom["subsets_raw"] == []

    def test_test_set_with_subsets(self):
        results = parse_test_sets_xml(COMBINED_XML)
        root = next(r for r in results if r["name"] == "root_tests")
        assert root["label"] == "//other:root_tests"
        assert root["subsets_raw"] == ["//other:other_suite"]
        assert root["tests_raw"] == ["//other:beta_wrapped"]

    def test_missing_requirement_id(self):
        results = parse_test_sets_xml(COMBINED_XML)
        other_suite = next(
            r for r in results if r["name"] == "other_suite_test"
        )
        assert other_suite["requirement_id"] == ""

    def test_ignores_test_set_test_rules(self):
        """parse_test_sets_xml only returns _test_set_rule_test."""
        results = parse_test_sets_xml(COMBINED_XML)
        labels = [r["label"] for r in results]
        assert "//ecommerce:cart_wrapped" not in labels
        assert "//other:alpha_wrapped" not in labels

    def test_empty_query(self):
        results = parse_test_sets_xml(EMPTY_XML)
        assert results == []


# ---------------------------------------------------------------------------
# resolve_depends_on
# ---------------------------------------------------------------------------


class TestResolveDependsOn:
    def test_resolves_to_test_labels(self):
        discovered = [
            {
                "test_set_test_label": "//pkg:a_wrapped",
                "test_label": "//pkg:a_raw",
                "depends_on_raw": [],
            },
            {
                "test_set_test_label": "//pkg:b_wrapped",
                "test_label": "//pkg:b_raw",
                "depends_on_raw": ["//pkg:a_wrapped"],
            },
        ]
        resolve_depends_on(discovered)
        assert discovered[0]["depends_on"] == []
        assert discovered[1]["depends_on"] == ["//pkg:a_raw"]

    def test_unknown_dependency_dropped(self):
        discovered = [
            {
                "test_set_test_label": "//pkg:a_wrapped",
                "test_label": "//pkg:a_raw",
                "depends_on_raw": ["//pkg:nonexistent_wrapped"],
            },
        ]
        resolve_depends_on(discovered)
        assert discovered[0]["depends_on"] == []

    def test_empty_depends_on(self):
        discovered = [
            {
                "test_set_test_label": "//pkg:a_wrapped",
                "test_label": "//pkg:a_raw",
                "depends_on_raw": [],
            },
        ]
        resolve_depends_on(discovered)
        assert discovered[0]["depends_on"] == []


# ---------------------------------------------------------------------------
# build_workspace_trees
# ---------------------------------------------------------------------------


class TestBuildWorkspaceTrees:
    def _make_tests(self):
        return [
            {
                "test_set_test_label": "//pkg:a_wrapped",
                "test_label": "//pkg:a_test",
            },
            {
                "test_set_test_label": "//pkg:b_wrapped",
                "test_label": "//pkg:b_test",
            },
            {
                "test_set_test_label": "//other:c_wrapped",
                "test_label": "//other:c_test",
            },
        ]

    def _make_test_sets(self):
        return [
            {
                "label": "//pkg:suite_tests",
                "name": "suite_tests",
                "assertion": "Package suite",
                "requirement_id": "SUITE-001",
                "tests_raw": ["//pkg:a_wrapped", "//pkg:b_wrapped"],
                "subsets_raw": [],
            },
            {
                "label": "//other:other_suite_test",
                "name": "other_suite_test",
                "assertion": "Other suite",
                "requirement_id": "",
                "tests_raw": ["//other:c_wrapped"],
                "subsets_raw": [],
            },
        ]

    def test_builds_trees_for_roots(self):
        trees = build_workspace_trees(
            self._make_test_sets(), self._make_tests(), set(),
        )
        assert len(trees) == 2
        names = {t["name"] for t in trees}
        assert names == {"suite_tests", "other_suite_test"}

    def test_skips_existing_names(self):
        trees = build_workspace_trees(
            self._make_test_sets(), self._make_tests(), {"suite_tests"},
        )
        assert len(trees) == 1
        assert trees[0]["name"] == "other_suite_test"

    def test_resolves_test_labels(self):
        trees = build_workspace_trees(
            self._make_test_sets(), self._make_tests(), set(),
        )
        suite = next(t for t in trees if t["name"] == "suite_tests")
        assert suite["tests"] == ["//pkg:a_test", "//pkg:b_test"]

    def test_includes_requirement_id(self):
        trees = build_workspace_trees(
            self._make_test_sets(), self._make_tests(), set(),
        )
        suite = next(t for t in trees if t["name"] == "suite_tests")
        assert suite["requirement_id"] == "SUITE-001"

    def test_omits_empty_requirement_id(self):
        trees = build_workspace_trees(
            self._make_test_sets(), self._make_tests(), set(),
        )
        other = next(t for t in trees if t["name"] == "other_suite_test")
        assert "requirement_id" not in other

    def test_nested_subsets(self):
        """Root with subsets builds nested tree."""
        test_sets = [
            {
                "label": "//pkg:child_test",
                "name": "child_test",
                "assertion": "Child",
                "requirement_id": "",
                "tests_raw": ["//pkg:a_wrapped"],
                "subsets_raw": [],
            },
            {
                "label": "//pkg:root_tests",
                "name": "root_tests",
                "assertion": "Root",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": ["//pkg:child"],  # alias reference
            },
        ]
        tests = [
            {
                "test_set_test_label": "//pkg:a_wrapped",
                "test_label": "//pkg:a_test",
            },
        ]
        trees = build_workspace_trees(test_sets, tests, set())
        # child_test is referenced by root_tests via alias, so only root
        assert len(trees) == 1
        assert trees[0]["name"] == "root_tests"
        assert len(trees[0]["subsets"]) == 1
        assert trees[0]["subsets"][0]["name"] == "child_test"
        assert trees[0]["subsets"][0]["tests"] == ["//pkg:a_test"]

    def test_alias_resolution(self):
        """Subset references via alias (label without _test) are resolved."""
        test_sets = [
            {
                "label": "//pkg:payment_region_test",
                "name": "payment_region_test",
                "assertion": "Payment",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": [],
            },
            {
                "label": "//pkg:root_tests",
                "name": "root_tests",
                "assertion": "Root",
                "requirement_id": "",
                "tests_raw": [],
                # Alias: //pkg:payment_region -> //pkg:payment_region_test
                "subsets_raw": ["//pkg:payment_region"],
            },
        ]
        trees = build_workspace_trees(test_sets, [], set())
        assert len(trees) == 1
        assert trees[0]["name"] == "root_tests"
        assert len(trees[0]["subsets"]) == 1
        assert trees[0]["subsets"][0]["name"] == "payment_region_test"

    def test_no_alias_for_tests_suffix(self):
        """Labels ending with _tests are NOT aliased (no stripping)."""
        test_sets = [
            {
                "label": "//pkg:my_tests",
                "name": "my_tests",
                "assertion": "My tests",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": [],
            },
            {
                "label": "//pkg:root_tests",
                "name": "root_tests",
                "assertion": "Root",
                "requirement_id": "",
                "tests_raw": [],
                # This uses the full label (no alias stripping for _tests)
                "subsets_raw": ["//pkg:my_tests"],
            },
        ]
        trees = build_workspace_trees(test_sets, [], set())
        assert len(trees) == 1
        assert trees[0]["name"] == "root_tests"
        assert len(trees[0]["subsets"]) == 1
        assert trees[0]["subsets"][0]["name"] == "my_tests"

    def test_identifies_roots(self):
        """Only root test_sets (not referenced as subset) appear at top level."""
        test_sets = [
            {
                "label": "//pkg:leaf_test",
                "name": "leaf_test",
                "assertion": "Leaf",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": [],
            },
            {
                "label": "//pkg:mid_test",
                "name": "mid_test",
                "assertion": "Mid",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": ["//pkg:leaf"],
            },
            {
                "label": "//pkg:root_tests",
                "name": "root_tests",
                "assertion": "Root",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": ["//pkg:mid"],
            },
        ]
        trees = build_workspace_trees(test_sets, [], set())
        assert len(trees) == 1
        assert trees[0]["name"] == "root_tests"

    def test_empty_test_sets(self):
        trees = build_workspace_trees([], [], set())
        assert trees == []

    def test_circular_reference_protection(self):
        """Circular subset references don't cause infinite recursion."""
        test_sets = [
            {
                "label": "//pkg:a_test",
                "name": "a_test",
                "assertion": "A",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": ["//pkg:b"],
            },
            {
                "label": "//pkg:b_test",
                "name": "b_test",
                "assertion": "B",
                "requirement_id": "",
                "tests_raw": [],
                "subsets_raw": ["//pkg:a"],
            },
        ]
        # Both reference each other, neither is a "root" in the strict sense.
        # But since both are referenced by the other, both end up in
        # referenced_labels, so no roots are found.
        trees = build_workspace_trees(test_sets, [], set())
        assert trees == []


# ---------------------------------------------------------------------------
# discover_workspace_tests
# ---------------------------------------------------------------------------


class TestDiscoverWorkspaceTests:
    def test_returns_none_without_workspace_dir(self):
        with patch.dict(os.environ, {}, clear=True):
            result = discover_workspace_tests(workspace_dir=None)
            assert result is None

    def test_returns_none_on_bazel_not_found(self):
        with patch(
            "orchestrator.discovery.workspace.subprocess.run",
            side_effect=FileNotFoundError("bazel"),
        ):
            result = discover_workspace_tests(workspace_dir="/ws")
            assert result is None

    def test_returns_none_on_timeout(self):
        with patch(
            "orchestrator.discovery.workspace.subprocess.run",
            side_effect=subprocess.TimeoutExpired("bazel", 60),
        ):
            result = discover_workspace_tests(workspace_dir="/ws")
            assert result is None

    def test_returns_none_on_bazel_failure(self):
        mock_result = type(
            "Result", (), {"returncode": 1, "stdout": "", "stderr": "error"},
        )()
        with patch(
            "orchestrator.discovery.workspace.subprocess.run",
            return_value=mock_result,
        ):
            result = discover_workspace_tests(workspace_dir="/ws")
            assert result is None

    def test_successful_discovery(self):
        mock_result = type(
            "Result", (), {
                "returncode": 0, "stdout": COMBINED_XML, "stderr": "",
            },
        )()
        with patch(
            "orchestrator.discovery.workspace.subprocess.run",
            return_value=mock_result,
        ):
            result = discover_workspace_tests(workspace_dir="/ws")
            assert result is not None
            assert "tests" in result
            assert "test_sets" in result
            assert len(result["tests"]) == 4
            assert len(result["test_sets"]) == 3
            # depends_on should be resolved
            payment = next(
                t for t in result["tests"]
                if t["test_label"] == "//ecommerce:payment_raw_test"
            )
            assert payment["depends_on"] == ["//ecommerce:cart_raw_test"]

    def test_empty_stdout_returns_empty_lists(self):
        mock_result = type(
            "Result", (), {"returncode": 0, "stdout": "", "stderr": ""},
        )()
        with patch(
            "orchestrator.discovery.workspace.subprocess.run",
            return_value=mock_result,
        ):
            result = discover_workspace_tests(workspace_dir="/ws")
            assert result == {"tests": [], "test_sets": []}

    def test_uses_build_workspace_directory_env(self):
        mock_result = type(
            "Result", (), {"returncode": 0, "stdout": EMPTY_XML, "stderr": ""},
        )()
        with patch.dict(
            os.environ, {"BUILD_WORKSPACE_DIRECTORY": "/my/workspace"},
        ):
            with patch(
                "orchestrator.discovery.workspace.subprocess.run",
                return_value=mock_result,
            ) as mock_run:
                discover_workspace_tests()
                mock_run.assert_called_once()
                assert mock_run.call_args.kwargs["cwd"] == "/my/workspace"

    def test_combined_query_used(self):
        """The query includes both test_set_test and _test_set_rule_test."""
        mock_result = type(
            "Result", (), {"returncode": 0, "stdout": EMPTY_XML, "stderr": ""},
        )()
        with patch(
            "orchestrator.discovery.workspace.subprocess.run",
            return_value=mock_result,
        ) as mock_run:
            discover_workspace_tests(workspace_dir="/ws")
            args = mock_run.call_args[0][0]
            query_arg = args[3]  # The query expression
            assert "test_set_test" in query_arg
            assert "_test_set_rule_test" in query_arg


# ---------------------------------------------------------------------------
# merge_discovered_tests
# ---------------------------------------------------------------------------


class TestMergeDiscoveredTests:
    def _make_manifest(self) -> dict:
        return {
            "test_set": {
                "name": "my_tests",
                "assertion": "My test set",
                "tests": ["@@//pkg:existing_test"],
                "subsets": [],
            },
            "test_set_tests": {
                "@@//pkg:existing_test": {
                    "assertion": "An existing test",
                    "depends_on": [],
                    "executable": "path/to/runner.sh",
                },
            },
        }

    def _make_discovery(self) -> dict:
        return {
            "tests": [
                {
                    "test_set_test_label": "//pkg:existing_wrapped",
                    "test_label": "//pkg:existing_test",
                    "assertion": "An existing test",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
                {
                    "test_set_test_label": "//other:new_wrapped",
                    "test_label": "//other:new_test",
                    "assertion": "A new test",
                    "requirement_id": "NEW-001",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [
                {
                    "label": "//other:new_suite_test",
                    "name": "new_suite_test",
                    "assertion": "New suite",
                    "requirement_id": "",
                    "tests_raw": ["//other:new_wrapped"],
                    "subsets_raw": [],
                },
            ],
        }

    def test_new_tests_added(self):
        manifest = self._make_manifest()
        discovery = self._make_discovery()
        merged = merge_discovered_tests(manifest, discovery)
        assert "//other:new_test" in merged["test_set_tests"]
        assert merged["test_set_tests"]["//other:new_test"]["assertion"] == (
            "A new test"
        )

    def test_existing_tests_skipped(self):
        manifest = self._make_manifest()
        discovery = self._make_discovery()
        merged = merge_discovered_tests(manifest, discovery)
        # Original key with @@ prefix should remain
        assert "@@//pkg:existing_test" in merged["test_set_tests"]
        # Should NOT have a duplicate without @@
        assert "//pkg:existing_test" not in merged["test_set_tests"]

    def test_label_normalization_for_matching(self):
        """@@//pkg:target in manifest matches //pkg:target from query."""
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//pkg:existing_wrapped",
                    "test_label": "//pkg:existing_test",
                    "assertion": "Same test",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [],
        }
        merged = merge_discovered_tests(manifest, discovery)
        # No new subsets since no new tests
        assert len(merged["test_set"]["subsets"]) == 0

    def test_workspace_root_created(self):
        """Discovered roots become peers of manifest root under Workspace."""
        manifest = self._make_manifest()
        discovery = self._make_discovery()
        merged = merge_discovered_tests(manifest, discovery)
        # Top-level should be a workspace root
        assert merged["test_set"]["name"] == "Workspace"
        ws_subsets = merged["test_set"]["subsets"]
        ws_names = [s["name"] for s in ws_subsets]
        # Manifest root and discovered root are peers
        assert "my_tests" in ws_names
        assert "new_suite_test" in ws_names

    def test_hierarchical_tree_as_peer(self):
        """Discovered test_sets appear as peers with correct tree."""
        manifest = self._make_manifest()
        discovery = self._make_discovery()
        merged = merge_discovered_tests(manifest, discovery)
        ws_subsets = merged["test_set"]["subsets"]
        new_suite = next(s for s in ws_subsets if s["name"] == "new_suite_test")
        assert new_suite["tests"] == ["//other:new_test"]

    def test_no_synthetic_subset_when_all_placed(self):
        """No 'Other workspace tests' when all tests are in discovered trees."""
        manifest = self._make_manifest()
        discovery = self._make_discovery()
        merged = merge_discovered_tests(manifest, discovery)
        ws_names = [s["name"] for s in merged["test_set"]["subsets"]]
        assert "Other workspace tests" not in ws_names

    def test_catch_all_for_unplaced_tests(self):
        """Tests not in any discovered tree go to catch-all subset."""
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//orphan:x_wrapped",
                    "test_label": "//orphan:x_test",
                    "assertion": "Orphan test",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [],  # No test_sets to place the test in
        }
        merged = merge_discovered_tests(manifest, discovery)
        assert merged["test_set"]["name"] == "Workspace"
        ws_subsets = merged["test_set"]["subsets"]
        ws_names = [s["name"] for s in ws_subsets]
        assert "my_tests" in ws_names
        assert "Other workspace tests" in ws_names
        catch_all = next(
            s for s in ws_subsets if s["name"] == "Other workspace tests"
        )
        assert "//orphan:x_test" in catch_all["tests"]

    def test_original_manifest_unmodified(self):
        manifest = self._make_manifest()
        original_keys = set(manifest["test_set_tests"].keys())
        discovery = self._make_discovery()
        merge_discovered_tests(manifest, discovery)
        assert set(manifest["test_set_tests"].keys()) == original_keys
        assert len(manifest["test_set"]["subsets"]) == 0

    def test_no_new_tests_no_subsets_added(self):
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//pkg:existing_wrapped",
                    "test_label": "//pkg:existing_test",
                    "assertion": "Same",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [],
        }
        merged = merge_discovered_tests(manifest, discovery)
        assert len(merged["test_set"]["subsets"]) == 0

    def test_depends_on_filtered_to_known_labels(self):
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//other:a_wrapped",
                    "test_label": "//other:a_test",
                    "assertion": "Test A",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": ["//pkg:existing_wrapped"],
                    "depends_on": ["//pkg:existing_test"],
                },
                {
                    "test_set_test_label": "//other:b_wrapped",
                    "test_label": "//other:b_test",
                    "assertion": "Test B",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": ["//unknown:missing_wrapped"],
                    "depends_on": [],
                },
            ],
            "test_sets": [],
        }
        merged = merge_discovered_tests(manifest, discovery)
        # a_test depends on existing_test (@@//pkg:existing_test)
        a_deps = merged["test_set_tests"]["//other:a_test"]["depends_on"]
        assert len(a_deps) == 1
        assert a_deps[0] == "@@//pkg:existing_test"  # uses existing key
        # b_test's dep was unknown, should be empty
        b_deps = merged["test_set_tests"]["//other:b_test"]["depends_on"]
        assert b_deps == []

    def test_disabled_test_added_with_flag(self):
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//other:disabled_wrapped",
                    "test_label": "//other:disabled_test",
                    "assertion": "Disabled test",
                    "requirement_id": "",
                    "disabled": True,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [],
        }
        merged = merge_discovered_tests(manifest, discovery)
        entry = merged["test_set_tests"]["//other:disabled_test"]
        assert entry["disabled"] is True

    def test_flat_manifest_gets_workspace_root(self):
        """Old-style manifest without subsets gets wrapped in workspace root."""
        manifest = {
            "test_set": {
                "name": "flat",
                "assertion": "Flat test set",
                "tests": ["//pkg:a_test"],
            },
            "test_set_tests": {
                "//pkg:a_test": {
                    "assertion": "Test A",
                    "depends_on": [],
                    "executable": "a.sh",
                },
            },
        }
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//other:b_wrapped",
                    "test_label": "//other:b_test",
                    "assertion": "Test B",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [],
        }
        merged = merge_discovered_tests(manifest, discovery)
        assert merged["test_set"]["name"] == "Workspace"
        ws_names = [s["name"] for s in merged["test_set"]["subsets"]]
        assert "flat" in ws_names

    def test_empty_executable_for_discovered(self):
        manifest = self._make_manifest()
        discovery = self._make_discovery()
        merged = merge_discovered_tests(manifest, discovery)
        new_entry = merged["test_set_tests"]["//other:new_test"]
        assert new_entry["executable"] == ""

    def test_nested_tree_structure(self):
        """Discovered test_sets with subsets produce nested tree structure."""
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//other:a_wrapped",
                    "test_label": "//other:a_test",
                    "assertion": "A",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
                {
                    "test_set_test_label": "//other:b_wrapped",
                    "test_label": "//other:b_test",
                    "assertion": "B",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [
                {
                    "label": "//other:child_test",
                    "name": "child_test",
                    "assertion": "Child set",
                    "requirement_id": "",
                    "tests_raw": ["//other:a_wrapped"],
                    "subsets_raw": [],
                },
                {
                    "label": "//other:parent_tests",
                    "name": "parent_tests",
                    "assertion": "Parent set",
                    "requirement_id": "P-001",
                    "tests_raw": ["//other:b_wrapped"],
                    "subsets_raw": ["//other:child"],
                },
            ],
        }
        merged = merge_discovered_tests(manifest, discovery)
        # Workspace root with manifest root and parent_tests as peers
        assert merged["test_set"]["name"] == "Workspace"
        ws_subsets = merged["test_set"]["subsets"]
        ws_names = [s["name"] for s in ws_subsets]
        assert "my_tests" in ws_names
        assert "parent_tests" in ws_names
        # child should NOT be at workspace level (it's nested)
        assert "child_test" not in ws_names
        # Verify nesting
        parent = next(s for s in ws_subsets if s["name"] == "parent_tests")
        assert parent["tests"] == ["//other:b_test"]
        assert len(parent["subsets"]) == 1
        child = parent["subsets"][0]
        assert child["name"] == "child_test"
        assert child["tests"] == ["//other:a_test"]

    def test_current_test_set_not_duplicated(self):
        """Test set with same name as manifest root is not added again."""
        manifest = self._make_manifest()  # name="my_tests"
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//other:new_wrapped",
                    "test_label": "//other:new_test",
                    "assertion": "New",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                },
            ],
            "test_sets": [
                {
                    "label": "//pkg:my_tests",
                    "name": "my_tests",  # same as manifest root
                    "assertion": "My test set",
                    "requirement_id": "",
                    "tests_raw": ["//pkg:existing_wrapped"],
                    "subsets_raw": [],
                },
                {
                    "label": "//other:new_suite_test",
                    "name": "new_suite_test",
                    "assertion": "New suite",
                    "requirement_id": "",
                    "tests_raw": ["//other:new_wrapped"],
                    "subsets_raw": [],
                },
            ],
        }
        merged = merge_discovered_tests(manifest, discovery)
        assert merged["test_set"]["name"] == "Workspace"
        ws_names = [s["name"] for s in merged["test_set"]["subsets"]]
        # my_tests appears once (manifest root), not twice
        assert ws_names.count("my_tests") == 1
        # new_suite_test should appear as peer
        assert "new_suite_test" in ws_names

    def test_parameters_included_in_merged_tests(self):
        """Parameters from discovery flow into merged test_set_tests."""
        manifest = self._make_manifest()
        discovery = {
            "tests": [
                {
                    "test_set_test_label": "//other:param_wrapped",
                    "test_label": "//other:param_test",
                    "assertion": "Parameterized test",
                    "requirement_id": "",
                    "disabled": False,
                    "depends_on_raw": [],
                    "depends_on": [],
                    "parameters": {"service": "api", "limit-gb": "0.5"},
                },
            ],
            "test_sets": [],
        }
        merged = merge_discovered_tests(manifest, discovery)
        entry = merged["test_set_tests"]["//other:param_test"]
        assert entry["parameters"] == {"service": "api", "limit-gb": "0.5"}
