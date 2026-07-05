#!/usr/bin/env python3
"""Scan recent bioRxiv preprints for ones relevant to computational drug discovery.

Pulls from the public bioRxiv details API
(https://api.biorxiv.org/details/biorxiv/{from}/{to}/{cursor}) over a date
window, scores each paper against a curated set of topic keywords, and prints
the hits as Markdown (and optionally writes JSON).

No third-party dependencies -- stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

from common import DEFAULT_SETTINGS, RELEVANT_CATEGORIES, load_settings, score_paper

API = "https://api.biorxiv.org/details/{server}/{frm}/{to}/{cursor}"
PAGE = 30  # papers per API page
SOURCE = "biorxiv"


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

def fetch_page(server: str, frm: str, to: str, cursor: int) -> dict:
    url = API.format(server=server, frm=frm, to=to, cursor=cursor)
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode())


def fetch_window(server: str, frm: str, to: str, limit: int | None = None) -> list[dict]:
    """Fetch all papers in [frm, to], paging through the API."""
    out: list[dict] = []
    cursor = 0
    while True:
        page = fetch_page(server, frm, to, cursor)
        msg = page["messages"][0]
        if msg.get("status") != "ok":
            raise RuntimeError(f"bioRxiv API error: {msg}")
        coll = page.get("collection", [])
        out.extend(coll)
        total = int(msg.get("total", len(out)))
        if limit and len(out) >= limit:
            return out[:limit]
        if len(coll) < PAGE or len(out) >= total:
            return out
        cursor += PAGE


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def biorxiv_url(doi: str) -> str:
    # bioRxiv DOIs look like 10.1101/2024.01.01.123456
    if doi.startswith("10.1101/"):
        return f"https://www.biorxiv.org/content/{doi}"
    return f"https://doi.org/{doi}"


def fmt_md(paper: dict, rank: int) -> str:
    title = paper["title"].strip().rstrip(".")
    doi = paper["doi"]
    url = biorxiv_url(doi)
    cat = paper.get("category", "")
    d = paper.get("date", "")
    authors = paper.get("authors", "")
    if len(authors) > 80:
        authors = authors[:80].rsplit(";", 1)[0] + "; ..."
    topics = paper["_topics"]
    lines = [
        f"## {rank}. {title}",
        f"- **{d}** · {cat}",
        f"- {authors}",
        f"- topics: {', '.join(topics)}",
        f"- {url}",
    ]
    abstract = paper.get("abstract", "").strip()
    if abstract:
        # collapse whitespace
        abstract = re.sub(r"\s+", " ", abstract)
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
    p.add_argument("--server", default="biorxiv", choices=["biorxiv", "medrxiv"],
                   help="bioRxiv or medRxiv (default: biorxiv)")
    p.add_argument("--days", type=int, help="Override settings.days")
    p.add_argument("--from", dest="frm", help="Start date YYYY-MM-DD (overrides --days)")
    p.add_argument("--to", dest="to", help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--min-score", type=int, help="Override settings.min_score")
    p.add_argument("--restrict-category", action="store_true", default=None,
                   help="Only consider papers in relevant subject categories")
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
        days = args.days if args.days is not None else int(s.get("days", 14))
        frm = (datetime.fromisoformat(to) - timedelta(days=days)).date().isoformat()

    min_score = args.min_score if args.min_score is not None else int(s.get("min_score", 3))
    restrict = args.restrict_category if args.restrict_category is not None else bool(s.get("restrict_category", False))

    print(f"# bioRxiv scan: {frm} → {to}", file=sys.stderr)
    print(f"fetching from {args.server} API…", file=sys.stderr)

    papers = fetch_window(args.server, frm, to, limit=args.limit)
    print(f"fetched {len(papers)} papers", file=sys.stderr)

    scored = []
    for p in papers:
        if restrict and p.get("category", "").lower() not in RELEVANT_CATEGORIES:
            continue
        score, topics, matches = score_paper(p.get("title", ""), p.get("abstract", ""))
        if score >= min_score:
            p["_score"] = score
            p["_topics"] = topics
            p["_topic_scores"] = matches
            p["_source"] = SOURCE
            p["url"] = biorxiv_url(p["doi"])
            scored.append(p)

    scored.sort(key=lambda p: p["_score"], reverse=True)
    print(f"{len(scored)} papers scored >= {min_score}", file=sys.stderr)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(scored, fh, indent=2)
        print(f"wrote JSON → {args.json}", file=sys.stderr)

    body = [f"# bioRxiv scan: {frm} → {to}",
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
