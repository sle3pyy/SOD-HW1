#!/usr/bin/env python3
"""Path distance error of the cross-validation (Hu et al., 2021, section 4).

training_tagging.py reports the linear error: the distance along the outline
between the predicted and the true entrance sample. This adds the path error:
the walking distance outside the building between the two, around the building
itself and the other path obstacles, on the same grid as the path features.
"""

import argparse
import csv
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from features import (
    CONTEXT_RADIUS_M,
    METRIC_CRS,
    PATH_START_OFFSET_M,
    Context,
    footprint,
    occupancy_grid,
    path_features,
    path_obstacles,
    sample_footprint,
)
from sod_common.paths import DATA_DIR
from sod_common.postgis import database_settings
from sqlalchemy import create_engine


def test_blocks(data_dir: Path, groups: int) -> list[int]:
    """Block index of every tagging_result row: groups 1..n, each in test_list order."""
    blocks = []
    for group in range(1, groups + 1):
        with (data_dir / str(group) / "test_list.csv").open(newline="") as f:
            blocks += [int(float(value)) for value in next(csv.reader(f))]
    return blocks


def summary(errors: pd.Series) -> str:
    return (
        f"mean {errors.mean():5.1f} m  median {errors.median():5.1f} m  "
        f"within 10 m {100 * (errors <= 10).mean():3.0f}%"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR / "entrance_tagging",
        help="Directory holding the cross-validation results.",
    )
    parser.add_argument("--groups", type=int, default=5, help="Number of CV groups.")
    parser.add_argument("--models", nargs="+", default=["wrf", "brf"])
    args = parser.parse_args()

    blocks = test_blocks(args.data_dir, args.groups)
    building_of = (
        pd.read_csv(args.data_dir / "samples-porto.csv")
        .groupby("block_index")["building_id"]
        .first()
    )
    engine = create_engine(database_settings().sqlalchemy_url)
    context = Context(engine)
    footprints = gpd.read_postgis(
        f"SELECT building_id, ST_Transform(geom, {METRIC_CRS}) AS geom "
        "FROM partb.label_buildings",
        engine,
        geom_col="geom",
    ).set_index("building_id")["geom"]

    # One grid per test building, shared by every model.
    walks = {}
    for block in blocks:
        geom = footprints[building_of[block]]
        samples = sample_footprint(footprint(geom))
        area = shapely.buffer(geom, CONTEXT_RADIUS_M)
        near = {kind: layer.near(area) for kind, layer in context.layers.items()}
        grid = occupancy_grid(
            area.bounds, path_obstacles(near, context.buildings.near(area))
        )
        starts = samples.points + samples.normal * PATH_START_OFFSET_M
        walks[block] = (grid, samples.points, starts)

    for model in args.models:
        results = pd.read_csv(args.data_dir / f"tagging_result_{model}.csv")
        if len(results) != len(blocks):
            raise SystemExit(f"error: {model} results do not match the CV groups")
        rows = []
        for block, (_, row) in zip(blocks, results.iterrows(), strict=True):
            predicted, truth = int(row["0"]), int(row["1"])
            path = 0.0
            if predicted != truth:
                grid, points, starts = walks[block]
                walk, access, _ = path_features(
                    grid,
                    points[[predicted]],
                    starts[[predicted]],
                    starts[[truth]],
                )
                # path_features adds the step out of the wall at the predicted end only.
                path = walk[0] + PATH_START_OFFSET_M if access[0] else np.nan
            rows.append(
                {
                    "building_id": building_of[block],
                    "linear_m": row["5"],
                    "path_m": path,
                }
            )
        errors = pd.DataFrame(rows)
        errors.to_csv(args.data_dir / f"path_error_{model}.csv", index=False)
        unreachable = int(errors["path_m"].isna().sum())
        print(f"{model} linear error: {summary(errors['linear_m'])}")
        print(
            f"{model} path error:   {summary(errors['path_m'].dropna())}"
            f"  ({unreachable} of {len(errors)} without a walking path)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
