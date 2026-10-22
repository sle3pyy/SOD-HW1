#!/usr/bin/env python3
"""Tag the main entrance of the target buildings and write it to PostGIS.

The model is trained on all labelled buildings, with the same strong-negative
sampling and imputation as the cross-validation (extraction.py). For each target
building the sample with the highest positive probability is the estimated main
entrance (Hu et al., 2021, section 3).
"""

import argparse
from pathlib import Path

import extraction
import geopandas as gpd
import numpy as np
import pandas as pd
from extraction import DataExtraction
from sod_common.paths import DATA_DIR as PROJECT_DATA_DIR
from sod_common.postgis import database_settings
from split_groups import count_buildings
from sqlalchemy import create_engine
from training_tagging import build_models

DATA_DIR = PROJECT_DATA_DIR / "entrance_tagging"
GROUP = "tagging"  # sub-directory of the data directory for this run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_DIR,
        help="Directory holding the feature files.",
    )
    parser.add_argument(
        "--model",
        choices=["brf", "wrf"],
        default="brf",
        help="Model (BRF had the lower CV error).",
    )
    parser.add_argument(
        "--pt", type=float, default=24, help="Physical distance threshold PT (m)."
    )
    parser.add_argument(
        "--ft", type=float, default=0.04, help="Feature distance threshold FT."
    )
    args = parser.parse_args()
    labels_file = args.data_dir / "osmfeatures-porto.csv"
    targets_file = args.data_dir / "osmfeatures-targets.csv"
    samples_file = args.data_dir / "samples-targets.csv"
    work = args.data_dir / GROUP
    work.mkdir(parents=True, exist_ok=True)
    missing_file = work / "missing.csv"

    # extraction.py writes under its module-level DATA_DIR / <group>.
    extraction.DATA_DIR = args.data_dir
    data = DataExtraction()
    print("Preparing the training set from all labelled buildings...")
    data.load_training_data([], labels_file, missing_file, GROUP, args.ft, args.pt)
    n_targets = count_buildings(targets_file)
    print(f"Preparing {n_targets} target buildings...")
    data.load_testing_data(list(range(n_targets)), targets_file, missing_file, GROUP)

    training = pd.read_csv(work / "features.csv")
    head = [column for column in training.columns if column != "class"]
    model = build_models()[args.model]
    model.fit(training[head].to_numpy(), training["class"].to_numpy())

    predictions = []
    for block in range(n_targets):
        test = pd.read_csv(work / f"{block}rftest.csv")
        probability = model.predict_proba(test[head].to_numpy())[:, 1]
        best = int(np.argmax(probability))
        runner_up = np.partition(probability, -2)[-2] if len(probability) > 1 else 0.0
        predictions.append(
            {
                "block_index": block,
                "sample_index": best,
                "n_samples": len(probability),
                "probability": float(probability[best]),
                # Small margins mean the model hesitates between two places.
                "margin": float(probability[best] - runner_up),
            }
        )
    predictions = pd.DataFrame(predictions).merge(
        pd.read_csv(samples_file), on=["block_index", "sample_index"]
    )

    engine = create_engine(database_settings().sqlalchemy_url)
    buildings = pd.read_sql(
        "SELECT building_id, name, building_type, amenity FROM partb.target_buildings",
        engine,
    )
    predicted = gpd.GeoDataFrame(
        predictions.merge(buildings, on="building_id", how="left").assign(
            model=args.model
        ),
        geometry=gpd.points_from_xy(predictions["lon"], predictions["lat"]),
        crs=4326,
    )[
        ["building_id", "name", "building_type", "amenity", "model", "probability"]
        + ["margin", "sample_index", "n_samples", "geometry"]
    ].rename_geometry("geom")
    predicted.to_postgis(
        "predicted_entrances", engine, schema="partb", if_exists="replace", index=False
    )
    geojson = args.data_dir / "predicted_entrances.geojson"
    predicted.to_file(geojson, driver="GeoJSON")
    print(
        f"Tagged {len(predicted)} buildings with {args.model}: partb.predicted_entrances, {geojson}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
