#!/usr/bin/env python3
"""Run the Part B pipeline: OSM import, features, CV, tagging and report figures."""

from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

from sod_common.paths import DATA_DIR as PROJECT_DATA_DIR
from sod_common.paths import ROOT

PART_B = Path(__file__).resolve().parent
SRC = PART_B / "src"
TAGGING = PART_B / "entrance_tagging"
DATA_DIR = PROJECT_DATA_DIR / "entrance_tagging"
# One lock for every run: they all share the partb tables, whatever --raw-file says.
LOCK_FILE = DATA_DIR / ".pipeline.lock"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--aoi-name", default="Porto", help="OSM name of the area-of-interest boundary."
    )
    parser.add_argument(
        "--aoi-admin-level",
        type=int,
        default=7,
        help="OSM admin_level of the boundary.",
    )
    parser.add_argument(
        "--extent-buffer-m",
        type=float,
        default=1000,
        help="Buffer around the AOI in metres.",
    )
    parser.add_argument(
        "--skip-osm-import",
        action="store_true",
        help="Reuse the existing partb tables.",
    )
    parser.add_argument(
        "--skip-features",
        action="store_true",
        help="Reuse the existing raw feature file of the labelled buildings.",
    )
    parser.add_argument(
        "--raw-file",
        type=Path,
        default=DATA_DIR / "osmfeatures-porto.csv",
        help="Raw feature file for cross-validation (with --skip-features, e.g. the paper's).",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="Skip the report statistics and figures.",
    )
    parser.add_argument(
        "--skip-tagging",
        action="store_true",
        help="Skip tagging the target buildings (and extracting their features).",
    )
    return parser.parse_args()


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True, text=True)


@contextmanager
def pipeline_lock() -> Generator[None]:
    """Hold an exclusive lock so two pipelines never write the same files and tables.

    The operating system releases the lock when the process exits, even after a
    crash or Ctrl-C, so a leftover lock file never blocks the next run.
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.seek(0)
            holder = lock.read().strip() or "unknown"
            raise RuntimeError(
                f"another Part B pipeline is running (pid {holder}); "
                "wait for it to finish before starting a new one"
            ) from None
        lock.truncate(0)
        lock.write(f"{os.getpid()}\n")
        lock.flush()
        yield


def main() -> int:
    args = parse_args()
    if args.extent_buffer_m < 0:
        raise ValueError("--extent-buffer-m must be non-negative")
    with pipeline_lock():
        run_stages(args)
    return 0


def run_stages(args: argparse.Namespace) -> None:
    if not args.skip_osm_import:
        run(["docker", "compose", "up", "-d", "postgis"])
        run(["sleep", "5"])
        run(
            [
                sys.executable,
                str(SRC / "import_osm.py"),
                "--aoi-name",
                args.aoi_name,
                "--aoi-admin-level",
                str(args.aoi_admin_level),
                "--extent-buffer-m",
                f"{args.extent_buffer_m:g}",
            ]
        )

    if not args.skip_features:
        out_dir = ["--out-dir", str(args.raw_file.parent)]
        which = "labels" if args.skip_tagging else "both"
        run([sys.executable, str(SRC / "features.py"), "--which", which, *out_dir])
    data_dir = ["--data-dir", str(args.raw_file.parent)]
    raw_file = ["--raw-file", str(args.raw_file)]
    run([sys.executable, str(TAGGING / "split_groups.py"), *data_dir, *raw_file])
    run([sys.executable, str(TAGGING / "extraction.py"), *data_dir, *raw_file])
    run([sys.executable, str(TAGGING / "training_tagging.py"), *data_dir])
    run([sys.executable, str(SRC / "path_error.py"), *data_dir])
    if not args.skip_tagging:
        run([sys.executable, str(TAGGING / "tag_entrances.py"), *data_dir])
    # The report reads the tagged targets, so it needs the tagging stage.
    if not args.skip_report and not args.skip_tagging:
        run([sys.executable, str(SRC / "report_figures.py"), *data_dir])

    # The tables are created by the pipeline; publish them after they exist.
    run(["docker", "compose", "run", "--rm", "geoserver-config"])


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
