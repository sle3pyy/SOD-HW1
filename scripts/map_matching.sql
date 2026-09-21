\set ON_ERROR_STOP on

\if :{?extent_buffer_m}
\else
\set extent_buffer_m 1000
\endif

\if :{?match_radius_m}
\else
\set match_radius_m 100
\endif

CREATE SCHEMA IF NOT EXISTS parta;

DO $$
BEGIN
  IF to_regclass('public.gis_osm_roads_free') IS NULL THEN
    RAISE EXCEPTION
      'Road layer public.gis_osm_roads_free is missing. Import the gis_osm_roads_free layer from portugal.gpkg before running this script.';
  END IF;
END
$$;

-- The GeoPackage roads use SRID 900914.  Index the same 4326 expression used
-- by the crop predicate, so PostGIS does not transform all source roads on
-- every run.
CREATE INDEX IF NOT EXISTS gis_osm_roads_free_geom_4326_idx
  ON public.gis_osm_roads_free
  USING gist (ST_Transform(geom, 4326))
  WHERE geom IS NOT NULL;

ANALYZE public.gis_osm_roads_free;

DROP TABLE IF EXISTS parta.gps_extent;
CREATE TABLE parta.gps_extent AS
SELECT
  ST_Envelope(ST_Collect(geom))::geometry(Polygon, 4326) AS geom,
  ST_Transform(
    ST_Buffer(ST_Envelope(ST_Collect(geom_3857)), :extent_buffer_m),
    4326
  )::geometry(Polygon, 4326) AS geom_buffered
FROM parta.porto_points;

CREATE INDEX gps_extent_geom_buffered_idx
  ON parta.gps_extent
  USING gist (geom_buffered);

DROP TABLE IF EXISTS parta.roads_cropped;
CREATE TABLE parta.roads_cropped AS
SELECT
  row_number() OVER ()::bigint AS road_pk,
  COALESCE(osm_id::text, '') AS osm_id,
  COALESCE(name::text, '') AS name,
  COALESCE(fclass::text, '') AS highway,
  ST_Multi(
    ST_CollectionExtract(
      ST_Transform(ST_MakeValid(ST_Force2D(r.geom)), 4326),
      2
    )
  )::geometry(MultiLineString, 4326) AS geom,
  ST_Transform(
    ST_Multi(
      ST_CollectionExtract(
        ST_Transform(ST_MakeValid(ST_Force2D(r.geom)), 4326),
        2
      )
    ),
    3857
  )::geometry(MultiLineString, 3857) AS geom_3857
FROM public.gis_osm_roads_free r
CROSS JOIN parta.gps_extent e
WHERE ST_Intersects(ST_Transform(r.geom, 4326), e.geom_buffered)
  AND NOT ST_IsEmpty(r.geom);

ALTER TABLE parta.roads_cropped
  ADD PRIMARY KEY (road_pk);

CREATE INDEX roads_cropped_geom_idx
  ON parta.roads_cropped
  USING gist (geom);

CREATE INDEX roads_cropped_geom_3857_idx
  ON parta.roads_cropped
  USING gist (geom_3857);

ANALYZE parta.roads_cropped;

DROP TABLE IF EXISTS parta.porto_points_matched;
CREATE TABLE parta.porto_points_matched AS
SELECT
  p.point_id,
  p.taxi_id,
  p.recorded_at,
  p.longitude,
  p.latitude,
  p.geom AS original_geom,
  nearest.road_pk,
  nearest.osm_id AS road_osm_id,
  nearest.name AS road_name,
  nearest.highway AS road_type,
  CASE
    WHEN nearest.road_pk IS NULL THEN NULL::geometry(Point, 4326)
    ELSE ST_Transform(
      ST_ClosestPoint(nearest.geom_3857, p.geom_3857),
      4326
    )::geometry(Point, 4326)
  END AS matched_geom,
  CASE
    WHEN nearest.road_pk IS NULL THEN NULL::double precision
    ELSE ST_Distance(
      p.geom::geography,
      ST_Transform(ST_ClosestPoint(nearest.geom_3857, p.geom_3857), 4326)::geography
    )
  END AS distance_meters
FROM parta.porto_points p
LEFT JOIN LATERAL (
  SELECT
    r.road_pk,
    r.osm_id,
    r.name,
    r.highway,
    r.geom_3857
  FROM parta.roads_cropped r
  WHERE ST_DWithin(p.geom_3857, r.geom_3857, :match_radius_m)
  ORDER BY p.geom_3857 <-> r.geom_3857
  LIMIT 1
) nearest ON true;

ALTER TABLE parta.porto_points_matched
  ADD PRIMARY KEY (point_id);

CREATE INDEX porto_points_matched_original_geom_idx
  ON parta.porto_points_matched
  USING gist (original_geom);

CREATE INDEX porto_points_matched_matched_geom_idx
  ON parta.porto_points_matched
  USING gist (matched_geom);

CREATE INDEX porto_points_matched_road_pk_idx
  ON parta.porto_points_matched (road_pk);

ANALYZE parta.porto_points_matched;

SELECT
  count(*) AS total_points,
  count(road_pk) AS matched_points,
  count(*) - count(road_pk) AS unmatched_points,
  round(avg(distance_meters)::numeric, 2) AS avg_distance_m,
  round(percentile_cont(0.5) WITHIN GROUP (ORDER BY distance_meters)::numeric, 2) AS median_distance_m,
  round(max(distance_meters)::numeric, 2) AS max_distance_m
FROM parta.porto_points_matched;
