\set ON_ERROR_STOP on

-- These are reference labels, never classifier input features.
CREATE SCHEMA IF NOT EXISTS partb;

DO $$
BEGIN
  IF to_regclass('partb.aoi') IS NULL THEN
    RAISE EXCEPTION 'partb.aoi is missing. Run uv run part_b/src/import_osm.py first.';
  END IF;
  IF to_regclass('partb.osm_entrances_raw') IS NULL THEN
    RAISE EXCEPTION 'partb.osm_entrances_raw is missing. Run uv run part_b/src/import_osm.py first.';
  END IF;
  IF to_regclass('partb.osm_buildings_raw') IS NULL THEN
    RAISE EXCEPTION 'partb.osm_buildings_raw is missing. Run uv run part_b/src/import_osm.py first.';
  END IF;
END
$$;

DROP TABLE IF EXISTS partb.osm_entrances;
CREATE TABLE partb.osm_entrances AS
SELECT
  NULLIF(r.osm_id, '')::bigint AS osm_id,
  NULLIF(r.name, '') AS name,
  lower((regexp_match(r.other_tags, '"entrance"=>"([^"]+)"'))[1]) AS entrance_kind,
  r.other_tags,
  ST_SetSRID(ST_Force2D(r.geom), 4326)::geometry(Point, 4326) AS geom,
  ST_Transform(ST_SetSRID(ST_Force2D(r.geom), 4326), 3857)::geometry(Point, 3857) AS geom_3857
FROM partb.osm_entrances_raw r
CROSS JOIN partb.aoi a
WHERE r.geom IS NOT NULL
  AND ST_Intersects(ST_Transform(ST_SetSRID(ST_Force2D(r.geom), 4326), 3857), a.extent_3857);

ALTER TABLE partb.osm_entrances ADD PRIMARY KEY (osm_id);
CREATE INDEX osm_entrances_geom_idx ON partb.osm_entrances USING gist (geom);
CREATE INDEX osm_entrances_geom_3857_idx ON partb.osm_entrances USING gist (geom_3857);
CREATE INDEX osm_entrances_kind_idx ON partb.osm_entrances (entrance_kind);

DROP TABLE IF EXISTS partb.osm_buildings;
CREATE TABLE partb.osm_buildings AS
SELECT
  row_number() OVER (ORDER BY COALESCE(NULLIF(osm_way_id, ''), NULLIF(osm_id, '')))::bigint AS building_id,
  NULLIF(osm_id, '') AS osm_relation_id,
  NULLIF(osm_way_id, '') AS osm_way_id,
  NULLIF(name, '') AS name,
  NULLIF(building, '') AS building_type,
  ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(geom)), 3))::geometry(MultiPolygon, 4326) AS geom,
  ST_Transform(
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(geom)), 3)), 3857
  )::geometry(MultiPolygon, 3857) AS geom_3857
FROM partb.osm_buildings_raw
WHERE geom IS NOT NULL
  AND NOT ST_IsEmpty(geom);

ALTER TABLE partb.osm_buildings ADD PRIMARY KEY (building_id);
CREATE INDEX osm_buildings_geom_idx ON partb.osm_buildings USING gist (geom);
CREATE INDEX osm_buildings_geom_3857_idx ON partb.osm_buildings USING gist (geom_3857);
CREATE INDEX osm_buildings_type_idx ON partb.osm_buildings (building_type);

DROP TABLE IF EXISTS partb.entrance_reference;
CREATE TABLE partb.entrance_reference AS
SELECT
  osm_id,
  name,
  entrance_kind,
  CASE
    WHEN entrance_kind = 'main' THEN 'main'
    WHEN entrance_kind IN ('yes', 'entrance') THEN 'generic'
    ELSE 'non_public_or_special'
  END AS reference_class,
  geom,
  geom_3857
FROM partb.osm_entrances;

ALTER TABLE partb.entrance_reference ADD PRIMARY KEY (osm_id);
CREATE INDEX entrance_reference_geom_3857_idx
  ON partb.entrance_reference USING gist (geom_3857);

-- An entrance node normally lies on a building boundary. Five metres absorbs
-- minor OSM digitising offsets without associating it to a distant building.
DROP TABLE IF EXISTS partb.entrance_reference_buildings;
CREATE TABLE partb.entrance_reference_buildings AS
SELECT
  e.osm_id AS entrance_osm_id,
  e.reference_class,
  e.entrance_kind,
  b.building_id,
  b.osm_relation_id AS building_osm_relation_id,
  b.osm_way_id AS building_osm_way_id,
  b.name AS building_name,
  b.building_type,
  e.geom AS entrance_geom,
  e.geom_3857 AS entrance_geom_3857,
  CASE WHEN b.building_id IS NULL THEN NULL::double precision
       ELSE ST_Distance(e.geom_3857, b.geom_3857) END AS building_distance_m
FROM partb.entrance_reference e
LEFT JOIN LATERAL (
  SELECT b.*
  FROM partb.osm_buildings b
  WHERE ST_DWithin(e.geom_3857, b.geom_3857, 5)
  ORDER BY
    CASE WHEN ST_Covers(b.geom, e.geom) THEN 0 ELSE 1 END,
    e.geom_3857 <-> b.geom_3857
  LIMIT 1
) b ON true;

CREATE INDEX entrance_reference_buildings_building_idx
  ON partb.entrance_reference_buildings (building_id);
CREATE INDEX entrance_reference_buildings_geom_3857_idx
  ON partb.entrance_reference_buildings USING gist (entrance_geom_3857);

ANALYZE partb.osm_entrances;
ANALYZE partb.osm_buildings;
ANALYZE partb.entrance_reference;
ANALYZE partb.entrance_reference_buildings;

SELECT reference_class, entrance_kind, count(*) AS entrance_count
FROM partb.entrance_reference
GROUP BY reference_class, entrance_kind
ORDER BY reference_class, entrance_kind;

SELECT
  (SELECT count(*) FROM partb.osm_buildings) AS building_count,
  count(*) FILTER (WHERE building_id IS NOT NULL) AS entrances_linked_to_building,
  count(*) FILTER (WHERE building_id IS NULL) AS unlinked_entrances
FROM partb.entrance_reference_buildings;
