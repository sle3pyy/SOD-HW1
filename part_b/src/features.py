#!/usr/bin/env python3
"""Extract per-sample entrance features from the Part B PostGIS tables.

Implements the sampling and the features of Hu et al. (2021), section 3:
each building's outer ring is split into ~3 m segments whose midpoints are the
samples (candidate entrances). Every sample gets intrinsic features (from the
footprint) and extrinsic features (from the spatial context within 150 m).

The output is the raw feature format read by entrance_tagging/extraction.py:
one block per building, a header row `building_id, entrance_index, lon, lat,
n_samples` followed by one row per sample `row_len, dist_to_next, lon, lat,
name1, value1, ...`. A feature whose context is absent around a building is left
out of all of its rows and imputed later.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import Transformer
from scipy import ndimage
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import dijkstra
from shapely import affinity
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from sod_common.paths import DATA_DIR
from sod_common.postgis import database_settings
from sqlalchemy import create_engine

METRIC_CRS = 3763  # PT-TM06/ETRS89, metres
SAMPLE_SPACING_M = 3.0  # typical width of a public building's main entrance
CONTEXT_RADIUS_M = 150.0  # buffer of the spatial context around a building
DISCRETISE_M = 5.0  # interval for visibility targets along lines and areas
SIMPLIFY_M = 0.5  # drops near-collinear footprint vertices
SIGHT_OFFSET_M = 0.5  # sight lines start just outside the wall
ANGLE_TOLERANCE_DEG = 20.0  # tolerance around 90/270 degree corners
AXIS_TOLERANCE_M = 1.5  # max Hausdorff distance of a mirrored footprint
# Same tolerance as the entrance-building link in prepare_entrances.sql. A labelled
# entrance farther than this from the sampled ring (e.g. on another part of a
# multi-part footprint) has no correct positive sample, so the building is skipped.
ENTRANCE_TOLERANCE_M = 5.0
# Path features (stage B): walking distance on an occupancy grid around obstacles.
GRID_M = 1.0  # grid cell size
PATH_START_OFFSET_M = 1.0  # paths start just outside the wall
MAX_SNAP_M = 2.0  # blocked starts/sources move to a free cell at most this far
LINE_OBSTACLE_M = 0.5  # half-width given to barrier and railway lines
NO_PATH_M = 3 * CONTEXT_RADIUS_M  # path distance of a sample without a path
OUT_DIR = DATA_DIR / "entrance_tagging"

TO_LONLAT = Transformer.from_crs(METRIC_CRS, 4326, always_xy=True)


# ---------------------------------------------------------------- footprint


@dataclass
class Samples:
    """Samples of one footprint; edges are the simplified ring's edges."""

    points: np.ndarray  # (n, 2) sample coordinates
    seg_a: np.ndarray  # (n, 2) start of each sample's segment
    seg_b: np.ndarray  # (n, 2) end of each sample's segment
    edge: np.ndarray  # (n,) index of each sample's master edge
    along: np.ndarray  # (n,) distance from the master edge's start
    dist_to_next: np.ndarray  # (n,) ring distance to the next sample
    normal: np.ndarray  # (n, 2) outward unit normal (OPL direction)
    edge_a: np.ndarray  # (m, 2) edge starts
    edge_b: np.ndarray  # (m, 2) edge ends


def footprint(geom) -> Polygon:
    """Largest polygon of a footprint, simplified and oriented counter-clockwise."""
    polygons = shapely.get_parts(geom)
    polygon = max(polygons, key=lambda part: part.area)
    simplified = polygon.simplify(SIMPLIFY_M)
    if simplified.is_empty or not simplified.is_valid:
        simplified = polygon
    return orient(Polygon(simplified.exterior), sign=1.0)


def sample_footprint(polygon: Polygon) -> Samples:
    """Split every edge into ~SAMPLE_SPACING_M segments; samples are their midpoints."""
    ring = np.asarray(polygon.exterior.coords)[:-1]
    edge_a, edge_b = ring, np.roll(ring, -1, axis=0)
    vectors = edge_b - edge_a
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    directions = vectors / lengths[:, None]
    counts = np.maximum(1, np.round(lengths / SAMPLE_SPACING_M)).astype(int)

    edge = np.repeat(np.arange(len(ring)), counts)
    k = np.concatenate([np.arange(count) for count in counts])
    step = (lengths / counts)[edge]
    along = (k + 0.5) * step
    points = edge_a[edge] + directions[edge] * along[:, None]
    seg_a = edge_a[edge] + directions[edge] * (k * step)[:, None]
    seg_b = seg_a + directions[edge] * step[:, None]

    ring_position = np.concatenate([[0.0], np.cumsum(lengths)[:-1]])[edge] + along
    perimeter = lengths.sum()
    dist_to_next = np.diff(ring_position, append=ring_position[0] + perimeter)
    # Outward normal of a counter-clockwise ring: the direction rotated clockwise.
    normal = np.column_stack([directions[edge, 1], -directions[edge, 0]])
    return Samples(
        points, seg_a, seg_b, edge, along, dist_to_next, normal, edge_a, edge_b
    )


def positive_index(samples: Samples, entrance: np.ndarray) -> tuple[int, float]:
    """Index of the sample whose segment is nearest the true entrance, and that distance."""
    ab = samples.seg_b - samples.seg_a
    t = np.clip(
        ((entrance - samples.seg_a) * ab).sum(axis=1) / (ab**2).sum(axis=1), 0, 1
    )
    distances = np.hypot(*(samples.seg_a + ab * t[:, None] - entrance).T)
    index = int(np.argmin(distances))
    return index, float(distances[index])


def interior_angles(edge_a: np.ndarray, edge_b: np.ndarray) -> np.ndarray:
    """Interior angle (degrees) at every vertex of a counter-clockwise ring."""
    incoming = (
        edge_b[np.arange(-1, len(edge_a) - 1)] - edge_a[np.arange(-1, len(edge_a) - 1)]
    )
    outgoing = edge_b - edge_a
    cross = incoming[:, 0] * outgoing[:, 1] - incoming[:, 1] * outgoing[:, 0]
    dot = (incoming * outgoing).sum(axis=1)
    return 180.0 - np.degrees(np.arctan2(cross, dot))


def edge_shapes(edge_a: np.ndarray, edge_b: np.ndarray) -> np.ndarray:
    """Concave (0), convex (1) or neither (-1) for every edge (paper, section 3.2.2)."""
    angles = interior_angles(edge_a, edge_b)
    m = len(angles)
    i = np.arange(m)
    start, end = angles[i], angles[(i + 1) % m]
    before, after = angles[(i - 1) % m], angles[(i + 2) % m]

    def near(values, target):
        return np.abs(values - target) <= ANGLE_TOLERANCE_DEG

    convex = near(start, 90) & near(end, 90) & near(before, 270) & near(after, 270)
    concave = near(start, 270) & near(end, 270) & near(before, 90) & near(after, 90)
    return np.where(convex, 1, np.where(concave, 0, -1))


def ray_hits(
    origins: np.ndarray, directions: np.ndarray, seg_a: np.ndarray, seg_b: np.ndarray
) -> np.ndarray:
    """Distance along each ray (rows) to each segment (columns); inf when missed."""
    if len(seg_a) == 0:
        return np.full((len(origins), 0), np.inf)
    e = seg_b - seg_a  # (k, 2)
    w = seg_a[None, :, :] - origins[:, None, :]  # (n, k, 2)
    d = directions[:, None, :]
    denom = d[..., 0] * e[None, :, 1] - d[..., 1] * e[None, :, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (w[..., 0] * e[None, :, 1] - w[..., 1] * e[None, :, 0]) / denom
        s = (w[..., 0] * d[..., 1] - w[..., 1] * d[..., 0]) / denom
    hit = (np.abs(denom) > 1e-12) & (t > 1e-6) & (s >= 0) & (s <= 1)
    return np.where(hit, t, np.inf)


def symmetry_axis(polygon: Polygon, samples: Samples):
    """Best reflection axis as (point, unit direction), or None if not symmetric."""
    centroid = np.asarray(polygon.centroid.coords[0])
    coords = np.asarray(polygon.exterior.coords) - centroid
    _, _, vt = np.linalg.svd(coords - coords.mean(axis=0), full_matrices=False)
    candidates = [(centroid, vt[0]), (centroid, vt[1])]
    mids = (samples.edge_a + samples.edge_b) / 2
    vectors = samples.edge_b - samples.edge_a
    normals = np.column_stack([vectors[:, 1], -vectors[:, 0]])
    normals /= np.hypot(normals[:, 0], normals[:, 1])[:, None]
    candidates += list(zip(mids, normals, strict=True))

    best, best_distance = None, AXIS_TOLERANCE_M
    for point, direction in candidates:
        ux, uy = direction
        r = np.array([[2 * ux**2 - 1, 2 * ux * uy], [2 * ux * uy, 2 * uy**2 - 1]])
        offset = point - r @ point
        mirrored = affinity.affine_transform(
            polygon, [r[0, 0], r[0, 1], r[1, 0], r[1, 1], offset[0], offset[1]]
        )
        distance = shapely.hausdorff_distance(polygon.exterior, mirrored.exterior)
        if distance < best_distance:
            best, best_distance = (point, np.asarray(direction)), distance
    return best


def ranks(values: np.ndarray) -> np.ndarray:
    """The paper's sorting feature: rank of each sample in its building divided by n."""
    return pd.Series(values).rank(method="min").to_numpy() / len(values)


def intrinsic_features(polygon: Polygon, samples: Samples) -> dict[str, np.ndarray]:
    n = len(samples.points)
    lengths = np.hypot(*(samples.edge_b - samples.edge_a).T)
    centroid = np.asarray(polygon.centroid.coords[0])
    cen_dis = np.hypot(*(samples.points - centroid).T)
    master_len = lengths[samples.edge]
    proportion = np.abs(samples.along - master_len / 2) / master_len
    shapes = edge_shapes(samples.edge_a, samples.edge_b)

    # Opposite edge: first other edge hit by the inward perpendicular bisector.
    mids = (samples.edge_a + samples.edge_b) / 2
    inward = (
        -np.column_stack(
            [
                (samples.edge_b - samples.edge_a)[:, 1],
                -(samples.edge_b - samples.edge_a)[:, 0],
            ]
        )
        / lengths[:, None]
    )
    hits = ray_hits(mids, inward, samples.edge_a, samples.edge_b)
    np.fill_diagonal(hits, np.inf)
    opposite = np.argmin(hits, axis=1)
    opp_shape = np.where(np.isfinite(hits.min(axis=1)), shapes[opposite], -1)

    # Face own: the outward ray from the sample hits another edge of the footprint.
    own = ray_hits(samples.points, samples.normal, samples.edge_a, samples.edge_b)
    own[np.arange(n), samples.edge] = np.inf
    face_own = np.isfinite(own).any(axis=1).astype(int)

    features = {
        "cen_dis": cen_dis,
        "cen_sort": ranks(cen_dis),
        "proportion": proportion,
        "proportion_sort": ranks(proportion),
        "edge_len": master_len,
        "edge_len_sort": ranks(master_len),
        "shape": shapes[samples.edge],
        "opp_shape": opp_shape[samples.edge],
        "face_own": face_own,
    }
    axis = symmetry_axis(polygon, samples)
    features["axis_exist"] = np.full(n, int(axis is not None))
    if axis is not None:
        point, (ux, uy) = axis
        side = lambda xy: (xy[:, 0] - point[0]) * uy - (xy[:, 1] - point[1]) * ux
        axis_dis = np.abs(side(samples.points))
        start, end = side(samples.edge_a), side(samples.edge_b)
        crosses = (start * end <= 0).astype(int)
        features |= {
            "axis_dis": axis_dis,
            "axis_sort": ranks(axis_dis),
            "axis_edge": crosses[samples.edge],
        }
    return features


# ---------------------------------------------------------------- context


@dataclass
class Layer:
    """Geometries of one context kind with a spatial index."""

    geoms: np.ndarray
    tree: shapely.STRtree
    names: np.ndarray | None = None

    @classmethod
    def of(cls, gdf: gpd.GeoDataFrame, names: bool = False) -> Layer:
        geoms = gdf.geometry.to_numpy()
        return cls(
            geoms, shapely.STRtree(geoms), gdf["name"].to_numpy() if names else None
        )

    def near(self, area) -> np.ndarray:
        """Geometries within the area (a building's context buffer)."""
        return self.geoms[self.tree.query(area, predicate="intersects")]


def segments(geoms) -> tuple[np.ndarray, np.ndarray]:
    """All straight segments of lines and polygon boundaries."""
    if len(geoms) == 0:
        return np.empty((0, 2)), np.empty((0, 2))
    lines = shapely.get_parts(
        [g.boundary if g.geom_type in ("Polygon", "MultiPolygon") else g for g in geoms]
    )
    lines = lines[shapely.get_type_id(lines) == 1]  # LineStrings only
    coords, index = shapely.get_coordinates(lines, return_index=True)
    same = index[1:] == index[:-1]
    return coords[:-1][same], coords[1:][same]


def targets(geoms) -> np.ndarray:
    """Points every DISCRETISE_M along lines and area boundaries; points stay points."""
    points = []
    for geom in geoms:
        if geom.geom_type == "Point":
            points.append(np.asarray(geom.coords))
            continue
        line = geom.boundary if geom.geom_type in ("Polygon", "MultiPolygon") else geom
        for part in shapely.get_parts(line):
            steps = np.arange(0, part.length + 1e-9, DISCRETISE_M)
            points.append(
                shapely.get_coordinates(shapely.line_interpolate_point(part, steps))
            )
    return np.vstack(points) if points else np.empty((0, 2))


class Context:
    """All context layers, loaded once from PostGIS in a metric CRS."""

    def __init__(self, engine):
        def read(sql: str) -> gpd.GeoDataFrame:
            return gpd.read_postgis(sql, engine, geom_col="geom")

        geom = f"ST_Transform(geom, {METRIC_CRS}) AS geom"
        buildings = read(f"SELECT building_id, {geom} FROM partb.osm_buildings")
        self.building_ids = buildings["building_id"].to_numpy()
        self.buildings = Layer.of(buildings)
        roads = read(f"SELECT kind, name, {geom} FROM partb.ctx_roads")
        rail = read(f"SELECT kind, {geom} FROM partb.ctx_rail")
        points = read(
            f"SELECT kind, ST_Centroid({geom.split(' AS')[0]}) AS geom FROM partb.ctx_points"
        )
        obstacles = read(f"SELECT kind, {geom} FROM partb.ctx_obstacles")
        self.roads = Layer.of(roads, names=True)
        self.layers = {
            "main": Layer.of(roads[roads["kind"] == "main"]),
            "pedestrian": Layer.of(roads[roads["kind"] == "pedestrian"]),
            "service": Layer.of(roads[roads["kind"] == "service"]),
            "rail": Layer.of(rail),
            **{
                kind: Layer.of(points[points["kind"] == kind])
                for kind in ["bicycle_parking", "landmark", "post_box"]
            },
            **{
                kind: Layer.of(obstacles[obstacles["kind"] == kind])
                for kind in ["barrier", "green", "water"]
            },
        }


def visible_counts(origins: np.ndarray, points: np.ndarray, blockers) -> np.ndarray:
    """For each origin, how many target points it sees without crossing a blocker."""
    if len(points) == 0:
        return np.zeros(len(origins), dtype=int)
    o = np.repeat(np.arange(len(origins)), len(points))
    p = np.tile(np.arange(len(points)), len(origins))
    near = np.hypot(*(origins[o] - points[p]).T) <= CONTEXT_RADIUS_M
    o, p = o[near], p[near]
    lines = shapely.linestrings(np.stack([origins[o], points[p]], axis=1))
    blocked = np.zeros(len(lines), dtype=bool)
    if len(blockers):
        hit, _ = shapely.STRtree(blockers).query(lines, predicate="intersects")
        blocked[hit] = True
    return np.bincount(o[~blocked], minlength=len(origins))


def extrinsic_features(
    building, samples: Samples, context: Context, paths: bool = True
) -> dict[str, np.ndarray]:
    area = shapely.buffer(building.geom, CONTEXT_RADIUS_M)
    points = shapely.points(samples.points)
    sight_origins = samples.points + samples.normal * SIGHT_OFFSET_M
    others = context.buildings.near(area)
    blockers = np.concatenate([others, context.layers["barrier"].near(area)])
    features: dict[str, np.ndarray] = {}

    def distance(name: str, geoms) -> None:
        if len(geoms):
            values = shapely.distance(points[:, None], geoms[None, :]).min(axis=1)
            features[f"{name}_dis"] = values
            features[f"{name}_sort"] = ranks(values)

    def visibility(name: str, flag: str, geoms, degree: bool = True) -> None:
        if len(geoms):
            counts = visible_counts(sight_origins, targets(geoms), blockers)
            if degree:
                features[name] = counts
                features[f"{name}_sort"] = ranks(counts)
            features[flag] = (counts > 0).astype(int)

    near = {kind: layer.near(area) for kind, layer in context.layers.items()}
    road_index = context.roads.tree.query(area, predicate="intersects")
    street = building.addr_street
    address = (
        context.roads.geoms[road_index][context.roads.names[road_index] == street]
        if isinstance(street, str)
        else np.empty(0, dtype=object)
    )
    for name, geoms in [
        ("main_p", near["main"]),
        ("ped", near["pedestrian"]),
        ("service_way", near["service"]),
        ("rail", near["rail"]),
        ("park", near["bicycle_parking"]),
        ("landmark", near["landmark"]),
        ("post", near["post_box"]),
        ("addr", address),
    ]:
        distance(name, geoms)
    visibility("vis_mr", "bool_visual_mr", near["main"])
    visibility("vis_se", "bool_visual_se", near["service"])
    visibility("vis_addr", "bool_v_addr", address)
    visibility("vis_rail", "bool_visual_rail", near["rail"])
    visibility("", "bool_park", near["bicycle_parking"], degree=False)
    visibility("", "bool_landmark", near["landmark"], degree=False)
    visibility("", "bool_post", near["post_box"], degree=False)

    # Open area and distance to buildings along the outer perpendicular line (OPL).
    # The building itself is excluded; face_own covers its own walls.
    own = (
        context.building_ids[context.buildings.tree.query(area, predicate="intersects")]
        == building.building_id
    )
    other_buildings = others[~own]
    open_geoms = np.concatenate(
        [
            other_buildings,
            near["green"],
            near["water"],
            near["barrier"],
            near["main"],
            near["rail"],
        ]
    )
    for name, geoms in [("open_area", open_geoms), ("bld_dis", other_buildings)]:
        values = np.minimum(
            ray_hits(samples.points, samples.normal, *segments(geoms)).min(
                axis=1, initial=np.inf
            ),
            CONTEXT_RADIUS_M,
        )
        features[name] = values
        features[f"{name}_sort"] = ranks(values)

    if paths:
        grid = occupancy_grid(area.bounds, path_obstacles(near, others))
        starts = samples.points + samples.normal * PATH_START_OFFSET_M
        for name, geoms in [
            ("main", near["main"]),
            ("ped", near["pedestrian"]),
            ("se", near["service"]),
            ("rail", near["rail"]),
            ("park", near["bicycle_parking"]),
            ("addr", address),
        ]:
            if len(geoms):
                path, access, turn = path_features(
                    grid, samples.points, starts, targets(geoms)
                )
                features |= {
                    f"{name}_path": path,
                    f"{name}_path_sort": ranks(path),
                    f"{name}_access": access,
                    f"{name}_turn": turn,
                }
    return features


# ---------------------------------------------------------------- paths


@dataclass
class Grid:
    """Occupancy grid; cell (r, c) has its centre at origin + ((c, r) + 0.5) * GRID_M."""

    origin: np.ndarray  # (2,) lower-left corner
    free: np.ndarray  # (rows, cols) walkable cells
    nearest: np.ndarray  # (2, rows, cols) row/col of the nearest free cell
    graph: csr_matrix  # 8-connected graph of the free cells

    def centres(self, nodes: np.ndarray) -> np.ndarray:
        rows, cols = np.divmod(nodes, self.free.shape[1])
        return self.origin + (np.column_stack([cols, rows]) + 0.5) * GRID_M

    def snap(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Node of the free cell for each point (-1 if none within MAX_SNAP_M), and the offset."""
        cols, rows = np.floor((xy - self.origin) / GRID_M).astype(int).T
        n_rows, n_cols = self.free.shape
        inside = (rows >= 0) & (rows < n_rows) & (cols >= 0) & (cols < n_cols)
        rows, cols = np.clip(rows, 0, n_rows - 1), np.clip(cols, 0, n_cols - 1)
        rows, cols = self.nearest[0, rows, cols], self.nearest[1, rows, cols]
        nodes = rows * n_cols + cols
        offset = np.hypot(*(xy - self.centres(nodes)).T)
        valid = inside & self.free[rows, cols] & (offset <= MAX_SNAP_M)
        return np.where(valid, nodes, -1), offset


def grid_graph(free: np.ndarray) -> csr_matrix:
    """Edges of 1 or sqrt(2) cells between 8-connected free cells."""
    n_rows, n_cols = free.shape
    index = np.arange(free.size).reshape(free.shape)
    rows, cols, weights = [], [], []
    for dy, dx in [(0, 1), (1, 0), (1, 1), (1, -1)]:
        a = index[: n_rows - dy, max(0, -dx) : n_cols - max(0, dx)].ravel()
        b = index[dy:, max(0, dx) : n_cols + min(0, dx)].ravel()
        both = free.ravel()[a] & free.ravel()[b]
        rows.append(a[both])
        cols.append(b[both])
        weights.append(np.full(both.sum(), np.hypot(dx, dy) * GRID_M))
    return coo_matrix(
        (np.concatenate(weights), (np.concatenate(rows), np.concatenate(cols))),
        shape=(free.size, free.size),
    ).tocsr()


def occupancy_grid(bounds, obstacles) -> Grid:
    """Grid over the bounds; a cell is blocked when its centre lies in an obstacle."""
    min_x, min_y, max_x, max_y = bounds
    n_cols = int(np.ceil((max_x - min_x) / GRID_M))
    n_rows = int(np.ceil((max_y - min_y) / GRID_M))
    xs = min_x + (np.arange(n_cols) + 0.5) * GRID_M
    ys = min_y + (np.arange(n_rows) + 0.5) * GRID_M
    blocked = np.zeros((n_rows, n_cols), dtype=bool)
    if len(obstacles):
        union = shapely.union_all(obstacles)
        shapely.prepare(union)
        blocked = shapely.contains_xy(union, *np.meshgrid(xs, ys))
    free = ~blocked
    _, nearest = ndimage.distance_transform_edt(blocked, return_indices=True)
    return Grid(np.array([min_x, min_y]), free, nearest, grid_graph(free))


def path_obstacles(near: dict[str, np.ndarray], buildings: np.ndarray) -> np.ndarray:
    """Path obstacles of the paper: buildings, barriers, green, water and railway."""
    lines = np.concatenate([near["barrier"], near["rail"]])
    return np.concatenate(
        [
            buildings,
            near["green"],
            near["water"],
            shapely.buffer(lines, LINE_OBSTACLE_M),
        ]
    )


def path_features(
    grid: Grid, samples_xy: np.ndarray, starts_xy: np.ndarray, sources_xy: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Shortest walking path from every sample to the nearest source point.

    Returns the path distance (NO_PATH_M without a path), accessible (0/1) and the
    turning degree: path distance / Euclidean distance to the source it reaches.
    """
    n = len(samples_xy)
    sources = np.unique(grid.snap(sources_xy)[0])
    sources = sources[sources >= 0]
    starts, offsets = grid.snap(starts_xy)
    if len(sources) == 0:
        return np.full(n, NO_PATH_M), np.zeros(n, dtype=int), np.zeros(n)
    distances, _, reached = dijkstra(
        grid.graph,
        directed=False,
        indices=sources,
        min_only=True,
        return_predecessors=True,
        limit=NO_PATH_M,
    )
    walk = np.where(starts >= 0, distances[np.maximum(starts, 0)], np.inf)
    access = np.isfinite(walk)
    path = np.where(access, PATH_START_OFFSET_M + offsets + walk, NO_PATH_M)
    target = grid.centres(np.where(access, reached[np.maximum(starts, 0)], 0))
    euclid = np.maximum(np.hypot(*(samples_xy - target).T), GRID_M)
    turn = np.where(access, path / euclid, 0.0)
    return path, access.astype(int), turn


# ---------------------------------------------------------------- output


def lonlat(xy: np.ndarray) -> np.ndarray:
    return np.column_stack(TO_LONLAT.transform(xy[:, 0], xy[:, 1]))


def write_blocks(
    buildings: gpd.GeoDataFrame,
    context: Context,
    raw_file: Path,
    samples_file: Path,
    paths: bool = True,
) -> None:
    """Write one raw-format block per building and the sample coordinates."""
    with (
        raw_file.open("w", newline="") as raw,
        samples_file.open("w", newline="") as coords,
    ):
        raw_writer, coords_writer = csv.writer(raw), csv.writer(coords)
        coords_writer.writerow(
            ["block_index", "building_id", "sample_index", "lon", "lat"]
        )
        block = 0
        for building in buildings.itertuples():
            polygon = footprint(building.geom)
            samples = sample_footprint(polygon)
            if np.isfinite(building.entrance_x):
                entrance = np.array([building.entrance_x, building.entrance_y])
                index, offset = positive_index(samples, entrance)
                if offset > ENTRANCE_TOLERANCE_M:
                    print(
                        f"  skipping building {building.building_id}: its entrance is "
                        f"{offset:.1f} m from the sampled outline"
                    )
                    continue
                entrance_lonlat = lonlat(entrance[None, :])[0]
            else:
                index, entrance_lonlat = -1, (0.0, 0.0)
            features = intrinsic_features(polygon, samples) | extrinsic_features(
                building, samples, context, paths
            )
            sample_lonlat = lonlat(samples.points)
            raw_writer.writerow(
                [building.building_id, index, *entrance_lonlat, len(samples.points)]
            )
            names = list(features)
            for i, (lon, lat) in enumerate(sample_lonlat):
                pairs = [
                    value
                    for name in names
                    for value in (name, f"{features[name][i]:.6g}")
                ]
                raw_writer.writerow(
                    [4 + len(pairs), f"{samples.dist_to_next[i]:.4f}", lon, lat, *pairs]
                )
                coords_writer.writerow([block, building.building_id, i, lon, lat])
            block += 1
            if block % 100 == 0:
                print(f"  {block}/{len(buildings)} buildings", flush=True)
    print(f"Wrote {block} of {len(buildings)} buildings to {raw_file}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--which",
        choices=["labels", "targets", "both"],
        default="both",
        help="Buildings to extract.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=OUT_DIR, help="Output directory."
    )
    parser.add_argument(
        "--no-path",
        action="store_true",
        help="Skip the path-based features (stage B), e.g. to compare with stage A.",
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_settings().sqlalchemy_url)
    print("Loading the spatial context...")
    context = Context(engine)
    geom = f"ST_Transform(geom, {METRIC_CRS}) AS geom"
    entrance = f"ST_Transform(entrance_geom, {METRIC_CRS})"
    label_sql = (
        f"SELECT building_id, addr_street, ST_X({entrance}) AS entrance_x, "
        f"ST_Y({entrance}) AS entrance_y, {geom} "
        "FROM partb.label_buildings ORDER BY building_id"
    )
    target_sql = (
        "SELECT building_id, addr_street, NULL::float AS entrance_x, "
        f"NULL::float AS entrance_y, {geom} "
        "FROM partb.target_buildings ORDER BY building_id"
    )
    jobs = {"labels": (label_sql, "porto"), "targets": (target_sql, "targets")}
    for which, (sql, suffix) in jobs.items():
        if args.which in (which, "both"):
            buildings = gpd.read_postgis(sql, engine, geom_col="geom")
            buildings[["entrance_x", "entrance_y"]] = buildings[
                ["entrance_x", "entrance_y"]
            ].astype(float)
            print(f"Extracting features for {len(buildings)} {which} buildings...")
            write_blocks(
                buildings,
                context,
                args.out_dir / f"osmfeatures-{suffix}.csv",
                args.out_dir / f"samples-{suffix}.csv",
                paths=not args.no_path,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
