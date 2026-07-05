#!/usr/bin/env python3
"""LLM-based relevance filter for preprint candidates.

Reads the JSON output of a scanner (biorxiv_scan.py or chemrxiv_scan.py),
batches the candidate abstracts, and asks a cheap LLM (via OpenRouter) to
judge each one against the user's "interesting" criteria defined in
settings.json. Emits a curated Markdown list + JSON.

Why two stages? A scanner cuts ~thousands of weekly papers down to a few
hundred with a fast keyword scorer (free, instant). This script then spends
a few cents having an LLM make the nuanced "is this actually interesting to
*me*?" call on the shortlist -- with a one-line reason for each keep.

The system prompt lives in settings.json under "system_prompt", so you edit
it once and it applies to both sources.

OpenRouter key is read from the OPENROUTER_API_KEY env var. Load it with:
    source ~/.pi/agent/skills/scripts/load-key.sh
(which just exports it from ~/.pi/agent/auth.json, where pi stores it).

No third-party dependencies -- stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import DEFAULT_SETTINGS, load_settings

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_SYSTEM_PROMPT = (
    "You are a curator for a computational drug-discovery researcher's weekly "
    "reading list. Edit settings.json's `system_prompt` to customise."
)


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    # fall back to pi's auth.json directly (same logic as the skill's load-key.sh)
    auth = Path.home() / ".pi" / "agent" / "auth.json"
    if auth.exists():
        try:
            return json.loads(auth.read_text())["openrouter"]["key"]
        except (KeyError, json.JSONDecodeError):
            pass
    raise SystemExit(
        "OPENROUTER_API_KEY not set "
        "(run: source ~/.pi/agent/skills/scripts/load-key.sh)"
    )


def call_llm(model: str, key: str, system: str, user: str,
             retries: int = 3) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 4096,
    }
    data = json.dumps(payload).encode()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/lews-homepage",
        "X-Title": "lews-homepage weekly scan",
    }
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(OPENROUTER_URL, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode())
            return body["choices"][0]["message"]["content"]
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            wait = 2 ** attempt
            print(f"  ! API error ({e}); retry in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {retries} retries: {last}")


def parse_json_response(text: str) -> dict:
    """Robustly extract a JSON object from an LLM response."""
    text = text.strip()
    # strip markdown fences if present
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    # fall back to first {...} block
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    return json.loads(text)


def make_user_prompt(batch: list[dict]) -> str:
    parts = [
        "Judge each preprint below. Respond with a JSON object of the form "
        '{"results": [ {"index": <int>, "interesting": <bool>, '
        '"topics": [<subset of the 5 topic names>], "reason": "<one short '
        'sentence>"} ... ]} where index is the [N] shown next to each paper.',
        "Topic names to use in the `topics` array (use the leading words): "
        "protein-ligand hit discovery, machine learning for comp chem, "
        "computational medicinal chemistry, ultra large virtual screening, "
        "first in class / emerging targets.",
        "",
    ]
    for i, p in enumerate(batch, 1):
        title = p.get("title", "").strip().rstrip(".")
        abstract = re.sub(r"\s+", " ", p.get("abstract", "")).strip()
        if len(abstract) > 1200:
            abstract = abstract[:1200].rsplit(" ", 1)[0] + " …"
        parts.append(f"[{i}] {title}\n{abstract}\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", help="JSON file from a scanner (biorxiv/chemrxiv)")
    p.add_argument("--settings", default=str(DEFAULT_SETTINGS),
                   help=f"Settings JSON (default: {DEFAULT_SETTINGS})")
    p.add_argument("--model", help="Override settings.model")
    p.add_argument("--batch-size", type=int, help="Override settings.batch_size")
    p.add_argument("--out", help="Write Markdown to this file (default: stdout)")
    p.add_argument("--json", help="Write judged results to this JSON file")
    return p.parse_args(argv)


def main(argv) -> int:
    args = parse_args(argv)
    s = load_settings(args.settings)
    key = load_key()

    model = args.model or s.get("model", DEFAULT_MODEL)
    batch_size = args.batch_size if args.batch_size is not None else int(s.get("batch_size", 10))
    system_prompt = s.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    papers = json.loads(Path(args.input).read_text())
    source = papers[0].get("_source", "preprint") if papers else "preprint"
    print(f"loaded {len(papers)} candidates from {args.input}", file=sys.stderr)
    print(f"source: {source}  model: {model}  batch size: {batch_size}", file=sys.stderr)

    judged = []
    for start in range(0, len(papers), batch_size):
        batch = papers[start:start + batch_size]
        n = start // batch_size + 1
        total = (len(papers) + batch_size - 1) // batch_size
        print(f"  batch {n}/{total}: judging {len(batch)} papers…", file=sys.stderr)
        user = make_user_prompt(batch)
        raw = call_llm(model, key, system_prompt, user)
        try:
            obj = parse_json_response(raw)
            results = obj.get("results", [])
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  ! failed to parse batch {n}: {e}", file=sys.stderr)
            results = []
        # match back by 1-based index
        by_idx = {r.get("index"): r for r in results if isinstance(r, dict)}
        for i, p in enumerate(batch, 1):
            r = by_idx.get(i, {})
            p["_llm_interesting"] = bool(r.get("interesting", False))
            p["_llm_topics"] = r.get("topics", []) or []
            p["_llm_reason"] = (r.get("reason") or "").strip()
            judged.append(p)

    keeps = [p for p in judged if p["_llm_interesting"]]
    keeps.sort(key=lambda p: p.get("_score", 0), reverse=True)
    rejects = [p for p in judged if not p["_llm_interesting"]]
    rejects.sort(key=lambda p: p.get("_score", 0), reverse=True)
    print(f"\nLLM kept {len(keeps)} / {len(judged)} (rejected {len(rejects)})", file=sys.stderr)

    if args.json:
        out = {"keeps": keeps, "rejects": rejects}
        Path(args.json).write_text(json.dumps(out, indent=2))
        print(f"wrote JSON → {args.json}", file=sys.stderr)

    # --- picks markdown ---
    body = [f"# {source} weekly picks ({len(keeps)} papers, judged by {model})",
            f"Curated from {len(judged)} keyword-filtered candidates.\n"]
    for i, p in enumerate(keeps, 1):
        body.append(fmt_paper_md(p, i, source))
        body.append("")
    md = "\n".join(body)
    if args.out:
        Path(args.out).write_text(md)
        print(f"wrote Markdown → {args.out}", file=sys.stderr)
    else:
        print(md)

    # --- rejects markdown (always written next to picks, for browsing) ---
    rejects_md = [
        f"# {source} weekly rejects ({len(rejects)} papers filtered out by {model})",
        f"These {len(rejects)} candidates were keyword-scored but rejected by "
        f"the LLM. Browse to spot false negatives; if too aggressive, edit "
        f"`settings.json`'s `system_prompt` to be more inclusive.\n",
    ]
    for i, p in enumerate(rejects, 1):
        rejects_md.append(fmt_paper_md(p, i, source, rejected=True))
        rejects_md.append("")
    rmd = "\n".join(rejects_md)
    if args.out:
        # derive rejects path: foo_picks.md → foo_rejects.md
        rejects_path = args.out.replace("_picks", "_rejects")
        if rejects_path == args.out:  # no _picks in name, append
            rejects_path = str(Path(args.out).with_suffix("")) + "_rejects.md"
        Path(rejects_path).write_text(rmd)
        print(f"wrote rejects → {rejects_path}", file=sys.stderr)
    return 0


def fmt_paper_md(p: dict, rank: int, source: str, rejected: bool = False) -> str:
    """Render one paper (kept or rejected) as a markdown block."""
    title = p["title"].strip().rstrip(".")
    doi = p["doi"]
    url = p.get("url") or f"https://doi.org/{doi}"
    topics = ", ".join(p["_llm_topics"]) or "—"
    reason_label = "why not" if rejected else "why"
    lines = [
        f"## {rank}. {title}",
        f"- **{p.get('date','')}** · {p.get('category','') or source}",
    ]
    if p.get("pdb_ids"):
        lines.append(f"- PDB: {', '.join(p['pdb_ids'])}")
    lines += [
        f"- {reason_label}: {p['_llm_reason']}",
        f"- topics: {topics}",
        f"- {url}",
    ]
    abstract = re.sub(r"\s+", " ", p.get("abstract", "")).strip()
    if abstract:
        lines.append(f"- abstract: {abstract}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
