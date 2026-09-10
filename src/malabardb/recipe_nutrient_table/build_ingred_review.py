"""
Step 6: build_ingred_review.py: walk the matching ladder over every recipe key
and write ONE file for human review.

    python -m malabardb.recipe_nutrient_table.build_ingred_review

    For each distinct ingredient key (recipe_keys.csv), try the rungs in
    match_to_ifct.py strictest-first: exact, tokenset, subset. Stop at the
    first that finds anything. If none do, rank the partial candidates.
    Then sort every key into one of three buckets:

        auto       one boolean-rung hit, code filled in   -> confirm or correct
        review     several candidates                     -> pick one, or none
        none       no candidate at any rung               -> search IFCT by hand,
                                                             or mark absent

    Strictest-first matters: 'drumstick' is unique (D046) at exact but ties
    (C019, D046) at subset. Stopping early keeps the confident answer.

REVIEW COLUMNS (the only ones that survive a rerun)
    chosen_ifct_code  the final code for this key. Fill it on EVERY row you
                      review, including auto rows (copy the code if it is
                      right). Blank = not reviewed.
    rank_found        where the right answer sat in candidate_codes:
                        1, 2, ...   position in the list (1 on a correct auto)
                        miss        a correct IFCT row exists but no rung found it
                        absent      IFCT has no usable row for this food
                      This column is the evaluation: precision of auto rows,
                      top-k recall of the candidate generator.
    hierarchical      Y if the accepted code is broader than a strict match
                      (dry red chilli -> G022 "Chillies, red"). Counted as
                      correct by convention; tracked so the strict number is
                      still computable.
    reviewer_notes    free text.

ORPHANS (n_lines = 0)
    Fixing name_raw upstream renames keys: 'ginger paste' became 'ginger',
    'mixed nut almond' became 'mixed nut'. A tagged key that no longer appears
    in recipe_keys.csv is kept in this file rather than dropped, so the
    decision stays visible and the audit trail survives.

    Its n_lines is 0 because zero lines of the CURRENT corpus use that
    spelling. That is not a demotion, it is the true count: the line it used
    to describe is now counted under the successor key. Giving orphans their
    old counts would push sum(n_lines) past the corpus total and inflate every
    coverage percentage computed from this file.

    successor_key names the current key the orphan most likely became (highest
    token overlap). It is a HINT, never an automatic transfer: 'mixed nut
    almond' -> H001 (almond) was decided when the key still said almond, and
    'mixed nut' may deserve a different code. Move the tag across yourself,
    then delete the orphan row -- it will not come back.

Design follows StandFood (Eftimov et al. 2017): the machine generates
candidates, a human accepts.
"""
import csv
import re
import shutil
from collections import Counter
from pathlib import Path

from malabardb import paths
from malabardb.recipe_nutrient_table.match_to_ifct import (
    op_exact, op_tokenset, op_subset, rank_partial, overlap, PARTIAL_MIN_SCORE)

LADDER = [("exact", op_exact), ("tokenset", op_tokenset), ("subset", op_subset)]
PARTIAL_TOP_K = 5   # ties at the cutoff are kept

HUMAN_COLUMNS = ["chosen_ifct_code", "rank_found", "hierarchical", "reviewer_notes"]

# IFCT publishes an "all varieties" aggregate for some variety spreads
# (brinjal D031, green chilli G008). When a key ties across nothing but
# varieties of one food, IFCT's own average is the defensible default.
ALL_VARIETIES_RE = re.compile(r"\ball\s+varieties\b", re.IGNORECASE)


def load_keys(path: Path) -> dict[str, dict]:
    """recipe_keys.csv -> {norm_key: {n_lines, names}}. Spellings that share a
    key pool their line counts; the most common spelling becomes generic_name."""
    keys: dict[str, dict] = {}
    for r in csv.DictReader(path.open(encoding="utf-8")):
        d = keys.setdefault(r["norm_key"], {"n_lines": 0, "names": Counter()})
        d["n_lines"] += int(r["n_lines"])
        d["names"][r["name_raw"]] += int(r["n_lines"])
    return keys


def load_prior(path: Path) -> dict[str, dict]:
    """Old queue -> {norm_key: {human column: value}}. Only HUMAN_COLUMNS are
    read; everything else is machine output that must be regenerated."""
    if not path.exists():
        return {}
    return {r["norm_key"]: {c: r.get(c, "") for c in HUMAN_COLUMNS}
            for r in csv.DictReader(path.open(encoding="utf-8"))}


def match_key(key: str, ifct: list[dict]) -> tuple[str, list[dict]]:
    """Walk the ladder. Returns (rule, hits); rule is 'none' if nothing hit."""
    for rule, op in LADDER:
        hits = sorted((c for c in ifct if op(key, c["name_key"])),
                      key=lambda c: c["code"])
        if hits:
            return rule, hits
    ranked = rank_partial(key, ifct, PARTIAL_MIN_SCORE, PARTIAL_TOP_K)
    if ranked:
        return "partial", [c for c, _ in ranked]
    return "none", []


def classify(rule: str, hits: list[dict]) -> tuple[str, str]:
    """(status, auto_code). Partial candidates never auto-fill."""
    if not hits:
        return "none", ""
    if rule == "partial":
        return "review", ""
    codes = {h["code"] for h in hits}
    if len(codes) == 1:
        return "auto", hits[0]["code"]
    aggregate = [h for h in hits if ALL_VARIETIES_RE.search(h["name"])]
    if aggregate and len({h["grup"] for h in hits}) == 1:
        return "auto", aggregate[0]["code"]
    return "review", ""


def best_successor(orphan: str, current: list[str]) -> str:
    """Current key with the highest token overlap against an orphaned key.
    Same measure the ladder uses. Blank below PARTIAL_MIN_SCORE, where the two
    keys share less than a head noun and the guess would be noise."""
    scored = sorted(((overlap(orphan, k), k) for k in current),
                    key=lambda sk: (-sk[0], sk[1]))
    return scored[0][1] if scored and scored[0][0] >= PARTIAL_MIN_SCORE else ""


def build_row(key, n_lines, generic_name, ifct, prior, successor=""):
    """One queue row: machine columns, then any reviewer input carried over."""
    rule, hits = match_key(key, ifct)
    status, code = classify(rule, hits)
    row = {
        "norm_key": key,
        "n_lines": n_lines,
        "generic_name": generic_name,
        "status": status,
        "link_rule": rule,
        "ifct_code": code,                     # machine's answer on auto rows
        "food_group": next((h["grup"] for h in hits if h["code"] == code), ""),
        "n_candidates": len({h["code"] for h in hits}),
        "candidate_codes": ";".join(h["code"] for h in hits),
        "candidate_names": ";".join(h["name"] for h in hits),
        "successor_key": successor,
    }
    row.update({c: "" for c in HUMAN_COLUMNS})
    row.update({c: v for c, v in prior.get(key, {}).items() if v.strip()})
    return row


def main() -> None:
    keys = load_keys(paths.RECIPE_KEYS)
    ifct = list(csv.DictReader(paths.IFCT_NORMALIZED.open(encoding="utf-8")))
    out = paths.REVIEW_QUEUE_INGREDIENTS
    prior = load_prior(out)            # read BEFORE regenerating over it

    rows = [build_row(key, d["n_lines"], d["names"].most_common(1)[0][0],
                      ifct, prior)
            for key, d in sorted(keys.items(), key=lambda kv: -kv[1]["n_lines"])]

    # Tagged keys that upstream renaming removed from the corpus.
    current = sorted(keys)
    orphans = sorted(k for k, v in prior.items()
                     if any(x.strip() for x in v.values()) and k not in keys)
    rows += [build_row(k, 0, "", ifct, prior, best_successor(k, current))
             for k in orphans]

    if out.exists():
        shutil.copy2(out, out.with_suffix(".bak.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # ---- report. Orphans excluded: they describe zero current lines. ----
    live = [r for r in rows if r["n_lines"] > 0]
    total = sum(r["n_lines"] for r in live)
    print(f"{len(live)} keys / {total} lines -> {out}\n")
    print(f"{'status':<8}{'keys':>6}{'lines':>8}{'line%':>8}")
    for s in ("auto", "review", "none"):
        sel = [r for r in live if r["status"] == s]
        ln = sum(r["n_lines"] for r in sel)
        print(f"{s:<8}{len(sel):>6}{ln:>8}{ln / total * 100:>7.1f}%")
    print("\nby rung: " + ", ".join(
        f"{k}={sum(r['link_rule'] == k for r in live)}"
        for k in ("exact", "tokenset", "subset", "partial", "none")))

    if prior:
        tagged = sum(any(r[c] for c in HUMAN_COLUMNS) for r in live)
        print(f"\ncarried forward: {tagged} tagged keys")
    if orphans:
        by_key = {r["norm_key"]: r for r in rows}
        print(f"\n{len(orphans)} orphaned keys kept with n_lines=0 "
              f"(move the tag to the successor, then delete the row):")
        print(f"    {'orphan':<36}{'code':<7}{'successor'}")
        for k in orphans:
            r = by_key[k]
            print(f"    {k:<36}{r['chosen_ifct_code']:<7}"
                  f"{r['successor_key'] or '(no close match)'}")


if __name__ == "__main__":
    main()