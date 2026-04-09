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
DEFAULT_SHOW_URL = "https://www.radiofrance.fr/franceculture/podcasts/les-nuits-de-france-culture"

# --- GraphQL ---
GQL_SMOKE = """
query Smoke($url: String!) {
  showByUrl(url: $url) { id title url }
}
"""

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
        podcastEpisode { title url playerUrl }
      }
    }
  }
}
"""

def to_iso(ts):
    if ts is None or ts == "":
        return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()

def make_session(total_timeout, retries=6, backoff=0.8):
    """Session with robust retries on timeouts/5xx."""
    sess = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        backoff_factor=backoff,      # exponential: 0.8, 1.6, 3.2, ...
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.request_timeout = total_timeout
    return sess

def gql_post(sess, api_key, query, variables):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-token": api_key,
    }
    # Give the server time; Radio France can be slow on large pages
    timeout = getattr(sess, "request_timeout", 60)
    r = sess.post(API_URL, json={"query": query, "variables": variables}, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if "errors" in data and data["errors"]:
        msgs = "; ".join(e.get("message", "GraphQL error") for e in data["errors"])
        raise RuntimeError(f"GraphQL returned errors: {msgs}")
    return data["data"]

def fetch_all(api_key, show_url, page_size=50, sleep_sec=0.2, total_timeout=90):
    sess = make_session(total_timeout=total_timeout)

    # Smoke test (fast) — validates key + URL
    _ = gql_post(sess, api_key, GQL_SMOKE, {"url": show_url})

    after = None
    count = 0
    while True:
        data = gql_post(
            sess,
            api_key,
            GQL_DIFFUSIONS,
            {"url": show_url, "first": page_size, "after": after},
        )
        edges = (data.get("diffusionsOfShowByUrl") or {}).get("edges") or []
        if not edges:
            break

        for e in edges:
            node = e.get("node") or {}
            yield {
                "id": node.get("id", ""),
                "title": node.get("title", "") or "",
                "description": node.get("standFirst", "") or "",
                "published_ts": node.get("published_date"),
                "published_iso": to_iso(node.get("published_date")),
                "web_url": node.get("url", "") or "",
                "podcast_title": ((node.get("podcastEpisode") or {}).get("title")) or "",
                "podcast_url": ((node.get("podcastEpisode") or {}).get("url")) or "",
                "player_url": ((node.get("podcastEpisode") or {}).get("playerUrl")) or "",
            }
            count += 1

        after = edges[-1].get("cursor")
        print(f"Fetched {count} items so far (cursor={after[:10]+'…' if after else 'None'})", file=sys.stderr)
        if sleep_sec:
            time.sleep(sleep_sec)

def main():
    ap = argparse.ArgumentParser(description="Dump all 'Les Nuits de France Culture' episodes.")
    ap.add_argument("--api-key", help="Radio France Open API key or env RADIOFRANCE_API_KEY")
    ap.add_argument("--show-url", default=DEFAULT_SHOW_URL, help="Show page URL")
    ap.add_argument("--out", default="les_nuits_dump.csv", help="Output CSV")
    ap.add_argument("--page-size", type=int, default=50, help="GraphQL page size (default 50)")
    ap.add_argument("--timeout", type=int, default=90, help="Read timeout seconds per request (default 90)")
    ap.add_argument("--no-sleep", action="store_true", help="Do not sleep between pages")
    args = ap.parse_args()

    api_key = args.api_key or os.getenv("RADIOFRANCE_API_KEY")
    if not api_key:
        print("ERROR: Provide --api-key or set RADIOFRANCE_API_KEY", file=sys.stderr)
        sys.exit(2)

    rows = list(fetch_all(
        api_key=api_key,
        show_url=args.show_url,
        page_size=max(1, args.page_size),
        sleep_sec=0.0 if args.no_sleep else 0.2,
        total_timeout=max(30, args.timeout),
    ))

    # Sort oldest → newest
    rows.sort(key=lambda r: (int(r["published_ts"] or 0), r["id"]))

    fieldnames = [
        "published_iso","published_ts","title","description",
        "web_url","podcast_title","podcast_url","player_url","id"
    ]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} records to {args.out}")

if __name__ == "__main__":
    main()

