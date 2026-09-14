'''
normalize.py - food names -> canonical keys.

A canonical key is a food name reduced to a form that can be compared across sources. 
Lowercased, singularized, stripped of digits and one-character tokens, with the words kept in their original order:
    'Green Chillies'            -> 'green chilli'
    'Chilli, green, all varieties' -> 'chilli green'
    'Bay leaf (tej patta)'      -> 'bay leaf tej patta'

Normalization exists so that a recipe ingredient name and an IFCT name can be tested for equality or overlap.

The normalizer is a port of Pellegrini et al. (2021),
`normalisation/helpers/recipe_normalizer.py` and
`normalisation/generate_final_clean_ingredients.py`, to spaCy 3.x.

  python -m malabardb.normalize            # vocabulary -> vocabulary_{spacy,rules}.json
  python -m malabardb.normalize --ifct     # IFCT keys  -> ifct_normalized.csv
'''

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from malabardb import paths

# Part-of-speech (POS) tags to label grammatical roles of words -> coarse categories. Verbatim from Pellegrini
TAG_MAPPING = {
    'NN': 'NOUN', 'NNS': 'NOUN', 'NNP': 'NOUN', 'NNPS': 'NOUN', '.': 'NOUN',
    'JJS': 'ADJ', 'JJR': 'ADJ',
    'VBD': 'VERB', 'VBG': 'VERB', 'VBN': 'VERB', 'VBZ': 'VERB', 'VBP': 'VERB',
}

# Only nouns are lemmatized, so 'Chillies' -> 'chilli' but a word the tagger reads as a verb is left alone. 
LEMMATIZATION_TYPES = ['NOUN']

MIN_TOKEN_CHARS = 2 # Single characters are stray punctuation or initials, never a food.

# IFCT spells variety groups as "all varieties" in the name. Stripping that phrase merges the summary row with its group.
# Dropping the phrase gives the summary row the same key as its group instead of a singleton. 
AGGREGATE_RE = re.compile(r"\ball\s+varieties\b", re.IGNORECASE)

class SpacyNormalizer:
    '''The pipeline normalizer. Port of Pellegrini's
    RecipeNormalizer(lemmatization_types=['NOUN']) to spaCy 3.x.

    Input:  list of raw food names, any capitalization
    Output: list of canonical keys, same length and order'''

    name = "spacy"

    def __init__(self):
        import spacy
        # ner/parser are not needed: the tagger supplies every tag we read.
        self.nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
        self.lemmatizer = self.nlp.get_pipe("lemmatizer")

    def _preclean(self, name: str) -> str:
        '''Text fixes that must happen BEFORE the tagger sees the string.'''
        s = name.lower()
        s = re.sub(r"-", " ", s) # 'green-1' is one spaCy token, so the digit would survive the length filter
        s = AGGREGATE_RE.sub(" ", s)
        return re.sub(r"\s+", " ", s).strip()

    def _lemmatize(self, token) -> str:
        '''One token -> its lemma, but only if the tagger called it a noun.'''
        coarse = TAG_MAPPING.get(token.tag_, token.tag_)
        if coarse not in LEMMATIZATION_TYPES:
            return token.text.lower()
        token.pos_ = coarse               # rule_lemmatize reads pos_, not tag_
        return self.lemmatizer.rule_lemmatize(token)[0].lower()

    def _keep(self, token) -> bool:
        '''Drop stray characters and IFCT's variety indices ('Brinjal 10').'''
        return len(token.text) >= MIN_TOKEN_CHARS and not token.text.isdigit()

    def normalize_many(self, names: list[str]) -> list[str]:
        '''Normalize a batch. Order is preserved: keys[i] belongs to names[i].'''
        out = []
        for doc in self.nlp.pipe((self._preclean(n) for n in names),
                                 batch_size=200):
            out.append(" ".join(self._lemmatize(t) for t in doc if self._keep(t)))
        return out


class RuleNormalizer:
    '''Deterministic control. NOT used in the pipeline.

    Exists to answer "does the spaCy dependency earn its place?" by running
    both over the same input. It does not: on the recipe vocabulary it yields
    'chilly' for 'chillies' and 'curry leave' for 'curry leaves'.'''

    name = "rules"
    IRREGULARS: dict[str, str] = {}

    def _singularize(self, word: str) -> str:
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
        '''Same steps as SpacyNormalizer, minus the tagger, so the two stay
        comparable on identical input.'''
        out = []
        for name in names:
            s = re.sub(r"-", " ", name.lower())
            s = AGGREGATE_RE.sub(" ", s)
            s = re.sub(r"\s+", " ", s).strip()
            words = [w for w in s.split()
                     if len(w) >= MIN_TOKEN_CHARS and not w.isdigit()]
            out.append(" ".join(self._singularize(w) for w in words))
        return out


# Vocabulary track (embeddings)
MIN_CHARS = 2

def build_vocabulary(counts: dict[str, int], normalizer,
                     max_words: int | None = None) -> dict:
    '''Candidate names + their line counts -> controlled vocabulary artifact.

    Input:  {'Green Chillies': 36, 'Turmeric powder': 82, ...}
    Output: dict with the key list, per-key counts, the surface->key map used
            to rewrite recipes, and whatever was dropped.'''
    surfaces = list(counts)
    canonicals = normalizer.normalize_many(surfaces)

    surface_to_canonical, entry_counts = {}, {}
    dropped = {"too_long": [], "too_short": []}

    for surface, canonical in zip(surfaces, canonicals):
        canonical = canonical.strip()
        if len(canonical) < MIN_CHARS:
            dropped["too_short"].append(surface)
            continue
        if max_words is not None and len(canonical.split()) > max_words:
            dropped["too_long"].append((surface, canonical))
            continue
        surface_to_canonical[surface] = canonical
        entry_counts[canonical] = entry_counts.get(canonical, 0) + counts[surface]

    entries = sorted(entry_counts)
    return {
        "meta": {
            "normalizer": normalizer.name,
            "max_words": max_words,
            "n_candidates_in": len(surfaces),
            "n_entries_out": len(entries),
            # how much distinct spelling the normalizer collapsed
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


# Linkage track (nutrition)
def normalize_ifct(ifct_path: Path, out_path: Path, normalizer) -> None:
    '''IFCT_index.csv -> ifct_normalized.csv, one row per IFCT food.

    Output columns: code, name, grup, name_key, n_sharing_key.

    `name` is kept and `name_key` is added alongside it. When
    several foods share a key (IFCT's 22 brinjal varieties), their nutrient
    rows all survive and the merge is REPORTED rather than resolved by row order.'''

    with open(ifct_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    keys = normalizer.normalize_many([r["name"] for r in rows])

    key_to_rows = defaultdict(list)
    for r, k in zip(rows, keys):
        key_to_rows[k].append(r)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["code", "name", "grup", "name_key", "n_sharing_key"])
        for r, k in zip(rows, keys):
            w.writerow([r["code"], r["name"], r.get("grup", ""), k,
                        len(key_to_rows[k])])

    report_collisions(rows, key_to_rows, out_path)


def report_collisions(rows, key_to_rows, out_path) -> None:
    '''Print keys held by more than one IFCT food.
    These are IFCT-side merges only: one key matching several IFCT foods.'''

    collisions = {k: rs for k, rs in key_to_rows.items() if len(rs) > 1}
    print(f"[ifct ] {len(rows)} rows -> {out_path.name}; "
          f"{len(key_to_rows)} distinct keys, "
          f"{len(collisions)} keys shared by >1 row")
    for k, rs in sorted(collisions.items(), key=lambda kv: -len(kv[1])):
        codes = [r["code"] for r in rs]
        shown = ",".join(codes[:6]) + ("..." if len(codes) > 6 else "")
        print(f"         {k!r:24} <- {len(rs):2} rows: {shown}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ifct", action="store_true",
                    help="normalize IFCT names instead of building the vocabulary")
    ap.add_argument("--ifct-path", type=Path, default=paths.IFCT_INDEX)
    ap.add_argument("--max-words", type=int, default=None,
                    help="drop vocabulary keys longer than this (junk filter; "
                         "use only for names that skipped parsing review)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    paths.ensure_dirs()

    if args.ifct:
        out = args.out or paths.IFCT_NORMALIZED
        normalize_ifct(args.ifct_path, out, SpacyNormalizer())
        return

    counts = json.loads(paths.EXTRACTED_NAMES.read_text(encoding="utf-8"))["counts"]
    for normalizer in (SpacyNormalizer(), RuleNormalizer()):
        vocab = build_vocabulary(counts, normalizer, max_words=args.max_words)
        out = paths.PROCESSED / f"vocabulary_{normalizer.name}.json"
        out.write_text(json.dumps(vocab, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        m = vocab["meta"]
        print(f"[{m['normalizer']:5s}] {m['n_candidates_in']} candidates -> "
              f"{m['n_entries_out']} entries (collapse {m['collapse_ratio']}), "
              f"dropped {m['n_dropped_too_long']} too-long")


if __name__ == "__main__":
    main()