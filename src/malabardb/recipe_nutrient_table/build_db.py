"""
build_db.py - DB construction: load every finished CSV into malabardb.db.

    recipes.csv                  -> recipes
    recipe_ingredients_final.csv -> recipe_ingredients   (from merge_corrections.py)
    ifct_nutrients.csv           -> ifct_nutrients       (from build_ifct_nutrients.py)
    ingredient_ifct_map.csv      -> ingredients          (from ingredient_ifct_map.py)
    recipe_keys.csv              -> ingredient_id backfill
    review_queue_units.csv       -> unit_conversions + recipe_ingredients.grams
                                                         (from build_unit_review.py)
    review_queue_duplicates.csv  -> drops the second copy of each confirmed
                                                         duplicate recipe pair (from
                                                         find_duplicates.py), if present

recipe_nutrition is a view, not a table: per-serving energy and macros, and the
per-recipe coverage counts that say how much of each recipe those numbers see.

    python -m malabardb.recipe_nutrient_table.build_db"""

import sqlite3
import numpy as np
import pandas as pd
from malabardb import paths
from malabardb.recipe_nutrient_table.build_unit_review import to_number

SCHEMA = """
PRAGMA foreign_keys = ON;

DROP VIEW  IF EXISTS recipe_nutrition;
DROP TABLE IF EXISTS unit_conversions;
DROP TABLE IF EXISTS recipe_ingredients;
DROP TABLE IF EXISTS ingredients;
DROP TABLE IF EXISTS ifct_nutrients;
DROP TABLE IF EXISTS recipes;

CREATE TABLE recipes (
    recipe_id       INTEGER PRIMARY KEY,
    dish_name       TEXT,
    region          TEXT,
    cuisine_raw     TEXT,
    servings        REAL,
    instructions    TEXT,
    source_row_id   INTEGER
);

CREATE TABLE ifct_nutrients (
    ifct_code       TEXT PRIMARY KEY,
    ifct_name       TEXT,
    food_group      TEXT,
    energy_kcal     REAL,       -- kJ/4.184, or Atwater where IFCT publishes none
    energy_kj       REAL,       -- as published; NULL on Atwater-derived rows
    energy_source   TEXT,       -- 'published' | 'atwater' | 'local'
    protein_g       REAL,
    carb_g          REAL,
    fat_g           REAL,
    fibre_g         REAL
);

CREATE TABLE ingredients (
    ingredient_id INTEGER PRIMARY KEY,
    norm_key      TEXT NOT NULL UNIQUE,
    generic_name  TEXT,
    ifct_code     TEXT,          -- NULL where no source has this food
    source_db     TEXT,          -- 'IFCT2017' | 'USDA' | 'LOCAL' | NULL
    hierarchical  TEXT,          -- 'Y' | NULL
    link_status   TEXT NOT NULL, -- 'linked' | 'absent'
    FOREIGN KEY (ifct_code) REFERENCES ifct_nutrients(ifct_code)
);

-- One row per decision in review_queue_units.csv that has a gram value.
-- has_qty changes what grams means:
--   1  grams per ONE unit: the line's qty multiplies it  ('1 sprig' -> 2 g/sprig)
--   0  grams for the WHOLE line: there is no qty          ('a few sprigs' -> 4 g)
-- so (curry leaves, sprig, 1) and (curry leaves, sprig, 0) are different rows.
CREATE TABLE unit_conversions (
    ingredient_id   INTEGER NOT NULL,
    unit            TEXT NOT NULL,    -- queue vocabulary: 'cup', '(count)', '(none)', ...
    has_qty         INTEGER NOT NULL,
    grams           REAL NOT NULL,
    source          TEXT,             -- 'exact' | 'indb_rule' | 'indb_units' | 'estimate' | ...
    notes           TEXT,
    PRIMARY KEY (ingredient_id, unit, has_qty),
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(ingredient_id)
);

CREATE TABLE recipe_ingredients (
    recipe_id       INTEGER,
    line_no         INTEGER,
    raw_line        TEXT,
    qty             TEXT,       -- as parsed: '1-1/2', '3/4', '2'
    qty_num         REAL,       -- qty as a number; NULL = no quantity ('to taste')
    unit            TEXT,       -- as parsed; NULL where the line names none
    unit_key        TEXT,       -- unit used to look up unit_conversions:
                                --   the parsed unit, else '(count)' if qty, else '(none)'
    grams           REAL,       -- qty_num x conversion, or the conversion alone
                                --   when qty_num is NULL; NULL = no decision yet
    name_raw        TEXT,
    state           TEXT,
    size            TEXT,       -- NOT YET EXTRACTED
    dry_fresh       TEXT,
    parse_status    TEXT,       -- 'reviewed' | 'needs_review' | 'auto'
    ingredient_id   INTEGER,
    PRIMARY KEY (recipe_id, line_no),
    FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id),
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(ingredient_id)
);

CREATE INDEX idx_ri_name_raw     ON recipe_ingredients(name_raw);
CREATE INDEX idx_ri_ingredient   ON recipe_ingredients(ingredient_id);
CREATE INDEX idx_ing_ifct_code   ON ingredients(ifct_code);

-- Per-serving nutrition plus the coverage behind it. SUM() skips NULLs, so a
-- line with no grams or no nutrients silently adds 0: read kcal_per_serving
-- only next to n_counted / n_lines.
CREATE VIEW recipe_nutrition AS
SELECT r.recipe_id,
       r.dish_name,
       r.servings,
       COUNT(*)                                                AS n_lines,
       SUM(i.ifct_code IS NOT NULL)                            AS n_linked,
       SUM(ri.grams IS NOT NULL)                               AS n_grams,
       SUM(ri.grams IS NOT NULL AND n.energy_kcal IS NOT NULL) AS n_counted,
       SUM(ri.grams * n.energy_kcal) / 100 / r.servings        AS kcal_per_serving,
       SUM(ri.grams * n.protein_g)   / 100 / r.servings        AS protein_g_per_serving,
       SUM(ri.grams * n.carb_g)      / 100 / r.servings        AS carb_g_per_serving,
       SUM(ri.grams * n.fat_g)       / 100 / r.servings        AS fat_g_per_serving
  FROM recipes r
  JOIN recipe_ingredients ri USING (recipe_id)
  JOIN ingredients i         USING (ingredient_id)
  LEFT JOIN ifct_nutrients n ON n.ifct_code = i.ifct_code
 GROUP BY r.recipe_id;
"""


def resolve(row) -> tuple[str | None, str | None]:
    """(code, source) for one mapping row. IFCT wins; fallback fills gaps.

    An absent food still becomes an ingredients row with a NULL ifct_code: it
    exists, it just has no nutrients yet."""
    ifct = str(row["chosen_ifct_code"]).strip()
    if ifct and ifct.lower() != "nan":
        return ifct, "IFCT2017"
    fb = str(row.get("fallback_code", "")).strip()
    if fb and fb.lower() != "nan":
        src = str(row.get("fallback_source", "")).strip()
        return fb, src if src and src.lower() != "nan" else "UNKNOWN"
    return None, None


def build_ingredients_frame(mapping: pd.DataFrame) -> pd.DataFrame:
    """Frozen mapping -> the ingredients table.

    Keyed on norm_key, NOT on ifct_code: eight keys share G022, and 'red chilli
    powder' and 'dry red chilli' must stay distinct because a teaspoon of each
    weighs a different amount."""
    rows = []
    for _, r in mapping.sort_values("norm_key").iterrows():
        code, source = resolve(r)
        hierarchical = str(r.get("hierarchical", "")).strip()
        rows.append({
            "norm_key": r["norm_key"],
            "generic_name": r["generic_name"],
            "ifct_code": code,
            "source_db": source,
            "hierarchical": hierarchical or None,
            "link_status": "linked" if code else "absent",
        })
    out = pd.DataFrame(rows)
    out.insert(0, "ingredient_id", range(1, len(out) + 1))
    return out


def drop_duplicate_recipes(recipes: pd.DataFrame, lines: pd.DataFrame):
    """Remove the second copy of each confirmed duplicate pair.

    review_queue_duplicates.csv (from find_duplicates.py) is a human-decided
    file: same_recipe='y' rows carry a drop_recipe naming which of the pair to
    remove (the Kaggle corpus keeps an English page and its machine-retranslated
    Hindi twin as separate rows). Absent entirely, nothing changes -- this is
    what let the pipeline run before that review existed.

    Returns (recipes, lines, dropped_lines): dropped_lines is how many rows
    left `lines` this way, which check() needs to reconcile against the
    frozen map's line count (counted before any duplicates were dropped)."""
    dup_path = paths.REVIEW_QUEUE_UNITS.parent / "review_queue_duplicates.csv"
    if not dup_path.exists():
        return recipes, lines, 0

    dup = pd.read_csv(dup_path, dtype=str, keep_default_na=False)
    yes = dup[dup["same_recipe"].str.strip().str.lower() == "y"]

    bad = yes[~yes.apply(lambda r: r["drop_recipe"] in (r["recipe_a"], r["recipe_b"]),
                          axis=1)]
    if len(bad):
        raise SystemExit(
            f"duplicate pairs with no valid drop_recipe: "
            f"{bad[['recipe_a', 'recipe_b']].values.tolist()}")

    drop = set(yes["drop_recipe"].astype(int))
    unknown = drop - set(recipes["recipe_id"])
    if unknown:
        raise SystemExit(f"drop_recipe ids not in recipes.csv: {sorted(unknown)}")

    keep = ~lines["recipe_id"].isin(drop)
    dropped_lines = int((~keep).sum())
    recipes = recipes[~recipes["recipe_id"].isin(drop)]
    lines = lines[keep]
    print(f"[dedup] dropped {len(drop)} duplicate recipes "
          f"({dropped_lines} lines): {sorted(drop)}")
    return recipes, lines, dropped_lines


def check(lines, mapping, ingredients, keys, dropped_lines: int = 0) -> None:
    """Refuse to build on inputs that disagree with each other.

    The line-count check exists so a stale or partial corpus can never be
    mistaken for genuine zero coverage: the frozen map carries its own
    n_lines, counted against the full corpus before any duplicate recipes are
    dropped, so dropped_lines (from drop_duplicate_recipes) is subtracted here
    rather than re-freezing the map every time a duplicate decision changes."""
    expected = int(mapping["n_lines"].sum()) - dropped_lines
    if len(lines) != expected:
        raise SystemExit(
            f"{len(lines)} ingredient lines, frozen map accounts for "
            f"{expected} ({int(mapping['n_lines'].sum())} minus {dropped_lines} "
            f"deduped). Rerun merge_corrections, or refreeze the map.")

    unmapped = sorted(set(keys["norm_key"]) - set(mapping["norm_key"]))
    if unmapped:
        raise SystemExit(f"{len(unmapped)} keys have no mapping row: "
                         f"{unmapped[:10]}")

    unknown = sorted({c for c in ingredients.ifct_code.dropna()}
                     - set(pd.read_csv(paths.IFCT_NUTRIENTS).ifct_code))
    if unknown:
        raise SystemExit(f"codes not in ifct_nutrients: {unknown}")


def attach_grams(lines: pd.DataFrame, ingredients: pd.DataFrame,
                 dropped_lines: int = 0):
    """qty + unit -> grams for every line, from the reviewed unit queue.

    The queue decides once per (ingredient, unit, has_qty) pair; this applies
    each decision back to its lines in four steps:
      1. qty text -> number          '1-1/2' -> 1.5; blank -> NaN
      2. rebuild the queue's label   blank unit -> '(count)' if qty, else '(none)'
      3. look up the pair            (curry leaves, sprig, True) -> 2.0
      4. apply it                    has_qty: qty_num x value; else: value

    Returns (lines with qty_num, unit_key, grams) and the unit_conversions
    frame: one row per pair that has a decision."""
    queue = pd.read_csv(paths.REVIEW_QUEUE_UNITS)

    # Same idea as check(): a queue built from a different corpus must not load.
    # build_unit_review counts the full corpus, before duplicates are dropped,
    # so the dropped lines are subtracted here just as in check(). Every line
    # that remains still has its pair in the queue: dedup only removes lines.
    expected = int(queue["n_lines"].sum()) - dropped_lines
    if len(lines) != expected:
        raise SystemExit(
            f"unit queue accounts for {expected} lines "
            f"({int(queue['n_lines'].sum())} minus {dropped_lines} deduped), corpus "
            f"has {len(lines)}. Rerun build_unit_review.")

    queue["ingredient_id"] = queue["norm_key"].map(
        dict(zip(ingredients.norm_key, ingredients.ingredient_id)))
    if queue["ingredient_id"].isna().any():
        bad = queue.loc[queue["ingredient_id"].isna(), "norm_key"].unique()
        raise SystemExit(f"unit queue keys not in ingredients: {list(bad[:10])}")
    queue["ingredient_id"] = queue["ingredient_id"].astype(int)

    # 1. qty text -> number. astype(float) turns to_number's None into NaN.
    lines["qty_num"] = lines["qty"].map(to_number).astype(float)
    has_qty = lines["qty_num"].notna()
    unreadable = lines["qty"].notna() & ~has_qty
    if unreadable.any():
        print(f"{int(unreadable.sum())} qty values could not be read as numbers; "
              f"treated as no quantity (as in build_unit_review)")

    # 2. the queue's label for each line. Must match build_unit_review.load_lines;
    #    if the two ever drift, step 3 stops the build instead of dropping lines.
    unit_key = lines["unit"].copy()
    unit_key[unit_key.isna() & has_qty] = "(count)"
    lines["unit_key"] = unit_key.fillna("(none)")

    # 3. look up each line's pair
    per_pair = queue.set_index(["ingredient_id", "unit", "has_qty"])["grams_per_unit"]
    if not per_pair.index.is_unique:
        raise SystemExit("unit queue has duplicate (ingredient, unit, has_qty) rows")
    line_pair = pd.MultiIndex.from_arrays(
        [lines["ingredient_id"], lines["unit_key"], has_qty])
    missing = ~line_pair.isin(per_pair.index)
    if missing.any():
        raise SystemExit(
            f"{int(missing.sum())} lines have no row in the unit queue; rerun "
            f"build_unit_review. e.g. {lines.loc[missing, 'raw_line'].head(3).tolist()}")
    value = per_pair.reindex(line_pair).to_numpy()

    # 4. apply. A blank value (undecided pair) gives NULL grams either way.
    lines["grams"] = np.where(has_qty, lines["qty_num"] * value, value)

    conv = (queue[queue["grams_per_unit"].notna()]
            .rename(columns={"grams_per_unit": "grams"})
            [["ingredient_id", "unit", "has_qty", "grams", "source", "notes"]])
    return lines, conv


def build() -> None:
    recipes = pd.read_csv(paths.RECIPES_TABLE)
    lines = pd.read_csv(paths.RECIPE_INGREDIENTS_FINAL, dtype={"qty": str})
    recipes, lines, dropped_lines = drop_duplicate_recipes(recipes, lines)
    ifct = pd.read_csv(paths.IFCT_NUTRIENTS)
    mapping = pd.read_csv(paths.INGREDIENT_IFCT_MAP)
    keys = pd.read_csv(paths.RECIPE_KEYS)

    ingredients = build_ingredients_frame(mapping)
    check(lines, mapping, ingredients, keys, dropped_lines)

    # Link each line to its ingredient through recipe_keys, the same artifact
    # the matcher used.
    key_to_id = dict(zip(ingredients.norm_key, ingredients.ingredient_id))
    name_to_id = {r.name_raw: key_to_id[r.norm_key] for r in keys.itertuples()}
    lines["ingredient_id"] = lines.name_raw.map(name_to_id)
    lines["size"] = None                     # NOT YET EXTRACTED

    orphan = int(lines.ingredient_id.isna().sum())
    if orphan:
        raise SystemExit(f"{orphan} lines have no ingredient_id; "
                         f"rerun match_to_ifct prepare")

    lines, conv = attach_grams(lines, ingredients, dropped_lines)

    paths.MALABARDB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(paths.MALABARDB)
    con.executescript(SCHEMA)
    # Parents before children: the foreign keys are enforced.
    recipes.to_sql("recipes", con, if_exists="append", index=False)
    ifct.to_sql("ifct_nutrients", con, if_exists="append", index=False)
    ingredients.to_sql("ingredients", con, if_exists="append", index=False)
    conv.to_sql("unit_conversions", con, if_exists="append", index=False)
    lines.to_sql("recipe_ingredients", con, if_exists="append", index=False)
    con.commit()

    report(con, recipes, lines, ifct, ingredients, conv)
    con.close()


def report(con, recipes, lines, ifct, ingredients, conv) -> None:
    print(f"{paths.MALABARDB} built")
    print(f"  recipes            {len(recipes):5d}")
    print(f"  recipe_ingredients {len(lines):5d}")
    print(f"  ingredients        {len(ingredients):5d}")
    print(f"  ifct_nutrients     {len(ifct):5d}"
          f"  ({int(ifct.energy_kcal.isna().sum())} with NULL energy)")
    print(f"  unit_conversions   {len(conv):5d}")

    needs_review = int((lines.parse_status == "needs_review").sum())
    print(f"\nparsing: {needs_review} lines still marked 'needs_review'")

    total, ifct_n, fallback, absent, hier = con.execute("""
        SELECT COUNT(*),
               SUM(i.source_db = 'IFCT2017'),
               SUM(i.ifct_code IS NOT NULL AND i.source_db <> 'IFCT2017'),
               SUM(i.ifct_code IS NULL),
               SUM(i.hierarchical = 'Y')
          FROM recipe_ingredients ri
          JOIN ingredients i USING (ingredient_id)""").fetchone()

    print("linkage:")
    for label, n in (("IFCT-linked", ifct_n), ("fallback", fallback),
                     ("total linked", ifct_n + fallback), ("absent", absent),
                     ("hierarchical", hier)):
        print(f"  {label:<14}{n:5d}  ({n / total * 100:5.1f}%)")

    # A line reaches the calorie total only if it has grams AND nutrients.
    with_grams, counted, kcal = con.execute("""
        SELECT SUM(ri.grams IS NOT NULL),
               SUM(ri.grams IS NOT NULL AND n.energy_kcal IS NOT NULL),
               SUM(ri.grams * n.energy_kcal / 100)
          FROM recipe_ingredients ri
          JOIN ingredients i USING (ingredient_id)
          LEFT JOIN ifct_nutrients n ON n.ifct_code = i.ifct_code""").fetchone()

    print("units:")
    print(f"  with grams    {with_grams:5d}  ({with_grams / total * 100:5.1f}%)")
    print(f"  counted       {counted:5d}  ({counted / total * 100:5.1f}%)"
          f"  grams AND nutrients")
    print(f"  corpus energy {kcal:,.0f} kcal")

    no_servings = int(recipes.servings.isna().sum())
    if no_servings:
        print(f"\n{no_servings} recipes have no servings: "
              f"their per-serving values will be NULL")


if __name__ == "__main__":
    build()