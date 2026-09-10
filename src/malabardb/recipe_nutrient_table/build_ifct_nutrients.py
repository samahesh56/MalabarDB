'''
build_ifct_nutrients.py - IFCT_index.csv -> ifct_nutrients.csv

A pure projection of an external reference table. No recipe data touches this
file and nothing in it is hand-edited, so the output is regenerable from the
raw download at any time. 

  python -m malabardb.recipe_nutrient_table.build_ifct_nutrients
'''

import numpy as np
import pandas as pd

from malabardb import paths

KJ_PER_KCAL = 4.184

# IFCT 2017 column abbreviations -> our schema names.
COLUMNS = {
    "code":     "ifct_code",
    "name":     "ifct_name",
    "grup":     "food_group",
    "enerc":    "energy_kj",   # energy, as published, kilojoules per 100 g
    "protcnt":  "protein_g",   # protein, total
    "choavldf": "carb_g",      # carbohydrate, available, by difference
    "fatce":    "fat_g",       # fat, crude, by continuous extraction
    "fibtg":    "fibre_g",     # dietary fibre, total
}

OUT_COLUMNS = ["ifct_code", "ifct_name", "food_group", "energy_kcal",
               "energy_kj", "protein_g", "carb_g", "fat_g", "fibre_g"]


def build(src=paths.IFCT_INDEX, out=paths.IFCT_NUTRIENTS) -> pd.DataFrame:
    df = pd.read_csv(src, usecols=list(COLUMNS)).rename(columns=COLUMNS)

    df["energy_kcal"] = (df.energy_kj / KJ_PER_KCAL).round(1)

    # enerc == 0 is a MISSING cell, not a measured zero: IFCT's oils/fats
    # section reports fatty acid composition, not proximates, so the energy
    # cell was never filled. Writing 0.0 would let a wrong value flow silently
    # into every recipe using an oil. NULL makes the gap loud at join time.
    # Deriving a value is a separate commit.
    missing_energy = df.energy_kj.eq(0)
    df.loc[missing_energy, ["energy_kj", "energy_kcal"]] = np.nan
    

    df = df[OUT_COLUMNS]

    # Locally-defined rows for foods IFCT does not carry. Kept in a separate
    # file so the IFCT projection above stays a pure copy of the published
    # table, and so the provenance of every non-IFCT value is a single grep.
    if paths.LOCAL_NUTRIENTS.exists():
        local = pd.read_csv(paths.LOCAL_NUTRIENTS)[OUT_COLUMNS]
        clash = set(local.ifct_code) & set(df.ifct_code)
        if clash:
            raise SystemExit(f"local codes collide with IFCT: {sorted(clash)}")
        df = pd.concat([df, local], ignore_index=True)
        print(f"[local] +{len(local)} rows: {list(local.ifct_code)}")

        missing_energy = df.energy_kj.isna()

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"[ifct ] {len(df)} rows -> {out.name}")
    print(f"         {df.ifct_code.nunique()} distinct codes, "
          f"{df.food_group.nunique()} food groups")
    print(f"         {int(missing_energy.sum())} rows with NULL energy, "
          f"groups: {sorted(df.loc[missing_energy, 'food_group'].unique())}")
    for col in ("energy_kcal", "protein_g", "carb_g", "fat_g", "fibre_g"):
        print(f"         {col:12s} min {df[col].min():7.2f}  "
              f"max {df[col].max():7.2f}  nulls {int(df[col].isna().sum())}")
    print()
    print(df[df.ifct_code.isin(["T001", "H007", "I001", "G033", "L002"])]
          .to_string(index=False))
    return df


if __name__ == "__main__":
    paths.ensure_dirs()
    build()