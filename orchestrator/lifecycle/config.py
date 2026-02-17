"""Test set configuration file management.

Reads and writes the .test_set_config JSON file that stores reliability
thresholds and statistical parameters, separate from the .tests/status
lifecycle state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Default configuration values
DEFAULT_CONFIG = {
    "min_reliability": 0.99,
    "statistical_significance": 0.95,
}


class TestSetConfig:
    """Manages the .test_set_config JSON configuration file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._data: dict[str, Any] = dict(DEFAULT_CONFIG)
        if path is not None and path.exists():
            self._load()

    def _load(self) -> None:
        """Load config from the file."""
        assert self.path is not None
        try:
            text = self.path.read_text()
            data = json.loads(text)
            if isinstance(data, dict):
                self._data = {**DEFAULT_CONFIG, **data}
        except (json.JSONDecodeError, OSError):
            self._data = dict(DEFAULT_CONFIG)

    def save(self) -> None:
        """Write config to the file."""
        if self.path is None:
            raise ValueError("No config file path specified")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)
            f.write("\n")

    @property
    def config(self) -> dict[str, Any]:
        """Get the full configuration dict."""
        return dict(self._data)

    @property
    def min_reliability(self) -> float:
        """Get the minimum reliability threshold."""
        return float(
            self._data.get("min_reliability", DEFAULT_CONFIG["min_reliability"])
        )

    @property
    def statistical_significance(self) -> float:
        """Get the statistical significance level."""
        return float(
            self._data.get(
                "statistical_significance",
                DEFAULT_CONFIG["statistical_significance"],
            )
        )

    def set_config(
        self,
        min_reliability: float | None = None,
        statistical_significance: float | None = None,
    ) -> None:
        """Update configuration values."""
        if min_reliability is not None:
            self._data["min_reliability"] = min_reliability
        if statistical_significance is not None:
            self._data["statistical_significance"] = statistical_significance
