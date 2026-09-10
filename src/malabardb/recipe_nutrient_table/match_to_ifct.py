"""
Step 5: match_to_ifct.py: the recipe side of the recipe->IFCT join, plus the operators
that decide whether two keys name the same food.

    prepare        DB.recipe_ingredients.name_raw -> recipe_keys.csv
                   One row per distinct spelling: name_raw, norm_key, n_lines.
    operators      op_exact / op_tokenset / op_subset / rank_partial.
                   build_ingred_review.py IMPORTS these and walks the ladder itself. 
    match          Applies ONE rung to every key and prints the keys/lines
                   table for that rung. Writes diag_<rung>.csv. 
                   Exists to measure how much each rung contributes, which is a methods-section number.

Run:
    python -m malabardb.recipe_nutrient_table.match_to_ifct prepare
    python -m malabardb.recipe_nutrient_table.match_to_ifct match --operator subset   # optional

Both sides are normalized by the SAME function (malabardb.normalize), which is
what makes a recipe key and an IFCT key comparable at all. 
"""
import argparse
import csv
import sqlite3
from collections import Counter
from pathlib import Path

from malabardb import paths


'''The operators: one number, four thresholds

Every rung is a condition on the overlap coefficient of the two token sets,
      overlap(A, B) = |A & B| / min(|A|, |B|)
read as "what share of the SHORTER name's words does the other name account for?" Extra words on either side are free.

  rung      condition                          example that reaches this rung
  exact     overlap = 1, same length, same order   'salt' / 'salt'
  tokenset  overlap = 1, same length               'green chilli' / 'chilli green'
  subset    overlap = 1                            'garlic' / 'garlic big clove'
  partial   overlap >= 0.5, ranked, human picks    'carrot gajjar' / 'carrot orange'

Relation to Kalra's modified Jaccard J* = |A & B| / |A| (A = recipe phrase):
identical whenever the recipe phrase is the shorter one, which is the normal
case. They differ only when the recipe phrase is LONGER, e.g. bilingual lines:

  A = {turmeric, powder, haldi}   B = IFCT G033 {turmeric, powder}
  J*      = 2/3 = 0.67   (imperfect: 'haldi' is an unexplained recipe word)
  overlap = 2/2 = 1.0    (perfect containment -> subset rung)'''


def tokens(key: str) -> set[str]:
    return set(key.split())


def overlap(q: str, c: str) -> float:
    """Overlap coefficient of two normalized keys. 0.0 if either is empty."""
    A, B = tokens(q), tokens(c)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def op_exact(q: str, c: str) -> bool:
    """Identical strings. Word order matters."""
    return q == c


def op_tokenset(q: str, c: str) -> bool:
    """Same words, any order."""
    return tokens(q) == tokens(c)


def op_subset(q: str, c: str) -> bool:
    """One key's words are fully contained in the other's, either direction.
    This IS overlap == 1.0; written that way so the ladder reads as thresholds
    on one number rather than as four unrelated tests."""
    return overlap(q, c) == 1.0


# Why 0.5: a shared head noun plus one unshared qualifier on each side scores
# exactly 1/2 ('carrot gajjar' vs 'carrot orange'). Below that, the two names
# share less than the head noun, and the candidate is noise.
PARTIAL_MIN_SCORE = 0.5


def rank_partial(key: str, ifct: list[dict],
                 min_score: float = PARTIAL_MIN_SCORE,
                 top_k: int = 5) -> list[tuple[dict, float]]:
    """Score every IFCT row against `key`, return the best few above min_score.

    Only reached when the three boolean rungs all found nothing, so no
    candidate here can score 1.0. Full containment was caught a rung up.
    Ties at the top_k cutoff are kept, never cut mid-tie. """

    scored = [(c, overlap(key, c["name_key"])) for c in ifct]
    scored = [(c, s) for c, s in scored if s >= min_score]
    scored.sort(key=lambda cs: (-cs[1], cs[0]["code"]))
    if len(scored) <= top_k:
        return scored
    cutoff = scored[top_k - 1][1]
    return [(c, s) for c, s in scored if s >= cutoff]


OPERATORS = {"exact": op_exact, "tokenset": op_tokenset, "subset": op_subset}


# prepare: the recipe side of the join
def prepare(db: Path, out: Path) -> None:
    """DB -> recipe_keys.csv, one row per distinct name_raw spelling.

    n_lines is the number of ingredient LINES using that spelling, summed over
    every recipe. It is the review priority: the key with the most lines is
    the decision that affects the most data. It is not a recipe count; two
    lines in one recipe count twice.
    """
    from malabardb.normalize import SpacyNormalizer

    con = sqlite3.connect(db)
    lines = Counter(name for (name,) in
                    con.execute("SELECT name_raw FROM recipe_ingredients"))
    con.close()

    names = sorted(lines)
    keys = SpacyNormalizer().normalize_many(names)   # keys[i] <-> names[i]

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name_raw", "norm_key", "n_lines"])
        for name, key in zip(names, keys):
            w.writerow([name, key, lines[name]])
    print(f"{len(names)} distinct name_raw -> {len(set(keys))} distinct keys, "
          f"{sum(lines.values())} lines -> {out}")


# match: per-rung diagnostic (not consumed by the pipeline)

def match(queries: Path, index: Path, operator: str, out: Path) -> None:
    """Apply one rung to every key and report keys/lines per bucket.

    Output is long format: one row per (key, matched code) pair, so a key that
    ties 8 codes gives 8 rows and a no_hit key gives 1 row with blank code
    columns. Every key appears; nothing disappears. """
    
    op = OPERATORS[operator]

    # Collapse spellings to keys; 'Salt' and 'salt' add their line counts.
    keys: Counter = Counter()
    for r in csv.DictReader(queries.open(encoding="utf-8")):
        keys[r["norm_key"]] += int(r["n_lines"])

    ifct = list(csv.DictReader(index.open(encoding="utf-8")))

    rows = []
    for key, n_lines in sorted(keys.items(), key=lambda kv: -kv[1]):
        hits = sorted((c for c in ifct if op(key, c["name_key"])),
                      key=lambda c: c["code"])
        codes = {c["code"] for c in hits}
        bucket = ("no_hit" if not codes else
                  "unique" if len(codes) == 1 else "multiple")
        for c in hits or [{"code": "", "name": ""}]:
            rows.append({"norm_key": key, "n_lines": n_lines, "bucket": bucket,
                         "n_codes": len(codes),
                         "ifct_code": c["code"], "ifct_name": c["name"]})

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Summary: dedupe back to one entry per key, then count per bucket.
    per_key = {r["norm_key"]: r for r in rows}
    n_terms = len(per_key)
    n_lines = sum(r["n_lines"] for r in per_key.values())
    print(f"\noperator: {operator}    {n_terms} keys / {n_lines} lines")
    print(f"{'bucket':<10}{'keys':>6}{'key%':>8}{'lines':>7}{'line%':>8}")
    for b in ("unique", "multiple", "no_hit"):
        sel = [r for r in per_key.values() if r["bucket"] == b]
        ln = sum(r["n_lines"] for r in sel)
        print(f"{b:<10}{len(sel):>6}{len(sel)/n_terms*100:>7.1f}%"
              f"{ln:>7}{ln/n_lines*100:>7.1f}%")
    print(f"\nwrote {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare")
    p.add_argument("--db", type=Path, default=paths.MALABARDB)
    p.add_argument("--out", type=Path, default=paths.RECIPE_KEYS)

    m = sub.add_parser("match", help="per-rung diagnostic; not a pipeline stage")
    m.add_argument("--operator", choices=OPERATORS, required=True)
    m.add_argument("--queries", type=Path, default=paths.RECIPE_KEYS)
    m.add_argument("--index", type=Path, default=paths.IFCT_NORMALIZED)
    m.add_argument("--out", type=Path, default=None)

    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.db, a.out)
    else:
        match(a.queries, a.index, a.operator,
              a.out or paths.PROCESSED / f"diag_{a.operator}.csv")


if __name__ == "__main__":
    main()