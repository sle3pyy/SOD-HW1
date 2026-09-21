#!/usr/bin/env bash
# Import portugal.gpkg's OSM road layer into the local PostGIS database.
set -euo pipefail

GEOPACKAGE=${GEOPACKAGE:-generated-data/portugal.gpkg}
LAYER=${LAYER:-gis_osm_roads_free}
TARGET_TABLE=${TARGET_TABLE:-public.gis_osm_roads_free}
POSTGRES_HOST=${POSTGRES_HOST:-localhost}
POSTGRES_PORT=${POSTGIS_PORT:-5432}
POSTGRES_DB=${POSTGRES_DB:-gis}
POSTGRES_USER=${POSTGRES_USER:-gis}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-gis}

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  echo "Optional overrides: GEOPACKAGE, LAYER, TARGET_TABLE, POSTGRES_HOST, POSTGIS_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD" >&2
  exit 2
fi

if [[ ! -f "$GEOPACKAGE" ]]; then
  echo "GeoPackage not found: $GEOPACKAGE" >&2
  exit 1
fi

if ! command -v ogr2ogr >/dev/null 2>&1; then
  echo "ogr2ogr is required. Install it with: sudo apt install gdal-bin" >&2
  exit 1
fi

if ! docker compose ps --status running --services | grep -qx postgis; then
  echo "PostGIS is not running. Start it with: docker compose up -d postgis" >&2
  exit 1
fi

echo "Importing $LAYER from $GEOPACKAGE into $TARGET_TABLE..."
ogr2ogr -f PostgreSQL \
  "PG:host=$POSTGRES_HOST port=$POSTGRES_PORT dbname=$POSTGRES_DB user=$POSTGRES_USER password=$POSTGRES_PASSWORD" \
  "$GEOPACKAGE" \
  "$LAYER" \
  -nln "$TARGET_TABLE" \
  -nlt PROMOTE_TO_MULTI \
  -overwrite

docker compose exec -T postgis \
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "ANALYZE $TARGET_TABLE;" \
  -c "SELECT count(*) AS imported_road_count FROM $TARGET_TABLE;"
