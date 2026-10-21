"""Import OSM PBF layers into PostGIS with GDAL's ogr2ogr."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from sod_common.paths import ROOT
from sod_common.postgis import DatabaseSettings, psql_command


def extent_buffer_meters() -> float:
    value = os.environ.get("EXTENT_BUFFER_M", "1000")
    try:
        buffer_m = float(value)
    except ValueError as exc:
        raise RuntimeError("EXTENT_BUFFER_M must be a non-negative number") from exc
    if buffer_m < 0:
        raise RuntimeError("EXTENT_BUFFER_M must be a non-negative number")
    return buffer_m


def query_bounds(settings: DatabaseSettings, sql: str, missing_hint: str) -> list[str]:
    """Run a query returning one xmin, ymin, xmax, ymax row in EPSG:4326, for ogr2ogr -spat."""
    result = subprocess.run(
        [*psql_command(settings), "-At", "-F", ",", "-c", sql],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    bounds = result.stdout.strip().split(",")
    if len(bounds) != 4 or any(not coordinate for coordinate in bounds):
        raise RuntimeError(f"Could not derive the import extent. {missing_hint}")
    return bounds


def import_layer(
    settings: DatabaseSettings,
    pbf: Path,
    layer: str,
    where: str,
    table: str,
    bounds: list[str] | None,
    geometry_type: str,
) -> None:
    """Copy the features of one PBF layer (points, lines, multipolygons, ...) into a PostGIS table."""
    spat = ["-spat", *bounds] if bounds else []
    subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "PostgreSQL",
            settings.ogr_dsn,
            str(pbf),
            layer,
            *spat,
            "-where",
            where,
            "-nln",
            table,
            "-lco",
            "GEOMETRY_NAME=geom",
            "-nlt",
            geometry_type,
            "-overwrite",
        ],
        cwd=ROOT,
        check=True,
    )
