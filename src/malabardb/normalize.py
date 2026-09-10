'''
normalize.py - surface food names -> canonical keys.

Shared by BOTH pipeline tracks, which is why it sits at the root:
    ingredient vocabulary  (embeddings)   build_vocabulary()
    IFCT linkage           (nutrition)    normalize_ifct()

A recipe key and an IFCT key are comparable ONLY because the same function
produced them. Same practice as Kalra et al. (2020), who lemmatize the
ingredient phrase and the USDA description with one lemmatizer, and StandFood
(Eftimov et al. 2017), which normalizes both sides before matching. 

The normalizer itself reproduces Pellegrini et al. (2021)
`normalisation/helpers/recipe_normalizer.py` and
`normalisation/generate_final_clean_ingredients.py`.

  python -m malabardb.normalize            # build vocabulary   -> vocabulary_{spacy,rules}.json
  python -m malabardb.normalize --ifct     # normalize IFCT     -> ifct_normalized.csv
'''

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from malabardb import paths

# Verbatim from Pellegrini, helpers/recipe_normalizer.py.
TAG_MAPPING = {
    'NN': 'NOUN', 'NNS': 'NOUN', 'NNP': 'NOUN', 'NNPS': 'NOUN', '.': 'NOUN',
    'JJS': 'ADJ', 'JJR': 'ADJ',
    'VBD': 'VERB', 'VBG': 'VERB', 'VBN': 'VERB', 'VBZ': 'VERB', 'VBP': 'VERB',
}

LEMMATIZATION_TYPES = ['NOUN']

# Applied to EVERY key, both tracks.
MIN_TOKEN_CHARS = 2

# Stripping this phrase is what merges IFCT's two "all varieties"
# rows (D031 brinjal, G008 green chilli) into their variety groups' keys.
# Removing this regex takes IFCT from 512 distinct keys back to 514.
AGGREGATE_RE = re.compile(r"\ball\s+varieties\b", re.IGNORECASE)


class SpacyNormalizer:
    '''Port of RecipeNormalizer(lemmatization_types=['NOUN']) to spaCy 3.x.'''

    name = "spacy"

    def __init__(self):
        import spacy
        self.nlp = spacy.load("en_core_web_sm", disable=["ner", "parser"])
        self.lemmatizer = self.nlp.get_pipe("lemmatizer")

    def _lemmatize(self, token) -> str:
        '''Lemmatize one token under the TAG_MAPPING gate.'''
        coarse = TAG_MAPPING.get(token.tag_, token.tag_)
        if coarse not in LEMMATIZATION_TYPES:
            return token.text.lower()
        token.pos_ = coarse                      # rule_lemmatize reads pos_, not tag_
        return self.lemmatizer.rule_lemmatize(token)[0].lower()

    def _preclean(self, name: str) -> str:
        '''Pre-tagger cleanup. Runs on every input string, both sides.'''
        s = name.lower()

        # hyphen -> space. spaCy keeps 'green-1' as ONE token, so the digit
        # survives the length filter. Splitting lets it be dropped below.
        s = re.sub(r"-", " ", s)

        # drop 'all varieties' so D031/G008 share their variety group's key instead of forming singletons.
        s = AGGREGATE_RE.sub(" ", s)

        # collapse whitespace runs left by ' - '.
        return re.sub(r"\s+", " ", s).strip()

    def normalize_many(self, names: list[str]) -> list[str]:
        '''Normalize a batch. Lowercasing happens in _preclean, before tagging -
        diverges from Pellegrini because our input is title-cased and the tagger is case-sensitive.'''
        out = []
        for doc in self.nlp.pipe((self._preclean(n) for n in names), batch_size=200):
            tokens = [
                self._lemmatize(t)
                for t in doc
                # drop pure digits. MIN_TOKEN_CHARS alone misses IFCT's 2-digit variety indices ('Brinjal 10'..'21').
                if len(t.text) >= MIN_TOKEN_CHARS and not t.text.isdigit()
            ]
            out.append(" ".join(tokens))
        return out


class RuleNormalizer:
    '''Deterministic control: no POS tagger, no statistical model.

    Exists to answer "does the spaCy dependency earn its place?" by measuring
    both against the same input, not to be used in the pipeline.
    '''

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
        '''Mirrors the _preclean steps so the control stays comparable on IFCT input.'''
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

# VOCABULARY-ONLY caps. These are NOT applied to the linkage track: match_to_ifct
# .prepare() calls normalize_many() directly and keeps every key regardless of
# length, which is why the ingredient review queue legitimately contains keys
# far longer than three words ('green chawli bean yard long bean karamani barbati'). 
MAX_WORDS = 3
MIN_CHARS = 2


def build_vocabulary(counts: dict[str, int], normalizer) -> dict:
    '''Candidate names -> controlled vocabulary artifact.'''
    surfaces = list(counts)
    canonicals = normalizer.normalize_many(surfaces)

    surface_to_canonical, entry_counts = {}, {}
    dropped = {"too_long": [], "too_short": []}
    for surface, canonical in zip(surfaces, canonicals):
        canonical = canonical.strip()
        if len(canonical) < MIN_CHARS:
            dropped["too_short"].append(surface)
            continue
        # the <=3-word cap doubles as a bug detector: '/ 2 cup water' lands here.
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


# Linkage track (nutrition)

def normalize_ifct(ifct_path: Path, out_path: Path, normalizer) -> None:
    '''Run the shared normalizer over IFCT names -> ifct_normalized.csv.

    NON-DESTRUCTIVE: `name` is preserved, `name_key` is derived. Variety
    spreads collapse in the KEY, not in the data, so their distinct nutrient
    rows survive and the merge surfaces as a logged collision rather than being
    resolved silently by row order.
    '''
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

    # The log flags merges normalization should NOT have made. Collisions are
    # IFCT-side only: a vocab term hitting several DIFFERENT keys is ambiguity,
    # invisible here, and surfaces in the join.
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
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    paths.ensure_dirs()

    if args.ifct:
        out = args.out or paths.IFCT_NORMALIZED
        normalize_ifct(args.ifct_path, out, SpacyNormalizer())
        return

    counts = json.loads(paths.EXTRACTED_NAMES.read_text(encoding="utf-8"))["counts"]
    for normalizer in (SpacyNormalizer(), RuleNormalizer()):
        vocab = build_vocabulary(counts, normalizer)
        out = paths.PROCESSED / f"vocabulary_{normalizer.name}.json"
        out.write_text(json.dumps(vocab, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        m = vocab["meta"]
        print(f"[{m['normalizer']:5s}] {m['n_candidates_in']} candidates -> "
              f"{m['n_entries_out']} entries (collapse {m['collapse_ratio']}), "
              f"dropped {m['n_dropped_too_long']} too-long")


if __name__ == "__main__":
    main()