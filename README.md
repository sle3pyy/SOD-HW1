# SOD Homework 1: Spatial Data Analysis

| Folder | Contents |
| --- | --- |
| [`part_a/`](part_a/README.md) | Part A.1: map-matching Porto taxi GPS points to the OSM road network in PostGIS |
| [`part_b/`](part_b/README.md) | Part B: main-entrance detection for public buildings from OSM (Hu et al., 2021) |
| `webgis/` | Part A.2: TypeScript/Leaflet map and GeoServer/PostGIS provisioning |
| `common/` | `sod_common`, the code shared by both parts: project paths, PostGIS access, OSM PBF imports |
| [`data/`](data/README.md) | Input and generated data (large files are not committed) |
| `qgis/` | QGIS project and the `pg_service.conf` for the PostGIS service |
| `postgres-init/` | SQL run when the PostGIS container is first created |

The two parts are independent. Part A uses the `parta` schema, Part B uses the `partb` schema, and
Part B does not use the taxi data.

## Setup

Requirements: Docker with Compose and [uv](https://docs.astral.sh/uv/). Part A also needs GDAL
(`ogr2ogr`); Part B reads the PBF with pyrosm and does not.

```bash
sudo apt install gdal-bin
```

Install the Python environment for every part. The project is a single uv workspace, so run this from
the project root:

```bash
uv sync
```

Download the OSM extract as described in [`data/README.md`](data/README.md). For Part A.2, copy
`.env.example` to `.env` and set a local GeoServer admin password. Start PostGIS, GeoServer, and the
WebGIS page:

```bash
cp .env.example .env
docker compose up -d --build postgis geoserver geoserver-config webgis
```

The WebGIS is available at <http://localhost:8088>; GeoServer administration is at
<http://localhost:8600/geoserver>. The sidebar ends with live counts for
predicted entrances, OSM buildings, and road features. The `geoserver-config` service waits for
GeoServer, then creates the `sod` workspace and `parta` and `partb` PostGIS stores using host
`postgis`, port `5432`, database `gis`, and the credentials from `.env`. The project network keeps
container-to-container traffic internal; the web UI is published only on localhost. GeoServer
configuration persists in the `geoserver-data` Docker volume. The TypeScript provisioning job is
built from the WebGIS npm project. After either Part A or Part B creates
its tables, the pipeline automatically publishes available spatial tables as GeoServer layers. To
rerun provisioning later, use `docker compose run --rm geoserver-config`.

## Run

All commands are run from the project root:

```bash
uv run part_a/run_pipeline.py
```

```bash
uv run part_b/run_pipeline.py
```

See each part's README for the individual stages and options.
part a took 333.6s

## Open the WebGIS map

The Compose `webgis` service runs Vite in development mode with the frontend source mounted from
`webgis/`. Changes to `index.html` and `src/` hot reload in the browser; changes to `vite.config.ts`
restart the dev server. Start it alongside GeoServer with:

```bash
docker compose up -d --build webgis
```

Open <http://localhost:8088>. Rebuild the service after changing npm dependencies.


## Clean up

`cleanup.sh` removes everything the pipelines generate: the PostGIS container, network and volume (all
`parta` and `partb` tables) and the generated files in `data/generated/` and `data/entrance_tagging/`
(features, cross-validation folds, results and figures). The inputs in `data/osm/` and
`data/porto_taxi/` are kept. It asks for confirmation (`-y` skips it), refuses to run while a Part B
pipeline is running, and `--dry-run` only lists what it would remove.

```bash
./cleanup.sh --dry-run
```
