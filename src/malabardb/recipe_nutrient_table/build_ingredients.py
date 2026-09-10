"""Step 9: build_ingredients.py -- turn the frozen mapping into a queryable
linkage layer.

Reads final/ingredient_ifct_map.csv (the human artifact) and writes one
ingredients row per normalized ingredient key, then points every recipe line
at its row via ingredient_id.

Keyed on norm_key, NOT on ifct_code: eight keys share G022, and 'red chilli
powder' and 'dry red chilli' must stay distinct because a teaspoon of each
weighs a different amount. They reference the same nutrient row; they are
not the same ingredient.

ifct_code is nullable. An absent ingredient still gets an ingredient_id --
it exists, it just has no nutrients yet. That makes coverage a property of
the data (WHERE ifct_code IS NULL) rather than a number computed elsewhere,
and means adding a fallback tier fills a column instead of reshaping a table.

Must run AFTER build_db.py, which recreates recipe_ingredients with
ingredient_id NULL.

    python -m malabardb.recipe_nutrient_table.build_ingredients
"""
import csv
import sqlite3

from malabardb import paths


def load(path):
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def resolve(row):
    """(code, source) for one mapping row. IFCT wins; fallback fills gaps."""
    ifct = row["chosen_ifct_code"].strip()
    if ifct:
        return ifct, "IFCT2017"
    fb = row.get("fallback_code", "").strip()
    if fb:
        return fb, row.get("fallback_source", "").strip() or "UNKNOWN"
    return None, None


def main() -> None:
    con = sqlite3.connect(paths.MALABARDB)
    cur = con.cursor()

    mapping = load(paths.INGREDIENT_IFCT_MAP)
    keys = load(paths.RECIPE_KEYS)
    valid = {c for (c,) in cur.execute("SELECT ifct_code FROM ifct_nutrients")}

    # -- guard: a wiped DB must never be mistaken for zero coverage ---------
    n_lines = cur.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0]
    expected = sum(int(r["n_lines"]) for r in mapping)
    if n_lines != expected:
        raise SystemExit(
            f"DB has {n_lines} ingredient lines, frozen map accounts for "
            f"{expected}. Rerun build_db.py, or refreeze the map.")

    # -- validate: every hand-typed code must resolve to a nutrient row -----
    bad = sorted({code for r in mapping
                  if (code := resolve(r)[0]) and code not in valid})
    if bad:
        raise SystemExit(f"codes not in ifct_nutrients: {bad}")

    mapped = {r["norm_key"] for r in mapping}
    missing = sorted({r["norm_key"] for r in keys} - mapped)
    if missing:
        raise SystemExit(f"{len(missing)} keys have no mapping row: {missing[:10]}")

    print(f"validated {len(mapping)} keys against {len(valid)} nutrient codes")

    # -- insert ------------------------------------------------------------
    cur.execute("DELETE FROM ingredients")          # idempotent: safe to rerun
    for r in mapping:
        code, source = resolve(r)
        cur.execute(
            """INSERT INTO ingredients
                 (norm_key, generic_name, ifct_code, source_db,
                  hierarchical, link_status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (r["norm_key"], r["generic_name"], code, source,
             r["hierarchical"].strip() or None,
             "linked" if code else "absent"))

    # -- backfill ingredient_id --------------------------------------------
    # norm_key comes from recipe_keys.csv, the same artifact the matcher used.
    # Re-running the normalizer here could drift with a spaCy version and
    # silently orphan rows.
    ids = dict(cur.execute("SELECT norm_key, ingredient_id FROM ingredients"))
    cur.execute("UPDATE recipe_ingredients SET ingredient_id = NULL")
    for r in keys:
        cur.execute(
            "UPDATE recipe_ingredients SET ingredient_id = ? WHERE name_raw = ?",
            (ids[r["norm_key"]], r["name_raw"]))
    con.commit()

    # -- report ------------------------------------------------------------
    orphan = cur.execute(
        "SELECT COUNT(*) FROM recipe_ingredients WHERE ingredient_id IS NULL"
    ).fetchone()[0]

    total, ifct, fallback, absent = cur.execute("""
        SELECT COUNT(*),
               SUM(i.source_db = 'IFCT2017'),
               SUM(i.ifct_code IS NOT NULL AND i.source_db <> 'IFCT2017'),
               SUM(i.ifct_code IS NULL)
          FROM recipe_ingredients ri
          JOIN ingredients i USING (ingredient_id)""").fetchone()

    hier = cur.execute("""
        SELECT COUNT(*) FROM recipe_ingredients ri
          JOIN ingredients i USING (ingredient_id)
         WHERE i.hierarchical = 'Y'""").fetchone()[0]

    print(f"inserted {len(mapping)} ingredients")
    print(f"lines {total}")
    print(f"  IFCT-linked   {ifct:5d}  ({ifct / total * 100:5.1f}%)")
    print(f"  fallback      {fallback:5d}  ({fallback / total * 100:5.1f}%)")
    print(f"  total linked  {ifct + fallback:5d}  "
          f"({(ifct + fallback) / total * 100:5.1f}%)")
    print(f"  absent        {absent:5d}  ({absent / total * 100:5.1f}%)")
    print(f"  hierarchical  {hier:5d}  ({hier / total * 100:5.1f}% of all lines)")
    print(f"lines with NO ingredient_id: {orphan}   <-- must be 0")

    con.close()


if __name__ == "__main__":
    main()