"""
build_corpus.py - vocabulary + extracted names -> word2vec training corpus.

One recipe becomes one "sentence": a list of canonical ingredient tokens. This is
the file train.py consumes.

All this does is look each recipe's extracted names up in
`surface_to_canonical` and underscore-join the result.

Pellegrini's canonical form is space-separated throughout Pass 1 ('flour tortilla hot'); 
underscores are applied only when ingredients are injected into a token stream. 
We follow that, which is why vocabulary_*.json holds 'red chilli powder' and this file emits
'red_chilli_powder'.

Input:  data/interim/extracted_names.json
        data/processed/vocabulary_{normalizer}.json
Output: data/processed/v0a_kerala_{normalizer}.txt
"""

import json
from pathlib import Path
from malabardb import paths

MIN_INGREDIENTS = 2   # a 1-ingredient recipe carries no co-occurrence signal


def build_corpus(normalizer_name: str = "spacy") -> tuple[list[list[str]], dict]:
    """Map each recipe's extracted names onto vocabulary tokens.

    Names absent from surface_to_canonical were dropped by Pass 1's filters
    (>3 words, or <2 chars). They are counted, not silently discarded, so the
    corpus-side cost of those filters is visible.
    """
    data = json.loads(paths.EXTRACTED_NAMES.read_text(encoding="utf-8"))
    vocab = json.loads((paths.PROCESSED / f"vocabulary_{normalizer_name}.json")
                       .read_text(encoding="utf-8"))
    surface_to_canonical = vocab["surface_to_canonical"]

    corpus, stats = [], {"oov_names": 0, "recipes_dropped_short": 0} 
    for recipe in data["recipes"]:
        tokens = []
        for name in recipe["names"]: # for each name, check if it is in the vocabulary and map to canonical form
            canonical = surface_to_canonical.get(name) 
            if canonical is None:              # filtered out by Pass 1
                stats["oov_names"] += 1
                continue
            tokens.append(canonical.replace(" ", "_")) # underscores for gensim tokenisation
        if len(tokens) >= MIN_INGREDIENTS:
            corpus.append(tokens) # only recipes with >=2 ingredients carry co-occurrence signal
        else:
            stats["recipes_dropped_short"] += 1
    return corpus, stats

def save_corpus(corpus: list[list[str]], name: str) -> Path:
    """One recipe per line, space-separated. Plain text so the corpus is diffable
    in review and decoupled from any particular training run."""
    paths.PROCESSED.mkdir(parents=True, exist_ok=True)
    out = paths.PROCESSED / f"{name}.txt"
    with out.open("w", encoding="utf-8") as f:
        for recipe in corpus:
            f.write(" ".join(recipe) + "\n")
    return out

if __name__ == "__main__":
    for normalizer_name in ("spacy", "rules"):
        corpus, stats = build_corpus(normalizer_name)
        out = save_corpus(corpus, f"v0a_kerala_{normalizer_name}")
        types = {t for r in corpus for t in r}
        tokens = sum(len(r) for r in corpus)
        print(f"[{normalizer_name:5s}] {len(corpus)} recipes, {tokens} tokens, "
              f"{len(types)} types -> {out}")
        print(f"          dropped {stats['oov_names']} names (filtered by Pass 1), "
              f"{stats['recipes_dropped_short']} recipes (<{MIN_INGREDIENTS} ingredients)")