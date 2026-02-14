"""Rigging dependency inference from structured test logs.

Maps feature events captured during rigging phases to test_set_test targets
in the manifest. Inferred dependencies are advisory and flagged as such.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def find_tests_verifying_feature(
    feature_name: str,
    manifest: dict[str, Any],
    feature_map: dict[str, list[str]] | None = None,
) -> list[str]:
    """Find test targets that verify a given feature.

    Uses two matching strategies:
    1. Explicit mapping from feature_map (if provided)
    2. Convention-based matching: feature name appears in test target name
       or assertion text (case-insensitive substring match)

    Args:
        feature_name: Feature name from a structured log feature event.
        manifest: Parsed manifest dict with test_set_tests.
        feature_map: Optional explicit feature-to-test mappings.

    Returns:
        List of test target names that verify this feature.
    """
    matches: list[str] = []

    # Strategy 1: Explicit mapping
    if feature_map and feature_name in feature_map:
        for target in feature_map[feature_name]:
            if target not in matches:
                matches.append(target)
        return matches

    # Strategy 2: Convention-based matching
    test_set_tests = manifest.get("test_set_tests", {})
    feature_lower = feature_name.lower()

    for test_name, test_data in test_set_tests.items():
        # Match against test target name
        if feature_lower in test_name.lower():
            if test_name not in matches:
                matches.append(test_name)
            continue

        # Match against assertion text
        assertion = test_data.get("assertion", "")
        if feature_lower in assertion.lower():
            if test_name not in matches:
                matches.append(test_name)

    return matches


def infer_rigging_dependencies(
    test_name: str,
    rigging_features: list[str],
    manifest: dict[str, Any],
    feature_map: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Infer dependency edges from rigging feature events.

    For each feature exercised during rigging, finds test targets that
    verify that feature. Dependencies that are already declared in
    depends_on are excluded.

    Args:
        test_name: Name of the test being analysed.
        rigging_features: Feature names from rigging phase.
        manifest: Parsed manifest dict.
        feature_map: Optional explicit feature-to-test mappings.

    Returns:
        List of inferred dependency dicts with keys:
        - target: The inferred dependency test name
        - feature: The feature that created this inference
        - inferred: Always True
    """
    test_set_tests = manifest.get("test_set_tests", {})
    test_data = test_set_tests.get(test_name, {})
    declared_deps = set(test_data.get("depends_on", []))

    inferred: list[dict[str, Any]] = []
    seen_targets: set[str] = set()

    for feature in rigging_features:
        matching_tests = find_tests_verifying_feature(
            feature, manifest, feature_map
        )
        for target in matching_tests:
            # Skip self-references and already-declared dependencies
            if target == test_name:
                continue
            if target in declared_deps:
                continue
            if target in seen_targets:
                continue

            seen_targets.add(target)
            inferred.append({
                "target": target,
                "feature": feature,
                "inferred": True,
            })

    return inferred


def load_feature_map(path: str | Path) -> dict[str, list[str]] | None:
    """Load explicit feature-to-test mapping from a JSON file.

    The file format is:
    {
        "feature_name": ["//test:target1", "//test:target2"],
        ...
    }

    Args:
        path: Path to the feature map JSON file.

    Returns:
        Parsed feature map dict, or None if file does not exist.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        if not isinstance(data, dict):
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None
