#!/usr/bin/env python3
"""Run the complete Part A and Part B data-preparation pipeline."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PART_A = Path(__file__).resolve().parent
ROOT = PART_A.parent
SRC = PART_A / "src"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare taxi, road, building, and OSM-entrance data in PostGIS."
    )
    parser.add_argument("--max-trips", type=int, default=0, help="Optional Porto trip limit.")
    parser.add_argument("--max-points", type=int, default=0, help="Optional Porto point limit.")
    parser.add_argument("--every-n", type=int, default=1, help="Keep one point every N samples.")
    parser.add_argument("--extent-buffer-m", type=float, default=1000, help="GPS crop buffer in metres.")
    parser.add_argument("--match-radius-m", type=float, default=100, help="Maximum road-snap distance in metres.")
    parser.add_argument("--skip-road-import", action="store_true", help="Reuse the existing OSM road table.")
    parser.add_argument("--skip-partb-osm", action="store_true", help="Skip OSM building and entrance import.")
    return parser.parse_args()


def run(command: list[str], *, input_text: str | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, text=True, input=input_text, env=env)


def psql_command() -> list[str]:
    settings = {
        "PGHOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PGPORT": os.environ.get("POSTGIS_PORT", "5432"),
        "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "gis"),
    }
    command = ["docker", "compose", "exec", "-T"]
    for key, value in settings.items():
        command.extend(["-e", f"{key}={value}"])
    return [
        *command,
        "postgis",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        os.environ.get("POSTGRES_USER", "gis"),
        "-d",
        os.environ.get("POSTGRES_DB", "gis"),
    ]


def run_sql(filename: Path, variables: dict[str, float] | None = None) -> None:
    command = psql_command()
    for key, value in (variables or {}).items():
        command.extend(["-v", f"{key}={value:g}"])
    command.extend(["-f", "/dev/stdin"])
    run(command, input_text=filename.read_text(encoding="utf-8"))


def main() -> int:
    args = parse_args()
    if args.extent_buffer_m < 0 or args.match_radius_m < 0:
        raise ValueError("distance options must be non-negative")

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
    run_sql(SRC / "import_clean_dataset.sql")

    if not args.skip_road_import:
        environment = os.environ.copy()
        environment["EXTENT_BUFFER_M"] = f"{args.extent_buffer_m:g}"
        run([sys.executable, str(SRC / "import_roads.py")], env=environment)
    run_sql(
        SRC / "map_matching.sql",
        {"extent_buffer_m": args.extent_buffer_m, "match_radius_m": args.match_radius_m},
    )

    if not args.skip_partb_osm:
        environment = os.environ.copy()
        environment["EXTENT_BUFFER_M"] = f"{args.extent_buffer_m:g}"
        run([sys.executable, str(SRC / "import_partb_osm.py")], env=environment)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
