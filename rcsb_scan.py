#!/usr/bin/env python3
"""Scan recent RCSB PDB structure releases for ones relevant to computational
drug discovery.

A "release" is a PDB entry that became publicly available in the last N days.
Because a single publication often accompanies several PDB entries (e.g. the
apo structure plus a few ligand-bound complexes), entries are **grouped by
their primary citation DOI** before scoring -- so one paper with five PDB
codes is judged once, not five times. Entries with no primary citation are
each their own group (they may still be interesting on their own, e.g. a
structure released ahead of publication).

Data is fetched from RCSB's public REST APIs (no package needed):

  * search API  https://search.rcsb.org/rcsbsearch/v2/query  (find entries
    released in the date window, return their PDB IDs)
  * data API    https://data.rcsb.org/graphql                 (pull title,
    method, resolution, primary citation, PubMed abstract, organism, etc.
    for each entry)

No third-party dependencies -- stdlib only. Same two-stage shape as the other
scanners: keyword pre-filter here, LLM judge in llm_filter.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta

from common import DEFAULT_SETTINGS, load_settings, score_paper, strip_jats

SOURCE = "rcsb"
SEARCH_URL = "https://search.rcsb.org/rcsbsearch/v2/query"
DATA_URL = "https://data.rcsb.org/graphql"
BATCH = 50  # entry IDs per GraphQL request (keep well under any size limit)


# ---------------------------------------------------------------------------
# API access
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: dict, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{url} failed after retries: {last}")


def search_entry_ids(frm: str, to: str) -> list[str]:
    """Return PDB entry IDs released in [frm, to] (inclusive), newest first."""
    payload = {
        "query": {
            "type": "group",
            "logical_operator": "and",
            "nodes": [
                {"type": "terminal", "service": "text", "parameters": {
                    "attribute": "rcsb_accession_info.initial_release_date",
                    "operator": "greater_or_equal", "value": frm}},
                {"type": "terminal", "service": "text", "parameters": {
                    "attribute": "rcsb_accession_info.initial_release_date",
                    "operator": "less_or_equal", "value": to}},
            ],
        },
        "return_type": "entry",
        "request_options": {
            "paginate": {"start": 0, "rows": 10000},
            "sort": [{"sort_by": "rcsb_accession_info.initial_release_date",
                      "direction": "desc"}],
        },
    }
    body = _post_json(SEARCH_URL, payload)
    return [r["identifier"] for r in body.get("result_set", [])]


# GraphQL query: one nested request per batch of entry IDs.
GRAPHQL_QUERY = """\
query($ids: [String!]!) {
  entries(entry_ids: $ids) {
    rcsb_id
    struct { title }
    exptl { method }
    rcsb_entry_info { resolution_combined experimental_method }
    rcsb_accession_info { initial_release_date }
    rcsb_primary_citation {
      title
      pdbx_database_id_DOI
      rcsb_authors
      journal_abbrev
      year
      pdbx_database_id_PubMed
    }
    pubmed { rcsb_pubmed_abstract_text }
    polymer_entities {
      rcsb_entity_source_organism { scientific_name }
      rcsb_polymer_entity_name_com { name }
    }
  }
}"""


def fetch_metadata(entry_ids: list[str]) -> list[dict]:
    """Batch-fetch metadata via the GraphQL data API."""
    out: list[dict] = []
    for i in range(0, len(entry_ids), BATCH):
        chunk = entry_ids[i:i + BATCH]
        body = _post_json(DATA_URL, {"query": GRAPHQL_QUERY,
                                     "variables": {"ids": chunk}})
        entries = (body.get("data") or {}).get("entries") or []
        out.extend(entries)
        print(f"  fetched {len(out)}/{len(entry_ids)}", file=sys.stderr)
        time.sleep(0.3)  # be gentle
    return out


# ---------------------------------------------------------------------------
# Normalisation + grouping
# ---------------------------------------------------------------------------

def safe_get(d, *keys):
    """Walk nested dicts/lists that may be None."""
    for k in keys:
        if not isinstance(d, dict):
            return ""
        d = d.get(k)
        if d is None:
            return ""
    return d or ""


def join_list(x, sep="; "):
    if isinstance(x, list):
        return sep.join(str(i) for i in x)
    return x or ""


def normalize_entry(e: dict) -> dict:
    """Flatten one raw GraphQL entry into a small record."""
    methods = [m.get("method") for m in (e.get("exptl") or []) if m.get("method")]
    res = (e.get("rcsb_entry_info") or {}).get("resolution_combined") or []
    cit = e.get("rcsb_primary_citation") or {}
    abstract = strip_jats(safe_get(e, "pubmed", "rcsb_pubmed_abstract_text"))
    organisms: list[str] = []
    protein_names: list[str] = []
    for pe in (e.get("polymer_entities") or []):
        for org in (pe.get("rcsb_entity_source_organism") or []):
            if org.get("scientific_name") and org["scientific_name"] not in organisms:
                organisms.append(org["scientific_name"])
        nm_field = pe.get("rcsb_polymer_entity_name_com")
        nm_items = nm_field if isinstance(nm_field, list) else ([nm_field] if nm_field else [])
        for item in nm_items:
            nm = (item or {}).get("name") if isinstance(item, dict) else None
            if nm and nm not in protein_names:
                protein_names.append(nm)
    return {
        "pdb_id": e.get("rcsb_id", ""),
        "struct_title": (e.get("struct") or {}).get("title", ""),
        "methods": methods,
        "resolution": res,
        "pub_title": cit.get("title", ""),
        "pub_doi": cit.get("pdbx_database_id_DOI", ""),
        "pub_authors": cit.get("rcsb_authors", []),
        "pub_journal": cit.get("journal_abbrev", ""),
        "pub_year": cit.get("year"),
        "pubmed_id": cit.get("pdbx_database_id_PubMed"),
        "abstract": abstract,
        "organisms": organisms,
        "protein_names": protein_names,
        "release_date": (e.get("rcsb_accession_info") or {})
                         .get("initial_release_date", ""),
    }


def group_by_publication(entries: list[dict]) -> list[dict]:
    """Group normalized entries by primary citation DOI.

    Entries sharing a DOI collapse into one group (one publication, many PDB
    codes). Entries with no DOI each form their own group.
    """
    by_doi: dict[str, list[dict]] = defaultdict(list)
    no_doi: list[dict] = []
    for e in entries:
        doi = (e["pub_doi"] or "").strip().lower()
        if doi:
            by_doi[doi].append(e)
        else:
            no_doi.append(e)

    groups: list[dict] = [build_group(doi, members) for doi, members in by_doi.items()]
    for e in no_doi:
        groups.append(build_group("", [e]))
    return groups


def build_group(doi: str, members: list[dict]) -> dict:
    """Assemble one 'paper' record from a group of PDB entries."""
    # Prefer the publication title; fall back to the first structure's title.
    pub_title = ""
    for m in members:
        if m["pub_title"]:
            pub_title = m["pub_title"]
            break
    if not pub_title:
        pub_title = members[0]["struct_title"]

    abstract = members[0]["abstract"]  # all members share the same publication
    authors = join_list(members[0]["pub_authors"], "; ")
    if len(authors) > 120:
        authors = authors[:120].rsplit(";", 1)[0] + "; ..."

    pdb_ids = [m["pdb_id"] for m in members]
    methods = sorted({m for mem in members for m in mem["methods"]})
    organisms = sorted({o for mem in members for o in mem["organisms"]})
    protein_names: list[str] = []
    for mem in members:
        for p in mem["protein_names"]:
            if p not in protein_names:
                protein_names.append(p)

    # Searchable text = PubMed abstract + a compact structure summary, so the
    # scorer and LLM have context even when there's no publication abstract.
    res_vals = [r for mem in members for r in mem["resolution"]]
    res_str = f" ({min(res_vals):.2f}-{max(res_vals):.2f} A)" if res_vals else ""
    summary = (
        f"PDB entries: {', '.join(pdb_ids)}. "
        f"Structure: {members[0]['struct_title']}. "
        f"Method: {', '.join(methods) or 'n/a'}{res_str}. "
        f"Organism: {', '.join(organisms) or 'n/a'}. "
        f"Protein: {', '.join(protein_names) or 'n/a'}."
    )
    combined = (abstract + "\n" + summary).strip() if abstract else summary

    dates = [m["release_date"][:10] for m in members if m["release_date"]]
    rel_date = min(dates) if dates else ""

    return {
        "doi": doi,
        "title": pub_title.strip().rstrip("."),
        "abstract": combined,
        "date": rel_date,
        "authors": authors,
        "category": ", ".join(methods),
        "url": f"https://doi.org/{doi}" if doi else
               f"https://www.rcsb.org/structure/{pdb_ids[0]}",
        "_source": SOURCE,
        "pdb_ids": pdb_ids,
        "_summary": summary,
        "n_pdb": len(pdb_ids),
    }


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_md(paper: dict, rank: int) -> str:
    title = paper["title"]
    doi = paper["doi"]
    url = paper["url"]
    d = paper.get("date", "")
    cat = paper.get("category", "")
    pdb_ids = paper.get("pdb_ids", [])
    topics = paper["_topics"]
    pdb_links = ', '.join(
        f'[{pid}](https://www.rcsb.org/structure/{pid})' for pid in pdb_ids)
    lines = [
        f"## {rank}. {title}",
        f"- **{d}** · {cat}"
        + (f" · {paper['n_pdb']} PDB entries" if len(pdb_ids) > 1 else ""),
        f"- PDB: {pdb_links}",
        f"- {url}",
        f"- topics: {', '.join(topics)}",
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
    p.add_argument("--limit", type=int, help="Cap entries fetched (debug)")
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

    print(f"# RCSB scan: {frm} → {to}", file=sys.stderr)
    print("searching for entries released in window…", file=sys.stderr)
    entry_ids = search_entry_ids(frm, to)
    if args.limit:
        entry_ids = entry_ids[:args.limit]
    print(f"found {len(entry_ids)} entries; fetching metadata…", file=sys.stderr)

    raw = fetch_metadata(entry_ids)
    entries = [normalize_entry(e) for e in raw]
    print(f"normalized {len(entries)} entries", file=sys.stderr)

    groups = group_by_publication(entries)
    n_pub = sum(1 for g in groups if g["doi"])
    n_solo = sum(1 for g in groups if not g["doi"])
    print(f"grouped into {len(groups)} papers ({n_pub} with publication, "
          f"{n_solo} standalone)", file=sys.stderr)

    scored = []
    for g in groups:
        score, topics, matches = score_paper(g.get("title", ""), g.get("abstract", ""))
        if score >= min_score:
            g["_score"] = score
            g["_topics"] = topics
            g["_topic_scores"] = matches
            scored.append(g)

    scored.sort(key=lambda g: g["_score"], reverse=True)
    print(f"{len(scored)} papers scored >= {min_score}", file=sys.stderr)

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(scored, fh, indent=2)
        print(f"wrote JSON → {args.json}", file=sys.stderr)

    body = [f"# RCSB scan: {frm} → {to}",
            f"{len(scored)} relevant structure groups out of {len(groups)} "
            f"publications ({len(entries)} PDB entries) scanned.\n"]
    for i, g in enumerate(scored, 1):
        body.append(fmt_md(g, i))
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
