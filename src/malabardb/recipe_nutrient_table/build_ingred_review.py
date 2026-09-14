"""
Step 6: build_ingred_review.py - walk the matching ladder over every recipe
key and write a queue for human review.
    recipe_keys.csv + ifct_normalized.csv -> review_queue_ingredients.csv

For each distinct ingredient key, try the rungs in match_to_ifct strictest
first (exact, tokenset, subset), stopping at the first that finds anything.
Each key then lands in one bucket:
    auto     exactly one code found, pre-filled   -> confirm it, or correct it
    review   several candidates, or partial only  -> pick one, or mark absent
    none     nothing at any rung                  -> search IFCT by hand

    chosen_ifct_code  the final code for this key. Fill it on EVERY reviewed
                      row, auto rows included (copy the code if it is right).
    rank_found        where the right answer sat in candidate_codes: 1, 2, ...
                      / 'miss' (a correct IFCT row exists, no rung found it)
                      / 'absent' (IFCT has no usable row). report() computes evaluation 
    hierarchical      Y if the accepted code is broader than a strict match
                      (dry red chilli -> G022 "Chillies, red"). 
    reviewer_notes    free text.

Design follows StandFood (Eftimov et al. 2017): the machine generates candidates, a human accepts. 
    python -m malabardb.recipe_nutrient_table.build_ingred_review"""

import csv
import re
from collections import Counter
from pathlib import Path

from malabardb import paths
from malabardb.recipe_nutrient_table.match_to_ifct import (
    op_exact, op_tokenset, op_subset, rank_partial, overlap, PARTIAL_MIN_SCORE)

LADDER = [("exact", op_exact), ("tokenset", op_tokenset), ("subset", op_subset)]
PARTIAL_TOP_K = 5   # ties at the cutoff are kept

HUMAN_COLUMNS = ["chosen_ifct_code", "rank_found", "hierarchical", "reviewer_notes"]

# IFCT publishes an "all varieties" aggregate for some variety spreads (brinjal D031, green chilli G008).
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
    """Old queue -> {norm_key: {human column: value}}."""
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
    """Decide whether the machine is confident enough to fill in a code.
    Returns (status, auto_code); auto_code is blank unless status is 'auto'.

    Four cases, in order:
    1. No hits            -> ('none', '').  Nothing to confirm.
    2. Partial rule       -> ('review', ''). Partial candidates are ranked by
       a score BELOW 1.0, so by construction no candidate contains the key.
    3. One distinct code  -> ('auto', code). Every hit agrees on same ifct row 
    4. Several codes      -> a tie. Usually a human decides

    The single-food-group test is what keeps this narrow. Without it,
    'coconut' ties across Nuts and Oil Seeds, Edible Oils and Fats, and Misc."""

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
    """Current key with the highest token overlap against an orphaned key."""
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

    # ORPHANS: a tagged key that upstream renaming removed from the corpus ('ginger paste' became 'ginger'). Kept with n_lines=0 
    current = sorted(keys)
    orphans = sorted(k for k, v in prior.items()
                     if any(x.strip() for x in v.values()) and k not in keys)
    rows += [build_row(k, 0, "", ifct, prior, best_successor(k, current))
             for k in orphans]

    if not rows:
        raise SystemExit("no recipe keys; run match_to_ifct prepare first")

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    report(rows, orphans, bool(prior), out)


def report(rows, orphans, had_prior, out) -> None:
    """Coverage, then the evaluation that rank_found exists to support.
    Orphans are excluded from every count: they describe zero current lines."""
    live = [r for r in rows if int(r["n_lines"]) > 0]
    total = sum(int(r["n_lines"]) for r in live)
    print(f"{len(live)} keys / {total} lines -> {out}\n")

    print(f"{'status':<8}{'keys':>6}{'lines':>8}{'line%':>8}")
    for s in ("auto", "review", "none"):
        sel = [r for r in live if r["status"] == s]
        ln = sum(int(r["n_lines"]) for r in sel)
        print(f"{s:<8}{len(sel):>6}{ln:>8}{ln / total * 100:>7.1f}%")
    print("\nby rung: " + ", ".join(
        f"{k}={sum(r['link_rule'] == k for r in live)}"
        for k in ("exact", "tokenset", "subset", "partial", "none")))

    reviewed = [r for r in live if r["chosen_ifct_code"].strip()
                or r["rank_found"].strip()]
    if not reviewed:
        return

    # Auto precision: on rows the machine filled in, how often did the reviewer keep that code?  reported accuracy.
    auto = [r for r in reviewed if r["status"] == "auto"]
    kept = [r for r in auto if r["chosen_ifct_code"] == r["ifct_code"]]
    if auto:
        print(f"\nauto precision: {len(kept)}/{len(auto)} "
              f"({len(kept) / len(auto) * 100:.0f}%) machine codes accepted")

    # Candidate recall: of the keys that HAVE a right answer in IFCT, where did it sit in the candidate list? 
    ranked = [r for r in reviewed if r["rank_found"].strip().isdigit()]
    missed = [r for r in reviewed if r["rank_found"].strip() == "miss"]
    absent = [r for r in reviewed if r["rank_found"].strip() == "absent"]
    findable = len(ranked) + len(missed)
    if findable:
        top1 = sum(1 for r in ranked if r["rank_found"].strip() == "1")
        top5 = sum(1 for r in ranked if int(r["rank_found"]) <= PARTIAL_TOP_K)
        print(f"candidate recall over {findable} findable keys: "
              f"rank 1 {top1} ({top1 / findable * 100:.0f}%), "
              f"top {PARTIAL_TOP_K} {top5} ({top5 / findable * 100:.0f}%), "
              f"anywhere {len(ranked)} ({len(ranked) / findable * 100:.0f}%)")
    print(f"not in IFCT: {len(absent)} keys "
          f"({sum(int(r['n_lines']) for r in absent)} lines)")

    if had_prior:
        tagged = sum(any(r[c] for c in HUMAN_COLUMNS) for r in live)
        print(f"carried forward: {tagged} tagged keys")

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