"""
build_db.py - DB construction: load every finished CSV into malabardb.db.

    recipes.csv                 -> recipes
    recipe_ingredients_final.csv-> recipe_ingredients   (from merge_corrections.py)
    ifct_nutrients.csv          -> ifct_nutrients       (from build_ifct_nutrients.py)
    ingredient_ifct_map.csv     -> ingredients          (from ingredient_ifct_map.py)
    recipe_keys.csv             -> ingredient_id backfill

unit_conversions is created empty: not yet built.
    python -m malabardb.recipe_nutrient_table.build_db"""

import sqlite3
import pandas as pd
from malabardb import paths

SCHEMA = """
PRAGMA foreign_keys = ON;

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

CREATE TABLE recipe_ingredients (
    recipe_id       INTEGER,
    line_no         INTEGER,
    raw_line        TEXT,
    qty             TEXT,
    unit            TEXT,
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

CREATE TABLE unit_conversions (
    ingredient_id   INTEGER,
    unit            TEXT,
    grams           REAL,
    source          TEXT,
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(ingredient_id)
);

CREATE INDEX idx_ri_name_raw     ON recipe_ingredients(name_raw);
CREATE INDEX idx_ri_ingredient   ON recipe_ingredients(ingredient_id);
CREATE INDEX idx_ing_ifct_code   ON ingredients(ifct_code);
"""

def resolve(row) -> tuple[str | None, str | None]:
    """(code, source) for one mapping row. IFCT wins; fallback fills gaps.

    An absent food still becomes an ingredients row with a NULL ifct_code: it exists, it just has no nutrients yet. """
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

    Keyed on norm_key, NOT on ifct_code: eight keys share G022, and 'red chilli powder' 
    and 'dry red chilli' must stay distinct because a teaspoon of each weighs a different amount."""
    
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


def check(lines, mapping, ingredients, keys) -> None:
    """Refuse to build on inputs that disagree with each other.

    The line-count check exists so a stale or partial corpus can never be
    mistaken for genuine zero coverage: the frozen map carries its own
    n_lines, and it must account for every line in the corpus."""
    expected = int(mapping["n_lines"].sum())
    if len(lines) != expected:
        raise SystemExit(
            f"{len(lines)} ingredient lines, frozen map accounts for "
            f"{expected}. Rerun merge_corrections, or refreeze the map.")

    unmapped = sorted(set(keys["norm_key"]) - set(mapping["norm_key"]))
    if unmapped:
        raise SystemExit(f"{len(unmapped)} keys have no mapping row: "
                         f"{unmapped[:10]}")

    unknown = sorted({c for c in ingredients.ifct_code.dropna()}
                     - set(pd.read_csv(paths.IFCT_NUTRIENTS).ifct_code))
    if unknown:
        raise SystemExit(f"codes not in ifct_nutrients: {unknown}")


def build() -> None:
    recipes = pd.read_csv(paths.RECIPES_TABLE)
    lines = pd.read_csv(paths.RECIPE_INGREDIENTS_FINAL)
    ifct = pd.read_csv(paths.IFCT_NUTRIENTS)
    mapping = pd.read_csv(paths.INGREDIENT_IFCT_MAP)
    keys = pd.read_csv(paths.RECIPE_KEYS)

    ingredients = build_ingredients_frame(mapping)
    check(lines, mapping, ingredients, keys)

    # Link each line to its ingredient through recipe_keys, the same artifact the matcher used. 
    key_to_id = dict(zip(ingredients.norm_key, ingredients.ingredient_id))
    name_to_id = {r.name_raw: key_to_id[r.norm_key] for r in keys.itertuples()}
    lines["ingredient_id"] = lines.name_raw.map(name_to_id)
    lines["size"] = None                     # NOT YET EXTRACTED

    orphan = int(lines.ingredient_id.isna().sum())
    if orphan:
        raise SystemExit(f"{orphan} lines have no ingredient_id; "
                         f"rerun match_to_ifct prepare")

    paths.MALABARDB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(paths.MALABARDB)
    con.executescript(SCHEMA)
    # Parents before children: the foreign keys are enforced.
    recipes.to_sql("recipes", con, if_exists="append", index=False)
    ifct.to_sql("ifct_nutrients", con, if_exists="append", index=False)
    ingredients.to_sql("ingredients", con, if_exists="append", index=False)
    lines.to_sql("recipe_ingredients", con, if_exists="append", index=False)
    con.commit()

    report(con, recipes, lines, ifct, ingredients)
    con.close()


def report(con, recipes, lines, ifct, ingredients) -> None:
    print(f"{paths.MALABARDB} built")
    print(f"  recipes            {len(recipes):5d}")
    print(f"  recipe_ingredients {len(lines):5d}")
    print(f"  ingredients        {len(ingredients):5d}")
    print(f"  ifct_nutrients     {len(ifct):5d}"
          f"  ({int(ifct.energy_kcal.isna().sum())} with NULL energy)")

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


if __name__ == "__main__":
    build()