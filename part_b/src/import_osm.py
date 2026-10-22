#!/usr/bin/env python3
"""Import OSM entrance labels and building footprints for the Part B area of interest.

The area of interest (AOI) is an OSM administrative boundary, the Porto
municipality by default. The PBF is read with pyrosm and the layers are
written to PostGIS with geopandas.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyrosm import OSM
from sod_common.paths import PBF, ROOT
from sod_common.postgis import database_settings, run_sql_file
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import JSONB

SQL = Path(__file__).resolve().parents[1] / "sql"
# Metric CRS for buffering in Portugal (PT-TM06/ETRS89).
METRIC_CRS = 3763
# Spatial contexts of the paper (Hu et al., 2021, Table 1).
MAIN_ROADS = ["primary", "secondary", "tertiary", "unclassified", "residential"]
MAIN_ROADS += [f"{road}_link" for road in MAIN_ROADS[:3]]


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


def tag(gdf: gpd.GeoDataFrame, key: str) -> pd.Series:
    """Tag value per row: pyrosm puts common keys in columns and the rest in JSON tags."""
    from_tags = gdf["tags"].map(
        lambda tags: json.loads(tags).get(key) if isinstance(tags, str) else None
    )
    return gdf[key].fillna(from_tags) if key in gdf else from_tags


def with_kind(
    gdf: gpd.GeoDataFrame, conditions: dict[str, pd.Series], columns: list[str]
) -> gpd.GeoDataFrame:
    """Label each row with the first matching kind and drop rows that match none."""
    kind = pd.Series(None, index=gdf.index, dtype=object)
    for name, condition in reversed(conditions.items()):
        kind = kind.mask(condition.fillna(False).astype(bool), name)
    return gdf.assign(kind=kind)[kind.notna()][["id", "kind", *columns, "geometry"]]


def read_context(osm: OSM) -> dict[str, gpd.GeoDataFrame]:
    """Read the context layers around the buildings, each with a kind column."""
    roads = osm.get_data_by_custom_criteria(
        custom_filter={"highway": [*MAIN_ROADS, "pedestrian", "service"]},
        keep_nodes=False,
        keep_relations=False,
    )
    rail = osm.get_data_by_custom_criteria(
        custom_filter={"railway": ["rail"]}, keep_nodes=False
    )
    points = osm.get_data_by_custom_criteria(
        custom_filter={
            "amenity": ["bicycle_parking", "post_box", "fountain"],
            "tourism": ["artwork"],
            "historic": ["memorial"],
            "man_made": ["water_well", "flagpole"],
        }
    )
    obstacles = osm.get_data_by_custom_criteria(
        custom_filter={
            "barrier": True,
            "landuse": ["grass"],
            "leisure": ["garden"],
            "natural": ["water"],
        }
    )
    # Several keys in one filter are combined with OR and other keys' values leak
    # through (e.g. tourism=attraction), so each kind is assigned explicitly here.
    highway, amenity = tag(roads, "highway"), tag(points, "amenity")
    return {
        "ctx_roads": with_kind(
            roads,
            {
                "main": highway.isin(MAIN_ROADS),
                "pedestrian": highway == "pedestrian",
                "service": highway == "service",
            },
            ["name"],
        ),
        "ctx_rail": with_kind(rail, {"rail": tag(rail, "railway") == "rail"}, []),
        "ctx_points": with_kind(
            points,
            {
                "bicycle_parking": amenity == "bicycle_parking",
                "post_box": amenity == "post_box",
                "landmark": (tag(points, "tourism") == "artwork")
                | (tag(points, "historic") == "memorial")
                | (amenity == "fountain")
                | tag(points, "man_made").isin(["water_well", "flagpole"]),
            },
            [],
        ),
        "ctx_obstacles": with_kind(
            obstacles,
            {
                # Barrier nodes (bollards, gates) do not block movement or sight.
                "barrier": tag(obstacles, "barrier").notna()
                & (obstacles.geom_type != "Point"),
                "green": (tag(obstacles, "landuse") == "grass")
                | (tag(obstacles, "leisure") == "garden"),
                "water": tag(obstacles, "natural") == "water",
            },
            [],
        ),
    }


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
    for key in ["amenity", "shop", "office", "tourism", "addr:street"]:
        buildings[key] = tag(buildings, key)
    print("Reading the spatial context (roads, railway, landmarks, obstacles)...")
    context = read_context(osm)

    engine = create_engine(database_settings().sqlalchemy_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS partb"))
    layers = {
        "aoi": aoi[["name", "admin_level", "geometry"]],
        "osm_entrances_raw": entrances[["id", "entrance", "tags", "geometry"]],
        "osm_buildings_raw": buildings[
            ["id", "osm_type", "name", "building", "amenity", "shop", "office"]
            + ["tourism", "addr:street", "tags", "geometry"]
        ].rename(columns={"addr:street": "addr_street"}),
        **context,
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
    for table, gdf in context.items():
        print(f"  {table}: {gdf['kind'].value_counts().to_dict()}")
    run_sql_file(ROOT, database_settings(), SQL / "prepare_entrances.sql")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
