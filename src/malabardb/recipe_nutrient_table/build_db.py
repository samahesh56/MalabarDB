"""
MalabarDB - SQLite build script

Current data coverage/schema: 
- recipes, recipe_ingredients: populated below, from this project's own
  extraction + review pipeline.
- ingredients, ifct_nutrients: schema created, but NOT populated here. 
  Requires ifct_index.csv (IFCT 2017) and the linkage_worksheet.csv /
  vocabulary_spacy.json outputs from the separate IFCT-linkage pipeline.
- unit_conversions: schema created, empty, not yet built at all.
"""

import sqlite3
import pandas as pd

DB_PATH = 'data/final/malabardb.db'

SCHEMA = """
DROP TABLE IF EXISTS recipes;
DROP TABLE IF EXISTS recipe_ingredients;
DROP TABLE IF EXISTS ingredients;
DROP TABLE IF EXISTS ifct_nutrients;
DROP TABLE IF EXISTS unit_conversions;

CREATE TABLE recipes (
    recipe_id       INTEGER PRIMARY KEY,
    dish_name       TEXT,
    region          TEXT,
    cuisine_raw     TEXT,
    servings        REAL,
    instructions    TEXT,
    source_row_id   INTEGER
);

CREATE TABLE recipe_ingredients (
    recipe_id       INTEGER,
    line_no         INTEGER,
    raw_line        TEXT,
    qty             TEXT,
    unit            TEXT,
    name_raw        TEXT,
    state           TEXT,
    size            TEXT,
    dry_fresh       TEXT,
    parse_status    TEXT,       -- 'clean' or 'needs_review', from the disagreement flags
    ingredient_id   INTEGER,    -- FK to ingredients.ingredient_id, NULL until IFCT linkage is finalized
    PRIMARY KEY (recipe_id, line_no),
    FOREIGN KEY (recipe_id) REFERENCES recipes(recipe_id)
);

CREATE TABLE ingredients (
    ingredient_id INTEGER PRIMARY KEY,
    norm_key      TEXT NOT NULL UNIQUE,
    generic_name  TEXT,
    ifct_code     TEXT,          -- NULL where no source has this food
    source_db     TEXT,          -- 'IFCT2017' | 'USDA' | 'LOCAL' | NULL
    hierarchical  TEXT,          -- 'Y' | 'N'
    link_status   TEXT NOT NULL, -- 'linked' | 'absent'
    FOREIGN KEY (ifct_code) REFERENCES ifct_nutrients(code)
);

CREATE TABLE ifct_nutrients (
    ifct_code       TEXT PRIMARY KEY,
    ifct_name       TEXT,
    food_group      TEXT,
    energy_kcal     REAL,       -- derived: energy_kj / 4.184; NULL where IFCT publishes none
    energy_kj       REAL,       -- as published, IFCT 2017
    protein_g       REAL,
    carb_g          REAL,
    fat_g           REAL,
    fibre_g         REAL
);

CREATE TABLE unit_conversions (
    ingredient_id   INTEGER,
    unit            TEXT,
    grams           REAL,
    source          TEXT,
    FOREIGN KEY (ingredient_id) REFERENCES ingredients(ingredient_id)
);
"""


def build():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    # recipes: direct load, no transformation needed ---
    recipes = pd.read_csv('data/final/recipes.csv')
    recipes.to_sql('recipes', conn, if_exists='append', index=False)

    # recipe_ingredients: load directly from the reviewed final CSV
    # merge_corrections.py already produces the right columns and names,
    recipe_ingredients = pd.read_csv('data/final/recipe_ingredients_final.csv')
    recipe_ingredients['size'] = None   # NOT YET EXTRACTED: ingredient-parser's size field not implemented yet
    recipe_ingredients['ingredient_id'] = None   # populated once IFCT linkage is finalized
    recipe_ingredients.to_sql('recipe_ingredients', conn, if_exists='append', index=False)

    # ifct_nutrients: external reference, loaded verbatim from the projection.
    # Read-only downstream. Nothing in the pipeline writes back to this table.
    ifct = pd.read_csv('data/final/ifct_nutrients.csv')
    ifct.to_sql('ifct_nutrients', conn, if_exists='append', index=False)

    linked = conn.execute(
        'SELECT COUNT(*) FROM recipe_ingredients ri '
        'JOIN ingredients i ON ri.ingredient_id = i.ingredient_id'
    ).fetchone()[0]
    print(f'  {len(ifct)} ifct_nutrients rows, '
          f'{int(ifct.energy_kcal.isna().sum())} with NULL energy')
    print(f'  {linked} of {len(recipe_ingredients)} lines linked to an ingredient')

    conn.commit()
    conn.close()
    needs_review = (recipe_ingredients['parse_status'] == 'needs_review').sum()
    
    print(f'{DB_PATH} built: {len(recipes)} recipes, {len(recipe_ingredients)} ingredient lines')
    print(f"  {needs_review} rows still marked parse_status='needs_review'")


if __name__ == '__main__':
    build()