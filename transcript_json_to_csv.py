#!/usr/bin/env python3

import csv
import json
import sys
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    episode_id = path.stem

    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    writer = csv.writer(sys.stdout)

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

