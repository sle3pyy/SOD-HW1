# Part A.1: Map-Matching Porto Taxi Points

The pipeline:

1. prepares a point CSV from the Porto taxi dataset;
2. imports the GPS points into PostGIS;
3. imports the OSM road layer from the Portugal PBF;
4. crops the roads to the GPS extent and map-matches the points.

Run every command from the project root.

## Run the full pipeline

```bash
uv run part_a/run_pipeline.py
```

The runner starts PostGIS, prepares and imports the Porto points, refreshes the roads and map-matches
the points. Pass `--skip-road-import` to reuse the existing roads. It also accepts sampling controls,
for example:

```bash
uv run part_a/run_pipeline.py --max-trips 100 --every-n 2
```

## Run the stages one by one

### 1. Start PostGIS

```bash
docker compose up -d postgis
```

### 2. Prepare the Porto dataset

```bash
uv run part_a/src/dataset_cleaning.py
```

This writes `data/generated/porto.csv`, which is mounted in the database container as
`/data/porto.csv`. For a smaller subset:

```bash
uv run part_a/src/dataset_cleaning.py --max-trips 100 --every-n 2
```

### 3. Import the GPS points

```bash
docker compose exec -T postgis psql -U gis -d gis < part_a/sql/import_clean_dataset.sql
```

The SQL file stays on the host, while Postgres reads the generated CSV from `/data/porto.csv` inside
the container.

### 4. Import the OSM roads

This imports `highway=*` line features from the PBF (see [`data/README.md`](../data/README.md)) into
`public.gis_osm_roads_free`, cropped to the GPS extent plus the buffer (`EXTENT_BUFFER_M`, default
1000 m). It replaces the existing road table.

```bash
uv run part_a/src/import_roads.py
```

### 5. Crop and map-match

The SQL crops the road network to the GPS extent plus the buffer, then snaps each GPS point to its
nearest road.

```bash
docker compose exec -T postgis psql -U gis -d gis -v extent_buffer_m=1000 -v match_radius_m=100 < part_a/sql/map_matching.sql
```

`match_radius_m` is the maximum snapping distance. Points with no road inside this radius stay
unmatched.

Useful QGIS layers:

- `parta.roads_cropped`
- `parta.porto_points`
- `parta.porto_points_matched`
