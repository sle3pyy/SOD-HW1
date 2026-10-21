# SOD Homework 1: Spatial Data Analysis

| Folder | Contents |
| --- | --- |
| [`part_a/`](part_a/README.md) | Part A.1: map-matching Porto taxi GPS points to the OSM road network in PostGIS |
| [`part_b/`](part_b/README.md) | Part B: main-entrance detection for public buildings from OSM (Hu et al., 2021) |
| `webgis/` | Part A.2: WebGIS over the Part B results (not started) |
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

Download the OSM extract as described in [`data/README.md`](data/README.md), then start PostGIS:

```bash
docker compose up -d postgis
```

## Run

All commands are run from the project root:

```bash
uv run part_a/run_pipeline.py
```

```bash
uv run part_b/run_pipeline.py
```

See each part's README for the individual stages and options.
