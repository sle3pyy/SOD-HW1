"""Project locations. Every script resolves data files from here, not from the working directory."""

from __future__ import annotations

import os
from pathlib import Path

# common/src/sod_common/paths.py -> project root
ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data"
# The OSM extract used by both parts; override with the PBF environment variable.
PBF = ROOT / os.environ.get("PBF", "data/osm/portugal-261020.osm.pbf")
