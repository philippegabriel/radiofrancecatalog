#!/usr/bin/env python3

import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Convert a transcript JSON file to CSV.")
    parser.add_argument("input", type=Path, help="Input transcript JSON file")
    parser.add_argument("--output", required=True, type=Path, help="Output CSV file")
    args = parser.parse_args()

    if args.output.exists():
        return

    path = args.input
    episode_id = path.stem

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)

        for seq, segment in enumerate(data["transcript"]):
            writer.writerow([
                episode_id,
                seq,
                segment["start"],
                segment["end"],
                segment.get("speaker"),
                segment["text"],
            ])


if __name__ == "__main__":
    main()
