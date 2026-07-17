"""
build_corpus.py — turn the raw Kaggle recipe table into a word2vec-ready corpus.

PIPELINE (one recipe -> one "sentence" of canonical ingredient tokens):
  Stage 1  SELECT: filter the table to our cuisine, drop null-ingredient rows
  Stage 2  SPLIT: split each recipe's ingredient string into individual lines
  Stage 3  PARSE: reduce each line to ONE canonical token (Decisions A-D)
  Stage 4  ASSEMBLE: collect a recipe's tokens into a list; save the corpus to disk

The pipeline follows the conventional shape of text-corpus preprocessing:
select the data, split into units, clean each unit, reassemble into model input 
"""

from itertools import count
import json, re # RegEx 
from pathlib import Path 

import pandas as pd

# --- paths (repo root) ---
RAW_PATH = Path("data/raw/IndianFoodDatasetXLS.xlsx")
OUT_DIR = Path("data/processed")

"""Units stripped whenever they appear in the leading quantity run.
# 'gram'/'grams' are deliberately NOT here they collide with ingredient names
# (Gram flour, black gram, Bengal Gram Dal)""" 
UNITS = {
    "cup", "cups", "tablespoon", "tablespoons", "teaspoon", "teaspoons",
    "tsp", "tbsp", "liter", "litre", "ml", "kg", "sprig", "sprigs",
    "inch", "clove", "cloves", "pinch", "bunch", "can", "cans",
}

# Decision B — parentheticals: 'Gram flour (besan)', 'Curd (Dahi / Yogurt)'.
_PAREN = re.compile(r"\(([^)]*)\)")
def extract_parenthetical(line: str, synonyms: dict) -> str:
    """Remove '(...)' from the token, but first record it as a synonym of the
    surrounding name, so 'Curd (Dahi / Yogurt)' both yields token 'curd' AND
    logs curd -> {dahi, yogurt}. `synonyms` is mutated in place (a collector
    passed in from build_corpus). We split the gloss on '/' and ',' because
    that's how this dataset stacks multiple synonyms."""
    glosses = _PAREN.findall(line)              # ['Dahi / Yogurt']
    name = _PAREN.sub("", line).strip()         # 'Curd'
    name = re.sub(r"\s+", " ", name)            # tidy doubled spaces left behind
    if glosses and name:
        key = name.lower()
        for g in glosses:
            for syn in re.split(r"[/,]", g):
                syn = syn.strip().lower()
                if syn:
                    synonyms.setdefault(key, set()).add(syn)
    return name

# Decision C — leading quantity + unit tokens: '3 tablespoon Red Chilli powder' -> 'Red Chilli powder'. 
MASS_UNITS = {"gram", "grams", "g"}   # 'gram(s)/g' count as a unit ONLY directly after a number (250 grams fish)
CONNECTORS = {"to", "or"}             # range joiners: "2 to 3", "1 or 2"

_NUM = re.compile(r"^[0-9]+([./-][0-9]+)*$")  # 3, 1/2, 1-1/2, 3.5
def strip_leading_qty(line: str) -> str:
    """Remove the FRONT run of quantity/unit tokens, stop at the first real word.
    'gram(s)' is a unit only when it directly follows a number (250 grams ...),
    so the ingredient word survives in 'Gram flour' and 'black gram'."""
    tokens = line.split()
    i = 0
    prev_was_number = False
    while i < len(tokens):
        low = tokens[i].lower()
        if _NUM.match(tokens[i]):
            prev_was_number = True
        elif low in UNITS:
            prev_was_number = False
        elif low in MASS_UNITS and prev_was_number:   # '250 grams' -> strip
            prev_was_number = False
        elif low in CONNECTORS and prev_was_number:   # '2 to 3'    -> skip the 'to'
            pass                                       # keep the flag; a number follows
        else:
            break                                      # first real word -> stop
        i += 1
    return " ".join(tokens[i:])

# Decision D — canonicalize: lowercase, collapse simple plurals (so 'onions' and 'onion' don't split-vote -- see audit Check 2),
#  and underscore-join the surviving words into ONE token ('red_chilli_powder').
def canonicalize(name: str) -> str | None:
    """Lowercase, collapse plurals to a CONSISTENT (not necessarily correct)
    form, underscore-join into one token. Returns None if nothing survives."""
    name = name.replace("/", " ")      # '/' is a word boundary: pods/seeds -> pods seeds
    words = name.lower().split()
    out = []
    for w in words:
        # consistent plural collapse — see note below on why crude is fine
        if w.endswith("ies") and len(w) > 4:
            w = w[:-3] + "i"      # chillies -> chilli, curries -> curri
        elif w.endswith("es") and len(w) > 3:
            w = w[:-2]            # tomatoes -> tomato, leaves stays leave-ish
        elif w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            w = w[:-1]            # onions -> onion, seeds -> seed  (ss guard keeps 'grass')
        out.append(w)
    token = "_".join(out) if out else None
    if token is None:
        return None
    if not token.isascii():        # drop Devanagari / any non-Latin token (prototype scope)
        return None
    return token

def parse_ingredient_line(line: str, synonyms: dict) -> str | None:
    """
    One ingredient line -> one canonical token (or None to drop the line).
    Applies the four cleaning decisions IN ORDER. Order matters: quantity is
    stripped (C) before the parenthetical is harvested (B), so the synonym key
    recorded in B is a clean ingredient name, not '1 brinjal'.

    e.g. '6 Karela (Bitter Gourd/ Pavakkai) - deseeded'  ->  'karela'
         '1 tablespoon Red Chilli powder'                ->  'red_chilli_powder'
         'Salt - to taste'                               ->  'salt'
    """
    # Decision A — trailing qualifier: everything after ' - ' is prep, not identity
    #   ('Onion - thinly sliced' -> 'Onion'). This one is done for you as the pattern:
    s = line.strip()
    if not s:                                # remove empty lines (e.g. from ', , ,') and drop them from the corpus
        return None
    s = s.split(" - ")[0]                     
    s = strip_leading_qty(s)                 # C strip leading quantity/units: kill the qty
    s = extract_parenthetical(s, synonyms)   # B after: key is clean
    return canonicalize(s)                   # D lowercase, collapse plurals, underscore-join

def build_corpus(cuisine_filter: str = "Kerala Recipes") -> tuple[list[list[str]], dict]:    
    """
    Stages 1-4. Returns a list of recipes, each a list of ingredient tokens.
    cuisine_filter=None uses the whole dataset (for later expansion).
    """
    # SELECT: load, filter to our cuisine, drop null-ingredient rows.
    df = pd.read_excel(RAW_PATH)
    if cuisine_filter is not None:
        df = df[df["Cuisine"] == cuisine_filter]
    df = df.dropna(subset=["TranslatedIngredients"])

    corpus: list[list[str]] = []
    synonyms: dict = {}
    for raw in df["TranslatedIngredients"]:
        # SPLIT: comma is our BETWEEN-ingredient delimiter 
        lines = [ln for ln in raw.split(",")]

        # PARSE: line -> token, dropping Nones.
        tokens = [tok for tok in (parse_ingredient_line(ln, synonyms) for ln in lines)
                  if tok is not None]

        if len(tokens) >= 2: # Keep only recipes with at least 2 ingredients (1-ingredient recipes are not useful for co-occurrence training)
            corpus.append(tokens)

    return corpus, synonyms


def save_corpus(corpus: list[list[str]], synonyms: dict, name: str) -> None:
    """Stage 4b — persist as an inspectable artifact: one recipe per line,
    space-separated tokens. Decouples parsing runs from training runs and makes
    the corpus diffable in git-review."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{name}.txt"
    with out.open("w", encoding="utf-8") as f:
        for recipe in corpus:
            f.write(" ".join(recipe) + "\n")
    print(f"wrote {len(corpus)} recipes -> {out}")
    with (OUT_DIR / f"{name}_synonyms.json").open("w", encoding="utf-8") as f:
        json.dump({k: sorted(v) for k, v in synonyms.items()}, f,
                  ensure_ascii=False, indent=2)


if __name__ == "__main__":
    corpus, synonyms = build_corpus(cuisine_filter="Kerala Recipes")
    save_corpus(corpus, synonyms, "v0a_kerala")
    print(f"{len(synonyms)} synonym keys harvested")
    for r in corpus[:3]:
        print(r)