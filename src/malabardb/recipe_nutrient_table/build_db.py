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
    ingredient_id   INTEGER PRIMARY KEY,
    generic_name    TEXT,
    norm_key        TEXT,
    food_group      TEXT,
    ifct_code       TEXT,       -- FK to ifct_nutrients.ifct_code, nullable
    link_rule       TEXT,
    link_confidence REAL,
    reviewed_by     TEXT,
    notes           TEXT
);

CREATE TABLE ifct_nutrients (
    ifct_code       TEXT PRIMARY KEY,
    ifct_name       TEXT,
    food_group      TEXT,
    energy_kcal     REAL,
    protein_g       REAL,
    carb_g          REAL,
    fat_g           REAL
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
    recipe_ingredients['size'] = ''   # NOT YET EXTRACTED: ingredient-parser's size field not implemented yet
    recipe_ingredients['ingredient_id'] = None   # populated once IFCT linkage is finalized
    recipe_ingredients.to_sql('recipe_ingredients', conn, if_exists='append', index=False)

    conn.commit()
    conn.close()
    needs_review = (recipe_ingredients['parse_status'] == 'needs_review').sum()
    print(f'{DB_PATH} built: {len(recipes)} recipes, {len(recipe_ingredients)} ingredient lines')
    print(f"  {needs_review} rows still marked parse_status='needs_review'")


if __name__ == '__main__':
    build()