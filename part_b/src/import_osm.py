#!/usr/bin/env python3
"""Import OSM entrance labels and building footprints for the Part B area of interest.

The area of interest (AOI) is an OSM administrative boundary, the Porto
municipality by default. It does not depend on the Part A taxi data.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from sod_common.ogr import extent_buffer_meters, import_layer, query_bounds
from sod_common.paths import PBF, ROOT
from sod_common.postgis import (
    database_settings,
    ensure_postgis_running,
    require_command,
    run_sql,
    run_sql_file,
)

SQL = Path(__file__).resolve().parents[1] / "sql"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aoi-name", default="Porto", help="OSM name of the boundary (default: Porto).")
    parser.add_argument(
        "--aoi-admin-level", type=int, default=7, help="OSM admin_level of the boundary (7 = Portuguese municipality)."
    )
    return parser.parse_args()


def import_aoi(settings, name: str, admin_level: int, buffer_m: float) -> None:
    """Load the boundary into partb.aoi, with extent_3857 = boundary + buffer for the context crop."""
    quoted_name = name.replace("'", "''")
    where = f"boundary = 'administrative' AND admin_level = '{admin_level}' AND name = '{quoted_name}'"
    print(f"Importing the {name} boundary (admin_level={admin_level})...")
    import_layer(settings, PBF, "multipolygons", where, "partb.aoi_raw", None, "PROMOTE_TO_MULTI")
    run_sql(
        ROOT,
        settings,
        f"""
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM partb.aoi_raw) THEN
    RAISE EXCEPTION 'No administrative boundary named {quoted_name} with admin_level {admin_level} in the PBF.';
  END IF;
END
$$;
DROP TABLE IF EXISTS partb.aoi;
CREATE TABLE partb.aoi AS
SELECT
  min(name) AS name,
  ST_Multi(ST_Union(ST_Force2D(geom)))::geometry(MultiPolygon, 4326) AS geom,
  ST_Multi(ST_Transform(ST_Union(ST_Force2D(geom)), 3857))::geometry(MultiPolygon, 3857) AS geom_3857,
  ST_Multi(ST_Buffer(ST_Transform(ST_Union(ST_Force2D(geom)), 3857), {buffer_m:g}))::geometry(MultiPolygon, 3857)
    AS extent_3857
FROM partb.aoi_raw;
""",
    )


def main() -> int:
    args = parse_args()
    if not PBF.is_file():
        raise RuntimeError(f"OSM PBF not found: {PBF}")
    buffer_m = extent_buffer_meters()
    require_command("ogr2ogr", "Install it with: sudo apt install gdal-bin")
    ensure_postgis_running(ROOT)
    settings = database_settings()
    run_sql(ROOT, settings, "CREATE SCHEMA IF NOT EXISTS partb;")
    import_aoi(settings, args.aoi_name, args.aoi_admin_level, buffer_m)
    bounds = query_bounds(
        settings,
        """
        SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e)
        FROM (SELECT ST_Transform(extent_3857, 4326) AS e FROM partb.aoi) AS aoi;
        """,
        "The partb.aoi table is empty.",
    )

    print(f"Importing OSM entrances and buildings within the AOI + {buffer_m:g} m...")
    import_layer(settings, PBF, "points", "other_tags LIKE '%\"entrance\"%'", "partb.osm_entrances_raw", bounds, "POINT")
    import_layer(settings, PBF, "multipolygons", "building IS NOT NULL", "partb.osm_buildings_raw", bounds, "PROMOTE_TO_MULTI")
    run_sql_file(ROOT, settings, SQL / "prepare_entrances.sql")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
