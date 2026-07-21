"""
extract_names.py - Stage 0: recipe line -> candidate ingredient NAME.

This module has ONE job: turn '1 tablespoon Red Chilli powder - roasted' into
'Red Chilli powder'. It does not lowercase, lemmatize, deduplicate, or join words. 

Pellegrini has no equivalent to this file. Their Pass 1 consumed
ingredients_yummly.json, a curated inventory of ingredient NAMES supplied by
Yummly. We have no such inventory for Kerala cuisine (see README), so this
module manufactures one from the recipe text itself.

Output: data/interim/extracted_names.json
"""

import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

RAW_PATH = Path("data/raw/IndianFoodDatasetXLS.xlsx")
OUT_PATH = Path("data/interim/extracted_names.json")

# Units stripped from the leading quantity run. 'gram'/'grams' are handled
# separately below: they collide with ingredient names (Gram flour, Bengal Gram Dal).
UNITS = {
    "cup", "cups", "tablespoon", "tablespoons", "teaspoon", "teaspoons",
    "tsp", "tbsp", "liter", "litre", "ml", "kg", "sprig", "sprigs",
    "inch", "clove", "cloves", "pinch", "bunch", "can", "cans",
}
MASS_UNITS = {"gram", "grams", "g"}   # unit ONLY when directly after a number
CONNECTORS = {"to", "or"}             # range joiners: '2 to 3', '1 or 2'

_NUM = re.compile(r"^[0-9]+([./-][0-9]+)*$")   # 3, 1/2, 1-1/2, 3.5
_PAREN = re.compile(r"\(([^)]*)\)")


def strip_leading_qty(line: str) -> str:
    """Remove the FRONT run of quantity/unit tokens, stopping at the first real word

    Strip a prefix, not every unit-like token anywhere in the line
    That is what keeps the ingredient word alive in 'Gram flour' and
    'black gram' while still killing the unit in '250 grams fish'.

    Known limitation: cannot handle 'Salt - to taste' or 'Curry leaves - few',
    where there is no leading quantity at all. """
    tokens = line.split()
    i = 0
    prev_was_number = False
    while i < len(tokens):
        low = tokens[i].lower()
        if _NUM.match(tokens[i]):
            prev_was_number = True
        elif low in UNITS:
            prev_was_number = False
        elif low in MASS_UNITS and prev_was_number:    # '250 grams' -> strip
            prev_was_number = False
        elif low in CONNECTORS and prev_was_number:    # '2 to 3' -> skip the 'to'
            pass                                       # keep flag: a number follows
        else:
            break                                      # first real word -> stop
        i += 1
    return " ".join(tokens[i:])


def extract_parenthetical(line: str, synonyms: dict) -> str:
    """Strip '(...)' from the name, recording its contents as synonyms first.

    Pellegrini discards bracketed text outright (custom_removel_component sets
    to_keep=False for everything between '(' and ')'). We diverge: in this dataset
    the bracket usually holds the regional-language name: 'Curd (Dahi / Yogurt)',
    'Karela (Bitter Gourd/ Pavakkai)' which is free alias supervision we would
    otherwise have to hand-build. 
    
    Synonyms are stored in a dict-of-sets, so 'Curd (Dahi / Yogurt)' yields synonyms['curd'] = {'dahi', 'yogurt'}."""
    glosses = _PAREN.findall(line)                # ['Dahi / Yogurt']
    name = _PAREN.sub("", line).strip()           # 'Curd'
    name = re.sub(r"\s+", " ", name)
    if glosses and name:
        key = name.lower()                        # key is lowercased; the VALUE
        for gloss in glosses:                     # of `name` returned is not
            for syn in re.split(r"[/,]", gloss):  # '/' and ',' both stack synonyms here
                syn = syn.strip().lower()
                if syn:
                    synonyms.setdefault(key, set()).add(syn)
    return name


def split_ingredient_lines(raw: str) -> list[str]:
    """Split one recipe's ingredient blob into individual lines on commas.

    Naive comma splitting, deliberately.  Stray brackets are stripped
    per fragment instead. """
    out = []
    for frag in raw.split(","):
        frag = frag.strip()
        if frag.count("(") != frag.count(")"):    # orphaned bracket from truncation
            frag = frag.replace("(", "").replace(")", "")
        if frag:
            out.append(frag)
    return out


def extract_name(line: str, synonyms: dict) -> str | None:
    """One ingredient line -> one candidate NAME string, or None to drop it.

    Order matters: quantity is stripped before the parenthetical is
    harvested, so the synonym key recorded is 'karela' and not '6 karela'.

    No lowercasing, no singularisation. Keeping stage 0 case-sensitive
    lets us score the two stages independently against the gold set."""
    s = line.strip()
    if not s:
        return None
    s = s.split(" - ")[0]                    # everything after ' - ' is prep, not identity
    s = strip_leading_qty(s)
    s = extract_parenthetical(s, synonyms)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def extract(cuisine_filter: str | None = "Kerala Recipes") -> dict:
    """Run Stage 0 over the dataset and return an inspectable record.

    Returns per-recipe name lists AND a frequency counter. build_corpus.py needs
    the former (a recipe is one 'sentence'); normalize_vocab.py needs the latter. """
    df = pd.read_excel(RAW_PATH).dropna(subset=["TranslatedIngredients"])
    if cuisine_filter is not None:
        df = df[df["Cuisine"] == cuisine_filter] # filter to a single cuisine for the corpus and vocabulary

    recipes, counts, synonyms = [], Counter(), {}
    dropped = {"empty": 0, "non_ascii": []}

    for _, row in df.iterrows():
        names = []
        for line in split_ingredient_lines(str(row["TranslatedIngredients"])):
            name = extract_name(line, synonyms)
            if name is None:
                dropped["empty"] += 1
                continue
            # Untranslated Devanagari survives in 'TranslatedIngredients'. We log
            # rather than silently discard, so the coverage loss is quantifiable.
            if not name.isascii():
                dropped["non_ascii"].append(name)
                continue
            names.append(name)
            counts[name] += 1
        recipes.append({"srno": int(row["Srno"]), "names": names})

    return {
        "meta": {
            "source": str(RAW_PATH),
            "cuisine_filter": cuisine_filter,
            "n_recipes": len(recipes),
            "n_names_total": sum(counts.values()),
            "n_names_distinct": len(counts),
            "n_dropped_empty": dropped["empty"],
            "n_dropped_non_ascii": len(dropped["non_ascii"]),
        },
        "recipes": recipes,
        "counts": dict(counts.most_common()),
        "synonyms": {k: sorted(v) for k, v in synonyms.items()},
        "dropped_non_ascii": dropped["non_ascii"],
    }


if __name__ == "__main__":
    result = extract()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    m = result["meta"]
    print(f"{m['n_recipes']} recipes -> {m['n_names_total']} names "
          f"({m['n_names_distinct']} distinct)")
    print(f"dropped: {m['n_dropped_empty']} empty, "
          f"{m['n_dropped_non_ascii']} non-ascii")
    print(f"{len(result['synonyms'])} synonym keys harvested")
    print(f"wrote {OUT_PATH}")
    for name, count in list(result["counts"].items())[:10]:
        print(f"   {count:4d}  {name}")