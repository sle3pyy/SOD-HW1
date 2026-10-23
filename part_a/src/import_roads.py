#!/usr/bin/env python3
"""Import GPS-local OSM roads from the Portugal PBF into PostGIS."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from time import perf_counter

from ogr import extent_buffer_meters, import_layer, query_bounds
from sod_common.paths import PBF, ROOT
from sod_common.postgis import (
    database_settings,
    ensure_postgis_running,
    require_command,
    run_sql,
)

IDENTIFIER = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*$")


def main() -> int:
    target_table = os.environ.get("TARGET_TABLE", "public.gis_osm_roads_free")
    if not PBF.is_file():
        raise RuntimeError(f"OSM PBF not found: {PBF}")
    buffer_m = extent_buffer_meters()
    if not IDENTIFIER.fullmatch(target_table):
        raise RuntimeError("TARGET_TABLE must be a PostgreSQL identifier, optionally schema-qualified")

    require_command("ogr2ogr", "Install it with: sudo apt install gdal-bin")
    ensure_postgis_running(ROOT)
    settings = database_settings()

    extent_sql = f"""
    WITH extent AS (
      SELECT ST_Transform(
        ST_Buffer(ST_Envelope(ST_Collect(geom_3857)), {buffer_m}), 4326
      ) AS geom
      FROM parta.porto_points
    )
    SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)
    FROM extent;
    """
    bounds = query_bounds(settings, extent_sql, "Import Part A points first.")

    import_layer(settings, PBF, "lines", "highway IS NOT NULL", target_table, bounds, "PROMOTE_TO_MULTI")
    run_sql(
        ROOT,
        settings,
        f"ANALYZE {target_table}; SELECT count(*) AS imported_road_count FROM {target_table};",
    )
    return 0


if __name__ == "__main__":
    started = perf_counter()
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        print(f"[TIMER] import_roads.py: {perf_counter() - started:.1f}s", flush=True)
