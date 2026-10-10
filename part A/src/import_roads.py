#!/usr/bin/env python3
"""Import GPS-local OSM roads from the Portugal PBF into PostGIS."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from postgis_cli import database_settings, ensure_postgis_running, psql_command, require_command, run_sql


# src/ lives inside part A/, whose parent is the project root containing the
# PBF, Docker Compose file, and generated-data directory.
ROOT = Path(__file__).resolve().parents[2]
IDENTIFIER = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*$")


def main() -> int:
    pbf = ROOT / os.environ.get("PBF", "qgis-data/portugal-261008.osm.pbf")
    extent_buffer_m = os.environ.get("EXTENT_BUFFER_M", "1000")
    target_table = os.environ.get("TARGET_TABLE", "public.gis_osm_roads_free")
    if not pbf.is_file():
        raise RuntimeError(f"OSM PBF not found: {pbf}")
    try:
        buffer_m = float(extent_buffer_m)
    except ValueError as exc:
        raise RuntimeError("EXTENT_BUFFER_M must be a non-negative number") from exc
    if buffer_m < 0:
        raise RuntimeError("EXTENT_BUFFER_M must be a non-negative number")
    if not IDENTIFIER.fullmatch(target_table):
        raise RuntimeError("TARGET_TABLE must be a PostgreSQL identifier, optionally schema-qualified")

    require_command("ogr2ogr", "Install it with: sudo apt install gdal-bin")
    ensure_postgis_running(ROOT)
    settings = database_settings()

    extent_sql = """
    WITH extent AS (
      SELECT ST_Transform(
        ST_Buffer(ST_Envelope(ST_Collect(geom_3857)), %s), 4326
      ) AS geom
      FROM parta.porto_points
    )
    SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)
    FROM extent;
    """ % buffer_m
    bounds_result = subprocess.run(
        [*psql_command(settings), "-At", "-F", ",", "-c", extent_sql],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    bounds = bounds_result.stdout.strip().split(",")
    if len(bounds) != 4 or any(not value for value in bounds):
        raise RuntimeError("Could not derive the GPS extent. Import Part A points first.")

    print(f"Importing PBF roads within GPS extent + {buffer_m:g} m into {target_table}...")
    subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "PostgreSQL",
            settings.ogr_dsn,
            str(pbf),
            "lines",
            "-spat",
            *bounds,
            "-where",
            "highway IS NOT NULL",
            "-nln",
            target_table,
            "-lco",
            "GEOMETRY_NAME=geom",
            "-nlt",
            "PROMOTE_TO_MULTI",
            "-overwrite",
        ],
        cwd=ROOT,
        check=True,
    )
    run_sql(
        ROOT,
        settings,
        f"ANALYZE {target_table}; SELECT count(*) AS imported_road_count FROM {target_table};",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
