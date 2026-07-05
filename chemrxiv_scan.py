#!/usr/bin/env python3
"""Scan recent chemRxiv preprints for ones relevant to computational drug
discovery.

chemRxiv no longer exposes a usable public API (its own API is Cloudflare-
blocked, and it migrated off Figshare so the old OAI-PMH route is stale). But
every chemRxiv DOI uses prefix 10.26434 and is registered with Crossref, whose
API is free, auth-optional, and built for this. We filter by DOI prefix +
created-date window, page via cursor, and reuse the same keyword scorer as
biorxiv_scan.py so both feeds drop into the same LLM filter.

Crossref asks for a polite contact email in the User-Agent; set
  export CROSSREF_EMAIL=you@example.com
to be a good citizen (otherwise an anonymous UA is used).

No third-party dependencies -- stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from common import DEFAULT_SETTINGS, load_settings, score_paper, strip_jats

CROSSREF = "https://api.crossref.org/prefixes/10.26434/works"
PAGE = 200  # Crossref allows up to 1000; 200 is a polite page size
SOURCE = "chemrxiv"


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

def make_headers() -> dict[str, str]:
    email = os.environ.get("CROSSREF_EMAIL")
    if email:
        return {"User-Agent": f"lews-homepage/0.1 (mailto:{email})"}
    return {"User-Agent": "lews-homepage/0.1 (anonymous)"}


def fetch_page(frm: str, to: str, cursor: str = "*") -> tuple[list[dict], str | None]:
    """Fetch one page of works. Returns (items, next_cursor or None)."""
    params = {
        "filter": f"from-created-date:{frm},until-created-date:{to}",
        "rows": str(PAGE),
        "select": "DOI,title,abstract,published,author,container-title",
        "cursor": cursor,
    }
    url = CROSSREF + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=make_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read().decode())
    msg = body.get("message", {})
    items = msg.get("items", [])
    next_cursor = msg.get("next-cursor")
    return items, next_cursor


def fetch_window(frm: str, to: str, limit: int | None = None) -> list[dict]:
    """Page through Crossref until exhausted or limit hit."""
    out: list[dict] = []
    cursor = "*"
    while True:
        items, cursor = fetch_page(frm, to, cursor)
        out.extend(items)
        if limit and len(out) >= limit:
            return out[:limit]
        if not items or not cursor:
            return out
        time.sleep(0.5)  # be gentle to Crossref


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalize(item: dict) -> dict:
    """Flatten a Crossref work into the common paper shape used by the filter."""
    doi = item.get("DOI", "")
    title = ""
    if item.get("title"):
        title = item["title"][0]
    abstract = strip_jats(item.get("abstract", ""))
    pub = item.get("published", {}).get("date-parts", [[None]])
    d = pub[0] if pub and pub[0] else [None]
    date_str = "-".join(f"{x:02d}" if isinstance(x, int) else "01"
                        for x in d) if d and d[0] else ""
    # yyyy-mm-dd
    if len(date_str.split("-")) == 3 and d[0]:
        date_str = f"{d[0]:04d}-{d[1] if len(d) > 1 else 1:02d}-{d[2] if len(d) > 2 else 1:02d}"
    authors = item.get("author", [])
    author_str = "; ".join(
        f"{a.get('family', '')}, {a.get('given', '')}".strip(", ")
        for a in authors
    )
    return {
        "doi": doi,
        "title": title,
        "abstract": abstract,
        "date": date_str,
        "authors": author_str,
        "category": "",  # chemRxiv has no category field in Crossref
        "url": f"https://doi.org/{doi}",
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_md(paper: dict, rank: int) -> str:
    title = paper["title"].strip().rstrip(".")
    doi = paper["doi"]
    url = paper.get("url") or f"https://doi.org/{doi}"
    d = paper.get("date", "")
    authors = paper.get("authors", "")
    if len(authors) > 80:
        authors = authors[:80].rsplit(";", 1)[0] + "; ..."
    topics = paper["_topics"]
    lines = [
        f"## {rank}. {title}",
        f"- **{d}** · chemRxiv",
        f"- {authors}",
        f"- topics: {', '.join(topics)}",
        f"- {url}",
    ]
    abstract = paper.get("abstract", "").strip()
    if abstract:
        if len(abstract) > 600:
            abstract = abstract[:600].rsplit(" ", 1)[0] + " …"
        lines.append(f"- abstract: {abstract}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--settings", default=str(DEFAULT_SETTINGS),
                   help=f"Settings JSON (default: {DEFAULT_SETTINGS})")
    p.add_argument("--days", type=int, help="Override settings.days")
    p.add_argument("--from", dest="frm", help="Start date YYYY-MM-DD (overrides --days)")
    p.add_argument("--to", dest="to", help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--min-score", type=int, help="Override settings.min_score")
    p.add_argument("--limit", type=int, help="Cap number of papers fetched (debug)")
    p.add_argument("--json", help="Write full scored results to this JSON file")
    p.add_argument("--out", help="Write Markdown to this file (default: stdout)")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    s = load_settings(args.settings)

    to = args.to or date.today().isoformat()
    if args.frm:
        frm = args.frm
    else:
        days = args.days if args.days is not None else int(s.get("days", 7))
        frm = (datetime.fromisoformat(to) - timedelta(days=days)).date().isoformat()

    min_score = args.min_score if args.min_score is not None else int(s.get("min_score", 3))

    print(f"# chemRxiv scan: {frm} → {to}", file=sys.stderr)
    print("fetching from Crossref (DOI prefix 10.26434)…", file=sys.stderr)

    raw = fetch_window(frm, to, limit=args.limit)
    print(f"fetched {len(raw)} works", file=sys.stderr)

    papers = [normalize(r) for r in raw]
    scored = []
    for p in papers:
        score, topics, matches = score_paper(p.get("title", ""), p.get("abstract", ""))
        if score >= min_score:
            p["_score"] = score
            p["_topics"] = topics
            p["_topic_scores"] = matches
            p["_source"] = SOURCE
            scored.append(p)

    scored.sort(key=lambda p: p["_score"], reverse=True)
    print(f"{len(scored)} papers scored >= {min_score}", file=sys.stderr)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(scored, fh, indent=2)
        print(f"wrote JSON → {args.json}", file=sys.stderr)

    body = [f"# chemRxiv scan: {frm} → {to}",
            f"{len(scored)} relevant preprints out of {len(papers)} scanned.\n"]
    for i, p in enumerate(scored, 1):
        body.append(fmt_md(p, i))
        body.append("")
    md = "\n".join(body)

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(md)
        print(f"wrote Markdown → {args.out}", file=sys.stderr)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
