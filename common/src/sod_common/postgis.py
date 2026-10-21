"""Small stdlib-only bridge to the project's PostGIS service.

GDAL runs on the host, while ``psql`` runs in the Compose PostGIS container.
The same POSTGRES_* / POSTGIS_PORT environment variables are supplied to both.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DatabaseSettings:
    host: str
    port: str
    database: str
    user: str
    password: str

    @property
    def ogr_dsn(self) -> str:
        return (
            f"PG:host={self.host} port={self.port} dbname={self.database} "
            f"user={self.user} password={self.password}"
        )


def database_settings() -> DatabaseSettings:
    return DatabaseSettings(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGIS_PORT", "5432"),
        database=os.environ.get("POSTGRES_DB", "gis"),
        user=os.environ.get("POSTGRES_USER", "gis"),
        password=os.environ.get("POSTGRES_PASSWORD", "gis"),
    )


def require_command(command: str, install_hint: str | None = None) -> None:
    if shutil.which(command) is None:
        hint = f" {install_hint}" if install_hint else ""
        raise RuntimeError(f"{command} is required.{hint}")


def ensure_postgis_running(workdir: Path) -> None:
    result = subprocess.run(
        ["docker", "compose", "ps", "--status", "running", "--services"],
        cwd=workdir,
        check=True,
        capture_output=True,
        text=True,
    )
    if "postgis" not in result.stdout.splitlines():
        raise RuntimeError("PostGIS is not running. Start it with: docker compose up -d postgis")


def psql_command(settings: DatabaseSettings) -> list[str]:
    return [
        "docker",
        "compose",
        "exec",
        "-T",
        "-e",
        f"PGHOST={settings.host}",
        "-e",
        f"PGPORT={settings.port}",
        "-e",
        f"PGPASSWORD={settings.password}",
        "postgis",
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        settings.user,
        "-d",
        settings.database,
    ]


def run_sql(
    workdir: Path,
    settings: DatabaseSettings,
    sql: str,
    *,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*psql_command(settings), "-c", sql],
        cwd=workdir,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def run_sql_file(
    workdir: Path,
    settings: DatabaseSettings,
    filename: Path,
    variables: dict[str, float] | None = None,
) -> None:
    """Run a SQL file from the host, optionally setting psql variables (``:name`` in the file)."""
    command = psql_command(settings)
    for key, value in (variables or {}).items():
        command.extend(["-v", f"{key}={value:g}"])
    subprocess.run(
        [*command, "-f", "/dev/stdin"],
        cwd=workdir,
        check=True,
        text=True,
        input=filename.read_text(encoding="utf-8"),
    )
