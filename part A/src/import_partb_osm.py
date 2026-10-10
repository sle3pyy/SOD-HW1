#!/usr/bin/env python3
"""Import OSM entrance labels and building footprints near the Part A GPS data."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from postgis_cli import (
    database_settings,
    ensure_postgis_running,
    psql_command,
    require_command,
    run_sql,
    run_sql_file,
)


# src/ lives inside part A/, whose parent is the project root containing the
# PBF, Docker Compose file, and generated-data directory.
ROOT = Path(__file__).resolve().parents[2]


def extent_buffer_meters() -> float:
    value = os.environ.get("EXTENT_BUFFER_M", "1000")
    try:
        buffer_m = float(value)
    except ValueError as exc:
        raise RuntimeError("EXTENT_BUFFER_M must be a non-negative number") from exc
    if buffer_m < 0:
        raise RuntimeError("EXTENT_BUFFER_M must be a non-negative number")
    return buffer_m


def query_gps_bounds(settings, buffer_m: float) -> list[str]:
    sql = """
    WITH extent AS (
      SELECT ST_Transform(
        ST_Buffer(ST_Envelope(ST_Collect(geom_3857)), %s), 4326
      ) AS geom
      FROM parta.porto_points
    )
    SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)
    FROM extent;
    """ % buffer_m
    command = [
        *psql_command(settings),
        "-At",
        "-F",
        ",",
        "-c",
        sql,
    ]
    result = subprocess.run(command, cwd=ROOT, check=True, text=True, capture_output=True)
    bounds = result.stdout.strip().split(",")
    if len(bounds) != 4 or any(not coordinate for coordinate in bounds):
        raise RuntimeError("Could not derive the GPS extent. Import Part A points first.")
    return bounds


def import_layer(settings, pbf: Path, layer: str, where: str, table: str, bounds: list[str], geometry_type: str) -> None:
    subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "PostgreSQL",
            settings.ogr_dsn,
            str(pbf),
            layer,
            "-spat",
            *bounds,
            "-where",
            where,
            "-nln",
            table,
            "-lco",
            "GEOMETRY_NAME=geom",
            "-nlt",
            geometry_type,
            "-overwrite",
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    pbf = ROOT / os.environ.get("PBF", "qgis-data/portugal-261008.osm.pbf")
    if not pbf.is_file():
        raise RuntimeError(f"OSM PBF not found: {pbf}")
    buffer_m = extent_buffer_meters()
    require_command("ogr2ogr", "Install it with: sudo apt install gdal-bin")
    ensure_postgis_running(ROOT)
    settings = database_settings()
    run_sql(ROOT, settings, "CREATE SCHEMA IF NOT EXISTS partb;")
    bounds = query_gps_bounds(settings, buffer_m)

    print(f"Importing OSM entrances and buildings within GPS extent + {buffer_m:g} m...")
    import_layer(settings, pbf, "points", "other_tags LIKE '%\"entrance\"%'", "partb.osm_entrances_raw", bounds, "POINT")
    import_layer(settings, pbf, "multipolygons", "building IS NOT NULL", "partb.osm_buildings_raw", bounds, "PROMOTE_TO_MULTI")
    run_sql_file(ROOT, settings, ROOT / "part A/src/prepare_partb_entrances.sql")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
