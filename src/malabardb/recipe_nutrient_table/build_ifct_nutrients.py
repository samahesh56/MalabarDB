'''
Step 4: build_ifct_nutrients.py IFCT_index.csv -> ifct_nutrients.csv

The nutrient reference table: one row per food, per 100 g. It reads only the published IFCT download, touches no
recipe data, and is regenerable at any time.

    published IFCT columns  ->  projected to our schema
    energy in kJ            ->  converted to kcal (IFCT 2017 publishes kJ only)
    energy missing          ->  derived from macronutrients where possible
    foods IFCT lacks        ->  appended from local_nutrients.csv

Every row records where its energy came from in energy_source, so a derived value is never mistaken for a published one.

Output: ifct_nutrients.csv '''

import numpy as np
import pandas as pd

from malabardb import paths

# FAO (2003), Food energy: methods of analysis and conversion factors.
# INDB (Vijayakumar et al.) uses the same constant to read IFCT 2017.
KJ_PER_KCAL = 4.184

# Atwater general factors, kcal per gram (FAO 2003, Conversion Guidelines Table 4.1-1).
ATWATER_KCAL_PER_G = {
    'protein_g': 4.0,
    'carb_g': 4.0,
    'fat_g': 9.0,
    'fibre_g': 2.0,
}

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
               "energy_kj", "energy_source", "protein_g", "carb_g", "fat_g", "fibre_g"]


def atwater_kcal(df):
    """Energy from macronutrients, kcal per 100 g. Applied only where IFCT publishes no energy value. (Oils)"""
    return sum(df[col] * kcal for col, kcal in ATWATER_KCAL_PER_G.items()).round(1)


def build(src=paths.IFCT_INDEX, out=paths.IFCT_NUTRIENTS) -> pd.DataFrame:
    df = pd.read_csv(src, usecols=list(COLUMNS)).rename(columns=COLUMNS)

    duplicate = df.ifct_code[df.ifct_code.duplicated()].tolist()
    if duplicate:
        raise SystemExit(f"duplicate IFCT codes in {src.name}: {duplicate}")

    df["energy_kcal"] = (df.energy_kj / KJ_PER_KCAL).round(1)
    df["energy_source"] = "published"

    # enerc == 0 is a MISSING cell, not a measured zero: IFCT's oils/fats
    # section reports fatty acid composition, not proximates, so the energy cell was never filled. 
    unpublished = df.energy_kj.eq(0)
    df.loc[unpublished, ["energy_kj", "energy_kcal"]] = np.nan

    # Fill missing gaps from the macronutrient data, which IFCT does publish for these foods. 
    # energy_kj stays NULL since IFCT never published it.
    df.loc[unpublished, "energy_kcal"] = atwater_kcal(df.loc[unpublished])
    df.loc[unpublished, "energy_source"] = "atwater"

    # Locally-defined rows for foods IFCT does not carry. Kept in a separate
    # file so the IFCT projection above stays a pure copy of the published table.
    if paths.LOCAL_NUTRIENTS.exists():
        local = pd.read_csv(paths.LOCAL_NUTRIENTS)
        local["energy_source"] = "local"
        clash = set(local.ifct_code) & set(df.ifct_code)
        if clash:
            raise SystemExit(f"local codes collide with IFCT: {sorted(clash)}")
        df = pd.concat([df, local[OUT_COLUMNS]], ignore_index=True)
        print(f"[local] +{len(local)} rows: {list(local.ifct_code)}")

    df = df[OUT_COLUMNS]
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    report(df, out)
    return df


def report(df, out):
    """Print what a reader needs to audit this table."""
    print(f"[ifct ] {len(df)} rows -> {out.name}")
    print(f"         {df.ifct_code.nunique()} distinct codes, "
          f"{df.food_group.nunique()} food groups")

    print(f"\n{'energy_source':<14}{'rows':>6}  food groups")
    for source, group in df.groupby("energy_source"):
        groups = sorted(group.food_group.unique())
        shown = groups if len(groups) <= 3 else f"{len(groups)} groups"
        print(f"{source:<14}{len(group):>6}  {shown}")

    still_null = int(df.energy_kcal.isna().sum())
    print(f"\n         {still_null} rows still have no energy_kcal")
    for col in ("energy_kcal", "protein_g", "carb_g", "fat_g", "fibre_g"):
        print(f"         {col:12s} min {df[col].min():7.2f}  "
              f"max {df[col].max():7.2f}  nulls {int(df[col].isna().sum())}")

    print("\nspot check (one published, one derived, one local):")
    print(df[df.ifct_code.isin(["T001", "H007", "I001", "G033", "L002",
                                "LOC001"])].to_string(index=False))


if __name__ == "__main__":
    paths.ensure_dirs()
    build()