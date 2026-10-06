"""Load the public, non-secret provider preset catalog."""

import json
import pathlib


def load_catalog(path: pathlib.Path) -> list[dict]:
    """Read provider presets from the shared UTF-8 JSON file."""
    with path.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)
