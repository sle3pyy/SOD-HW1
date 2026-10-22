"""Assign the buildings of the raw feature file to cross-validation groups.

Writes <data-dir>/<g>/test_list.csv, a single row with the indices of the
buildings tested in group g, which extraction.py reads.
"""

import argparse
import csv
import random
from pathlib import Path

from sod_common.paths import DATA_DIR as PROJECT_DATA_DIR

DATA_DIR = PROJECT_DATA_DIR / "entrance_tagging"

def count_buildings(raw_file_name):
    """Each building is a header row followed by one row per sample (header column 4)."""
    with open(raw_file_name, newline='') as f:
        rows = list(csv.reader(f))
    count = 0
    cur_row_index = 0
    while cur_row_index < len(rows):
        cur_row_index += int(float(rows[cur_row_index][4])) + 1
        count += 1
    return count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split the buildings into cross-validation groups.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Directory holding the raw file and the group folders.")
    parser.add_argument("--raw-file", type=Path, help="Raw feature file (default: <data-dir>/osmfeatures-porto.csv).")
    parser.add_argument("--groups", type=int, default=5, help="Number of cross-validation groups.")
    parser.add_argument("--seed", type=int, default=0, help="Seed of the random building shuffle.")
    args = parser.parse_args()
    raw_file_name = args.raw_file or args.data_dir / 'osmfeatures-porto.csv'
    buildings = list(range(count_buildings(raw_file_name)))
    random.Random(args.seed).shuffle(buildings)
    for g in range(1, args.groups+1):
        test_list = sorted(buildings[g-1::args.groups])
        (args.data_dir / str(g)).mkdir(parents=True, exist_ok=True)
        with open(args.data_dir / str(g) / 'test_list.csv', 'w', newline='') as f:
            csv.writer(f).writerow(test_list)
        print(f'group {g}: {len(test_list)} test buildings')
