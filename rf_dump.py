#!/usr/bin/env python3

import argparse
import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_URL = "https://openapi.radiofrance.fr/v1/graphql"
DEFAULT_SHOW_URL = (
    "https://www.radiofrance.fr/franceculture/podcasts/"
    "les-nuits-de-france-culture"
)

GQL_DIFFUSIONS = """
query GetDiffusions($url: String!, $first: Int!, $after: String) {
  diffusionsOfShowByUrl(url: $url, first: $first, after: $after) {
    edges {
      cursor
      node {
        id
        title
        url
        standFirst
        published_date
        podcastEpisode {
          title
          url
          playerUrl
        }
      }
    }
  }
}
"""


def to_iso(ts):
    if ts is None or ts == "":
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def parse_since(value):
    """
    Parse an ISO 8601 date/time such as:

        2026-05-02T13:04:50+00:00
        2026-05-02 13:04:50+00:00
        2026-05-02 13:04:50
        2026-05-02T13:04:50Z

    A timezone-naive value is interpreted as UTC.
    """
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--since must be a valid ISO 8601 date/time, for example "
            "'2026-05-02T13:04:50+00:00'"
        ) from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return int(dt.timestamp())


def make_session(total_timeout, retries=6, backoff=0.8):
    session = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=10,
        pool_maxsize=10,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.request_timeout = total_timeout
    return session


def gql_post(session, api_key, query, variables):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-token": api_key,
    }
    timeout = getattr(session, "request_timeout", 60)

    response = session.post(
        API_URL,
        json={"query": query, "variables": variables},
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()

    payload = response.json()
    if payload.get("errors"):
        messages = "; ".join(
            error.get("message", "GraphQL error")
            for error in payload["errors"]
        )
        raise RuntimeError(f"GraphQL returned errors: {messages}")

    return payload["data"]


def fetch_all(
    api_key,
    show_url,
    since_ts=0,
    page_size=50,
    sleep_sec=0.2,
    total_timeout=90,
):
    session = make_session(total_timeout=total_timeout)
    after = None
    count = 0

    while True:
        data = gql_post(
            session,
            api_key,
            GQL_DIFFUSIONS,
            {
                "url": show_url,
                "first": page_size,
                "after": after,
            },
        )

        edges = (
            (data.get("diffusionsOfShowByUrl") or {}).get("edges")
            or []
        )
        if not edges:
            break

        reached_cutoff = False

        for edge in edges:
            node = edge.get("node") or {}
            published_ts = node.get("published_date")

            if (
                since_ts
                and published_ts is not None
                and int(published_ts) <= since_ts
            ):
                reached_cutoff = True
                continue

            yield {
                "id": node.get("id", ""),
                "title": node.get("title", "") or "",
                "description": node.get("standFirst", "") or "",
                "published_ts": published_ts,
                "published_iso": to_iso(published_ts),
                "web_url": node.get("url", "") or "",
                "podcast_title": (
                    (node.get("podcastEpisode") or {}).get("title") or ""
                ),
                "podcast_url": (
                    (node.get("podcastEpisode") or {}).get("url") or ""
                ),
                "player_url": (
                    (node.get("podcastEpisode") or {}).get("playerUrl") or ""
                ),
            }
            count += 1

        print(f"Fetched {count} new items so far", file=sys.stderr)

        if reached_cutoff:
            print(
                f"Reached existing catalogue at timestamp {since_ts}; stopping.",
                file=sys.stderr,
            )
            break

        after = edges[-1].get("cursor")
        if not after:
            break

        if sleep_sec:
            time.sleep(sleep_sec)


def main():
    parser = argparse.ArgumentParser(
        description="Dump Radio France show diffusions to CSV."
    )
    parser.add_argument(
        "--api-key",
        help="Radio France Open API key or env RADIOFRANCE_API_KEY",
    )
    parser.add_argument(
        "--show-url",
        default=DEFAULT_SHOW_URL,
        help="Show page URL",
    )
    parser.add_argument(
        "--out",
        default="les_nuits_dump.csv",
        help="Output CSV",
    )
    parser.add_argument(
        "--since",
        type=parse_since,
        default=0,
        metavar='"ISO-8601-DATETIME"',
        help=(
            "Only write records newer than this date/time. "
            "Example: --since '2026-05-02T13:04:50+00:00'. "
            "Timezone-naive values are interpreted as UTC. "
            "If omitted, fetch the complete catalogue."
        ),
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        help="GraphQL page size (default 50)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=90,
        help="Read timeout seconds per request (default 90)",
    )
    parser.add_argument(
        "--no-sleep",
        action="store_true",
        help="Do not sleep between pages",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.getenv("RADIOFRANCE_API_KEY")
    if not api_key:
        print(
            "ERROR: provide --api-key or set RADIOFRANCE_API_KEY",
            file=sys.stderr,
        )
        return 2

    rows = list(
        fetch_all(
            api_key=api_key,
            show_url=args.show_url,
            since_ts=args.since,
            page_size=max(1, args.page_size),
            sleep_sec=0.0 if args.no_sleep else 0.2,
            total_timeout=max(30, args.timeout),
        )
    )

    rows.sort(
        key=lambda row: (
            int(row["published_ts"] or 0),
            row["id"],
        )
    )

    fieldnames = [
        "published_iso",
        "published_ts",
        "title",
        "description",
        "web_url",
        "podcast_title",
        "podcast_url",
        "player_url",
        "id",
    ]

    with open(args.out, "w", newline="", encoding="utf-8") as file_obj:
        writer = csv.DictWriter(file_obj, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} records to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
