"""
normalize_vocab.py - candidate names -> controlled vocabulary.

Reproduces Pellegrini et al. (2021) `normalisation/generate_final_clean_ingredients.py`
and `normalisation/helpers/recipe_normalizer.py`

Input:  data/interim/extracted_names.json   (from extract_names.py)
Output: data/processed/vocabulary_{spacy,rules}.json
"""

import json
from pathlib import Path

IN_PATH = Path("data/interim/extracted_names.json")
OUT_DIR = Path("data/processed")

# Verbatim from Pellegrini, helpers/recipe_normalizer.py.
# Note '.': 'NOUN': lemmatizing punctuation-tagged tokens as nouns appears
# incidental, but it is in the published code, so it is reproduced and flagged.
TAG_MAPPING = {
    'NN': 'NOUN', 'NNS': 'NOUN', 'NNP': 'NOUN', 'NNPS': 'NOUN', '.': 'NOUN',
    'JJS': 'ADJ', 'JJR': 'ADJ',
    'VBD': 'VERB', 'VBG': 'VERB', 'VBN': 'VERB', 'VBZ': 'VERB', 'VBP': 'VERB',
}

# The gate. Only these coarse tags are lemmatized; everything else is lowercased
# only. This preserves 'dried'/'ground'/'chopped' — inflection is noise, derivation
# is signal (README §4). It also protects transliterations: when the tagger misfires
# on 'hing' or 'ghee' it misfires toward VERB, which the gate leaves alone.
LEMMATIZATION_TYPES = ['NOUN']

# Pellegrini's post-filters, applied identically to both normalizers.
MAX_WORDS = 3          # len(elem.split()) <= 3
MIN_CHARS = 2          # len(elem) > 1
MIN_TOKEN_CHARS = 2    # len(token) > 1

# NOT REPRODUCED: Pellegrini's custom_removel_component (recipe_normalizer.py).
# Worth revisiting if Stage 0 is replaced by a sequence tagger, or if the vocabulary
# is ever built from a source that has not been through Stage 0 — in either case
# this component becomes a useful independent safety net.


class SpacyNormalizer:
    """Port of RecipeNormalizer(lemmatization_types=['NOUN']) to spaCy 3.x."""

    name = "spacy"

    def __init__(self):
        import spacy
        # en_core_web_sm, not _lg. Pellegrini loads _lg but never touches the
        # vectors, so the larger model buys nothing here. Divergence D-006.
        self.nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
        self.lemmatizer = self.nlp.get_pipe("lemmatizer")

    def _lemmatize(self, token) -> str:
        """Lemmatize one token under the TAG_MAPPING gate.
        force token.pos_ from TAG_MAPPING, then call the rule lemmatizer.
        """
        coarse = TAG_MAPPING.get(token.tag_, token.tag_)
        if coarse not in LEMMATIZATION_TYPES:
            return token.text.lower()
        token.pos_ = coarse                        # rule_lemmatize reads pos_, not tag_
        return self.lemmatizer.rule_lemmatize(token)[0].lower()  # [0]: as Pellegrini does

    def normalize_many(self, names: list[str]) -> list[str]:
        """Normalize a batch. Batched because spaCy's .pipe is far faster than
        per-string calls, matching the reference implementation's use of .pipe.

        Input is LOWERCASED before tagging. This diverges from Pellegrini, who tags
        original case and lowercases only at lemmatization. his input (Yummly) was
        already lowercase, ours is title-cased from the Kaggle sheet. The tagger is
        case-sensitive: 'Green Chillies' tags Chillies as NNP and is left alone,
        while 'green chillies' tags NNS and correctly yields 'green chilli'.
        """
        out = []
        for doc in self.nlp.pipe((n.lower() for n in names), batch_size=200):
            tokens = [self._lemmatize(t) for t in doc if len(t.text) >= MIN_TOKEN_CHARS]
            out.append(" ".join(tokens))
        return out


class RuleNormalizer:
    """Deterministic control: no POS tagger, no statistical model.

    Exists so SpacyNormalizer's output has something to be compared against. If the
    two agree everywhere that matters, prefer this one — no model dependency, and
    its failures are inspectable rather than emergent.
    """

    name = "rules"
    IRREGULARS: dict[str, str] = {}

    def _singularize(self, word: str) -> str:
        """Collapse an English plural to a consistent form.

        Consistency matters more than English correctness. The skip-gram never reads
        a token as a word, and the IFCT join is a curated link table rather than a
        string match, so a 'wrong' lemma costs nothing mechanically. It costs only
        readability for humans inspecting the vocabulary

        Note IFCT itself uses plural forms ('Agathi leaves', 'Basella leaves'), so
        leaving '-ves' words alone is likely an appropraite approach 
        """
        if word in self.IRREGULARS:
            return self.IRREGULARS[word]
        if word.endswith("ies") and len(word) > 4:
            return word[:-3] + "y"
        if word.endswith("oes") and len(word) > 4:
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            return word[:-1]
        return word

    def normalize_many(self, names: list[str]) -> list[str]:
        out = []
        for name in names:
            words = [w for w in name.lower().split() if len(w) >= MIN_TOKEN_CHARS]
            out.append(" ".join(self._singularize(w) for w in words))
        return out


def build_vocabulary(counts: dict[str, int], normalizer) -> dict:
    """Candidate names -> vocabulary artifact.

    Mirrors generate_final_clean_ingredients.py, with one deliberate divergence: we
    emit a surface->canonical MAP alongside the sorted entry list. Pellegrini emits
    only a list, because they use n-gram matching and never needs provenance.
    """
    surfaces = list(counts)
    canonicals = normalizer.normalize_many(surfaces)

    surface_to_canonical, entry_counts, dropped = {}, {}, {"too_long": [], "too_short": []}
    for surface, canonical in zip(surfaces, canonicals):
        canonical = canonical.strip()
        if len(canonical) < MIN_CHARS:
            dropped["too_short"].append(surface)
            continue
        # <=3-word cap. It doubles as a bug detector: entries like
        # '/ 2 cup water' land here, exposing a Stage 0 fraction-parsing failure.
        if len(canonical.split()) > MAX_WORDS:
            dropped["too_long"].append((surface, canonical))
            continue
        surface_to_canonical[surface] = canonical
        entry_counts[canonical] = entry_counts.get(canonical, 0) + counts[surface]

    entries = sorted(entry_counts)
    return {
        "meta": {
            "normalizer": normalizer.name,
            "n_candidates_in": len(surfaces),
            "n_entries_out": len(entries),
            "collapse_ratio": round(len(entries) / len(surfaces), 3),
            "n_dropped_too_long": len(dropped["too_long"]),
            "n_dropped_too_short": len(dropped["too_short"]),
        },
        "entries": entries,
        "counts": dict(sorted(entry_counts.items(), key=lambda kv: -kv[1])),
        "surface_to_canonical": surface_to_canonical,
        "dropped_too_long": dropped["too_long"],
        "dropped_too_short": dropped["too_short"],
    }


if __name__ == "__main__":
    counts = json.loads(IN_PATH.read_text(encoding="utf-8"))["counts"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for normalizer in (SpacyNormalizer(), RuleNormalizer()):
        vocab = build_vocabulary(counts, normalizer)
        out = OUT_DIR / f"vocabulary_{normalizer.name}.json"
        out.write_text(json.dumps(vocab, ensure_ascii=False, indent=2), encoding="utf-8")
        m = vocab["meta"]
        print(f"[{m['normalizer']:5s}] {m['n_candidates_in']} candidates -> "
              f"{m['n_entries_out']} entries (collapse {m['collapse_ratio']}), "
              f"dropped {m['n_dropped_too_long']} too-long")