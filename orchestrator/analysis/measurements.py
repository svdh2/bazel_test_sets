"""Measurement storage for structured test output.

Stores and retrieves measurements captured from [TST] structured log events.
Each test label gets a JSON file containing its measurements, keyed by a
sanitised version of the Bazel label.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _label_to_filename(test_label: str) -> str:
    """Convert a Bazel label to a safe filename.

    Replaces non-alphanumeric characters (except hyphens and underscores)
    with underscores, then strips leading/trailing underscores.

    Args:
        test_label: Bazel test label, e.g. "//test:a".

    Returns:
        Safe filename string without extension.
    """
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", test_label)
    return safe.strip("_")


def store_measurements(
    test_label: str,
    measurements: list[dict[str, Any]],
    output_dir: str | Path,
) -> Path:
    """Store measurements for a test label as a JSON file.

    Creates the output directory if it does not exist. Overwrites any
    existing measurement file for the same label.

    Args:
        test_label: Bazel test label, e.g. "//test:a".
        measurements: List of measurement dicts (name, value, block).
        output_dir: Directory to write the measurement file into.

    Returns:
        Path to the written measurement file.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    filename = _label_to_filename(test_label) + ".json"
    file_path = output_path / filename

    data = {
        "test_label": test_label,
        "measurements": measurements,
    }

    file_path.write_text(json.dumps(data, indent=2) + "\n")
    return file_path


def load_measurements(
    test_label: str,
    output_dir: str | Path,
) -> dict[str, Any] | None:
    """Load stored measurements for a test label.

    Args:
        test_label: Bazel test label, e.g. "//test:a".
        output_dir: Directory containing measurement files.

    Returns:
        Dictionary with 'test_label' and 'measurements' keys, or None if
        no measurement file exists for the label.
    """
    output_path = Path(output_dir)
    filename = _label_to_filename(test_label) + ".json"
    file_path = output_path / filename

    if not file_path.exists():
        return None

    data = json.loads(file_path.read_text())
    return data
