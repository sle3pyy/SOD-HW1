
\set ON_ERROR_STOP on

CREATE SCHEMA IF NOT EXISTS parta;
CREATE EXTENSION IF NOT EXISTS postgis;

DROP TABLE IF EXISTS parta.porto_points_raw;
CREATE TABLE parta.porto_points_raw (
  taxi_id integer NOT NULL,
  recorded_at timestamp without time zone NOT NULL,
  longitude double precision NOT NULL,
  latitude double precision NOT NULL
);

COPY parta.porto_points_raw (taxi_id, recorded_at, longitude, latitude)
FROM '/data/porto.csv'
WITH (FORMAT csv, HEADER true);

DROP TABLE IF EXISTS parta.porto_points;
CREATE TABLE parta.porto_points AS
SELECT
  row_number() OVER (ORDER BY taxi_id, recorded_at, longitude, latitude)::bigint AS point_id,
  taxi_id,
  recorded_at,
  longitude,
  latitude,
  ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geometry(Point, 4326) AS geom,
  ST_Transform(
    ST_SetSRID(ST_MakePoint(longitude, latitude), 4326),
    3857
  )::geometry(Point, 3857) AS geom_3857
FROM parta.porto_points_raw
WHERE longitude BETWEEN -180 AND 180
  AND latitude BETWEEN -90 AND 90;

ALTER TABLE parta.porto_points
  ADD PRIMARY KEY (point_id);

CREATE INDEX porto_points_taxi_time_idx
  ON parta.porto_points (taxi_id, recorded_at);

CREATE INDEX porto_points_geom_idx
  ON parta.porto_points
  USING gist (geom);

CREATE INDEX porto_points_geom_3857_idx
  ON parta.porto_points
  USING gist (geom_3857);

ANALYZE parta.porto_points_raw;
ANALYZE parta.porto_points;

SELECT
  count(*) AS point_count,
  count(DISTINCT taxi_id) AS taxi_count,
  min(recorded_at) AS first_timestamp,
  max(recorded_at) AS last_timestamp,
  ST_AsText(ST_Extent(geom)) AS lon_lat_extent
FROM parta.porto_points;
