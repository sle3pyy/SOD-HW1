# Part A Scripts

These scripts implement the Part A pipeline without the report:

1. prepare a point CSV from the Porto taxi dataset;
2. import GPS points into PostGIS;
3. import the OSM road layer from the Portugal GeoPackage;
4. crop roads to the GPS extent and map-match the points.

Run commands from the `HW1` directory.

## Before Running

Add portugal.gpkg to /generated-data. Can be found in https://download.geofabrik.de/europe/portugal.html.

## Run The Full Pipeline

Install GDAL once if `ogr2ogr` is not already available:

```bash
sudo apt install gdal-bin
```

Run the complete pipeline:

```bash
cd ~/Desktop/aulas/4ano1sem/SOD/HW1

docker compose up -d postgis

python3 scripts/dataset_cleaning.py

docker compose exec -T postgis \
  psql -U gis -d gis < scripts/import_clean_dataset.sql

scripts/import_roads.sh

docker compose exec -T postgis \
  psql -U gis -d gis \
  -v extent_buffer_m=1000 \
  -v match_radius_m=100 \
  < scripts/map_matching.sql
```

The road import replaces `public.gis_osm_roads_free`; omit that one command
when the existing road table does not need refreshing.

## 1. Start PostGIS

```bash
docker compose up -d postgis
```

## 2. Prepare The Porto Dataset

```bash
python3 scripts/dataset_cleaning.py
```

This writes `generated-data/porto.csv`, which is mounted in the database container
as `/data/porto.csv`.

Optional smaller subset:

```bash
python3 scripts/dataset_cleaning.py \
  --max-trips 100 \
  --every-n 2
```

## 3. Import GPS Points

```bash
docker compose exec -T postgis \
  psql -U gis -d gis < scripts/import_clean_dataset.sql
```

This uses shell input redirection: the SQL file stays on the host, while
Postgres reads the generated CSV from `/data/porto.csv` inside the
container.

## 4. Import OSM Roads

This imports `generated-data/portugal.gpkg` layer `gis_osm_roads_free` as
`public.gis_osm_roads_free`. It replaces the existing road table, so run it
only when you need to load or refresh the GeoPackage source. `ogr2ogr` from
`gdal-bin` is required.

```bash
scripts/import_roads.sh
```

## 5. Crop And Map-Match

The SQL crops the imported road network to the GPS extent plus the configured
buffer, then map-matches each GPS point to its nearest road.

```bash
docker compose exec -T postgis \
  psql -U gis -d gis \
  -v extent_buffer_m=1000 \
  -v match_radius_m=100 \
  < scripts/map_matching.sql
```

`match_radius_m` controls the maximum snapping distance. Points with no road
inside this radius stay unmatched.

Useful QGIS layers:

- `parta.roads_cropped`
- `parta.porto_points`
- `parta.porto_points_matched`
