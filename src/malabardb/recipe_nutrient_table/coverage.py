"""
coverage.py - per-recipe coverage: how much of each recipe the nutrition
numbers actually see.

A line is COUNTED when it has grams (from unit_conversions) AND nutrients
(from ifct_nutrients). Anything else adds 0 to the recipe's totals, silently.
Coverage = counted lines / all lines, per recipe: the measure behind the
Kalra et al. (2020) Fig. 2 histogram.

    reads   malabardb.db   (recipe_nutrition view, recipe_ingredients)
    writes  coverage_hist.png        the histogram
            coverage_by_recipe.csv   one row per recipe, with what it's missing

    python -m malabardb.recipe_nutrient_table.coverage
"""

import sqlite3
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from malabardb import paths

OUT = paths.MALABARDB.parent            # next to the DB; move if you keep a reports/ folder
BINS = np.arange(0, 101, 10)                     # replaces the linspace line

MISSING = """
SELECT ri.recipe_id, GROUP_CONCAT(i.norm_key, '; ') AS missing
  FROM recipe_ingredients ri
  JOIN ingredients i USING (ingredient_id)
  LEFT JOIN ifct_nutrients n ON n.ifct_code = i.ifct_code
 WHERE ri.grams IS NULL OR n.energy_kcal IS NULL
 GROUP BY ri.recipe_id"""


def load() -> pd.DataFrame:
    """recipe_nutrition + three coverage ratios + the list of uncounted keys."""
    with sqlite3.connect(paths.MALABARDB) as con:
        rn = pd.read_sql("SELECT * FROM recipe_nutrition", con)
        missing = pd.read_sql(MISSING, con)
    for m in ("linked", "grams", "counted"):
        rn[f"cov_{m}"] = rn[f"n_{m}"] / rn["n_lines"]
    rn["pct_counted"] = rn["n_counted"] * 100 / rn["n_lines"]   # 7*100/10 is exactly 70.0
    return rn.merge(missing, on="recipe_id", how="left")


def report(rn: pd.DataFrame) -> None:
    cov = rn["cov_counted"]
    print(f"{len(rn)} recipes  |  coverage (counted / lines)")
    print(f"  mean {cov.mean():.1%}   median {cov.median():.1%}   min {cov.min():.1%}")
    print(f"  100% covered  {int((cov == 1).sum()):4d}")
    print(f"  >= 90%        {int((cov >= 0.9).sum()):4d}")
    print(f"  >= 80%        {int((cov >= 0.8).sum()):4d}")

    counts, _ = np.histogram(rn["pct_counted"], BINS)        # same bins as the plot
    print("\nrecipes per coverage bin:")
    for lo, hi, n in zip(BINS[:-1], BINS[1:], counts):
         print(f"  {lo:3d}-{hi:3d}%  {n:4d}  {'#' * n}")

    # Which gap is it? Lines missing nutrients vs lines missing only grams.
    no_link = int((rn.n_lines - rn.n_linked).sum())
    no_grams_only = int((rn.n_linked - rn.n_counted).sum())
    print(f"\nuncounted lines: {no_link + no_grams_only}")
    print(f"  no nutrients (linkage gap)       {no_link:4d}")
    print(f"  nutrients but no grams (units)   {no_grams_only:4d}")


def plot(rn: pd.DataFrame, path) -> None:
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.hist(rn["pct_counted"], bins=BINS, edgecolor="black")
    ax.axvline(rn["cov_counted"].median() * 100, linestyle="--", color="grey",
               label=f"median {rn['cov_counted'].median():.0%}")
    ax.set_xlabel("ingredient lines with grams and nutrients (%)")
    ax.set_ylabel("recipes")
    ax.set_title(f"Per-recipe coverage (n = {len(rn)})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=200)


def main() -> None:
    rn = load()
    report(rn)
    plot(rn, OUT / "coverage_hist.png")
    cols = ["recipe_id", "dish_name", "servings", "n_lines", "n_linked", "n_grams",
            "n_counted", "cov_counted", "kcal_per_serving", "missing"]
    rn.sort_values("cov_counted")[cols].to_csv(OUT / "coverage_by_recipe.csv", index=False)
    print(f"\nwrote {OUT / 'coverage_hist.png'} and coverage_by_recipe.csv")


if __name__ == "__main__":
    main()