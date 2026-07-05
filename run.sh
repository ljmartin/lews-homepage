#!/usr/bin/env bash
# Run the full weekly scan: each source scanner, then the LLM filter on each.
#
# Usage:
#   ./run.sh                  # scan all sources, last N days (from settings.json)
#   ./run.sh biorxiv          # just one source
#   ./run.sh --days 14        # override the window
#
# Output lands in out/ :  <src>.json (keyword candidates)
#                        <src>.md   (keyword markdown)
#                        <src>_picks.json  (LLM keeps+rejects)
#                        <src>_picks.md   (final curated list)
#
# OpenRouter key is loaded from the pi skill's load-key.sh. Crossref
# (chemrxiv) prefers a contact email in CROSSREF_EMAIL.

set -euo pipefail
cd "$(dirname "$0")"
mkdir -p out

# --- args -------------------------------------------------------------------
SOURCES=(biorxiv chemrxiv rcsb)
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    biorxiv|chemrxiv|rcsb) SOURCES=("$1"); shift ;;
    --days) EXTRA+=(--days "$2"); shift 2 ;;
    --from) EXTRA+=(--from "$2"); shift 2 ;;
    --to)   EXTRA+=(--to   "$2"); shift 2 ;;
    --model) EXTRA_FILTER+=(--model "$2"); shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- load OpenRouter key ----------------------------------------------------
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  if [[ -f ~/.pi/agent/skills/scripts/load-key.sh ]]; then
    # shellcheck disable=SC1091
    source ~/.pi/agent/skills/scripts/load-key.sh
  else
    echo "OPENROUTER_API_KEY not set and ~/.pi/agent/skills/scripts/load-key.sh missing" >&2
    exit 1
  fi
fi

# --- run each source --------------------------------------------------------
for src in "${SOURCES[@]}"; do
  echo
  echo "=========================================="
  echo "  $src  (scan + filter)"
  echo "=========================================="
  python3 "${src}_scan.py" ${EXTRA[@]:-} \
    --json "out/${src}.json" --out "out/${src}.md"
  python3 llm_filter.py "out/${src}.json" ${EXTRA_FILTER[@]:-} \
    --out "out/${src}_picks.md" --json "out/${src}_picks.json"
done

echo
echo "done. picks:"
for src in "${SOURCES[@]}"; do echo "  out/${src}_picks.md"; done

# --- publish to docs/ (frontmattered markdown, rendered client-side) ---
if [[ -z "${NO_PUBLISH:-}" ]]; then
  echo
  echo "=========================================="
  echo "  publishing to docs/"
  echo "=========================================="
  python3 publish.py
fi
