"""Project locations. Every script resolves data files from here, not from the working directory."""

from __future__ import annotations

import os
from pathlib import Path

# common/src/sod_common/paths.py -> project root
ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"


def find_pbf() -> Path:
    """Resolve the shared OSM extract, allowing an explicit path override."""
    override = os.environ.get("PBF")
    if override:
        return (ROOT / override).resolve()

    candidates = sorted(path.resolve() for path in DATA_DIR.rglob("*.osm.pbf"))
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(
            f"No .osm.pbf file found under {DATA_DIR}; download an OSM extract "
            "or set the PBF environment variable."
        )
    paths = "\n".join(f"  - {path}" for path in candidates)
    raise RuntimeError(
        f"Found multiple .osm.pbf files under {DATA_DIR}:\n{paths}\n"
        "Set the PBF environment variable to choose one."
    )


# Shared by Part A's road import and Part B's OSM import.
PBF = find_pbf()
