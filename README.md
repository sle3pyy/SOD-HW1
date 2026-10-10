# Spatial Data Analysis Pipeline

These scripts implement the Part A pipeline without the report:

1. prepare a point CSV from the Porto taxi dataset;
2. import GPS points into PostGIS;
3. import the OSM road layer from the Portugal GeoPackage;
4. crop roads to the GPS extent and map-match the points.

Run commands from the `HW1` directory. `part A/` is a `uv` workspace and its
pipeline has no third-party Python dependencies.

## Before Running

The full pipeline uses only `qgis-data/portugal-261008.osm.pbf`. It must be
present before running the road, building, or entrance import stages.

## Run The Full Pipeline

Install GDAL once if `ogr2ogr` is not already available:

```bash
sudo apt install gdal-bin
```

Run the complete Part A + Part B data-preparation pipeline:

```bash
cd ~/Desktop/aulas/4ano1sem/SOD/HW1

cd "part A"
uv sync
uv run python run_pipeline.py
```

The runner starts PostGIS, prepares and imports Porto points, refreshes roads,
map-matches them, and imports cropped OSM buildings and entrance labels. To
reuse roads or skip Part B's OSM import, pass `--skip-road-import` or
`--skip-partb-osm`. It accepts the earlier sampling controls too, for example:

```bash
cd "part A"
uv run python run_pipeline.py --max-trips 100 --every-n 2
```

## 1. Start PostGIS

```bash
docker compose up -d postgis
```

## 2. Prepare The Porto Dataset

```bash
python3 "part A/dataset_cleaning.py"
```

This writes `generated-data/porto.csv`, which is mounted in the database container
as `/data/porto.csv`.

Optional smaller subset:

```bash
python3 "part A/dataset_cleaning.py" \
  --max-trips 100 \
  --every-n 2
```

## 3. Import GPS Points

```bash
docker compose exec -T postgis \
  psql -U gis -d gis < "part A/import_clean_dataset.sql"
```

This uses shell input redirection: the SQL file stays on the host, while
Postgres reads the generated CSV from `/data/porto.csv` inside the
container.

## 4. Import OSM Roads

This imports `highway=*` line features from `qgis-data/portugal-261008.osm.pbf`
into `public.gis_osm_roads_free`, cropped to the GPS extent plus the configured
buffer. It replaces the existing road table. `ogr2ogr` from `gdal-bin` is
required.

```bash
python3 "part A/import_roads.py"
```

## 5. Crop And Map-Match

The SQL crops the imported road network to the GPS extent plus the configured
buffer, then map-matches each GPS point to its nearest road.

```bash
docker compose exec -T postgis \
  psql -U gis -d gis \
  -v extent_buffer_m=1000 \
  -v match_radius_m=100 \
  < "part A/map_matching.sql"
```

`match_radius_m` controls the maximum snapping distance. Points with no road
inside this radius stay unmatched.

Useful QGIS layers:

- `parta.roads_cropped`
- `parta.porto_points`
- `parta.porto_points_matched`

## Part B Foundation: OSM Entrance Labels

Part B uses the current OSM PBF as a reference-label source for real building
entrances. Entrances are labels for training and evaluation, never model input
features. Regenerate the Part A CSV first: it now preserves `trip_id` and
`point_index`, which are needed to identify each trip's final point and
approach direction.

```bash
python3 "part A/dataset_cleaning.py"

docker compose exec -T postgis \
  psql -U gis -d gis < "part A/import_clean_dataset.sql"

python3 "part A/import_partb_osm.py"
```

The final command imports `entrance=*` nodes and `building=*` footprints from
`qgis-data/portugal-261008.osm.pbf`, cropped to the same GPS extent plus a
1 km buffer used by the Part A road workflow. Override that buffer with, for
example, `EXTENT_BUFFER_M=2000 python3 "part A/import_partb_osm.py"`. It creates:

- `partb.osm_entrances` — all nearby OSM entrance nodes;
- `partb.osm_buildings` — all nearby OSM building footprints;
- `partb.entrance_reference` — `main`, `generic`, and
  `non_public_or_special` reference classes.
- `partb.entrance_reference_buildings` — entrance labels spatially linked to
  their nearest building footprint within 5 m.

Use `main` nodes as the primary supervised labels and `entrance=yes` as an
optional supplementary label. The feature-generation step must join those
labels to the supplied important-building footprints, while using only
road/POI/building geometry and taxi trajectories as predictor features.
