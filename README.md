# lews-homepage

[**View the live digest →**](https://ljmartin.github.io/lews-homepage/)

A weekly digest of interesting preprints and structures for a computational
drug-discovery researcher. Set the page (or its RSS feed, eventually) as your
browser homepage and never miss a release.

## Sources

| Source | Stage 1 (fetch + keyword filter) | How it's reached |
|---|---|---|
| bioRxiv | `biorxiv_scan.py` | public bioRxiv details API |
| chemRxiv | `chemrxiv_scan.py` | Crossref API (DOI prefix `10.26434`) |
| RCSB PDB | `rcsb_scan.py` | RCSB search + data REST APIs (no package) |

Each scanner pulls recent preprints over a date window, scores them against a
curated keyword set, and writes the shortlist to JSON + Markdown.

## Pipeline

```
        ┌─────────────────┐    keyword     ┌──────────────┐    LLM      ┌──────────────┐
source → │  <src>_scan.py  │ ──→ score ──→ │ candidates   │ ──→ judge ─→ │ picks .md/.json
        └─────────────────┘    (free)      │   .json/.md  │  (cents)   └──────────────┘
                                          └──────────────┘
```

1. **Keyword pre-filter** (`<src>_scan.py` via `common.score_paper`) — free,
   instant, cuts thousands of weekly papers down to a few hundred.
2. **LLM judge** (`llm_filter.py`) — a cheap model (default
   `google/gemini-3.1-flash-lite`) makes the nuanced "is this actually
   interesting to *me*?" call on each candidate, with a one-line reason.

The LLM's selection criteria live in `settings.json` under `system_prompt` —
edit once, applies to all sources.

## Quick start

```bash
# one-time: make scripts runnable
chmod +x run.sh *.py

# run the whole pipeline (both sources, scan + filter)
./run.sh

# ...or one source at a time
python3 biorxiv_scan.py  --json out/biorxiv.json --out out/biorxiv.md
python3 chemrxiv_scan.py --json out/chemrxiv.json --out out/chemrxiv.md
source ~/.pi/agent/skills/scripts/load-key.sh   # exports OPENROUTER_API_KEY
python3 llm_filter.py out/biorxiv.json --out out/biorxiv_picks.md
```

Output lands in `out/`:
- `<src>.json` / `<src>.md` — keyword-filtered candidates
- `<src>_picks.json` / `<src>_picks.md` — final LLM-curated picks

## Configuration (`settings.json`)

| key | default | what it does |
|---|---|---|
| `days` | `7` | lookback window for each scanner |
| `model` | `google/gemini-3.1-flash-lite` | OpenRouter model for the judge stage |
| `min_score` | `3` | keyword-score threshold for the candidate shortlist |
| `batch_size` | `10` | abstracts per LLM call (fewer = more reliable, more calls) |
| `restrict_category` | `false` | (biorxiv only) skip papers outside relevant subject categories |
| `system_prompt` | *(see file)* | the LLM's curation criteria — edit this to tune what counts as "interesting" |

Any of these can be overridden per-run with flags (`--days`, `--model`,
`--min-score`, `--batch-size`, `--restrict-category`).

## Keys

The LLM filter calls OpenRouter. The key is read from `OPENROUTER_API_KEY`,
which `run.sh` loads automatically from `~/.pi/agent/skills/scripts/load-key.sh`
(that just does `export OPENROUTER_API_KEY=$(jq -r '.openrouter.key'
~/.pi/agent/auth.json)`). If you're not using pi, set the env var directly.

chemRxiv is fetched via Crossref, which asks for a polite contact email:
```bash
export CROSSREF_EMAIL=you@example.com   # optional but appreciated
```

## Weekly automation

`run.sh` is what you'd schedule. It runs every source's scanner + LLM filter,
then publishes the picks and rejects to `docs/data/digest.json`. Example
crontab entry (Mondays 08:00):
```
0 8 * * 1  /path/to/lews-homepage/run.sh >> /tmp/lews.log 2>&1
```

## Website (GitHub Pages)

The `docs/` directory is a static site rendered client-side with `marked.js`,
split across two pages that share one `site.js`:

- **`index.html`** (the homepage) — shows only the **picks** (one section per
  source: bioRxiv, chemRxiv, RCSB). This is what you'd set as your browser
  homepage.
- **`rejects.html`** — shows only the **rejects**, for debugging the curation.
  Linked from the homepage's nav; a `← Picks` link brings you back.

Both pages fetch `data/digest.json` and filter by `kind` (set via
`<body data-kind="picks|rejects">`). A sticky nav at the top shows the
source links, which smooth-scroll to each section.

Source colour-coding: every section header carries a coloured badge and every
paper title (H2) gets a matching left border, so you can tell at a glance
where a result came from:

- 🟠 bioRxiv → amber (`#d97706`)
- 🔵 chemRxiv → blue (`#2563eb`)
- 🟣 RCSB → purple (`#7c3aed`)

`publish.py` is the bridge: it reads `out/<src>_picks.md` and
`out/<src>_rejects.md`, strips their H1s, and bundles them into one
`docs/data/digest.json` (ordered: all picks, then all rejects). `run.sh`
calls it automatically at the end; set `NO_PUBLISH=1` to skip.

To preview locally:
```bash
cd docs && python3 -m http.server 8000
```

For GitHub Pages, point the deploy source at the `docs/` directory (Settings →
Pages → Source: Deploy from a branch → `/docs`). `docs/data/digest.json` is
committed so the site has content to serve.

## Layout

```
settings.json       ← days, model, min_score, batch_size, system_prompt
common.py           ← shared keyword scorer (TOPICS + score_paper) + settings loader
biorxiv_scan.py     ← bioRxiv scanner (Stage 1)
chemrxiv_scan.py    ← chemRxiv scanner (Stage 1, via Crossref)
rcsb_scan.py        ← RCSB PDB scanner (Stage 1, via search + data REST APIs)
llm_filter.py       ← LLM judge (Stage 2, source-agnostic) → picks + rejects
run.sh              ← run the whole pipeline (all sources, scan + filter + publish)
publish.py          ← bridge out/*.md → docs/data/digest.json (single page)
docs/               ← static GitHub-Pages site (two pages sharing site.js)
├── index.html      ← homepage: fetches digest.json, shows picks only
├── rejects.html    ← debug page: shows rejects only
├── site.js         ← shared render logic (filters digest.json by kind)
├── stylesheet.css
└── data/digest.json ← all picks + rejects for one week (committed)
out/                ← pipeline output (candidates + picks + rejects, gitignored)
```

## Notes on data sources

- **bioRxiv** exposes a clean public JSON API at `api.biorxiv.org`. No auth,
  no Cloudflare.
- **chemRxiv** no longer has a usable public API (its own API is Cloudflare-
  blocked, and it migrated off Figshare so the old OAI-PMH route is stale).
  But every chemRxiv DOI uses prefix `10.26434` and is registered with
  Crossref, whose API is free, auth-optional, and built for this. We filter
  by DOI prefix + created-date window.
- **RCSB PDB** is queried via its public REST APIs (search API at
  `search.rcsb.org` to find entries released in the date window, GraphQL data
  API at `data.rcsb.org` to pull title/method/organism/primary-citation +
  PubMed abstract). No package needed, stdlib only. Because a single
  publication often accompanies several PDB entries, structures are **grouped
  by their primary citation DOI** before scoring — one paper with five PDB
  codes is judged once, not five times. Entries with no publication are each
  their own group.
- **JMC** — TODO.
