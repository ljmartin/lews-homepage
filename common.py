"""Shared scoring + settings logic for the lews-homepage preprint scanners.

Both biorxiv_scan.py and chemrxiv_scan.py import from here so the keyword
topology lives in one place. The LLM filter's system prompt lives in
settings.json (so it can be edited without touching code); this module just
handles the cheap first-pass keyword scorer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_SETTINGS = Path(__file__).parent / "settings.json"

# ---------------------------------------------------------------------------
# Keyword scoring
# ---------------------------------------------------------------------------
# Each topic carries a list of (regex, weight) pairs. A paper's score is the
# sum of weights for every keyword that matches its title+abstract.

TOPICS: dict[str, list[tuple[str, int]]] = {
    "protein-ligand hit discovery": [
        (r"\b(hit|hits|hit discovery|hit identification)\b", 3),
        (r"\b(fragment screening|fragment based|FBLD|FBDD)\b", 3),
        (r"\b(binding affinity|binding free energy|binding pose)\b", 2),
        (r"\b(protein[\- ]?ligand|ligand binding|ligand-protein)\b", 2),
        (r"\b(thermal shift|differential scanning fluorimetry|DSF|SPR|ITC)\b", 2),
        (r"\b(crystal structure|co[\- ]?crystal|x[\- ]?ray structure)\b", 1),
        (r"\b(affinity|Kd|Ki|IC50|EC50)\b", 1),
    ],
    "machine learning for comp chem": [
        (r"\b(machine learning|deep learning|neural network|graph neural|GNN|transformer)\b", 3),
        (r"\b(reinforcement learning|self[\- ]?supervised|contrastive learning)\b", 2),
        (r"\b(generative model|generative chemist|de novo design|molecule generation)\b", 3),
        (r"\b(active learning|bayesian optimization|QSAR)\b", 2),
        (r"\b(chemical space|molecular representation|molecular embedding|fingerprint)\b", 2),
        (r"\b(rdkit|RDKit|SMILES|SELFIES|InChI)\b", 2),
        (r"\b(protein structure prediction|AlphaFold|ESMFold|fold)\b", 1),
        (r"\b(docking|docking score|pose prediction)\b", 2),
        (r"\b(free energy perturbation|FEP|relative binding free energy|RBFE|ABFE)\b", 3),
    ],
    "computational medicinal chemistry": [
        (r"\b(medici(nal|ne) chemist|drug design|lead optimization|lead optimisation)\b", 3),
        (r"\b(structure[\- ]?based drug design|SBDD|ligand[\- ]?based drug design|LBDD)\b", 3),
        (r"\b(docking|virtual screening|pharmacophore)\b", 2),
        (r"\b(molecular dynamics|MD simulation|free energy|enhanced sampling)\b", 2),
        (r"\b(scaffold|bioisostere|lead[\- ]?like|drug[\- ]?like)\b", 2),
        (r"\b(QM/MM|quantum mechanics|DFT|semiempirical)\b", 1),
        (r"\b(selectivity|off[\- ]?target|ADMET|DMPK|pharmacokinetics)\b", 1),
    ],
    "ultra large virtual screening": [
        (r"\b(ultra[\- ]?large|billion|zinc|make[\- ]?it[\- ]?on demand|Enamine REAL)\b", 3),
        (r"\b(virtual screening|compound library|chemical library|enumeration)\b", 2),
        (r"\b(HTE|high[\- ]?throughput|HTS)\b", 1),
        (r"\b(docking at scale|distributed docking|GPU docking)\b", 3),
    ],
    "first in class / emerging targets": [
        (r"\b(first[\- ]?in[\- ]?class)\b", 3),
        (r"\b(novel target|emerging target|undrugged|undruggable)\b", 3),
        (r"\b(protein degradation|PROTAC|molecular glue|covalent inhibitor)\b", 2),
        (r"\b(allosteric|cryptic pocket|cryptic site)\b", 2),
        (r"\b(chemical probe|chemogenomic|target engagement)\b", 2),
    ],
}

# bioRxiv subject categories more likely to contain relevant work. Used only
# when --restrict-category is set. chemRxiv has no equivalent field.
RELEVANT_CATEGORIES = {
    "biochemistry",
    "bioinformatics",
    "biophysics",
    "chemical biology",
    "pharmacology and toxicology",
    "structural biology",
    "synthetic chemistry",
    "systems biology",
}


def score_paper(title: str, abstract: str) -> tuple[int, list[str], list[tuple[str, int]]]:
    """Return (total_score, matched_topics, [(topic, topic_score), ...])."""
    text = f"{title}\n{abstract}"
    total = 0
    matched_topics: list[str] = []
    matches: list[tuple[str, int]] = []
    for topic, kws in TOPICS.items():
        topic_score = 0
        for pat, weight in kws:
            if re.search(pat, text, re.IGNORECASE):
                topic_score += weight
        if topic_score > 0:
            matched_topics.append(topic)
            total += topic_score
            matches.append((topic, topic_score))
    return total, matched_topics, matches


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def load_settings(path: str | Path = DEFAULT_SETTINGS) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def strip_jats(abstract: str) -> str:
    """Strip JATS XML tags (e.g. <jats:p>) from a Crossref/abstract string."""
    if not abstract:
        return ""
    text = re.sub(r"<[^>]+>", " ", abstract)
    return re.sub(r"\s+", " ", text).strip()
