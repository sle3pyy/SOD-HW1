#!/usr/bin/env python3
"""Run the Part B pipeline: OSM import into PostGIS, then entrance-tagging cross-validation."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sod_common.paths import DATA_DIR as PROJECT_DATA_DIR
from sod_common.paths import ROOT

PART_B = Path(__file__).resolve().parent
SRC = PART_B / "src"
TAGGING = PART_B / "entrance_tagging"
DATA_DIR = PROJECT_DATA_DIR / "entrance_tagging"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aoi-name", default="Porto", help="OSM name of the area-of-interest boundary.")
    parser.add_argument("--aoi-admin-level", type=int, default=7, help="OSM admin_level of the boundary.")
    parser.add_argument("--extent-buffer-m", type=float, default=1000, help="Buffer around the AOI in metres.")
    parser.add_argument("--skip-osm-import", action="store_true", help="Reuse the existing partb tables.")
    parser.add_argument(
        "--raw-file",
        type=Path,
        default=DATA_DIR / "osmfeatures-test25.csv",
        help="Raw feature file for cross-validation; that stage is skipped when it does not exist.",
    )
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, text=True, env=env)


def main() -> int:
    args = parse_args()
    if args.extent_buffer_m < 0:
        raise ValueError("--extent-buffer-m must be non-negative")

    if not args.skip_osm_import:
        run(["docker", "compose", "up", "-d", "postgis"])
        environment = os.environ.copy()
        environment["EXTENT_BUFFER_M"] = f"{args.extent_buffer_m:g}"
        run(
            [
                sys.executable,
                str(SRC / "import_osm.py"),
                "--aoi-name", args.aoi_name,
                "--aoi-admin-level", str(args.aoi_admin_level),
            ],
            env=environment,
        )

    # The feature extractor that writes the raw file from PostGIS is not implemented yet.
    if not args.raw_file.is_file():
        print(f"No raw feature file at {args.raw_file}; skipping entrance-tagging cross-validation.")
        return 0
    data_dir = ["--data-dir", str(args.raw_file.parent)]
    raw_file = ["--raw-file", str(args.raw_file)]
    run([sys.executable, str(TAGGING / "split_groups.py"), *data_dir, *raw_file])
    run([sys.executable, str(TAGGING / "extraction.py"), *data_dir, *raw_file])
    run([sys.executable, str(TAGGING / "training_tagging.py"), *data_dir])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
