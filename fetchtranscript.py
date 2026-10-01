#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests


BASE_URL = "https://www.radiofrance.fr/transistor/aod/{uuid}/transcript"

UUID_SUFFIX_RE = re.compile(r"_\d+$")


def aod_uuid(graphql_id: str) -> str:
    """Remove the GraphQL occurrence suffix from an AOD UUID."""
    return UUID_SUFFIX_RE.sub("", graphql_id.strip())


def fetch_transcript(uuid: str) -> dict | None:
    """Fetch one transcript from Radio France."""
    url = BASE_URL.format(uuid=uuid)

    response = requests.get(url, timeout=30)

    if response.status_code == 404:
        print(f"{uuid}: transcript not available")
        return None

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise RuntimeError(
            f"{uuid}: rate limited"
            + (f"; Retry-After={retry_after}" if retry_after else "")
        )

    response.raise_for_status()
    return response.json()


def load_ids(filename: Path):
    """Yield non-empty GraphQL IDs from the input file."""
    with filename.open(encoding="utf-8") as f:
        for line in f:
            graphql_id = line.strip()

            if graphql_id:
                yield graphql_id


def main():
    parser = argparse.ArgumentParser(
        description="Download Radio France podcast transcripts."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="File containing one Radio France GraphQL ID per line.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=Path("transcripts"),
        help="Directory in which transcripts are stored.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=1.0,
        help="Delay between requests in seconds (default: 1.0).",
    )

    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)

    for graphql_id in load_ids(args.input):
        uuid = aod_uuid(graphql_id)
        output = args.output_dir / f"{graphql_id}.json"

        if output.exists():
            print(f"{uuid}: already downloaded")
            continue

        print(f"{graphql_id} -> {uuid}: fetching...", flush=True)

        try:
            transcript = fetch_transcript(uuid)
        except requests.RequestException as exc:
            print(f"{uuid}: HTTP error: {exc}", file=sys.stderr)
            continue
        except (RuntimeError, ValueError) as exc:
            print(exc, file=sys.stderr)
            break

        if transcript is not None:
            with output.open("w", encoding="utf-8") as f:
                json.dump(
                    transcript,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )

            print(f"{uuid}: saved to {output}")

        time.sleep(args.delay)


if __name__ == "__main__":
    main()
