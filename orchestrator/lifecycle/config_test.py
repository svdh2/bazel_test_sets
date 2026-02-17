"""Unit tests for the config module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from orchestrator.lifecycle.config import DEFAULT_CONFIG, TestSetConfig


class TestTestSetConfigCreate:
    """Tests for creating TestSetConfig instances."""

    def test_no_path_uses_defaults(self):
        """No path gives default config values."""
        cfg = TestSetConfig(None)
        assert cfg.min_reliability == DEFAULT_CONFIG["min_reliability"]
        assert cfg.statistical_significance == DEFAULT_CONFIG["statistical_significance"]
        assert cfg.max_test_percentage == 0.10
        assert cfg.max_hops == 2
        assert cfg.max_reruns == 100
        assert cfg.max_failures is None
        assert cfg.max_parallel is None

    def test_nonexistent_path_uses_defaults(self):
        """Nonexistent file path gives default config values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = TestSetConfig(Path(tmpdir) / "missing.json")
            assert cfg.min_reliability == 0.99
            assert cfg.statistical_significance == 0.95

    def test_load_from_file(self):
        """Config is loaded from a JSON file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text(json.dumps({
                "min_reliability": 0.95,
                "statistical_significance": 0.90,
            }))
            cfg = TestSetConfig(path)
            assert cfg.min_reliability == 0.95
            assert cfg.statistical_significance == 0.90

    def test_partial_file_fills_defaults(self):
        """Missing keys in config file are filled from defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text(json.dumps({"min_reliability": 0.80}))
            cfg = TestSetConfig(path)
            assert cfg.min_reliability == 0.80
            assert cfg.statistical_significance == 0.95  # default

    def test_corrupted_file_uses_defaults(self):
        """Corrupted JSON file falls back to defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text("{ invalid json }")
            cfg = TestSetConfig(path)
            assert cfg.min_reliability == 0.99
            assert cfg.statistical_significance == 0.95


class TestTestSetConfigSave:
    """Tests for saving config."""

    def test_save_creates_file(self):
        """save() creates the config file on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            cfg = TestSetConfig(path)
            cfg.save()

            assert path.exists()
            data = json.loads(path.read_text())
            assert data["min_reliability"] == 0.99
            assert data["statistical_significance"] == 0.95

    def test_save_creates_parent_dirs(self):
        """save() creates parent directories if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sub" / "dir" / ".test_set_config"
            cfg = TestSetConfig(path)
            cfg.save()
            assert path.exists()

    def test_save_without_path_raises(self):
        """save() raises ValueError when no path is set."""
        import pytest

        cfg = TestSetConfig(None)
        with pytest.raises(ValueError, match="No config file path"):
            cfg.save()

    def test_roundtrip(self):
        """Config survives save/load roundtrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            cfg1 = TestSetConfig(path)
            cfg1.set_config(min_reliability=0.90, statistical_significance=0.80)
            cfg1.save()

            cfg2 = TestSetConfig(path)
            assert cfg2.min_reliability == 0.90
            assert cfg2.statistical_significance == 0.80


class TestTestSetConfigSetConfig:
    """Tests for set_config method."""

    def test_set_both_values(self):
        """Setting both config values works."""
        cfg = TestSetConfig(None)
        cfg.set_config(min_reliability=0.80, statistical_significance=0.70)
        assert cfg.min_reliability == 0.80
        assert cfg.statistical_significance == 0.70

    def test_set_partial(self):
        """Setting one value doesn't affect the other."""
        cfg = TestSetConfig(None)
        cfg.set_config(min_reliability=0.80)
        assert cfg.min_reliability == 0.80
        assert cfg.statistical_significance == 0.95  # unchanged

    def test_config_property_returns_copy(self):
        """config property returns a dict copy."""
        cfg = TestSetConfig(None)
        d = cfg.config
        d["min_reliability"] = 0.0
        assert cfg.min_reliability == 0.99  # unchanged


class TestTestSetConfigExecutionProperties:
    """Tests for execution tuning config properties."""

    def test_load_execution_properties_from_file(self):
        """Execution properties are loaded from config file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text(json.dumps({
                "max_test_percentage": 0.25,
                "max_hops": 3,
                "max_reruns": 50,
                "max_failures": 5,
                "max_parallel": 8,
            }))
            cfg = TestSetConfig(path)
            assert cfg.max_test_percentage == 0.25
            assert cfg.max_hops == 3
            assert cfg.max_reruns == 50
            assert cfg.max_failures == 5
            assert cfg.max_parallel == 8

    def test_partial_execution_properties_fill_defaults(self):
        """Missing execution properties fall back to defaults."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text(json.dumps({"max_reruns": 200}))
            cfg = TestSetConfig(path)
            assert cfg.max_reruns == 200
            assert cfg.max_test_percentage == 0.10  # default
            assert cfg.max_hops == 2  # default
            assert cfg.max_failures is None  # default
            assert cfg.max_parallel is None  # default

    def test_null_max_failures_is_none(self):
        """Explicit null in config gives None for max_failures."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text(json.dumps({"max_failures": None}))
            cfg = TestSetConfig(path)
            assert cfg.max_failures is None

    def test_null_max_parallel_is_none(self):
        """Explicit null in config gives None for max_parallel."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / ".test_set_config"
            path.write_text(json.dumps({"max_parallel": None}))
            cfg = TestSetConfig(path)
            assert cfg.max_parallel is None
