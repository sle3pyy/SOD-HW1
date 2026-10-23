#!/usr/bin/env python3
"""Statistics and figures for the Part B report (exploratory analysis and validation).

Reads the partb tables and the files of the pipeline, never writes to the
database, and writes every figure (PNG and the CSV of its data) and table to
data/entrance_tagging/figures/, with summary.md collecting the tables.
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from features import METRIC_CRS, PATH_START_OFFSET_M, path_features
from matplotlib.lines import Line2D
from path_error import block_buildings, building_walks, test_blocks
from scipy import stats
from scipy.spatial import cKDTree
from sod_common.paths import DATA_DIR
from sod_common.postgis import database_settings
from sqlalchemy import create_engine

# Reference palette of the dataviz guidelines (light mode, for print).
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e6e5e1"
ROADS = "#d2d1cc"  # map context, a step darker than the grid
REFERENCE = "#8e8d88"  # baselines: neutral grey, never a series hue
LABELS = "#2a78d6"  # series 1: labelled buildings, positives, BRF
TARGETS = "#eb6834"  # series 2: target buildings, negatives, WRF
SERIES_3 = "#1baf7a"

CONTEXT_FEATURES = {
    "main road": "main_p_dis",
    "pedestrian way": "ped_dis",
    "service way": "service_way_dis",
    "railway": "rail_dis",
    "bicycle parking": "park_dis",
    "landmark": "landmark_dis",
    "post box": "post_dis",
    "address street": "addr_dis",
    "symmetry axis": "axis_dis",
}
CUE_FEATURES = {
    "cen_sort": "distance to centroid (rank)",
    "main_p_sort": "distance to main road (rank)",
    "main_path_sort": "walk to main road (rank)",
    "vis_mr_sort": "main-road visibility (rank)",
    "open_area_sort": "open area in front (rank)",
    "proportion": "position on edge (0 = middle)",
}


# ---------------------------------------------------------------- helpers


def style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "text.color": TEXT,
            "axes.labelcolor": TEXT_SECONDARY,
            "axes.edgecolor": GRID,
            "xtick.color": TEXT_SECONDARY,
            "ytick.color": TEXT_SECONDARY,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.axisbelow": True,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "lines.linewidth": 2,
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.titleweight": "bold",
            "legend.frameon": False,
        }
    )


class Writer:
    """Writes figures with their data and collects the tables for summary.md."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        self.sections: list[str] = []

    def figure(self, fig, name: str, data: pd.DataFrame) -> None:
        fig.savefig(self.out_dir / f"{name}.png", dpi=200, bbox_inches="tight")
        plt.close(fig)
        data.to_csv(self.out_dir / f"{name}.csv", index=False)

    def table(self, name: str, title: str, data: pd.DataFrame, note: str = "") -> None:
        data.to_csv(self.out_dir / f"{name}.csv", index=False)
        self.sections.append(
            f"## {title}\n\n{markdown(data)}\n" + (f"\n{note}\n" if note else "")
        )

    def summary(self, header: str) -> None:
        text = f"# Part B report statistics\n\n{header}\n\n" + "\n".join(self.sections)
        (self.out_dir / "summary.md").write_text(text)


def markdown(data: pd.DataFrame) -> str:
    def cell(value) -> str:
        # Values are rounded where they are computed; %g drops float noise like 21.0.
        return f"{value:g}" if isinstance(value, float) else str(value)

    lines = ["| " + " | ".join(data.columns) + " |", "|" + "---|" * len(data.columns)]
    lines += [
        "| " + " | ".join(cell(v) for v in row) + " |"
        for row in data.itertuples(index=False)
    ]
    return "\n".join(lines)


def read_blocks(path: Path) -> list[dict]:
    """Raw feature file -> one dict per building (same layout as extraction.py reads)."""
    rows = path.read_text().splitlines()
    blocks, i = [], 0
    while i < len(rows):
        header = rows[i].split(",")
        n = int(header[4])
        samples = [row.split(",") for row in rows[i + 1 : i + 1 + n]]
        features = pd.DataFrame(
            [dict(zip(r[4::2], map(float, r[5::2]), strict=True)) for r in samples]
        )
        blocks.append(
            {
                "building_id": int(header[0]),
                "entrance_index": int(header[1]),
                "dist": np.array([float(r[1]) for r in samples]),
                "features": features,
            }
        )
        i += n + 1
    return blocks


def ring_distance(dist: np.ndarray, a: int, b: int) -> float:
    """Distance along the outline between samples a and b (get_distance in training_tagging.py)."""
    a, b = sorted((a, b))
    return min(dist[a:b].sum(), dist.sum() - dist[a:b].sum())


def ecdf(ax, values, **kwargs) -> None:
    values = np.sort(np.asarray(values, dtype=float))
    ax.step(values, np.arange(1, len(values) + 1) / len(values), where="post", **kwargs)


def percent(mask) -> float:
    return 100 * float(np.mean(mask))


# ---------------------------------------------------------------- task 2


def inventory(engine, writer: Writer, labels_used: int) -> None:
    sql = """
    SELECT 1 AS n, 'AOI area (km²)' AS item, round((ST_Area(geom::geography) / 1e6)::numeric, 1)::text AS value FROM partb.aoi
    UNION ALL SELECT 2, 'buildings in AOI + 1 km', count(*)::text FROM partb.osm_buildings
    UNION ALL SELECT 3, 'public facilities in AOI + 1 km', count(*) FILTER (WHERE is_public)::text FROM partb.osm_buildings
    UNION ALL SELECT 4, 'entrances in AOI + 1 km', count(*)::text FROM partb.osm_entrances
    UNION ALL SELECT 5, 'entrances: ' || reference_class, count(*)::text FROM partb.entrance_reference GROUP BY reference_class
    UNION ALL SELECT 6, 'label buildings (public, exactly one main entrance)', count(*)::text FROM partb.label_buildings
    UNION ALL SELECT 7, 'target buildings (public, no main entrance)', count(*)::text FROM partb.target_buildings
    UNION ALL SELECT 8, 'context: road ' || kind, count(*)::text FROM partb.ctx_roads GROUP BY kind
    UNION ALL SELECT 9, 'context: railway', count(*)::text FROM partb.ctx_rail
    UNION ALL SELECT 10, 'context: ' || kind, count(*)::text FROM partb.ctx_points GROUP BY kind
    UNION ALL SELECT 11, 'context: obstacle ' || kind, count(*)::text FROM partb.ctx_obstacles GROUP BY kind
    ORDER BY n, item
    """
    data = pd.read_sql(sql, engine).drop(columns="n")
    data.loc[len(data)] = ["label buildings used for training", str(labels_used)]
    writer.table("data_inventory", "Data inventory", data)


def anomalies(engine, writer: Writer, used_ids: set[int]) -> None:
    rows = pd.read_sql(
        """
        WITH main AS (
          SELECT building_id, count(*) AS n FROM partb.entrance_reference_buildings
          WHERE reference_class = 'main' AND building_id IS NOT NULL GROUP BY building_id
        )
        SELECT
          (SELECT count(*) FROM main WHERE n > 1) AS several_main,
          (SELECT count(*) FROM main m JOIN partb.osm_buildings b USING (building_id)
             WHERE m.n > 1 AND b.is_public) AS several_main_public,
          (SELECT count(*) FROM partb.entrance_reference_buildings WHERE building_id IS NULL) AS unlinked,
          (SELECT count(*) FROM partb.entrance_reference_buildings
             WHERE building_id IS NULL AND reference_class = 'main') AS unlinked_main,
          (SELECT count(*) FROM partb.label_buildings WHERE ST_NumGeometries(geom) > 1) AS multipart_labels,
          (SELECT count(*) FROM partb.target_buildings WHERE ST_NumGeometries(geom) > 1) AS multipart_targets
        """,
        engine,
    ).iloc[0]
    offsets = pd.read_sql(
        """
        SELECT l.building_id, ST_Distance(ST_Transform(l.entrance_geom, 3763),
               ST_Boundary(ST_Transform(l.geom, 3763))) AS to_outline_m
        FROM partb.label_buildings l
        """,
        engine,
    )
    skipped = offsets[~offsets["building_id"].isin(used_ids)]
    data = pd.DataFrame(
        [
            [
                "buildings with more than one main entrance (excluded)",
                int(rows.several_main),
            ],
            ["  of which public facilities", int(rows.several_main_public)],
            ["entrances not within 5 m of any building", int(rows.unlinked)],
            ["  of which main entrances", int(rows.unlinked_main)],
            ["multi-part footprints among labels", int(rows.multipart_labels)],
            ["multi-part footprints among targets", int(rows.multipart_targets)],
            ["labels skipped (entrance > 5 m from the sampled outline)", len(skipped)],
            [
                "main entrance to footprint outline, median (m)",
                round(offsets["to_outline_m"].median(), 2),
            ],
            [
                "main entrance to footprint outline, 90th percentile (m)",
                round(offsets["to_outline_m"].quantile(0.9), 2),
            ],
            [
                "main entrance to footprint outline, max (m)",
                round(offsets["to_outline_m"].max(), 2),
            ],
        ],
        columns=["anomaly", "value"],
        dtype=object,
    )
    writer.table("anomalies", "Anomalies and data quality", data)


def buildings(engine) -> gpd.GeoDataFrame:
    sql = f"""
    SELECT building_id, 'labels' AS role, building_type, amenity, ST_Transform(geom, {METRIC_CRS}) AS geom
    FROM partb.label_buildings
    UNION ALL
    SELECT building_id, 'targets', building_type, amenity, ST_Transform(geom, {METRIC_CRS})
    FROM partb.target_buildings
    """
    data = gpd.read_postgis(sql, engine, geom_col="geom")
    data["area_m2"] = data.area
    data["perimeter_m"] = data.length
    return data


def spatial_stats(engine, writer: Writer, data: gpd.GeoDataFrame) -> None:
    area = float(
        pd.read_sql(
            f"SELECT ST_Area(ST_Transform(geom, {METRIC_CRS})) AS a FROM partb.aoi",
            engine,
        )["a"].iloc[0]
    )
    rows = []
    for role, group in data.groupby("role"):
        xy = np.column_stack([group.centroid.x, group.centroid.y])
        n = len(xy)
        nearest = cKDTree(xy).query(xy, k=2)[0][:, 1]
        expected = 0.5 * np.sqrt(area / n)
        z = (nearest.mean() - expected) / (0.26136 / np.sqrt(n**2 / area))
        centre = xy.mean(axis=0)
        rows.append(
            {
                "set": role,
                "n": n,
                "mean NN distance (m)": round(nearest.mean(), 1),
                "expected if random (m)": round(expected, 1),
                "Clark-Evans R": round(nearest.mean() / expected, 3),
                "z": round(z, 2),
                "standard distance (m)": round(
                    float(np.sqrt(((xy - centre) ** 2).sum(axis=1).mean())), 1
                ),
            }
        )
    writer.table(
        "spatial_stats",
        "Spatial distribution (Clark-Evans nearest-neighbour ratio)",
        pd.DataFrame(rows),
        "R < 1: clustered, R = 1: random, R > 1: dispersed; |z| > 1.96 is significant at 5%.",
    )


def fig_map(engine, writer: Writer, data: gpd.GeoDataFrame) -> None:
    aoi = gpd.read_postgis(
        f"SELECT ST_Transform(geom, {METRIC_CRS}) AS geom FROM partb.aoi",
        engine,
        geom_col="geom",
    )
    roads = gpd.read_postgis(
        f"SELECT ST_Transform(geom, {METRIC_CRS}) AS geom FROM partb.ctx_roads WHERE kind = 'main'",
        engine,
        geom_col="geom",
    )
    fig, ax = plt.subplots(figsize=(8, 5.2))
    roads.plot(ax=ax, color=ROADS, linewidth=0.5)
    aoi.boundary.plot(ax=ax, color=TEXT_SECONDARY, linewidth=1)
    for role, color, size in [("targets", TARGETS, 5), ("labels", LABELS, 22)]:
        points = data[data["role"] == role].centroid
        ax.scatter(
            points.x,
            points.y,
            s=size,
            color=color,
            edgecolor=SURFACE,
            linewidth=0.8,
            zorder=3,
        )
    minx, miny, maxx, maxy = aoi.total_bounds
    ax.set_xlim(minx - 300, maxx + 300)
    ax.set_ylim(miny - 300, maxy + 300)
    ax.set_aspect("equal")
    ax.set_axis_off()
    x0, y0 = minx, miny - 150
    ax.plot([x0, x0 + 1000], [y0, y0], color=TEXT, linewidth=2)
    ax.text(x0 + 500, y0 + 80, "1 km", ha="center", color=TEXT_SECONDARY)
    counts = data["role"].value_counts()
    ax.legend(
        handles=[
            Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                color=LABELS,
                markersize=7,
                label=f"labelled buildings ({counts['labels']})",
            ),
            Line2D(
                [],
                [],
                marker="o",
                linestyle="",
                color=TARGETS,
                markersize=5,
                label=f"target buildings ({counts['targets']})",
            ),
            Line2D([], [], color=TEXT_SECONDARY, label="Porto municipality"),
            Line2D([], [], color=ROADS, linewidth=2, label="main roads"),
        ],
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
    )
    ax.set_title(
        "Public facilities in Porto: training labels and tagging targets", loc="left"
    )
    table = pd.DataFrame(
        {
            "building_id": data["building_id"],
            "role": data["role"],
            "x_3763": data.centroid.x.round(1),
            "y_3763": data.centroid.y.round(1),
        }
    )
    writer.figure(fig, "fig_map", table)


def fig_building_size(
    writer: Writer, data: gpd.GeoDataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.2), sharey=True)
    tests = []
    for ax, column, title in [
        (axes[0], "area_m2", "Footprint area (m²)"),
        (axes[1], "perimeter_m", "Perimeter (m)"),
    ]:
        for role, color in [("labels", LABELS), ("targets", TARGETS)]:
            ecdf(ax, data.loc[data["role"] == role, column], color=color, label=role)
        ax.set_xscale("log")
        ax.set_xlabel(title)
        result = stats.ks_2samp(
            data.loc[data["role"] == "labels", column],
            data.loc[data["role"] == "targets", column],
        )
        tests.append(
            {
                "measure": column,
                "KS statistic": round(result.statistic, 3),
                "p-value": f"{result.pvalue:.1e}",
            }
        )
    axes[0].set_ylabel("share of buildings")
    axes[0].legend(loc="upper left")
    fig.suptitle(
        "Building size: labels vs targets (ECDF)", x=0.01, ha="left", fontweight="bold"
    )
    writer.figure(
        fig,
        "fig_building_size",
        data[["building_id", "role", "area_m2", "perimeter_m"]].round(1),
    )
    summary = (
        data.groupby("role")[["area_m2", "perimeter_m"]].median().round(0).reset_index()
    )
    summary.columns = ["set", "median area (m²)", "median perimeter (m)"]
    return pd.DataFrame(tests), summary


def fig_building_types(writer: Writer, data: gpd.GeoDataFrame) -> None:
    category = data["amenity"].fillna(data["building_type"]).fillna("unknown")
    shares = pd.crosstab(category, data["role"], normalize="columns") * 100
    top = shares.sort_values("targets", ascending=False).head(10)
    other = 100 - top.sum()
    shares = pd.concat([top, other.to_frame("other").T])
    fig, ax = plt.subplots(figsize=(7, 4.2))
    y = np.arange(len(shares))
    for offset, role, color in [(-0.2, "labels", LABELS), (0.2, "targets", TARGETS)]:
        ax.barh(
            y + offset,
            shares[role],
            height=0.38,
            color=color,
            label=role,
            edgecolor=SURFACE,
            linewidth=2,
        )
    ax.set_yticks(y, shares.index)
    ax.invert_yaxis()
    ax.set_xlabel("share of buildings (%)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="center right")
    ax.set_title("Facility types (amenity, else building tag)", loc="left")
    writer.figure(
        fig, "fig_building_types", shares.round(1).rename_axis("category").reset_index()
    )


def coverage(blocks: list[dict]) -> pd.Series:
    present = pd.DataFrame(
        [
            {
                name: feature in b["features"]
                for name, feature in CONTEXT_FEATURES.items()
            }
            for b in blocks
        ]
    )
    return present.mean() * 100


def fig_context_coverage(
    writer: Writer, labels: list[dict], targets: list[dict]
) -> None:
    shares = pd.DataFrame({"labels": coverage(labels), "targets": coverage(targets)})
    fig, ax = plt.subplots(figsize=(7, 3.6))
    y = np.arange(len(shares))
    for offset, role, color in [(-0.2, "labels", LABELS), (0.2, "targets", TARGETS)]:
        ax.barh(
            y + offset,
            shares[role],
            height=0.38,
            color=color,
            label=role,
            edgecolor=SURFACE,
            linewidth=2,
        )
    ax.set_yticks(y, shares.index)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("buildings with this context within 150 m (%)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower right")
    ax.set_title("Spatial context coverage", loc="left")
    writer.figure(
        fig,
        "fig_context_coverage",
        shares.round(1).rename_axis("context").reset_index(),
    )


def fig_class_imbalance(
    writer: Writer, labels: list[dict], data_dir: Path
) -> pd.DataFrame:
    n = np.array([len(b["dist"]) for b in labels])
    training = data_dir / "tagging" / "features.csv"
    resampled = (
        pd.read_csv(training)["class"] if training.exists() else pd.Series(dtype=float)
    )
    fig, ax = plt.subplots(figsize=(6, 3.2))
    ax.hist(n, bins=20, color=LABELS, edgecolor=SURFACE, linewidth=2)
    ax.set_xlabel("samples (candidate entrances) per building")
    ax.set_ylabel("buildings")
    ax.set_title("One positive sample per building", loc="left")
    writer.figure(
        fig,
        "fig_class_imbalance",
        pd.DataFrame({"building_id": [b["building_id"] for b in labels], "samples": n}),
    )
    rows = [
        ["samples in labelled buildings", int(n.sum())],
        ["positive samples", len(labels)],
        ["positive share (%)", round(100 * len(labels) / n.sum(), 2)],
        ["negatives per positive", round((n.sum() - len(labels)) / len(labels), 1)],
        ["samples per building, median", int(np.median(n))],
    ]
    if len(resampled):
        rows += [
            ["training samples after strong-negative sampling", len(resampled)],
            ["positive share after sampling (%)", round(100 * resampled.mean(), 2)],
        ]
    return pd.DataFrame(rows, columns=["measure", "value"])


def fig_positive_vs_negative(writer: Writer, labels: list[dict]) -> pd.DataFrame:
    frames = []
    for b in labels:
        f = b["features"].copy()
        f["sample"] = np.where(
            np.arange(len(f)) == b["entrance_index"], "positive", "negative"
        )
        frames.append(f)
    samples = pd.concat(frames, ignore_index=True)
    fig, axes = plt.subplots(2, 3, figsize=(9, 5.2), sharey=True)
    tests = []
    for ax, (feature, title) in zip(axes.ravel(), CUE_FEATURES.items(), strict=True):
        for kind, color in [("positive", LABELS), ("negative", TARGETS)]:
            ecdf(
                ax,
                samples.loc[samples["sample"] == kind, feature].dropna(),
                color=color,
                label=kind,
            )
        ax.set_title(title, loc="left", fontweight="normal")
        pos = samples.loc[samples["sample"] == "positive", feature].dropna()
        neg = samples.loc[samples["sample"] == "negative", feature].dropna()
        test = stats.mannwhitneyu(pos, neg)
        tests.append(
            {
                "feature": feature,
                "median positive": round(pos.median(), 3),
                "median negative": round(neg.median(), 3),
                "Mann-Whitney p": f"{test.pvalue:.1e}",
            }
        )
    for ax in axes[:, 0]:
        ax.set_ylabel("share of samples")
    axes[0, 0].legend(loc="lower right")
    fig.suptitle(
        "Where are the entrances? Positive vs negative samples (ECDF)",
        x=0.01,
        ha="left",
        fontweight="bold",
    )
    fig.tight_layout()
    writer.figure(
        fig, "fig_positive_vs_negative", samples[["sample", *CUE_FEATURES]].round(4)
    )
    return pd.DataFrame(tests)


# ---------------------------------------------------------------- task 4


def validation(
    engine, writer: Writer, labels: list[dict], data_dir: Path, groups: int
) -> pd.DataFrame:
    """Linear and path errors of the models and the baselines on the same buildings."""
    building_of = block_buildings(data_dir)
    blocks = list(range(len(labels)))
    walks = building_walks(engine, blocks, building_of)

    # One Dijkstra from the true entrance gives the walking error of every sample.
    linear, path = [], []
    for block, b in enumerate(labels):
        truth, dist = b["entrance_index"], b["dist"]
        n = len(dist)
        linear.append(np.array([ring_distance(dist, i, truth) for i in range(n)]))
        grid, points, starts = walks[block]
        walk, access, _ = path_features(grid, points, starts, starts[[truth]])
        errors = np.where(access == 1, walk + PATH_START_OFFSET_M, np.nan)
        errors[truth] = 0.0
        path.append(errors)

    picks = {
        "centroid-nearest sample": [
            int(b["features"]["cen_dis"].idxmin()) for b in labels
        ],
        "main-road-nearest sample": [
            int(b["features"]["main_p_dis"].idxmin())
            if "main_p_dis" in b["features"]
            else int(b["features"]["cen_dis"].idxmin())
            for b in labels
        ],
    }
    order = test_blocks(data_dir, groups)
    for model in ["brf", "wrf"]:
        results = pd.read_csv(data_dir / f"tagging_result_{model}.csv")
        predicted = dict(zip(order, results["0"].astype(int), strict=True))
        picks[model.upper()] = [predicted[block] for block in blocks]

    rows, curves, intervals = [], [], []
    for method, chosen in picks.items():
        lin = np.array([linear[k][i] for k, i in enumerate(chosen)])
        pth = np.array([path[k][i] for k, i in enumerate(chosen)])
        rows.append(metrics(method, lin, pth))
        if method in ("BRF", "WRF"):
            intervals.append(bootstrap(method, lin, pth))
        curves.append(
            pd.DataFrame(
                {
                    "method": method,
                    "building_id": building_of.loc[blocks].to_numpy(),
                    "linear_m": lin,
                    "path_m": pth,
                }
            )
        )
    # Random sample, in expectation: average over all samples of each building.
    expected = metrics(
        "random sample (expected)",
        np.array([e.mean() for e in linear]),
        np.array([np.nanmean(e) for e in path]),
        within=lambda limit: (
            percent(np.concatenate([e <= limit for e in linear])),
            percent(np.concatenate([e[np.isfinite(e)] <= limit for e in path])),
        ),
    )
    rows.append(expected)
    table = pd.DataFrame(rows)
    writer.table(
        "results",
        "Validation: distance error over the 86 labelled buildings (5-fold CV for the models)",
        table,
        "Linear: distance along the outline between the predicted and the true entrance sample "
        "(Hu et al.: ~21 m). Path: walking distance outside the building (Hu et al.: ~22 m); "
        "buildings without a walking path are left out of the path columns.",
    )
    writer.table(
        "results_ci",
        f"Validation: 95% bootstrap intervals ({BOOTSTRAP_RESAMPLES} building resamples)",
        pd.DataFrame(intervals),
    )
    fig_error_cdf(writer, pd.concat(curves, ignore_index=True))
    fig_examples(engine, writer, labels, picks, linear, building_of, data_dir)
    return table


BOOTSTRAP_RESAMPLES = 10_000


def bootstrap(method: str, linear: np.ndarray, path: np.ndarray) -> dict:
    """Percentile intervals of the mean and median error, resampling buildings."""
    rng = np.random.default_rng(0)
    row = {"method": method}
    for name, values in [("linear", linear), ("path", path[np.isfinite(path)])]:
        resampled = rng.choice(values, size=(BOOTSTRAP_RESAMPLES, len(values)))
        for stat, func in [("mean", np.mean), ("median", np.median)]:
            low, high = np.percentile(func(resampled, axis=1), [2.5, 97.5])
            row[f"{name} {stat} 95% CI (m)"] = f"{low:.1f}–{high:.1f}"
    return row


def fig_examples(
    engine,
    writer: Writer,
    labels: list[dict],
    picks: dict,
    linear: list[np.ndarray],
    building_of: pd.Series,
    data_dir: Path,
) -> None:
    """Three labelled buildings: a BRF hit where the nearest road fails, a typical case, a miss."""
    brf = np.array([linear[k][i] for k, i in enumerate(picks["BRF"])])
    road = np.array(
        [linear[k][i] for k, i in enumerate(picks["main-road-nearest sample"])]
    )
    hits = np.flatnonzero(brf <= 3)
    chosen = {
        "(a) hit": int(hits[np.argmax(road[hits])]),
        "(b) typical": int(np.argmin(np.abs(brf - np.median(brf)))),
        "(c) miss": int(np.argmax(brf)),
    }
    ids = ", ".join(str(int(building_of[k])) for k in chosen.values())
    footprints = gpd.read_postgis(
        f"SELECT building_id, building_type, amenity, ST_Transform(geom, {METRIC_CRS}) AS geom "
        f"FROM partb.label_buildings WHERE building_id IN ({ids})",
        engine,
        geom_col="geom",
    ).set_index("building_id")
    roads = gpd.read_postgis(
        f"SELECT kind, ST_Transform(r.geom, {METRIC_CRS}) AS geom FROM partb.ctx_roads r "
        "JOIN partb.label_buildings b ON ST_DWithin(ST_Transform(r.geom, "
        f"{METRIC_CRS}), ST_Transform(b.geom, {METRIC_CRS}), 60) "
        f"WHERE r.kind IN ('main', 'pedestrian') AND b.building_id IN ({ids})",
        engine,
        geom_col="geom",
    )
    samples = pd.read_csv(data_dir / "samples-porto.csv")
    samples = gpd.GeoDataFrame(
        samples,
        geometry=gpd.points_from_xy(samples["lon"], samples["lat"]),
        crs=4326,
    ).to_crs(METRIC_CRS)

    fig, axes = plt.subplots(1, 3, figsize=(10, 4))
    rows = []
    for ax, (title, block) in zip(axes, chosen.items(), strict=True):
        building = footprints.loc[int(building_of[block])]
        points = samples[samples["block_index"] == block].sort_values("sample_index")
        xy = np.column_stack([points.geometry.x, points.geometry.y])
        marks = {
            "true entrance": labels[block]["entrance_index"],
            "BRF": picks["BRF"][block],
            "nearest main road": picks["main-road-nearest sample"][block],
        }
        minx, miny, maxx, maxy = building.geom.buffer(25).bounds
        for kind, color, width in [("main", REFERENCE, 2), ("pedestrian", ROADS, 1)]:
            roads[roads["kind"] == kind].plot(ax=ax, color=color, linewidth=width)
        gpd.GeoSeries([building.geom]).plot(
            ax=ax, color=GRID, edgecolor=TEXT_SECONDARY, linewidth=1
        )
        ax.scatter(xy[:, 0], xy[:, 1], s=6, color=REFERENCE, zorder=3)
        # Hollow markers drawn first, so an exact hit still shows the BRF dot.
        styles = {
            "true entrance": {
                "s": 110,
                "facecolor": "none",
                "edgecolor": TARGETS,
                "marker": "D",
            },
            "nearest main road": {"s": 90, "facecolor": "none", "edgecolor": TEXT},
            "BRF": {"s": 35, "color": LABELS},
        }
        for mark, style_ in styles.items():
            x, y = xy[marks[mark]]
            ax.scatter(x, y, zorder=4, linewidth=1.5, label=mark, **style_)
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect("equal")
        ax.set_axis_off()
        kind = next(
            (
                v
                for v in (building.amenity, building.building_type)
                if pd.notna(v) and v != "yes"
            ),
            "building",
        )
        ax.set_title(
            f"{title} ({kind})\nBRF {brf[block]:.0f} m, road {road[block]:.0f} m",
            loc="left",
        )
        rows.append(
            {
                "panel": title,
                "building_id": int(building_of[block]),
                "type": kind,
                "brf_error_m": round(float(brf[block]), 1),
                "road_error_m": round(float(road[block]), 1),
            }
        )
    axes[0].legend(loc="lower left", bbox_to_anchor=(0, -0.25), ncol=3)
    writer.figure(fig, "fig_examples", pd.DataFrame(rows))


def metrics(method: str, linear: np.ndarray, path: np.ndarray, within=None) -> dict:
    finite = path[np.isfinite(path)]
    lin10, path10 = (
        within(10) if within else (percent(linear <= 10), percent(finite <= 10))
    )
    lin20 = within(20)[0] if within else percent(linear <= 20)
    return {
        "method": method,
        "linear mean (m)": round(float(linear.mean()), 1),
        "linear median (m)": round(float(np.median(linear)), 1),
        "linear ≤ 10 m (%)": round(lin10),
        "linear ≤ 20 m (%)": round(lin20),
        "path mean (m)": round(float(finite.mean()), 1),
        "path median (m)": round(float(np.median(finite)), 1),
        "path ≤ 10 m (%)": round(path10),
        # An expectation over all samples has no count of buildings without a path.
        "no walking path": "n/a" if within else int((~np.isfinite(path)).sum()),
    }


def fig_error_cdf(writer: Writer, curves: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6), sharey=True)
    series = [
        ("BRF", LABELS, "-"),
        ("WRF", TARGETS, "-"),
        ("main-road-nearest sample", REFERENCE, "--"),
        ("centroid-nearest sample", REFERENCE, ":"),
    ]
    for ax, column, title in [
        (axes[0], "linear_m", "Linear error (along the outline)"),
        (axes[1], "path_m", "Path error (walking distance)"),
    ]:
        for method, color, line in series:
            values = curves.loc[curves["method"] == method, column].dropna()
            ecdf(ax, values, color=color, linestyle=line, label=method)
        ax.set_xlim(0, 150)
        ax.set_xlabel("error (m)")
        ax.set_title(title, loc="left")
        ax.axvline(10, color=GRID, linewidth=1, zorder=0)
    axes[0].set_ylabel("share of buildings")
    axes[0].legend(loc="lower right")
    writer.figure(fig, "fig_error_cdf", curves.round(2))


def fig_prediction_confidence(engine, writer: Writer) -> pd.DataFrame:
    data = pd.read_sql(
        "SELECT building_id, probability, margin FROM partb.predicted_entrances", engine
    )
    fig, axes = plt.subplots(1, 2, figsize=(8, 3.2))
    for ax, column, title in [
        (axes[0], "probability", "Probability of the chosen sample"),
        (axes[1], "margin", "Margin to the second best sample"),
    ]:
        ax.hist(data[column], bins=25, color=LABELS, edgecolor=SURFACE, linewidth=2)
        ax.set_title(title, loc="left")
        ax.set_xlabel(column)
    axes[0].set_ylabel("target buildings")
    writer.figure(fig, "fig_prediction_confidence", data.round(4))
    return (
        data[["probability", "margin"]]
        .describe()
        .round(3)
        .reset_index()
        .rename(columns={"index": "statistic"})
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    data_dir = DATA_DIR / "entrance_tagging"
    parser.add_argument(
        "--data-dir", type=Path, default=data_dir, help="Pipeline output directory."
    )
    parser.add_argument("--groups", type=int, default=5, help="Number of CV groups.")
    args = parser.parse_args()
    plt.switch_backend("Agg")  # files only, no window
    style()
    writer = Writer(args.data_dir / "figures")
    engine = create_engine(database_settings().sqlalchemy_url)
    labels = read_blocks(args.data_dir / "osmfeatures-porto.csv")
    targets = read_blocks(args.data_dir / "osmfeatures-targets.csv")

    print("Exploratory analysis...")
    inventory(engine, writer, len(labels))
    anomalies(engine, writer, {b["building_id"] for b in labels})
    data = buildings(engine)
    spatial_stats(engine, writer, data)
    fig_map(engine, writer, data)
    size_tests, size_summary = fig_building_size(writer, data)
    writer.table("building_size", "Building size (median)", size_summary)
    writer.table(
        "building_size_tests",
        "Building size: labels vs targets (two-sample Kolmogorov-Smirnov)",
        size_tests,
    )
    fig_building_types(writer, data)
    fig_context_coverage(writer, labels, targets)
    writer.table(
        "class_imbalance",
        "Class imbalance",
        fig_class_imbalance(writer, labels, args.data_dir),
    )
    writer.table(
        "entrance_cues",
        "Entrance cues: positive vs negative samples",
        fig_positive_vs_negative(writer, labels),
        "Rank features are a sample's rank in its building divided by the number of samples.",
    )

    print("Validation...")
    results = validation(engine, writer, labels, args.data_dir, args.groups)
    writer.table(
        "prediction_confidence",
        "Tagged targets: confidence",
        fig_prediction_confidence(engine, writer),
    )
    writer.summary(
        "Data: OpenStreetMap snapshot of 2026-10-20 (Geofabrik Portugal extract), Porto municipality. "
        "There is a single snapshot and no trajectory data, so there is no temporal dimension to analyse."
    )
    print(results.to_string(index=False))
    print(f"Wrote the figures and tables to {writer.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
