#!/usr/bin/env python3
"""Publish out/*.md digests into site/data/digest.json as a single page.

Reads out/<src>_picks.md and out/<src>_rejects.md (produced by run.sh),
strips the leading H1, and bundles them into one JSON file consumed by
site/index.html — which renders all sections on one page with source
color-coding and anchor nav.

Sections are ordered: all picks (biorxiv, chemrxiv, rcsb) then all rejects,
so the nav's "Rejects" link jumps past the keepers.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "out"
SITE_DIR = ROOT / "site"
DATA_DIR = SITE_DIR / "data"
DIGEST_FILE = DATA_DIR / "digest.json"

SOURCES = ["biorxiv", "chemrxiv", "rcsb"]
KINDS = ["picks", "rejects"]


def derive_date() -> str:
    """Pull the YYYY-MM-DD from the first picks file (uses the 'to' date)."""
    for src in SOURCES:
        p = OUT_DIR / f"{src}_picks.md"
        if p.exists():
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", p.read_text(encoding="utf-8"))
            if dates:
                return dates[-1]
    return ""


def strip_h1(md: str) -> str:
    """Drop the leading '# ...' line so the section has no duplicate H1."""
    return re.sub(r"\A#\s+.*\n", "", md, count=1)


def count_papers(md: str) -> int:
    """Count H2 entries (each paper is an H2)."""
    return len(re.findall(r"(?m)^##\s+", md))


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--date", help="Use this date (YYYY-MM-DD) instead of deriving")
    args = p.parse_args(argv)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    date = args.date or derive_date()
    if not date:
        print("no out/*_picks.md found; run ./run.sh first", file=sys.stderr)
        return 1

    sections = []
    for kind in KINDS:
        for src in SOURCES:
            f = OUT_DIR / f"{src}_{kind}.md"
            if not f.exists():
                continue
            md = strip_h1(f.read_text(encoding="utf-8"))
            sections.append({
                "id": f"{src}-{kind}",
                "source": src,
                "kind": kind,
                "count": count_papers(md),
                "markdown": md,
            })

    digest = {"date": date, "sections": sections}
    DIGEST_FILE.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    print(f"wrote {len(sections)} sections to {DIGEST_FILE.relative_to(ROOT)}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
