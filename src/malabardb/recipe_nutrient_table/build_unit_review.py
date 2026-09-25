"""
build_unit_review.py - the unit worklist: one row per (ingredient, unit) pair
that needs a grams-per-unit value, with every value the machine can decide
already filled in.

Every '1 sprig curry leaves' in every recipe needs the same number (grams in
one sprig of curry leaves), so conversions are decided per pair, not per line.

Two kinds of pair are filled automatically:
    exact      gram, kilogram: the unit already is a weight
    indb_rule  teaspoon, tablespoon, sprig, pinch, liquid cups, ml/liter:
               looked up by (unit, IFCT food group) in indb_category_rules.csv,
               hand-transcribed from INDB.do lines 163-210 (Vijayakumar 2024)
Everything else is left blank for a human: cups of solids, counts, cloves,
inches, and lines with no quantity. A human value always wins over a machine
value, and is carried forward when the queue is regenerated.

    reads   recipe_ingredients_final.csv   parsed lines (qty, unit, name_raw)
            recipe_keys.csv                name_raw -> norm_key
            ingredient_ifct_map.csv        norm_key -> IFCT / fallback code
            ifct_nutrients.csv             code -> name, kcal per 100 g
            indb_category_rules.csv        (unit, food group) -> grams
    writes  review/review_queue_units.csv

    python -m malabardb.recipe_nutrient_table.build_unit_review
"""

import pandas as pd
from malabardb import paths

EXACT = {"gram": 1.0, "kilogram": 1000.0}
AUTO_SOURCES = {"exact", "indb_rule"}          # anything else was typed by a human
KEY = ["norm_key", "unit", "has_qty"]  # identifies a pair across reruns. Not code:
                                      # grams describe the physical food, so a relink
                                      # (code change) must not discard the decision

# Local fallback codes start with 'L' (LOC001), which would read as IFCT group L
# (milk). Give them the group INDB itself filed them under instead.
LOCAL_GROUP = {"LOC001": "G",   # salt  - INDB code G528
               "LOC002": "K",   # water - INDB code K505
               "LOC003": "A"}   # cooked rice - IFCT cereals group


def to_number(q) -> float | None:
    """'1-1/2' -> 1.5, '3/4' -> 0.75, '2.5' -> 2.5, blank -> None.

    qty is stored as text; it has to be a number before it can be multiplied
    by a gram weight. Anything that is not one of these three shapes returns
    None and is reported, never guessed."""
    if pd.isna(q) or not str(q).strip():
        return None
    s = str(q).strip()
    try:
        if "-" in s and "/" in s:              # mixed number: 1-1/2
            whole, frac = s.split("-", 1)
            num, den = frac.split("/")
            return float(whole) + float(num) / float(den)
        if "/" in s:                           # fraction: 3/4
            num, den = s.split("/")
            return float(num) / float(den)
        return float(s)                        # plain: 2 or 2.5
    except ValueError:
        return None


def food_group(code) -> str | None:
    """IFCT food group = first letter of the code (G010 -> G)."""
    if pd.isna(code):
        return None
    return LOCAL_GROUP.get(code, code[0])


def load_lines() -> pd.DataFrame:
    """One row per ingredient line, with its numeric qty, key and code."""
    lines = pd.read_csv(paths.RECIPE_INGREDIENTS_FINAL, dtype={"qty": str})
    keys = pd.read_csv(paths.RECIPE_KEYS)
    mapping = pd.read_csv(paths.INGREDIENT_IFCT_MAP, dtype=str)
    ifct = pd.read_csv(paths.IFCT_NUTRIENTS)

    lines["qty_num"] = lines["qty"].map(to_number)
    bad = lines[lines["qty"].notna() & lines["qty_num"].isna()]
    if len(bad):
        print(f"{len(bad)} qty values could not be read as numbers (fix at source):")
        print(bad[["recipe_id", "line_no", "raw_line", "qty"]].to_string(index=False))

    # line -> norm_key -> code (IFCT first, fallback second, as in build_db)
    lines["norm_key"] = lines["name_raw"].map(dict(zip(keys.name_raw, keys.norm_key)))
    mapping["code"] = mapping["chosen_ifct_code"].fillna(mapping["fallback_code"])
    lines = lines.merge(mapping[["norm_key", "code"]], on="norm_key", how="left")
    lines = lines.merge(ifct[["ifct_code", "ifct_name", "energy_kcal"]],
                        left_on="code", right_on="ifct_code", how="left")

    lines["has_qty"] = lines["qty_num"].notna()                              # False: 'to taste'
    lines.loc[lines["unit"].isna() & lines["has_qty"], "unit"] = "(count)"   # '4 Green Chillies'
    lines["unit"] = lines["unit"].fillna("(none)")                           # 'Oil - for cooking'
    return lines


def build_pairs(lines: pd.DataFrame) -> pd.DataFrame:
    """Collapse lines to one row per (ingredient, unit) pair."""
    pairs = (lines
             .groupby(["norm_key", "code", "unit", "has_qty"], dropna=False)
             .agg(ifct_name=("ifct_name", "first"),
                  kcal_per_100g=("energy_kcal", "first"),
                  n_lines=("raw_line", "size"),
                  n_recipes=("recipe_id", "nunique"),
                  total_qty=("qty_num", "sum"),
                  example_line=("raw_line", "first"))
             .reset_index())
    pairs["food_group"] = pairs["code"].map(food_group)
    pairs["linked"] = pairs["code"].notna()
    return pairs


def fill_auto(pairs: pd.DataFrame) -> pd.DataFrame:
    """Fill grams_per_unit for every pair the machine can decide on its own."""
    rules = pd.read_csv(paths.INDB_RULES, dtype={"indb_line": str})
    by_group = {(r.unit, r.food_group): r for r in rules.itertuples()}

    grams, source, notes = [], [], []
    for p in pairs.itertuples():
        rule = by_group.get((p.unit, p.food_group)) or by_group.get((p.unit, "*"))
        if not p.has_qty:                          # 'to taste': needs a default, not a rule
            g, s, n = None, "", ""
        elif p.unit in EXACT:
            g, s, n = EXACT[p.unit], "exact", ""
        elif rule is not None and p.linked:
            g, s, n = rule.grams_per_unit, "indb_rule", f"INDB.do line {rule.indb_line}"
        else:
            g, s, n = None, "", ""
        grams.append(g); source.append(s); notes.append(n)

    pairs["grams_per_unit"] = grams
    pairs["source"] = source
    pairs["notes"] = notes
    return pairs


def carry_forward(pairs: pd.DataFrame, out) -> pd.DataFrame:
    """Keep every human decision from the previous queue.

    A human row is one with grams filled in and a source that is not one of
    the machine's. It overrides whatever the machine proposed this run. A
    human row whose pair no longer exists stops the run: the corpus changed
    underneath a decision, and a person should look at it."""
    if not out.exists():
        return pairs
    prior = pd.read_csv(out, dtype=str, keep_default_na=False)
    human = prior[(prior["grams_per_unit"].str.strip() != "")
                  & ~prior["source"].isin(AUTO_SOURCES)]
    if human.empty:
        return pairs

    def keyed(df):
        # lower(): Excel saves booleans as TRUE/FALSE, pandas writes True/False
        return df[KEY].fillna("").astype(str).agg("|".join, axis=1).str.lower()

    pairs["_k"] = keyed(pairs)
    human = human.assign(_k=keyed(human),
                         grams_per_unit=pd.to_numeric(human["grams_per_unit"])).set_index("_k")

    lost = sorted(set(human.index) - set(pairs["_k"]))
    if lost:
        raise SystemExit(f"{len(lost)} reviewed pairs no longer in the corpus: {lost[:5]}")

    hit = pairs["_k"].isin(human.index)
    for col in ("grams_per_unit", "source", "notes"):
        pairs.loc[hit, col] = pairs.loc[hit, "_k"].map(human[col])
    print(f"carried forward {int(hit.sum())} reviewed pairs")
    return pairs.drop(columns="_k")


def build() -> None:
    lines = load_lines()
    pairs = fill_auto(build_pairs(lines))
    out = paths.REVIEW_QUEUE_UNITS
    pairs = carry_forward(pairs, out)

    pairs = pairs.sort_values(["linked", "n_lines"], ascending=[False, False])
    pairs = pairs[["norm_key", "code", "food_group", "ifct_name", "kcal_per_100g",
                   "unit", "has_qty", "n_lines", "n_recipes", "total_qty",
                   "example_line", "linked", "grams_per_unit", "source", "notes"]]
    out.parent.mkdir(parents=True, exist_ok=True)
    pairs.to_csv(out, index=False)
    report(lines, pairs, out)


def report(lines, pairs, out) -> None:
    linked = pairs[pairs.linked]
    filled = pd.to_numeric(linked["grams_per_unit"], errors="coerce").notna()
    print(f"\nwrote {len(pairs)} pairs -> {out}")
    print(f"  lines {len(lines)}  |  linked lines {int(linked.n_lines.sum())}"
          f"  |  linked pairs {len(linked)}")

    print("\nlinked lines by how grams_per_unit was decided:")
    src = linked.assign(source=linked["source"].where(filled, "(blank - needs a human)"))
    print(src.groupby("source").agg(pairs=("norm_key", "size"),
                                    lines=("n_lines", "sum")).to_string())

    print("\nstill blank, by unit:")
    todo = linked[~filled]
    print(todo.groupby(["unit", "has_qty"]).agg(pairs=("norm_key", "size"),
                                                lines=("n_lines", "sum"))
              .sort_values("lines", ascending=False).to_string())


if __name__ == "__main__":
    build()