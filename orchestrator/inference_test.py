"""Tests for rigging dependency inference."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orchestrator.inference import (
    find_tests_verifying_feature,
    infer_rigging_dependencies,
    load_feature_map,
)


# Sample manifest used across tests
SAMPLE_MANIFEST = {
    "test_set": {
        "name": "payment_tests",
        "assertion": "Payment system works correctly",
    },
    "test_set_tests": {
        "auth_test": {
            "assertion": "Authentication works correctly",
            "executable": "/bin/auth_test",
            "depends_on": [],
        },
        "billing_test": {
            "assertion": "Billing calculations are accurate",
            "executable": "/bin/billing_test",
            "depends_on": ["auth_test"],
        },
        "checkout_test": {
            "assertion": "Checkout flow completes successfully",
            "executable": "/bin/checkout_test",
            "depends_on": ["auth_test", "billing_test"],
        },
        "notification_test": {
            "assertion": "Notifications are sent for payment events",
            "executable": "/bin/notification_test",
            "depends_on": [],
        },
    },
}


class TestFindTestsVerifyingFeature:
    """Tests for convention-based feature matching."""

    def test_match_by_test_name(self):
        """Feature name matching test target name."""
        matches = find_tests_verifying_feature("auth", SAMPLE_MANIFEST)
        assert "auth_test" in matches

    def test_match_by_assertion_text(self):
        """Feature name matching assertion text."""
        matches = find_tests_verifying_feature("billing", SAMPLE_MANIFEST)
        assert "billing_test" in matches

    def test_case_insensitive_matching(self):
        """Matching is case-insensitive."""
        matches = find_tests_verifying_feature("AUTH", SAMPLE_MANIFEST)
        assert "auth_test" in matches

    def test_no_match(self):
        """Feature with no matching tests returns empty list."""
        matches = find_tests_verifying_feature("database", SAMPLE_MANIFEST)
        assert matches == []

    def test_match_in_assertion_not_name(self):
        """Feature matching in assertion but not in test name."""
        matches = find_tests_verifying_feature("payment", SAMPLE_MANIFEST)
        # "notification_test" has "payment events" in assertion
        assert "notification_test" in matches

    def test_empty_manifest(self):
        """Empty manifest returns no matches."""
        matches = find_tests_verifying_feature("auth", {"test_set_tests": {}})
        assert matches == []


class TestFindTestsWithExplicitMap:
    """Tests for explicit feature map matching."""

    def test_explicit_map_overrides_convention(self):
        """Explicit feature map takes priority over convention."""
        feature_map = {"auth": ["custom_auth_test"]}
        matches = find_tests_verifying_feature(
            "auth", SAMPLE_MANIFEST, feature_map
        )
        assert matches == ["custom_auth_test"]
        # Convention match (auth_test) should NOT appear
        assert "auth_test" not in matches

    def test_explicit_map_missing_feature(self):
        """Feature not in map falls back to convention matching."""
        feature_map = {"other_feature": ["other_test"]}
        matches = find_tests_verifying_feature(
            "auth", SAMPLE_MANIFEST, feature_map
        )
        assert "auth_test" in matches

    def test_explicit_map_multiple_targets(self):
        """Explicit map can map a feature to multiple targets."""
        feature_map = {"auth": ["test_a", "test_b", "test_c"]}
        matches = find_tests_verifying_feature(
            "auth", SAMPLE_MANIFEST, feature_map
        )
        assert matches == ["test_a", "test_b", "test_c"]


class TestInferRiggingDependencies:
    """Tests for full dependency inference."""

    def test_infer_single_dependency(self):
        """Infer a single dependency from rigging feature."""
        inferred = infer_rigging_dependencies(
            "checkout_test",
            ["notification"],
            SAMPLE_MANIFEST,
        )
        assert len(inferred) == 1
        assert inferred[0]["target"] == "notification_test"
        assert inferred[0]["feature"] == "notification"
        assert inferred[0]["inferred"] is True

    def test_skip_declared_dependency(self):
        """Already-declared dependencies are not inferred."""
        inferred = infer_rigging_dependencies(
            "checkout_test",
            ["auth"],
            SAMPLE_MANIFEST,
        )
        # auth_test is already in checkout_test.depends_on
        assert all(d["target"] != "auth_test" for d in inferred)

    def test_skip_self_reference(self):
        """Test does not infer dependency on itself."""
        inferred = infer_rigging_dependencies(
            "auth_test",
            ["auth"],
            SAMPLE_MANIFEST,
        )
        assert all(d["target"] != "auth_test" for d in inferred)

    def test_no_features_no_inference(self):
        """No rigging features means no inferred dependencies."""
        inferred = infer_rigging_dependencies(
            "checkout_test",
            [],
            SAMPLE_MANIFEST,
        )
        assert inferred == []

    def test_no_matching_features(self):
        """Features that match nothing produce no inferred deps."""
        inferred = infer_rigging_dependencies(
            "checkout_test",
            ["nonexistent_feature"],
            SAMPLE_MANIFEST,
        )
        assert inferred == []

    def test_multiple_features_multiple_deps(self):
        """Multiple features can produce multiple inferred deps."""
        # checkout_test already depends on auth_test and billing_test
        # so only notification_test should be inferred
        inferred = infer_rigging_dependencies(
            "checkout_test",
            ["notification", "auth"],  # auth is already declared
            SAMPLE_MANIFEST,
        )
        targets = [d["target"] for d in inferred]
        assert "notification_test" in targets
        assert "auth_test" not in targets  # already declared
        assert "billing_test" not in targets  # already declared

    def test_deduplicate_inferred_deps(self):
        """Same target inferred from multiple features appears once."""
        inferred = infer_rigging_dependencies(
            "billing_test",
            ["notification", "payment"],  # both could match notification_test
            SAMPLE_MANIFEST,
        )
        targets = [d["target"] for d in inferred]
        assert targets.count("notification_test") <= 1

    def test_with_explicit_feature_map(self):
        """Feature map used for inference."""
        feature_map = {"custom_feature": ["notification_test"]}
        inferred = infer_rigging_dependencies(
            "checkout_test",
            ["custom_feature"],
            SAMPLE_MANIFEST,
            feature_map,
        )
        assert len(inferred) == 1
        assert inferred[0]["target"] == "notification_test"

    def test_test_not_in_manifest(self):
        """Test not in manifest produces no inference errors."""
        inferred = infer_rigging_dependencies(
            "nonexistent_test",
            ["auth"],
            SAMPLE_MANIFEST,
        )
        # Should still find matches but no declared deps to skip
        assert any(d["target"] == "auth_test" for d in inferred)


class TestLoadFeatureMap:
    """Tests for feature map file loading."""

    def test_load_valid_feature_map(self):
        """Load a valid feature map JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feature_map.json"
            data = {"auth": ["//test:auth_test"], "billing": ["//test:billing_test"]}
            path.write_text(json.dumps(data))

            result = load_feature_map(path)
            assert result == data

    def test_load_missing_file(self):
        """Missing file returns None."""
        result = load_feature_map("/nonexistent/path/feature_map.json")
        assert result is None

    def test_load_invalid_json(self):
        """Invalid JSON returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feature_map.json"
            path.write_text("not json")

            result = load_feature_map(path)
            assert result is None

    def test_load_non_dict_json(self):
        """JSON that is not a dict returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feature_map.json"
            path.write_text("[1, 2, 3]")

            result = load_feature_map(path)
            assert result is None

    def test_load_empty_dict(self):
        """Empty dict is a valid feature map."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "feature_map.json"
            path.write_text("{}")

            result = load_feature_map(path)
            assert result == {}
