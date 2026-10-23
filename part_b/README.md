# Part B: Main-Entrance Detection

Part B infers the main entrance of public buildings using only OpenStreetMap data. It reproduces

> X. Hu, A. Noskov, H. Fan, T. Novack, H. Li, F. Gu, J. Shang and A. Zipf (2021). Tagging the main
> entrances of public buildings based on OpenStreetMap and binary imbalanced learning. *International
> Journal of Geographical Information Science*, 35(9), 1773–1801.
> <https://doi.org/10.1080/13658816.2020.1861282>

The code in `entrance_tagging/` is adapted from the authors' repository
[xukehu/entrance_tagging](https://github.com/xukehu/entrance_tagging), via the fork
[tiago-bd-oliveira/entrance_tagging](https://github.com/tiago-bd-oliveira/entrance_tagging). It was
changed to run on Linux, and it uses only the weighted (WRF) and balanced (BRF) random forests, the
two best models in the paper.

Part B does not use the taxi data. Its area of interest (AOI) is an OSM administrative boundary: the
Porto municipality by default.

Run every command from the project root.

## Run the full pipeline

```bash
uv run part_b/run_pipeline.py
```

This runs the stages below: the OSM import, the feature extraction, the cross-validation and the
tagging of the target buildings (about 11 minutes in total, most of it the target features). Options:
`--aoi-name`, `--aoi-admin-level`, `--extent-buffer-m`, `--skip-osm-import`, `--skip-features`,
`--skip-tagging`, `--skip-report` and `--raw-file`.
At the end, it starts GeoServer if needed and publishes any spatial tables in the `partb` schema
that are not already registered as layers.

Only one pipeline can run at a time: all runs write the same `partb` tables and files in
`data/entrance_tagging/`, so a second `run_pipeline.py` exits with an error while another one holds the
lock (`data/entrance_tagging/.pipeline.lock`). The lock is released automatically when the run ends,
even if it crashes. Running the individual scripts by hand is not locked.

## 1. OSM import: labels, targets and spatial context

```bash
uv run part_b/src/import_osm.py
```

This reads the PBF (see [`data/README.md`](../data/README.md)) with
[pyrosm](https://pyrosm.readthedocs.io/) and writes the layers to PostGIS with geopandas; GDAL is not
needed. It looks up the AOI boundary (`--aoi-name`, `--aoi-admin-level`; the name must match
exactly), then reads the `entrance=*` nodes and the building footprints within the AOI plus a buffer
(`--extent-buffer-m`, default 1000 m), and the spatial context of the paper's Table 1. It creates:

- `partb.aoi`: the AOI boundary (`name`, `admin_level`, `geom`);
- `partb.osm_entrances`: all OSM entrance nodes in the extent, with their tags as `jsonb`;
- `partb.osm_buildings`: all OSM building footprints in the extent (`osm_type`, `osm_id`, tags);
- `partb.entrance_reference`: entrances classified as `main`, `generic` or `non_public_or_special`;
- `partb.entrance_reference_buildings`: entrance labels linked to their nearest building footprint
  within 5 m;
- `partb.label_buildings`: public facilities in the AOI with exactly one `main` entrance (the training
  buildings);
- `partb.target_buildings`: all public facilities in the AOI, including labelled buildings, for
  generating predictions that can be compared visually with known entrances;
- `partb.ctx_roads` (`main`, `pedestrian`, `service`), `partb.ctx_rail`, `partb.ctx_points`
  (`bicycle_parking`, `landmark`, `post_box`) and `partb.ctx_obstacles` (`barrier`, `green`,
  `water`): the spatial context, each with a `kind` column.

A building is a public facility when its `building` value is one of a list of public types (public,
hospital, school, university, church, ...) or it has an `amenity`, `shop`, `office` or `tourism` tag
(`is_public` in `prepare_entrances.sql`).

`main` nodes are the supervised labels. Entrances are used only as labels for training and evaluation,
never as model input features.

Predictions for buildings that also appear in `partb.label_buildings` are in-sample predictions;
use the held-out cross-validation results for independent model evaluation.
For each prediction with a linked `main` entrance, the pipeline stores the distance in metres to
the nearest such entrance as `true_distance_m`; the WebGIS displays the mean over available matches.

## 2. Feature extraction

```bash
uv run part_b/src/features.py --which labels
```

Following section 3 of the paper, each footprint's outer ring is split into ~3 m segments; their
midpoints are the samples (candidate entrances). For every sample it computes:

- intrinsic features from the footprint: distance to the centroid, position on and length of its edge,
  concave/convex shape of its edge and of the opposite edge, whether its outward normal faces the
  building itself, and the reflection symmetry axis (existence, distance, edge crossing);
- extrinsic features from the context within 150 m: distance to each context type, the visibility
  degree of main roads, service ways, the address street and the railway (sight lines blocked by
  buildings and barriers), whether bicycle parking, landmarks and post boxes are visible, the open
  area in front of the sample and the distance to the next building along its outward normal;
- path features, for main roads, pedestrian ways, service ways, the railway, bicycle parking and the
  address street: the shortest walking path around obstacles (buildings, barriers, green, water,
  railway), whether such a path exists, and its turning degree (path / straight-line distance).

Most measures also get a `*_sort` feature: the sample's rank in its building divided by the number of
samples.

The paper runs A* from every sample to every context point. Here the context points of one type are
the sources of a single multi-source Dijkstra (`scipy.sparse.csgraph.dijkstra`) on a 1 m occupancy grid
around the building. That gives the same shortest paths for all samples at once, and the source each
one reaches, for the turning degree. A sample without a path gets a path distance of 450 m, accessible
0 and turning degree 0. `--no-path` skips these features, e.g. to compare with the model without them;
they add only about 10% to the extraction time.

It writes `data/entrance_tagging/osmfeatures-porto.csv` (raw format, below) and `samples-porto.csv`
(the coordinates of every sample). `--which targets` writes the same files with the `targets` suffix
for the target buildings, whose entrance index is `-1`. A labelled building whose entrance is more than
5 m from its sampled outline (e.g. on another part of a multi-part footprint) is skipped.

## 3. Cross-validation

The scripts read and write under `data/entrance_tagging/` by default; pass `--data-dir <dir>` to use
another directory.

The raw feature file (default `osmfeatures-porto.csv`, override with `--raw-file`) has one block per
building: a header row `building_id, entrance_index, entrance_x, entrance_y, n_samples`, followed by
`n_samples` rows `row_len, dist_to_next_sample, x, y, name1, value1, name2, value2, ...`. A feature
that is missing for a building is left out of its rows and imputed later. The paper's original data is
[osmfeatures-test25.xls](https://drive.google.com/file/d/1lVG3zhWKvR1SVVN3QgLGAyxmc0c97QXJ/view?usp=sharing),
which must be exported to CSV first.

Split the buildings into five cross-validation groups, then build each group's training and test sets.
Missing data is imputed, and only strong negative samples are kept (`--pt` and `--ft` set the physical
and feature distance thresholds):

```bash
uv run part_b/entrance_tagging/split_groups.py
```

```bash
uv run part_b/entrance_tagging/extraction.py
```

Train WRF and BRF and report the mean distance error over the test buildings (writes
`tagging_result_<model>.csv`):

```bash
uv run part_b/entrance_tagging/training_tagging.py
```

This is the paper's linear distance error: the distance along the outline between the predicted and
the true entrance sample. The path distance error, the walking distance between them outside the
building, is computed on the same grid as the path features (writes `path_error_<model>.csv`):

```bash
uv run part_b/src/path_error.py
```

`uv run part_b/entrance_tagging/prediction_test.py` runs the same evaluation and plots the cumulative
distribution of the distance error (`--no-show` saves it to `error_cdf.png` instead).

## 4. Tagging the target buildings

```bash
uv run part_b/entrance_tagging/tag_entrances.py
```

This trains a model (`--model brf`, the default, or `wrf`) on all labelled buildings, with the same
strong-negative sampling and imputation as the cross-validation, and picks the sample with the highest
positive probability in every target building. It needs `osmfeatures-targets.csv` and
`samples-targets.csv` from `features.py --which targets`. The result is written to
`partb.predicted_entrances` and `data/entrance_tagging/predicted_entrances.geojson`: one point per
target building, with its `probability` and the `margin` to the second most likely sample.

## 5. Report statistics and figures

```bash
uv run part_b/src/report_figures.py
```

This reads the `partb` tables and the pipeline's files (it never writes to the database) and writes the
exploratory analysis and the validation for the report to `data/entrance_tagging/figures/`. Every
figure is a PNG with a CSV of its data, and `summary.md` collects all tables:

- exploratory analysis: `data_inventory`, `anomalies` (several main entrances, unlinked entrances,
  multi-part footprints, skipped labels), `spatial_stats` (Clark-Evans nearest-neighbour ratio),
  `fig_map`, `fig_building_size` (with a Kolmogorov-Smirnov test, labels vs targets),
  `fig_building_types`, `fig_context_coverage`, `fig_class_imbalance` and `fig_positive_vs_negative`
  (which cues separate the true entrances, with Mann-Whitney tests);
- validation: `results` (linear and path error of BRF, WRF and three baselines: the sample nearest the
  centroid, the sample nearest a main road and a random sample), `fig_error_cdf` and
  `fig_prediction_confidence`.
