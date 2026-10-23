#!/usr/bin/env python3
"""Extract each Porto taxi trip's final valid point for PostGIS map-matching."""

from __future__ import annotations

import argparse
import ast
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

from sod_common.paths import DATA_DIR

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Porto taxi trajectory points for the Part A PostGIS pipeline."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DATA_DIR / "porto_taxi/train.csv",
        help="Porto taxi CSV containing TRIP_ID, TAXI_ID, TIMESTAMP, and POLYLINE.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DATA_DIR / "generated/porto.csv",
        help="Output CSV consumed by part_a/sql/import_clean_dataset.sql.",
    )
    parser.add_argument(
        "--max-trips",
        type=int,
        default=0,
        help="Optional trip limit. 0 means all trips.",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=0,
        help="Optional global point limit. 0 means all points.",
    )
    return parser.parse_args()


def parse_polyline(value: str) -> list[tuple[int, float, float]]:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError) as exc:
        raise ValueError("invalid POLYLINE literal") from exc

    if not isinstance(parsed, list):
        raise TypeError("POLYLINE is not a list")

    points: list[tuple[int, float, float]] = []
    for point_index, point in enumerate(parsed):
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not isinstance(point[0], (int, float))
            or not isinstance(point[1], (int, float))
        ):
            raise ValueError("POLYLINE contains an invalid point")
        lon = float(point[0])
        lat = float(point[1])
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            points.append((point_index, lon, lat))
    return points


def main() -> int:
    args = parse_args()
    if args.max_trips < 0:
        raise SystemExit("--max-trips must be >= 0")
    if args.max_points < 0:
        raise SystemExit("--max-points must be >= 0")
    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    args.output.parent.mkdir(parents=True, exist_ok=True)

    trips_read = 0
    trips_skipped = 0
    points_written = 0
    seen_trip_ids: set[str] = set()

    try:
        output_file = args.output.open("w", newline="", encoding="utf-8")
    except PermissionError as exc:
        raise SystemExit(f"Cannot write {args.output}. Check permissions.") from exc

    with args.input.open("r", newline="", encoding="utf-8") as input_file, output_file:
        reader = csv.DictReader(input_file)
        required_columns = {"TRIP_ID", "TAXI_ID", "TIMESTAMP", "POLYLINE"}
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            raise SystemExit(f"Input CSV missing columns: {', '.join(sorted(missing_columns))}")

        writer = csv.writer(output_file)
        writer.writerow(
            ["trip_id", "taxi_id", "point_index", "recorded_at", "longitude", "latitude"]
        )

        for row in reader:
            if args.max_trips and trips_read >= args.max_trips:
                break
            trips_read += 1

            try:
                trip_id = row["TRIP_ID"].strip()
                if not trip_id:
                    raise ValueError("TRIP_ID is empty")
                if trip_id in seen_trip_ids:
                    trips_skipped += 1
                    continue
                seen_trip_ids.add(trip_id)
                taxi_id = int(row["TAXI_ID"])
                start_time = datetime.fromtimestamp(int(row["TIMESTAMP"]), tz=UTC)
                points = parse_polyline(row["POLYLINE"])
            except (TypeError, ValueError):
                trips_skipped += 1
                continue

            if not points:
                trips_skipped += 1
                continue

            point_index, lon, lat = points[-1]
            # Porto challenge polylines are sampled every 15 seconds.
            recorded_at = start_time + timedelta(seconds=15 * point_index)
            writer.writerow(
                [
                    trip_id,
                    taxi_id,
                    point_index,
                    recorded_at.replace(tzinfo=None).isoformat(sep=" "),
                    f"{lon:.8f}",
                    f"{lat:.8f}",
                ]
            )
            points_written += 1

            if args.max_points and points_written >= args.max_points:
                break

    print(
        f"Wrote {points_written} final points from {trips_read} trips "
        f"({trips_skipped} skipped) to {args.output}."
    )
    return 0


if __name__ == "__main__":
    started = perf_counter()
    try:
        raise SystemExit(main())
    finally:
        print(
            f"[TIMER] dataset_cleaning.py: {perf_counter() - started:.1f}s",
            flush=True,
        )
