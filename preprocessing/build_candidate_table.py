'''build_candidate_table.py 

Reproduces FoodKG's extract-names.py (src/prep-scripts/usda/) against IFCT 2017.
Deviations from FoodKG are deliberate and documented inline.

INPUT:  index.csv  (nodef/ifct2017 full compositions: code, name, lang, grup, ...)
OUTPUT: candidate_table.csv   [match_token, ifct_code, grup]   -- NOT YET BUILT
Run this file directly to see both functions checked against known cases.'''

import re
import sys
import csv


# The 20 language-tag prefixes observed in IFCT's `lang` column.
# TODO: harvest this set FROM the data rather than hand-typing it, so the
# script reports unexpected tags instead of silently ignoring them.
# These language tags are found in the `lang` column of IFCT 2017. They are used to identify the language 
# of the ingredient name, and are followed by a period. 
# For example, "A." stands for Assamese, "B." for Bengali, "Common." for common names, etc. 
# The full list of tags is defined in the LANG_TAGS set below.
LANG_TAGS = {
    "A.", "B.", "Common.", "E.", "G.", "H.", "Kan.", "Kash.", "Kh.", "Kon.",
    "M.", "Mal.", "Mar.", "N.", "O.", "P.", "S.", "Tam.", "Tel.", "U.",
}


def normalize(name: str) -> str:
    """FoodKG line 23 analog: strip parentheticals, lowercase, collapse whitespace.

    DELIBERATELY OMITS FoodKG line 22 (3-field truncation). On USDA that removed
    noise; on IFCT it collapses "Egg, poultry, whole, raw" and "...boiled" onto
    the same token (13 such collisions, verified against index.csv). 

    TODO: plural normalisation. "Chillies, green-1" must meet "green chilli",
    and right now it does not.
    """
    name = re.sub(r"\(.*?\)", " ", name)        # FoodKG line 23
    name = name.lower()
    name = re.sub(r"[^a-z0-9]+", " ", name)     # commas / hyphens -> whitespace
    return " ".join(name.split())


def flatten_lang(cell: str) -> list[str]:
    """The one component with no FoodKG analog (see nodef setupIndex regexes).

    FoodKG's alias source (USDA ComName) is a bare comma list, so their whole
    alias step is `row[4].split(",")`. IFCT's `lang` is structured, so it needs
    a real parser
    """
    aliases = []
    for section in cell.split(";"):
        section = re.sub(r"\[.*?\]", " ", section).strip()     # drop editorial notes
        if not section:
            continue

        # consume leading tags, which may be comma-chained
        while True:
            m = re.match(r"\s*([A-Z][a-z]*\.)\s*,?\s*", section)
            if not m or m.group(1) not in LANG_TAGS:
                break
            section = section[m.end():]

        # whatever survives is one or more names separated by commas
        for part in section.split(","):
            part = part.strip().rstrip(".").strip()
            if part:
                aliases.append(part.lower())
    return aliases


def build(rows):
    """FoodKG lines 20-31, minus the grup filter (moved to link time),
    minus silent dedupe (replaced by collision logging).
    Emit (match_token, code, grup) for primary name and every alias.

    NOT IMPLEMENTED -- v0.3.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Checks. Run: python build_candidate_table.py [index.csv]
# ---------------------------------------------------------------------------
CASES = [
    ("basic split",           "H. Ramdana; Mal. Cheera vithu.",  ["ramdana", "cheera vithu"]),
    ("comma-SHARED tags",     "A., Kash. Baajra",                ["baajra"]),
    ("comma-separated names", "E. Eggplant, Aubergine",          ["eggplant", "aubergine"]),
    ("interior capital",      "O. Kosala sag manji Dhala",       ["kosala sag manji dhala"]),
    ("editorial note",        "Mal. Thenga; [Place of collection: Kochi]", ["thenga"]),
]

if __name__ == "__main__":
    print("flatten_lang:")
    for label, inp, want in CASES:
        got = flatten_lang(inp)
        print(f"  [{'ok  ' if got == want else 'FAIL'}] {label}")
        if got != want:
            print(f"         want {want}\n         got  {got}")

    print("\nnormalize (both sides must agree):")
    for a, b in [("Pepper, black", "black pepper"),
                 ("Curd (Dahi)", "curd"),
                 ("Chillies, green-1", "green chilli")]:
        na, nb = normalize(a), normalize(b)
        ok = sorted(na.split()) == sorted(nb.split())
        print(f"  [{'ok  ' if ok else 'FAIL'}] {a!r} -> {na!r}  vs  {b!r} -> {nb!r}")

    if len(sys.argv) > 1:
        rows = list(csv.DictReader(open(sys.argv[1], newline="", encoding="utf-8")))
        n = sum(len(flatten_lang(r["lang"])) for r in rows)
        print(f"\non {len(rows)} IFCT rows: {n} aliases harvested "
              f"({n/len(rows):.1f} per food)")