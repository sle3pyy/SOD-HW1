#!/usr/bin/env python3
"""Run the complete Part A pipeline: Porto taxi points map-matched to OSM roads."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from sod_common.paths import ROOT
from sod_common.postgis import database_settings, run_sql_file

PART_A = Path(__file__).resolve().parent
SRC = PART_A / "src"
SQL = PART_A / "sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare taxi points and roads in PostGIS and map-match them."
    )
    parser.add_argument("--max-trips", type=int, default=0, help="Optional Porto trip limit.")
    parser.add_argument("--max-points", type=int, default=0, help="Optional Porto point limit.")
    parser.add_argument("--every-n", type=int, default=1, help="Keep one point every N samples.")
    parser.add_argument("--extent-buffer-m", type=float, default=1000, help="GPS crop buffer in metres.")
    parser.add_argument("--match-radius-m", type=float, default=100, help="Maximum road-snap distance in metres.")
    parser.add_argument("--skip-road-import", action="store_true", help="Reuse the existing OSM road table.")
    return parser.parse_args()


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, text=True, env=env)


def main() -> int:
    args = parse_args()
    if args.extent_buffer_m < 0 or args.match_radius_m < 0:
        raise ValueError("distance options must be non-negative")
    settings = database_settings()

    run(["docker", "compose", "up", "-d", "postgis"])
    run(
        [
            sys.executable,
            str(SRC / "dataset_cleaning.py"),
            "--max-trips", str(args.max_trips),
            "--max-points", str(args.max_points),
            "--every-n", str(args.every_n),
        ]
    )
    run_sql_file(ROOT, settings, SQL / "import_clean_dataset.sql")

    if not args.skip_road_import:
        environment = os.environ.copy()
        environment["EXTENT_BUFFER_M"] = f"{args.extent_buffer_m:g}"
        run([sys.executable, str(SRC / "import_roads.py")], env=environment)
    run_sql_file(
        ROOT,
        settings,
        SQL / "map_matching.sql",
        {"extent_buffer_m": args.extent_buffer_m, "match_radius_m": args.match_radius_m},
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
