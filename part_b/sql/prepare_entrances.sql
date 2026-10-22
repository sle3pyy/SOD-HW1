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
  osm_id,
  NULLIF(tags->>'name', '') AS name,
  lower(entrance) AS entrance_kind,
  tags,
  ST_Force2D(geom)::geometry(Point, 4326) AS geom,
  ST_Transform(ST_Force2D(geom), 3857)::geometry(Point, 3857) AS geom_3857
FROM partb.osm_entrances_raw
WHERE geom IS NOT NULL;

ALTER TABLE partb.osm_entrances ADD PRIMARY KEY (osm_id);
CREATE INDEX osm_entrances_geom_idx ON partb.osm_entrances USING gist (geom);
CREATE INDEX osm_entrances_geom_3857_idx ON partb.osm_entrances USING gist (geom_3857);
CREATE INDEX osm_entrances_kind_idx ON partb.osm_entrances (entrance_kind);

DROP TABLE IF EXISTS partb.osm_buildings;
CREATE TABLE partb.osm_buildings AS
SELECT
  row_number() OVER (ORDER BY osm_type, osm_id)::bigint AS building_id,
  osm_type,
  osm_id,
  name,
  building_type,
  amenity,
  shop,
  office,
  tourism,
  addr_street,
  -- Public facilities, as in the paper (building=public), widened to the OSM tags
  -- that mark a building open to the public.
  (
    building_type IN (
      'public', 'civic', 'government', 'hospital', 'school', 'university', 'college',
      'train_station', 'transportation', 'church', 'cathedral', 'chapel',
      'kindergarten', 'museum', 'library', 'commercial', 'retail', 'office', 'hotel',
      'sports_hall', 'stadium'
    )
    OR COALESCE(amenity, shop, office, tourism) IS NOT NULL
  ) AS is_public,
  tags,
  geom,
  ST_Transform(geom, 3857)::geometry(MultiPolygon, 3857) AS geom_3857
FROM (
  SELECT
    osm_type,
    osm_id,
    NULLIF(name, '') AS name,
    NULLIF(building, '') AS building_type,
    NULLIF(amenity, '') AS amenity,
    NULLIF(shop, '') AS shop,
    NULLIF(office, '') AS office,
    NULLIF(tourism, '') AS tourism,
    NULLIF(addr_street, '') AS addr_street,
    tags,
    ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_Force2D(geom)), 3))::geometry(MultiPolygon, 4326) AS geom
  FROM partb.osm_buildings_raw
  WHERE geom IS NOT NULL
) polygons
-- Buildings mapped as single nodes have no footprint and are dropped here.
WHERE NOT ST_IsEmpty(geom);

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
  b.osm_type AS building_osm_type,
  b.osm_id AS building_osm_id,
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

-- Training buildings: public facilities in the AOI with exactly one main entrance.
DROP TABLE IF EXISTS partb.label_buildings;
CREATE TABLE partb.label_buildings AS
WITH main_links AS (
  SELECT building_id, min(entrance_osm_id) AS entrance_osm_id, count(*) AS main_count
  FROM partb.entrance_reference_buildings
  WHERE reference_class = 'main' AND building_id IS NOT NULL
  GROUP BY building_id
)
SELECT
  b.building_id,
  b.osm_type,
  b.osm_id,
  b.name,
  b.building_type,
  b.amenity,
  b.addr_street,
  e.osm_id AS entrance_osm_id,
  e.geom AS entrance_geom,
  b.geom,
  b.geom_3857
FROM partb.osm_buildings b
JOIN main_links m ON m.building_id = b.building_id AND m.main_count = 1
JOIN partb.osm_entrances e ON e.osm_id = m.entrance_osm_id
WHERE b.is_public
  AND EXISTS (SELECT 1 FROM partb.aoi a WHERE ST_Intersects(a.geom, b.geom));

ALTER TABLE partb.label_buildings ADD PRIMARY KEY (building_id);

-- Tagging targets: public facilities in the AOI without a main entrance.
DROP TABLE IF EXISTS partb.target_buildings;
CREATE TABLE partb.target_buildings AS
SELECT
  b.building_id,
  b.osm_type,
  b.osm_id,
  b.name,
  b.building_type,
  b.amenity,
  b.addr_street,
  b.geom,
  b.geom_3857
FROM partb.osm_buildings b
WHERE b.is_public
  AND EXISTS (SELECT 1 FROM partb.aoi a WHERE ST_Intersects(a.geom, b.geom))
  AND NOT EXISTS (
    SELECT 1 FROM partb.entrance_reference_buildings r
    WHERE r.building_id = b.building_id AND r.reference_class = 'main'
  );

ALTER TABLE partb.target_buildings ADD PRIMARY KEY (building_id);

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

SELECT
  (SELECT count(*) FROM partb.label_buildings) AS label_buildings,
  (SELECT count(*) FROM partb.target_buildings) AS target_buildings;
