"""Tests for the measurement storage module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orchestrator.analysis.measurements import (
    _label_to_filename,
    load_measurements,
    store_measurements,
)


class TestLabelToFilename:
    """Tests for Bazel label to filename conversion."""

    def test_simple_label(self):
        """Convert a simple Bazel label."""
        assert _label_to_filename("//test:a") == "test_a"

    def test_nested_label(self):
        """Convert a nested Bazel label."""
        assert _label_to_filename("//path/to/package:target") == "path_to_package_target"

    def test_label_with_hyphens(self):
        """Hyphens are preserved."""
        assert _label_to_filename("//my-package:my-test") == "my-package_my-test"

    def test_label_with_underscores(self):
        """Underscores are preserved."""
        assert _label_to_filename("//my_package:my_test") == "my_package_my_test"


class TestStoreMeasurements:
    """Tests for storing measurements."""

    def test_store_creates_file(self):
        """Storing measurements creates a JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [{"name": "latency", "value": 100}]
            path = store_measurements("//test:a", measurements, tmpdir)
            assert path.exists()
            assert path.suffix == ".json"

    def test_store_correct_content(self):
        """Stored file contains correct JSON content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [
                {"name": "latency", "value": 100},
                {"name": "throughput", "value": {"value": 1000, "unit": "rps"}},
            ]
            path = store_measurements("//test:a", measurements, tmpdir)

            data = json.loads(path.read_text())
            assert data["test_label"] == "//test:a"
            assert data["measurements"] == measurements

    def test_store_creates_directory(self):
        """Store creates output directory if it does not exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "nested" / "dir"
            measurements = [{"name": "x", "value": 1}]
            path = store_measurements("//test:a", measurements, output_dir)
            assert path.exists()

    def test_store_overwrites_existing(self):
        """Storing measurements overwrites existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements_v1 = [{"name": "latency", "value": 100}]
            measurements_v2 = [{"name": "latency", "value": 200}]

            store_measurements("//test:a", measurements_v1, tmpdir)
            store_measurements("//test:a", measurements_v2, tmpdir)

            loaded = load_measurements("//test:a", tmpdir)
            assert loaded is not None
            assert loaded["measurements"] == measurements_v2

    def test_store_empty_measurements(self):
        """Storing empty measurements list works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = store_measurements("//test:a", [], tmpdir)
            data = json.loads(path.read_text())
            assert data["measurements"] == []

    def test_store_returns_path(self):
        """Store returns the path to the written file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = store_measurements("//test:a", [], tmpdir)
            assert isinstance(path, Path)
            assert path.parent == Path(tmpdir)


class TestLoadMeasurements:
    """Tests for loading measurements."""

    def test_load_roundtrip(self):
        """Store and load produce identical measurements."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [
                {"name": "response_time", "value": {"value": 142.0, "unit": "ms"}},
                {"name": "status_code", "value": 200},
            ]
            store_measurements("//test:payment", measurements, tmpdir)
            loaded = load_measurements("//test:payment", tmpdir)

            assert loaded is not None
            assert loaded["test_label"] == "//test:payment"
            assert loaded["measurements"] == measurements

    def test_load_missing_file(self):
        """Loading non-existent measurement file returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_measurements("//test:nonexistent", tmpdir)
            assert result is None

    def test_load_missing_directory(self):
        """Loading from non-existent directory returns None."""
        result = load_measurements("//test:a", "/nonexistent/dir/12345")
        assert result is None

    def test_load_different_labels(self):
        """Different labels produce different measurement files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            m1 = [{"name": "x", "value": 1}]
            m2 = [{"name": "y", "value": 2}]

            store_measurements("//test:a", m1, tmpdir)
            store_measurements("//test:b", m2, tmpdir)

            loaded_a = load_measurements("//test:a", tmpdir)
            loaded_b = load_measurements("//test:b", tmpdir)

            assert loaded_a is not None
            assert loaded_b is not None
            assert loaded_a["measurements"] == m1
            assert loaded_b["measurements"] == m2

    def test_load_with_path_object(self):
        """Load works with Path object as output_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [{"name": "x", "value": 1}]
            store_measurements("//test:a", measurements, Path(tmpdir))
            loaded = load_measurements("//test:a", Path(tmpdir))
            assert loaded is not None
            assert loaded["measurements"] == measurements


class TestMeasurementStructuredValues:
    """Tests for structured measurement values."""

    def test_nested_dict_value(self):
        """Measurement with nested dict value roundtrips correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [
                {
                    "name": "performance",
                    "value": {
                        "p50": 10.0,
                        "p95": 50.0,
                        "p99": 100.0,
                        "unit": "ms",
                    },
                }
            ]
            store_measurements("//test:perf", measurements, tmpdir)
            loaded = load_measurements("//test:perf", tmpdir)
            assert loaded is not None
            assert loaded["measurements"][0]["value"]["p95"] == 50.0

    def test_list_value(self):
        """Measurement with list value roundtrips correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [
                {"name": "samples", "value": [1, 2, 3, 4, 5]}
            ]
            store_measurements("//test:samples", measurements, tmpdir)
            loaded = load_measurements("//test:samples", tmpdir)
            assert loaded is not None
            assert loaded["measurements"][0]["value"] == [1, 2, 3, 4, 5]

    def test_null_value(self):
        """Measurement with None value roundtrips correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            measurements = [{"name": "optional", "value": None}]
            store_measurements("//test:opt", measurements, tmpdir)
            loaded = load_measurements("//test:opt", tmpdir)
            assert loaded is not None
            assert loaded["measurements"][0]["value"] is None
