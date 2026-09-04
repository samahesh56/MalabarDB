"""
build_review_queue.py: all operators -> ONE human-editable queue.

    python -m malabardb.recipe_nutrient_table.build_review_queue

Four rungs, strictest first. The first rung that finds anything wins, and
link_rule records which one it was:

    exact     identical strings                                 
    tokenset  same tokens, any order                            
    subset    one token set contains the other                  
    partial   overlap >= threshold, top few kept, RANKED        

Strictest-wins matters because relaxing an operator can turn a clean answer
into a tie: 'drumstick' is unique (D046) under exact but ties (C019, D046)
under subset. Taking the strictest hit keeps the confident answer.

HUMAN-OWNED COLUMNS
Everything except these four is regenerated from scratch on every run. These
four are read back out of the old file and reattached by norm_key, so a rerun
no longer erases tagging that has already been done.

    chosen_ifct_code  the code you pick, for any row where status != 'auto'

    rank_found        where the right answer sat in candidate_names:
                        1, 2, 3, ...  1-based position in the list
                        miss          a correct IFCT row EXISTS but no operator
                                      retrieved it -> a recall gap to close
                        absent        no usable IFCT row exists at all
                                      -> falls through to the fallback hierarchy

    hierarchical      'Y' if the code you accepted is broader than a strictly
                      perfect match (dry red chilli -> G022 "Chillies, red").
                      Project convention counts these correct; recording them
                      separately is what keeps the strict number computable too.
"""
import csv
import re
import shutil
from collections import Counter
from pathlib import Path

from malabardb import paths
from malabardb.recipe_nutrient_table.match_to_ifct import (
    op_exact, op_tokenset, op_subset, rank_partial)

BOOLEAN_LADDER = [("exact", op_exact), ("tokenset", op_tokenset),
                  ("subset", op_subset)]

PARTIAL_MIN_SCORE = 0.5   # a shared head noun + one unshared qualifier each side
PARTIAL_TOP_K = 5         # ties at the cutoff are kept even past this

# Columns this script must NOT regenerate.
HUMAN_COLUMNS = ["chosen_ifct_code", "rank_found", "hierarchical",
                 "reviewer_notes"]

# link_confidence - StandFood's method (needs work)
CONFIDENCE = {"exact": 1.0, "tokenset": 0.9, "subset": 0.8}

# IFCT publishes its own aggregate row for some variety spreads. Defaulting a pure variety tie to it uses IFCT's average
ALL_VARIETIES_RE = re.compile(r"\ball\s+varieties\b", re.IGNORECASE)

# Curated: foods IFCT has no usable row for, plus the two cases where a token operator matches the WRONG food. 
# 'sugar' and 'water' hit melon rows because both words appear as variety terms in "Water melon, dark green (sugar baby)"
ABSENT = {
    "salt":              "not in IFCT; zero-macro sentinel",
    "sugar":             "refined sugar not in IFCT; melon hit is a modifier collision",
    "water":             "not an IFCT food; melon/coconut hits are modifier collisions",
    "coconut milk":      "not in IFCT; derive from H007 or USDA",
    "curd dahi yogurt":  "no curd row in Milk & Milk Products; USDA fallback",
}

# Unit words that escaped ingredient-line parsing. 
UNIT_TOKENS = {"tsp", "tbsp", "cup", "cups", "gm", "gms", "kg", "ml", "inch",
               "pinch", "teaspoon", "tablespoon"}


def load_keys(path: Path) -> dict[str, dict]:
    """recipe_keys.csv -> one entry per norm_key, with line count and the
    surface spellings that collapsed into it."""
    keys: dict[str, dict] = {}
    for r in csv.DictReader(path.open(encoding="utf-8")):
        d = keys.setdefault(r["norm_key"], {"n_lines": 0, "names": Counter()})
        d["n_lines"] += int(r["n_lines"])
        d["names"][r["name_raw"]] += int(r["n_lines"])
    return keys


def load_prior(path: Path) -> dict[str, dict]:
    """Old queue -> {norm_key: {human column: value}}. Empty dict if no file yet.

    Only HUMAN_COLUMNS are read back. Everything else is a machine output and
    carrying it forward would freeze a stale operator result into the new file.
    """
    if not path.exists():
        return {}
    prior = {}
    for r in csv.DictReader(path.open(encoding="utf-8")):
        prior[r["norm_key"]] = {c: r.get(c, "") for c in HUMAN_COLUMNS}
    return prior


def classify(key, rule, hits):
    """Decide what happens to one key. Returns (status, auto_code, note).

        auto            code filled in, no human needed
        needs_pick      real tie among varieties of the same food
        needs_name_fix  tie spans food groups -> the RECIPE name is
                        underspecified ('oil', 'water'); usually fix name_raw
        needs_partial   partial-rung candidates, ranked, human selects
        absent          curated: no usable IFCT row (fallback policy)
        no_candidate    nothing matched at any rung -- NOT the same as absent;
                        this is a recall gap we have not closed yet
        residue         parser escape; send back to line parsing
    """
    if key in ABSENT:
        return "absent", "", ABSENT[key]
    if set(key.split()) & UNIT_TOKENS:
        return "residue", "", "unit token in name_raw; fix in parsing"
    if not hits:
        return "no_candidate", "", "no candidate at any rung"

    # partial NEVER auto-fills, however few candidates it returned
    if rule == "partial":
        return "needs_partial", "", f"{len(hits)} ranked candidates"

    codes = {h["code"] for h in hits}
    if len(codes) == 1:
        return "auto", hits[0]["code"], ""

    aggregate = [h for h in hits if ALL_VARIETIES_RE.search(h["name"])]
    groups = {h.get("grup", "") for h in hits}
    if aggregate and len(groups) == 1:
        return "auto", aggregate[0]["code"], "IFCT 'all varieties' aggregate"
    if len(groups) > 1:
        return "needs_name_fix", "", f"candidates span {len(groups)} food groups"
    return "needs_pick", "", f"{len(codes)} varieties, no IFCT aggregate row"


def main() -> None:
    keys = load_keys(paths.PROCESSED / "recipe_keys.csv")
    ifct = list(csv.DictReader(paths.IFCT_NORMALIZED.open(encoding="utf-8")))

    # Read any tagging already done BEFORE we regenerate over the top of it.
    out = paths.REVIEW_QUEUE_INGREDIENTS
    prior = load_prior(out)

    rows = []
    for key, d in sorted(keys.items(), key=lambda kv: -kv[1]["n_lines"]):
        rule, hits, score = "none", [], ""

        # walk the boolean rungs, stop at the first that finds anything
        for name, op in BOOLEAN_LADDER:
            found = sorted((c for c in ifct if op(key, c["name_key"])),
                           key=lambda c: c["code"])
            if found:
                rule, hits, score = name, found, CONFIDENCE[name]
                break

        # nothing yet -> generate ranked candidates for a human
        if not hits:
            ranked = rank_partial(key, ifct, PARTIAL_MIN_SCORE, PARTIAL_TOP_K)
            if ranked:
                rule = "partial"
                hits = [c for c, _ in ranked]
                score = round(ranked[0][1], 3)      # best candidate's score

        status, code, note = classify(key, rule, hits)
        row = {
            "norm_key": key,
            "n_lines": d["n_lines"],
            "generic_name": d["names"].most_common(1)[0][0],
            "link_rule": rule,
            "link_confidence": score,
            "n_candidates": len({h["code"] for h in hits}),
            "candidate_codes": ";".join(h["code"] for h in hits),
            "candidate_names": ";".join(h["name"] for h in hits),
            "ifct_code": code,               # auto-filled where safe, else blank
            "food_group": next((h.get("grup", "") for h in hits
                                if h["code"] == code), ""),
            "status": status,
            "notes": note,
        }
        row.update({c: "" for c in HUMAN_COLUMNS})   # <- YOU fill these in

        # Reattach anything already tagged. Only non-empty values overwrite
        row.update({c: v for c, v in prior.get(key, {}).items() if v.strip()})
        rows.append(row)

    # Back up before overwriting a file a human has typed into.
    if out.exists():
        shutil.copy2(out, out.with_suffix(".bak.csv"))

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    total = sum(r["n_lines"] for r in rows)
    print(f"{len(rows)} keys / {total} lines -> {out}\n")
    print(f"{'status':<16}{'keys':>6}{'lines':>8}{'line%':>8}")
    for s in ("auto", "needs_pick", "needs_name_fix", "needs_partial",
              "absent", "no_candidate", "residue"):
        sel = [r for r in rows if r["status"] == s]
        ln = sum(r["n_lines"] for r in sel)
        print(f"{s:<16}{len(sel):>6}{ln:>8}{ln/total*100:>7.1f}%")
    print("\nby rung: " + ", ".join(
        f"{k}={sum(1 for r in rows if r['link_rule'] == k)}"
        for k in ("exact", "tokenset", "subset", "partial", "none")))

    part = [r for r in rows if r["link_rule"] == "partial"]
    if part:
        avg = sum(r["n_candidates"] for r in part) / len(part)
        print(f"partial rung: {len(part)} keys, "
              f"{avg:.1f} candidates/key to review")

    # Rerun safety report
    if prior:
        tagged = sum(1 for r in rows if any(r[c] for c in HUMAN_COLUMNS))
        orphans = [k for k, v in prior.items()
                   if any(x.strip() for x in v.values())
                   and k not in {r["norm_key"] for r in rows}]
        print(f"\ncarried forward: {tagged} tagged keys")
        if orphans:
            print(f"WARNING {len(orphans)} tagged keys no longer in queue: "
                  + ", ".join(orphans[:5]) + (" ..." if len(orphans) > 5 else ""))


if __name__ == "__main__":
    main()