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

This imports the OSM data below, then runs the entrance-tagging cross-validation if a raw feature file
exists. Options: `--aoi-name`, `--aoi-admin-level`, `--extent-buffer-m`, `--skip-osm-import` and
`--raw-file`.

## 1. OSM entrance labels

```bash
uv run part_b/src/import_osm.py
```

This reads the PBF (see [`data/README.md`](../data/README.md)) with
[pyrosm](https://pyrosm.readthedocs.io/) and writes the layers to PostGIS with geopandas; GDAL is not
needed. It looks up the AOI boundary (`--aoi-name`, `--aoi-admin-level`; the name must match
exactly), then reads the `entrance=*` nodes and the building footprints within the AOI plus a buffer
(`--extent-buffer-m`, default 1000 m). It creates:

- `partb.aoi`: the AOI boundary (`name`, `admin_level`, `geom`);
- `partb.osm_entrances`: all OSM entrance nodes in the extent, with their tags as `jsonb`;
- `partb.osm_buildings`: all OSM building footprints in the extent (`osm_type`, `osm_id`, tags);
- `partb.entrance_reference`: entrances classified as `main`, `generic` or `non_public_or_special`;
- `partb.entrance_reference_buildings`: entrance labels linked to their nearest building footprint
  within 5 m.

`main` nodes are the supervised labels. Entrances are used only as labels for training and evaluation,
never as model input features.

## 2. Entrance tagging

The scripts read and write under `data/entrance_tagging/` by default; pass `--data-dir <dir>` to use
another directory.

The raw feature file (default `osmfeatures-test25.csv`, override with `--raw-file`) has one block per
building: a header row `building_id, entrance_index, entrance_x, entrance_y, n_samples`, followed by
`n_samples` rows `row_len, dist_to_next_sample, x, y, name1, value1, name2, value2, ...`. A feature
that is missing for a sample is left out of its row and imputed later. The paper's original data is
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

`uv run part_b/entrance_tagging/prediction_test.py` runs the same evaluation and plots the cumulative
distribution of the distance error (`--no-show` saves it to `error_cdf.png` instead).
