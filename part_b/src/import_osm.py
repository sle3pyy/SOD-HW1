#!/usr/bin/env python3
"""Import OSM entrance labels and building footprints for the Part B area of interest.

The area of interest (AOI) is an OSM administrative boundary, the Porto
municipality by default. The PBF is read with pyrosm and the layers are
written to PostGIS with geopandas.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyrosm import OSM
from sod_common.paths import PBF, ROOT
from sod_common.postgis import database_settings, run_sql_file
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB

SQL = Path(__file__).resolve().parents[1] / "sql"
# Metric CRS for buffering in Portugal (PT-TM06/ETRS89).
METRIC_CRS = 3763


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--aoi-name", default="Porto", help="Exact OSM boundary name.")
    parser.add_argument(
        "--aoi-admin-level",
        default="7",
        help="OSM admin_level of the boundary (7 = Portuguese municipality).",
    )
    parser.add_argument(
        "--extent-buffer-m",
        type=float,
        default=1000,
        help="Buffer around the AOI, in metres, for the imported features.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not PBF.is_file():
        sys.exit(f"error: OSM PBF not found: {PBF}")

    print(
        f"Reading the {args.aoi_name} boundary (admin_level={args.aoi_admin_level})..."
    )
    boundaries = OSM(str(PBF)).get_boundaries(name=args.aoi_name)
    # pyrosm's name filter matches substrings ("Porto de Mós"), so match exactly here.
    aoi = boundaries[
        (boundaries["name"] == args.aoi_name)
        & (boundaries["admin_level"] == args.aoi_admin_level)
    ]
    if aoi.empty:
        sys.exit(
            f"error: no boundary named {args.aoi_name!r} with admin_level "
            f"{args.aoi_admin_level} in {PBF}"
        )
    extent = aoi.to_crs(METRIC_CRS).buffer(args.extent_buffer_m).to_crs(4326).iloc[0]

    print(
        f"Reading entrances and buildings within the AOI + {args.extent_buffer_m:g} m..."
    )
    osm = OSM(str(PBF), bounding_box=extent)
    entrances = osm.get_data_by_custom_criteria(
        custom_filter={"entrance": True}, keep_ways=False, keep_relations=False
    )
    buildings = osm.get_buildings()

    engine = create_engine(database_settings().sqlalchemy_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS partb"))
    layers = {
        "aoi": aoi[["name", "admin_level", "geometry"]],
        "osm_entrances_raw": entrances[["id", "entrance", "tags", "geometry"]],
        "osm_buildings_raw": buildings[
            ["id", "osm_type", "name", "building", "tags", "geometry"]
        ],
    }
    for table, gdf in layers.items():
        gdf.rename(columns={"id": "osm_id"}).rename_geometry("geom").to_postgis(
            table,
            engine,
            schema="partb",
            if_exists="replace",
            index=False,
            dtype={"tags": JSONB},
        )
    print(f"Wrote {len(entrances)} entrances and {len(buildings)} buildings.")
    run_sql_file(ROOT, database_settings(), SQL / "prepare_entrances.sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
