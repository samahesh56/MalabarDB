"""
export_release.py - malabardb.db -> the seven published tables.

The pipeline's own files (data/final, data/review, the .db) are working files:
they hold the full IFCT table, the recipe instructions, duplicate recipes and
review columns. This script is the ONLY place where working data becomes
published data, so every difference between the two is written down here,
one function per published table.

    reads   data/final/malabardb.db               everything computed so far
            data/raw/kerala_source.csv            source URL of each recipe
            data/review/review_queue_ingredients.csv   how each link was made
            data/raw/fallback_map.csv             note on each fallback link
            data/raw/local_nutrients.csv          source_ref of non-IFCT foods
            datapackage.template.json             hand-written descriptions

    writes  release/data/csv/<table>.csv              the seven tables
            release/data/csv/<table>.csv-schema.json  one schema per table
            release/data/csv/datapackage.json         all schemas + size + hash
            release/data/csv/README.md                table list

The release folder is regenerable: delete it and run this again.

    python -m malabardb.recipe_nutrient_table.export_release
"""

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from malabardb import paths

OUT = paths.ROOT / "release" / "data" / "csv"
TEMPLATE = Path(__file__).with_name("datapackage.template.json")

# --- settings: the choices a reader might want to change -------------------
LOW_COVERAGE = 0.90             # recipes below this mass coverage are flagged
KEEP_INGREDIENT_NOTES = True    # False leaves ingredients.notes out of the release
SALT_WATER = {"LOC001", "LOC002"}   # carry no nutrients; excluded from mass coverage

# Published order: the table a new reader should open first comes first.
TABLE_ORDER = ["recipe_nutrition", "recipes", "recipe_ingredients", "ingredients",
               "synonyms", "unit_conversions", "nutrients"]

# energy_source (how the pipeline got the energy value) -> which database the row is from
DATA_SOURCE = {"published": "IFCT2017", "atwater": "IFCT2017", "indb": "INDB",
               "usda": "USDA", "derived": "DERIVED", "local": "LOCAL"}

TRACE = []      # (table, text) lines, printed at the end


def trace(table: str, text: str) -> None:
    TRACE.append((table, text))


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load() -> dict:
    """Every database table as a DataFrame, plus the four side files."""
    with sqlite3.connect(paths.MALABARDB) as con:
        db = {t: pd.read_sql(f"SELECT * FROM {t}", con)
              for t in ("recipes", "recipe_ingredients", "ingredients",
                        "ifct_nutrients", "unit_conversions", "recipe_nutrition")}
    db["source"] = pd.read_csv(paths.KERALA_SOURCE, usecols=["recipe_id", "URL"])
    db["queue"] = pd.read_csv(paths.REVIEW_QUEUE_INGREDIENTS, dtype=str, keep_default_na=False)
    db["fallback"] = pd.read_csv(paths.FALLBACK_MAP, dtype=str, keep_default_na=False)
    db["local"] = pd.read_csv(paths.LOCAL_NUTRIENTS, dtype=str, keep_default_na=False)
    return db


# ---------------------------------------------------------------------------
# One function per published table
# ---------------------------------------------------------------------------

def recipes(db) -> pd.DataFrame:
    """One row per recipe.

    KEPT     recipe_id, dish_name, servings, servings_source
    RENAMED  source_row_id -> source_id (the recipe's Srno in the Kaggle dataset)
    ADDED    source_url, from kerala_source.csv
    DROPPED  instructions  (the source site's text; readers follow source_url)
             region, cuisine_raw  (the same value on every row)
    The 14 duplicate recipes are already gone: build_db removed them."""
    r = db["recipes"].merge(db["source"], on="recipe_id", how="left", validate="1:1")
    out = pd.DataFrame({
        "recipe_id": r["recipe_id"],
        "dish_name": r["dish_name"],
        "servings": r["servings"],
        "servings_source": r["servings_source"],
        "source_id": r["source_row_id"],
        "source_url": r["URL"],
    }).sort_values("recipe_id")
    trace("recipes", f"{len(out)} recipes")
    trace("recipes", "dropped columns: instructions, region, cuisine_raw")
    trace("recipes", "added column: source_url")
    changed = int((out["servings"] != out["servings_source"]).sum())
    trace("recipes", f"{changed} recipes have servings corrected (servings != servings_source)")
    return out


def recipe_ingredients(db) -> pd.DataFrame:
    """One row per ingredient line.

    KEPT     every column and every row of the database table
    DROPPED  size  (never extracted; empty on every row)
    ROUNDED  grams to 3 decimals"""
    out = db["recipe_ingredients"].drop(columns="size").copy()
    out["grams"] = out["grams"].round(3)
    out = out.sort_values(["recipe_id", "line_no"])
    trace("recipe_ingredients", f"{len(out)} lines")
    trace("recipe_ingredients", "dropped column: size (empty)")
    trace("recipe_ingredients", f"{int(out['grams'].isna().sum())} lines have no gram weight")
    return out


def link_method(q) -> str:
    """How one ingredient's link was decided, from its review-queue row."""
    if q["chosen_ifct_code"]:
        if q["status"] == "auto" and q["chosen_ifct_code"] == q["ifct_code"]:
            return "auto_confirmed"         # the machine's pick, accepted by the reviewer
        if q["rank_found"].isdigit():
            return "reviewer_selected"      # reviewer picked one of the machine's candidates
        return "reviewer_manual"            # reviewer supplied a code the machine never offered
    return "fallback" if q["fallback_code"] else "absent"


def ingredients(db, lines) -> pd.DataFrame:
    """One row per ingredient that a published recipe uses.

    KEPT     ingredient_id, norm_key, generic_name, source_db, link_status, hierarchical
    RENAMED  ifct_code -> food_code  (not every code is an IFCT code)
    ADDED    food_name      name of the linked food, from the nutrient table
             link_method, match_rule, candidate_rank   from the ingredient review queue
             n_lines        how many published lines use the ingredient
             notes          fallback_map note for fallback links, else the reviewer's note
    DROPPED  ingredients no published line uses: they occurred only in the
             duplicate recipes that build_db removed."""
    ing = db["ingredients"]
    used = lines["ingredient_id"].value_counts()
    n_before = len(ing)
    ing = ing[ing["ingredient_id"].isin(used.index)].copy()

    q = db["queue"].merge(db["fallback"].rename(columns={"note": "fallback_note"}),
                          on="norm_key", how="left").fillna("")
    q["link_method"] = q.apply(link_method, axis=1)
    q["notes"] = [r.fallback_note if r.fallback_code else r.reviewer_notes
                  for r in q.itertuples()]
    q["candidate_rank"] = pd.to_numeric(q["rank_found"], errors="coerce").astype("Int64")
    ing = ing.merge(q[["norm_key", "link_method", "link_rule", "candidate_rank", "notes"]],
                    on="norm_key", how="left", validate="1:1")

    names = db["ifct_nutrients"].set_index("ifct_code")["ifct_name"]
    out = pd.DataFrame({
        "ingredient_id": ing["ingredient_id"],
        "norm_key": ing["norm_key"],
        "generic_name": ing["generic_name"],
        "food_code": ing["ifct_code"],
        "food_name": ing["ifct_code"].map(names),
        "source_db": ing["source_db"],
        "link_status": ing["link_status"],
        "link_method": ing["link_method"],
        "match_rule": ing["link_rule"],
        "candidate_rank": ing["candidate_rank"],
        "hierarchical": ing["hierarchical"].where(ing["hierarchical"].isin(["Y", "N"])),
        "n_lines": ing["ingredient_id"].map(used),
        "notes": ing["notes"].str.strip().replace("", pd.NA),
    }).sort_values("ingredient_id")
    if not KEEP_INGREDIENT_NOTES:
        out = out.drop(columns="notes")
        trace("ingredients", "dropped column: notes (KEEP_INGREDIENT_NOTES is False)")

    stale = out[(out["link_method"] == "absent") != (out["link_status"] == "absent")]
    if len(stale):
        raise SystemExit(f"the database and fallback_map.csv disagree on {stale['norm_key'].tolist()}: "
                         f"run make db first")

    trace("ingredients", f"{len(out)} ingredients ({n_before - len(out)} unused ones dropped)")
    trace("ingredients", "added columns: food_name, link_method, match_rule, candidate_rank, n_lines, notes")
    trace("ingredients", "by source: " + ", ".join(
        f"{k} {v}" for k, v in out["source_db"].fillna("absent").value_counts().items()))
    return out


def nutrients(db, ingredients_out) -> pd.DataFrame:
    """One row per food that a published ingredient is linked to.

    KEPT     the per-100 g values exactly as in the nutrient table
    RENAMED  ifct_code -> food_code, ifct_name -> food_name
    ADDED    data_source   which database the row is from
             source_ref    for non-IFCT rows, where the values come from
    DROPPED  every food no published ingredient links to. The working table
             holds all of IFCT 2017; only the linked foods are published."""
    n = db["ifct_nutrients"]
    used = set(ingredients_out["food_code"].dropna())
    n_before = len(n)
    n = n[n["ifct_code"].isin(used)]
    ref = db["local"].set_index("ifct_code")["source_ref"]
    out = pd.DataFrame({
        "food_code": n["ifct_code"],
        "food_name": n["ifct_name"],
        "food_group": n["food_group"],
        "data_source": n["energy_source"].map(DATA_SOURCE),
        "energy_kcal": n["energy_kcal"],
        "energy_kj": n["energy_kj"],
        "energy_source": n["energy_source"],
        "protein_g": n["protein_g"],
        "carb_g": n["carb_g"],
        "fat_g": n["fat_g"],
        "fibre_g": n["fibre_g"],
        "source_ref": n["ifct_code"].map(ref).replace("", pd.NA),
    }).sort_values("food_code")
    trace("nutrients", f"{len(out)} foods published of {n_before} in the working table")
    trace("nutrients", "added columns: data_source, source_ref")
    trace("nutrients", "by source: " + ", ".join(
        f"{k} {v}" for k, v in out["data_source"].value_counts().items()))
    return out


def unit_conversions(db, lines, ingredients_out) -> pd.DataFrame:
    """One row per (ingredient, unit) pair that a published line uses.

    KEPT     ingredient_id, unit, has_qty, grams, notes
    ADDED    norm_key      repeated so the table can be read on its own
    SPLIT    the working 'source' label into three plain columns:
               'indb_units (analogy)' -> method indb_units, by_analogy true
               'indb_rule (manual)'   -> method indb_rule,  assigned_by reviewer
               'indb_units+rule'      -> method indb_units_rule
             assigned_by is 'pipeline' only for 'exact' and 'indb_rule'.
    DROPPED  pairs no published line uses (duplicate recipes again)."""
    uc = db["unit_conversions"]
    has_qty = lines["qty_num"].notna()
    used = set(zip(lines["ingredient_id"], lines["unit_key"], has_qty))
    n_before = len(uc)
    keep = [(i, u, bool(h)) in used
            for i, u, h in zip(uc["ingredient_id"], uc["unit"], uc["has_qty"])]
    uc = uc[keep]

    src = uc["source"]
    out = pd.DataFrame({
        "ingredient_id": uc["ingredient_id"],
        "norm_key": uc["ingredient_id"].map(ingredients_out.set_index("ingredient_id")["norm_key"]),
        "unit": uc["unit"],
        "has_qty": uc["has_qty"].astype(bool),
        "grams": uc["grams"],
        "method": (src.str.replace(r"\s*\(.*\)$", "", regex=True)
                      .str.replace("+", "_", regex=False)),
        "by_analogy": src.str.contains("analogy"),
        "assigned_by": src.isin(["exact", "indb_rule"]).map({True: "pipeline", False: "reviewer"}),
        "notes": uc["notes"].str.strip().replace("", pd.NA),
    }).sort_values(["ingredient_id", "unit", "has_qty"])
    trace("unit_conversions", f"{len(out)} pairs published of {n_before} in the working table")
    trace("unit_conversions", "added columns: norm_key, method, by_analogy, assigned_by (from 'source')")
    return out


def synonyms(lines, ingredients_out) -> pd.DataFrame:
    """One row per ingredient name as written in the published recipes.

    BUILT FROM the published lines themselves: each distinct name_raw, the
    ingredient it belongs to, that ingredient's linked food, and how many
    lines use that spelling."""
    s = (lines.groupby(["name_raw", "ingredient_id"]).size()
              .rename("n_lines").reset_index())
    s = s.merge(ingredients_out[["ingredient_id", "norm_key", "food_code", "food_name"]],
                on="ingredient_id", how="left", validate="m:1")
    out = (s[["name_raw", "norm_key", "ingredient_id", "food_code", "food_name", "n_lines"]]
           .sort_values(["food_name", "ingredient_id", "name_raw"], na_position="last"))
    trace("synonyms", f"{len(out)} spellings of {out['ingredient_id'].nunique()} ingredients")
    return out


def recipe_nutrition(db, lines, ingredients_out) -> pd.DataFrame:
    """One row per recipe: per-serving values and how complete they are.

    KEPT     every column of the database view (counts and per-serving values)
    ADDED    coverage_lines  n_counted / n_lines
             coverage_mass   share of the recipe's weight that has nutrient values,
                             leaving salt and water out of both sides
             low_coverage    true when coverage_mass is below LOW_COVERAGE:
                             the per-serving values miss a main ingredient
    ROUNDED  per-serving values to 1 decimal, coverage to 4"""
    rn = db["recipe_nutrition"].copy()

    code = lines["ingredient_id"].map(ingredients_out.set_index("ingredient_id")["food_code"])
    m = lines[~code.isin(SALT_WATER)].assign(code=code)
    total = m.groupby("recipe_id")["grams"].sum()
    counted = m[m["code"].notna()].groupby("recipe_id")["grams"].sum()
    mass = (counted.reindex(total.index).fillna(0) / total).rename("coverage_mass")

    rn = rn.merge(mass, on="recipe_id", how="left")
    rn["coverage_lines"] = (rn["n_counted"] / rn["n_lines"]).round(4)
    rn["coverage_mass"] = rn["coverage_mass"].round(4)
    rn["low_coverage"] = rn["coverage_mass"] < LOW_COVERAGE
    per_serving = ["kcal_per_serving", "protein_g_per_serving",
                   "carb_g_per_serving", "fat_g_per_serving"]
    rn[per_serving] = rn[per_serving].round(1)

    out = rn[["recipe_id", "dish_name", "servings", "n_lines", "n_linked", "n_grams",
              "n_counted", "coverage_lines", "coverage_mass", "low_coverage"]
             + per_serving].sort_values("recipe_id")
    low = out.loc[out["low_coverage"], "recipe_id"].tolist()
    trace("recipe_nutrition", f"{len(out)} recipes")
    trace("recipe_nutrition", "added columns: coverage_lines, coverage_mass, low_coverage")
    trace("recipe_nutrition", f"{len(low)} recipes flagged low_coverage (< {LOW_COVERAGE}): {low}")
    return out


# ---------------------------------------------------------------------------
# Write, describe, check
# ---------------------------------------------------------------------------

def write_csv(df: pd.DataFrame, path: Path) -> None:
    """utf-8, '\\n' line endings on every operating system, so the hash in
    datapackage.json is the same wherever the release is built."""
    df.to_csv(path, index=False, lineterminator="\n", encoding="utf-8")


def check(name: str, df: pd.DataFrame, schema: dict, tables: dict) -> list[str]:
    """Compare one written table with its schema. Returns a list of problems."""
    problems = []
    fields = [f["name"] for f in schema["fields"]]
    if list(df.columns) != fields:
        problems.append(f"columns differ from schema: table has {sorted(set(df.columns) - set(fields))} "
                        f"extra, schema has {sorted(set(fields) - set(df.columns))} extra, or order differs")
        return problems
    for f in schema["fields"]:
        col, rules = df[f["name"]], f.get("constraints", {})
        if rules.get("required") and col.isna().any():
            problems.append(f"{f['name']}: {int(col.isna().sum())} blank values in a required column")
        if rules.get("unique") and col.dropna().duplicated().any():
            problems.append(f"{f['name']}: duplicate values in a unique column")
        if "enum" in rules:
            bad = sorted(set(col.dropna().astype(str)) - set(rules["enum"]))
            if bad:
                problems.append(f"{f['name']}: values not in the allowed list: {bad}")
        if f["type"] in ("integer", "number") and not pd.api.types.is_numeric_dtype(col):
            problems.append(f"{f['name']}: declared {f['type']} but holds text")
    key = schema.get("primaryKey")
    if key and df.duplicated(key).any():
        problems.append(f"primary key {key} is not unique")
    for fk in schema.get("foreignKeys", []):
        child = df[fk["fields"][0]].dropna()
        parent = tables[fk["reference"]["resource"]][fk["reference"]["fields"][0]]
        missing = sorted(set(child) - set(parent))
        if missing:
            problems.append(f"{fk['fields'][0]}: {len(missing)} values not found in "
                            f"{fk['reference']['resource']}: {missing[:5]}")
    return problems


def readme(package: dict, tables: dict) -> str:
    rows = "\n".join(f"| {r['name']} | csv | {len(tables[r['name']])} | {r['bytes']} | {r['title']} |"
                     for r in package["resources"])
    return (f"# {package['title']}\n\n{package['description']}\n\n## RESOURCES\n\n"
            f"| Name | Format | Rows | Size (bytes) | Title |\n|---|---|---|---|---|\n{rows}\n\n"
            "Each `<name>.csv` has a matching `<name>.csv-schema.json` describing its columns. "
            "`datapackage.json` describes all of them together, including how the tables join.\n")


def build() -> None:
    db = load()

    tables = {}
    tables["recipes"] = recipes(db)
    tables["recipe_ingredients"] = lines = recipe_ingredients(db)
    tables["ingredients"] = ing = ingredients(db, lines)
    tables["nutrients"] = nutrients(db, ing)
    tables["unit_conversions"] = unit_conversions(db, lines, ing)
    tables["synonyms"] = synonyms(lines, ing)
    tables["recipe_nutrition"] = recipe_nutrition(db, lines, ing)

    OUT.mkdir(parents=True, exist_ok=True)
    package = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    by_name = {r["name"]: r for r in package["resources"]}
    if set(by_name) != set(tables):
        raise SystemExit(f"template describes {sorted(by_name)}, script builds {sorted(tables)}")
    package["resources"] = [by_name[t] for t in TABLE_ORDER]
    package["created"] = datetime.now().astimezone().isoformat(timespec="seconds")

    problems = {}
    for name in TABLE_ORDER:
        resource, path = by_name[name], OUT / f"{name}.csv"
        write_csv(tables[name], path)
        written = pd.read_csv(path)             # check what a reader will actually get
        data = path.read_bytes()
        resource["bytes"] = len(data)
        resource["hash"] = hashlib.md5(data).hexdigest()
        (OUT / f"{name}.csv-schema.json").write_text(
            json.dumps(resource["schema"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        tables[name] = written
    for name in TABLE_ORDER:
        problems[name] = check(name, tables[name], by_name[name]["schema"], tables)

    (OUT / "datapackage.json").write_text(
        json.dumps(package, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUT / "README.md").write_text(readme(package, tables), encoding="utf-8")

    report(tables, problems)


def report(tables: dict, problems: dict) -> None:
    print(f"release written to {OUT}\n")
    for name in TABLE_ORDER:
        df = tables[name]
        print(f"{name}.csv  {len(df)} rows x {len(df.columns)} columns")
        for table, text in TRACE:
            if table == name:
                print(f"    {text}")
        for p in problems[name]:
            print(f"    PROBLEM  {p}")
    n = sum(len(p) for p in problems.values())
    print(f"\nschema check: {n} problem(s)" if n else "\nschema check: every table matches its schema")

    ing = tables["ingredients"]
    unset = ing[(ing["link_status"] == "linked") & ing["hierarchical"].isna()]
    if len(unset):
        print(f"\nto fill in review_queue_ingredients.csv: {len(unset)} linked ingredients have no "
              f"hierarchical value (Y/N): {unset['norm_key'].tolist()}")


if __name__ == "__main__":
    build()