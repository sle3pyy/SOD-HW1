"""Geometry checks for the Part B feature extractor (part_b/src/features.py)."""

import numpy as np
import pytest
from features import (
    NO_PATH_M,
    edge_shapes,
    footprint,
    intrinsic_features,
    occupancy_grid,
    path_features,
    positive_index,
    ray_hits,
    sample_footprint,
    symmetry_axis,
    visible_counts,
)
from shapely.geometry import Point, Polygon, box

RECTANGLE = footprint(box(0, 0, 30, 10))
# A 30 x 10 m block with a 6 x 4 m bump on top (the bump's top edge is convex).
BUMP = footprint(
    Polygon(
        [(0, 0), (30, 0), (30, 10), (18, 10), (18, 14), (12, 14), (12, 10), (0, 10)]
    )
)
# A 30 x 20 m block with a 10 x 10 m notch from the top (the notch floor is concave).
NOTCH = footprint(
    Polygon(
        [(0, 0), (30, 0), (30, 20), (20, 20), (20, 10), (10, 10), (10, 20), (0, 20)]
    )
)


def edge_index(polygon, a, b):
    """Index of the ring edge from vertex a to vertex b."""
    ring = [tuple(xy) for xy in np.asarray(polygon.exterior.coords)[:-1]]
    i = ring.index(a)
    assert ring[(i + 1) % len(ring)] == b
    return i


def test_rectangle_sampling_covers_the_perimeter():
    samples = sample_footprint(RECTANGLE)
    # 30 m edges give 10 samples, 10 m edges give round(10 / 3) = 3.
    assert len(samples.points) == 26
    assert samples.dist_to_next.sum() == pytest.approx(80)
    assert (samples.dist_to_next > 0).all()


def test_normals_point_outwards():
    samples = sample_footprint(RECTANGLE)
    outside = samples.points + samples.normal
    assert not any(RECTANGLE.contains(Point(p)) for p in outside)
    bottom = samples.points[:, 1] == pytest.approx(0)
    assert np.allclose(samples.normal[bottom], [0, -1])


def test_positive_index_is_the_nearest_segment():
    samples = sample_footprint(RECTANGLE)
    index, distance = positive_index(samples, np.array([16.0, -0.5]))
    assert samples.points[index] == pytest.approx([16.5, 0])
    assert distance == pytest.approx(0.5)


def test_edge_shapes():
    bump = edge_shapes(*_edges(BUMP))
    assert bump[edge_index(BUMP, (18, 14), (12, 14))] == 1
    notch = edge_shapes(*_edges(NOTCH))
    assert notch[edge_index(NOTCH, (20, 10), (10, 10))] == 0
    assert (edge_shapes(*_edges(RECTANGLE)) == -1).all()


def test_symmetry_axis():
    assert symmetry_axis(RECTANGLE, sample_footprint(RECTANGLE)) is not None
    irregular = footprint(Polygon([(0, 0), (20, 0), (25, 13), (3, 9)]))
    assert symmetry_axis(irregular, sample_footprint(irregular)) is None


def test_intrinsic_features_have_one_value_per_sample():
    samples = sample_footprint(NOTCH)
    features = intrinsic_features(NOTCH, samples)
    assert all(len(values) == len(samples.points) for values in features.values())
    assert features["axis_exist"][0] == 1  # the notch is centred
    assert ((features["proportion"] >= 0) & (features["proportion"] <= 0.5)).all()
    # Samples on the notch walls face the other wall of the same building.
    assert features["face_own"].any() and not features["face_own"].all()


def test_ray_hits():
    hits = ray_hits(
        np.array([[0.0, 0.0]]),
        np.array([[1.0, 0.0]]),
        np.array([[5.0, -1.0], [-5.0, -1.0]]),
        np.array([[5.0, 1.0], [-5.0, 1.0]]),
    )
    assert hits[0, 0] == pytest.approx(5)
    assert np.isinf(hits[0, 1])  # behind the ray


def test_visibility_is_blocked_by_buildings():
    origins = np.array([[0.0, 0.0]])
    targets = np.array([[10.0, 0.0], [0.0, 10.0]])
    blocker = np.array([box(4, -1, 6, 1)])
    assert visible_counts(origins, targets, blocker).tolist() == [1]


def _edges(polygon):
    samples = sample_footprint(polygon)
    return samples.edge_a, samples.edge_b


# ---------------------------------------------------------------- paths

OPEN = (0, 0, 50, 50)
SAMPLE = np.array([[9.0, 25.0]])
START = SAMPLE + [1.0, 0.0]  # 1 m outside the wall, as in extrinsic_features


def test_path_in_open_space_is_straight():
    grid = occupancy_grid(OPEN, np.array([]))
    path, access, turn = path_features(grid, SAMPLE, START, np.array([[40.0, 25.0]]))
    assert access.tolist() == [1]
    assert path[0] == pytest.approx(31, abs=1.5)
    assert turn[0] == pytest.approx(1, abs=0.08)


def test_path_goes_around_a_wall():
    wall = np.array([box(20, 0, 22, 45)])
    grid = occupancy_grid(OPEN, wall)
    path, access, turn = path_features(grid, SAMPLE, START, np.array([[40.0, 25.0]]))
    assert access.tolist() == [1]
    assert path[0] > 31 * 1.3
    assert turn[0] > 1.3


def test_enclosed_start_has_no_path():
    ring = box(5, 20, 15, 30).difference(box(7, 22, 13, 28))
    grid = occupancy_grid(OPEN, np.array([ring]))
    path, access, turn = path_features(grid, SAMPLE, START, np.array([[40.0, 25.0]]))
    assert access.tolist() == [0]
    assert path[0] == NO_PATH_M
    assert turn[0] == 0


def test_path_reaches_the_nearest_source():
    grid = occupancy_grid(OPEN, np.array([]))
    sources = np.array([[40.0, 25.0], [16.0, 25.0]])
    path, _, turn = path_features(grid, SAMPLE, START, sources)
    assert path[0] == pytest.approx(7, abs=1.5)
    assert turn[0] == pytest.approx(1, abs=0.2)
