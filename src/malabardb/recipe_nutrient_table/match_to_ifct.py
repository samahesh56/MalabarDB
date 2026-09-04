"""
match_to_ifct.py: link recipe ingredient names to IFCT 2017 food codes.

Run order:
    python -m malabardb.recipe_nutrient_table.match_to_ifct prepare
    python -m malabardb.recipe_nutrient_table.match_to_ifct match --operator exact
    python -m malabardb.recipe_nutrient_table.match_to_ifct match --operator tokenset
    python -m malabardb.recipe_nutrient_table.match_to_ifct match --operator subset

Each operator relaxes exactly one assumption of the one before it:
    exact    : keys are identical strings
    tokenset : ... ignoring word order            (green chilli = chilli green)
    subset   : ... tolerating extra qualifiers    (garlic <= garlic big clove)

Control flow follows StandFood (Eftimov et al. 2017):
every matching IFCT code is returned, ties included, for human selection.
"""
import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path


'''operators help answer if the 2 normalized keys refer 
to the same food (recipe side and IFCT side)''' 

def op_exact(q: str, c: str) -> bool:
    return q == c

def op_tokenset(q: str, c: str) -> bool:
    return set(q.split()) == set(c.split())

def op_subset(q: str, c: str) -> bool:
    """Overlap coefficient |A&B| / min(|A|,|B|) == 1.0, i.e. the shorter
    token set is fully contained in the longer one. Handles both directions:
    query shorter ({garlic} in {garlic,big,clove}) and query longer
    ({turmeric,powder} in {turmeric,powder,haldi}).
    """
    A, B = set(q.split()), set(c.split())
    return A <= B or B <= A

def overlap(q: str, c: str) -> float:
    """Overlap coefficient: |A & B| / min(|A|, |B|).
 
    Shared tokens as a fraction of the SHORTER name.
    'carrot gajjar' vs 'carrot orange' shares one of two possible tokens either way
    """
    A, B = set(q.split()), set(c.split())
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))

def rank_partial(key: str, ifct: list[dict],
                 min_score: float = 0.5, top_k: int = 5) -> list[tuple[dict, float]]:
    """Score every IFCT row, return the best few ABOVE min_score.
 
    Only reached when exact/tokenset/subset all found nothing, which means no
    candidate here can score 1.0. full containment would have been caught a rung earlier. 

    """
    scored = [(c, overlap(key, c["name_key"])) for c in ifct]
    scored = [(c, s) for c, s in scored if s >= min_score]
    scored.sort(key=lambda cs: (-cs[1], cs[0]["code"]))
    if len(scored) <= top_k:
        return scored
    cutoff = scored[top_k - 1][1]
    return [(c, s) for c, s in scored if s >= cutoff]

OPERATORS = {"exact": op_exact, "tokenset": op_tokenset, "subset": op_subset}


''' prepare's goal is to turn the recipe_ingredients raw lines into one row per distinct ingredient name
SpaCy runs and it includes the normalized key and how many lines use it. 
'''

def prepare(db: Path, out: Path) -> None:
    from malabardb.ingred_vocab.normalize_vocab import SpacyNormalizer

    # Count how many lines each distinct name_raw appears in.
    # Counter is a dict whose values are counts: lines["Salt"] -> 119.
    con = sqlite3.connect(db)
    lines = Counter()
    for (name,) in con.execute("SELECT name_raw FROM recipe_ingredients"):
        lines[name] += 1
    con.close()

    # Normalize every distinct name in one batch. names and keys are parallel
    # lists: keys[i] is the normalized form of names[i].
    names = sorted(lines)
    keys = SpacyNormalizer().normalize_many(names)

    # Write the cache: one row per distinct name_raw.
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name_raw", "norm_key", "n_lines"])
        for name, key in zip(names, keys):
            w.writerow([name, key, lines[name]])
    print(f"{len(names)} distinct name_raw -> {len(set(keys))} distinct keys, "
          f"{sum(lines.values())} lines")


# match's goal is to ask the chosen op which IFCT tows match the norm_key.

def match(queries: Path, index: Path, operator: str, out: Path) -> None:
    op = OPERATORS[operator]

    # Collapse the cache to one entry per distinct norm_key. Several name_raw
    # spellings ("Salt", "salt") share a key, so their line counts add up.
    # One key = one linkage decision.
    keys: dict[str, int] = Counter()
    for r in csv.DictReader(queries.open(encoding="utf-8")):
        keys[r["norm_key"]] += int(r["n_lines"])

    # Load the whole IFCT side into memory: 542 dicts, one per food row.
    ifct = list(csv.DictReader(index.open(encoding="utf-8")))

    # The output is "long format": one CSV row per (key, matched code) PAIR,
    # not per key. A key that ties 8 codes produces 8 rows sharing the same
    # norm_key/bucket; a no_hit key produces 1 row with blank code columns,
    # so every key appears in the report and nothing silently disappears.
    rows = []
    for key, n_lines in sorted(keys.items(), key=lambda kv: -kv[1]):
        # every IFCT row the operator says yes to
        hits = sorted((c for c in ifct if op(key, c["name_key"])),
                      key=lambda c: c["code"])
        codes = {c["code"] for c in hits}
        bucket = ("no_hit" if not codes else
                  "unique" if len(codes) == 1 else "multiple")
        # `hits or [blank]`: if hits is empty, loop once over a blank stand-in
        # so the no_hit key still gets its one row.
        for c in hits or [{"code": "", "name": ""}]:
            rows.append({"norm_key": key, "n_lines": n_lines, "bucket": bucket,
                         "n_codes": len(codes),
                         "ifct_code": c["code"], "ifct_name": c["name"]})

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Summary table. The report has one row per (key, code) pair, so first
    # deduplicate back to one entry per key (any row of the key carries its
    # bucket and n_lines). Then count terms and lines per bucket
    per_key = {r["norm_key"]: r for r in rows}
    n_terms, n_lines = len(per_key), sum(r["n_lines"] for r in per_key.values())
    print(f"\noperator: {operator}    {n_terms} keys / {n_lines} lines")
    print(f"{'bucket':<10}{'terms':>6}{'term%':>8}{'lines':>7}{'line%':>8}")
    for b in ("unique", "multiple", "no_hit"):
        sel = [r for r in per_key.values() if r["bucket"] == b]
        ln = sum(r["n_lines"] for r in sel)
        print(f"{b:<10}{len(sel):>6}{len(sel)/n_terms*100:>7.1f}%"
              f"{ln:>7}{ln/n_lines*100:>7.1f}%")
    print(f"\nwrote {out}")


# argparse gives the script a command line to make subparsing simpler 
def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)   # a.cmd = which mini-program ran

    p = sub.add_parser("prepare")
    p.add_argument("--db", type=Path, default=Path("malabardb.db"))
    p.add_argument("--out", type=Path,
                   default=Path("data/processed/recipe_keys.csv"))

    m = sub.add_parser("match")
    m.add_argument("--operator", choices=OPERATORS, required=True)  # rejects anything not in the dict
    m.add_argument("--queries", type=Path,
                   default=Path("data/processed/recipe_keys.csv"))
    m.add_argument("--index", type=Path,
                   default=Path("data/processed/ifct_normalized.csv"))
    m.add_argument("--out", type=Path, default=None)

    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.db, a.out)
    else:
        # default output name carries the operator, so runs never overwrite
        # each other: match_exact.csv, match_tokenset.csv, match_subset.csv
        match(a.queries, a.index, a.operator,
              a.out or Path(f"data/processed/match_{a.operator}.csv"))


if __name__ == "__main__":
    main()