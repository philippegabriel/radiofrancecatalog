#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dump France Culture 'Fictions' podcasts since origin, writing incrementally to CSV.

Strategy:
1) List all shows for FRANCECULTURE.
2) Keep only shows whose page URL contains '/franceculture/podcasts/fictions'.
3) For each show, paginate diffusionsOfShowByUrl and append rows as they arrive.

All code comments are in English (per user preference).
"""
import argparse, csv, os, sys, time, json, re
from datetime import datetime, timezone
from collections.abc import Iterable
from typing import cast
from radiofrance_types import FictionRow, GraphQLData, GraphQLVariables, RadioFranceNode, Timestamp

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL   = "https://openapi.radiofrance.fr/v1/graphql"
MAX_FIRST = 100  # GraphQL 'first' cap

# -------- GraphQL (Brand enum as LITERAL) --------

GQL_SHOWS_FC = """
query FCShows($first: Int!, $after: String) {
  shows(station: FRANCECULTURE, first: $first, after: $after) {
    edges {
      cursor
      node { id title url }
    }
  }
}
"""

GQL_DIFFUSIONS_OF_SHOW = """
query DiffusionsOfShowByUrl($url: String!, $first: Int!, $after: String) {
  diffusionsOfShowByUrl(url: $url, first: $first, after: $after) {
    edges {
      cursor
      node {
        id
        title
        url
        standFirst
        published_date
        podcastEpisode { id title url playerUrl created duration }
      }
    }
  }
}
"""

# -------- Helpers --------

def dbg(level: int, want: int, *args: object) -> None:
    if level >= want:
        print(*args, file=sys.stderr)

def make_session(timeout_sec: int, retries: int = 6, backoff: float = 0.8) -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=retries, connect=retries, read=retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter); s.mount("http://", adapter)
    setattr(s, "request_timeout", timeout_sec)
    return s

def gql_post(sess: requests.Session, api_key: str, query: str, variables: GraphQLVariables, debug: int, tag: str) -> GraphQLData:
    headers = {"Content-Type":"application/json","Accept":"application/json","x-token":api_key}
    payload = {"query": query, "variables": variables}
    try:
        r = sess.post(API_URL, json=payload, headers=headers, timeout=getattr(sess, "request_timeout", 60))
    except requests.RequestException as e:
        dbg(debug, 1, f"[{tag}] network error:", e); raise
    if r.status_code >= 400:
        dbg(debug, 1, f"[{tag}] HTTP {r.status_code}")
        try: dbg(debug, 2, f"[{tag}] error body:", json.dumps(r.json(), ensure_ascii=False)[:2000])
        except Exception: dbg(debug, 2, f"[{tag}] error text:", (r.text or "")[:2000])
        r.raise_for_status()
    data = r.json()
    if "errors" in data and data["errors"]:
        dbg(debug, 1, f"[{tag}] GraphQL errors:", data["errors"])
        raise RuntimeError("; ".join(e.get("message","GraphQL error") for e in data["errors"]))
    return cast(GraphQLData, data.get("data") or {})

def to_iso(ts: Timestamp) -> str:
    if not ts: return ""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()

def ensure_header(csv_path: str, fieldnames: list[str]) -> None:
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0: return
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames); w.writeheader()

def load_seen_ids(csv_path: str, debug: int) -> set[str]:
    seen: set[str] = set()
    if not os.path.exists(csv_path): return seen
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("id"): seen.add(row["id"])
        dbg(debug, 2, f"[resume] loaded {len(seen)} existing ids from {csv_path}")
    except Exception as e:
        dbg(debug, 1, f"[resume] WARNING: cannot read {csv_path}: {e}")
    return seen

def append_row(csv_path: str, fieldnames: list[str], row: FictionRow) -> None:
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writerow(row); f.flush(); os.fsync(f.fileno())

# -------- Core --------

def iter_fc_shows(sess: requests.Session, api_key: str, page_size: int, debug: int) -> Iterable[RadioFranceNode]:
    """Iterate all France Culture shows (paged)."""
    page_size = min(MAX_FIRST, max(1, page_size))
    after = None; page = 0
    while True:
        page += 1
        dbg(debug, 2, f"[shows] requesting first={page_size} after={after!r}")
        data = gql_post(sess, api_key, GQL_SHOWS_FC, {"first": page_size, "after": after}, debug, tag="shows")
        edges = (data.get("shows") or {}).get("edges") or []
        dbg(debug, 1, f"[shows] page {page}: edges={len(edges)} after={after!r}")
        if not edges: break
        for e in edges:
            yield e.get("node") or {}
        after = edges[-1].get("cursor")
        if not after: break

def iter_diffusions_of_show(sess: requests.Session, api_key: str, show_url: str, page_size: int, debug: int, sleep_sec: float) -> Iterable[RadioFranceNode]:
    """Iterate all diffusions for a given show URL (paged)."""
    page_size = min(MAX_FIRST, max(1, page_size))
    after = None; page = 0
    while True:
        page += 1
        dbg(debug, 2, f"[diffusions] {show_url} first={page_size} after={after!r}")
        data = gql_post(sess, api_key, GQL_DIFFUSIONS_OF_SHOW,
                        {"url": show_url, "first": page_size, "after": after}, debug, tag="diffusionsOfShow")
        edges = (data.get("diffusionsOfShowByUrl") or {}).get("edges") or []
        dbg(debug, 1, f"[diffusions] page {page}: edges={len(edges)}")
        if not edges: break
        for e in edges:
            yield e.get("node") or {}
        after = edges[-1].get("cursor")
        if sleep_sec: time.sleep(sleep_sec)
        if not after: break

def main() -> None:
    ap = argparse.ArgumentParser(description="Dump France Culture 'Fictions' podcasts (incremental CSV).")
    ap.add_argument("--api-key", help="Radio France Open API key or env RADIOFRANCE_API_KEY")
    ap.add_argument("--out", default="fc_fictions.csv", help="Output CSV")
    ap.add_argument("--page-size", type=int, default=80, help="GraphQL page size (<=100)")
    ap.add_argument("--timeout", type=int, default=120, help="Per-request timeout seconds")
    ap.add_argument("--no-sleep", action="store_true", help="Do not sleep between pages")
    ap.add_argument("--debug", type=int, default=1, help="Debug 0-quiet,1-info,2-trace")
    # Allow custom include pattern just in case, but default targets FC 'fictions-*'
    ap.add_argument("--include-pattern", default=r"/franceculture/podcasts/fictions", help="Regex to select FC 'Fictions' shows by URL")
    args = ap.parse_args()

    api_key = args.api_key or os.getenv("RADIOFRANCE_API_KEY")
    if not api_key:
        print("ERROR: Provide --api-key or set RADIOFRANCE_API_KEY", file=sys.stderr); sys.exit(2)

    sess = make_session(timeout_sec=max(30, args.timeout))
    sleep_sec = 0.0 if args.no_sleep else 0.2
    debug = max(0, min(args.debug, 2))
    inc_re = re.compile(args.include_pattern)

    # Prepare CSV + resume
    fieldnames = [
        "published_iso","published_ts","title","description","web_url",
        "podcast_title","podcast_url","player_url","podcast_duration",
        "id","show_title","show_url"
    ]
    ensure_header(args.out, fieldnames)
    seen_ids = load_seen_ids(args.out, debug)

    # 1) Discover FC 'Fictions' shows
    shows = []
    for sh in iter_fc_shows(sess, api_key, page_size=args.page_size, debug=debug):
        url = (sh.get("url") or "")
        if url and inc_re.search(url):
            shows.append(sh)
    if debug >= 1:
        dbg(debug, 1, f"[shows] selected {len(shows)} France Culture 'Fictions' show(s)")
        if debug >= 2:
            for s in shows[:8]:
                dbg(debug, 2, f"[shows] kept: {s.get('title')!r} — {s.get('url')}")

    # 2) Walk each selected show and stream-write rows
    total_seen = 0; kept = 0; skipped_no_podcast = 0; skipped_seen = 0
    for sh in shows:
        show_title = sh.get("title") or ""
        show_url   = sh.get("url") or ""
        if not show_url: continue

        for node in iter_diffusions_of_show(sess, api_key, show_url, page_size=args.page_size, debug=debug, sleep_sec=sleep_sec):
            total_seen += 1
            pe = node.get("podcastEpisode")
            if not pe:
                skipped_no_podcast += 1
                if debug >= 2 and skipped_no_podcast <= 5:
                    dbg(debug, 2, f"[skip] no podcastEpisode id={node.get('id')} title={node.get('title')!r}")
                continue
            nid = node.get("id")
            if not nid or nid in seen_ids:
                skipped_seen += 1
                continue

            row: FictionRow = {
                "published_iso": to_iso(node.get("published_date")),
                "published_ts": node.get("published_date") or "",
                "title": node.get("title") or "",
                "description": node.get("standFirst") or "",
                "web_url": node.get("url") or "",
                "podcast_title": pe.get("title") or "",
                "podcast_url": pe.get("url") or "",
                "player_url": pe.get("playerUrl") or "",
                "podcast_duration": pe.get("duration") or "",
                "id": nid,
                "show_title": show_title,
                "show_url": show_url,
            }
            append_row(args.out, fieldnames, row)
            seen_ids.add(nid); kept += 1
            if kept % 50 == 0 and debug >= 1:
                dbg(debug, 1, f"[progress] kept={kept} total_seen={total_seen} skipped(no_podcast={skipped_no_podcast}, seen={skipped_seen})")

    dbg(debug, 1, f"Done. Wrote {kept} new rows to {args.out} (seen={total_seen}, skipped no_podcast={skipped_no_podcast}, seen-dup={skipped_seen})")

if __name__ == "__main__":
    main()
