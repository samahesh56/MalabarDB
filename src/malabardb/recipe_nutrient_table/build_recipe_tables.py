import pandas as pd

from malabardb import paths

'''
STEP 1: Reads the frozen Kerala subset of the Kaggle dataset, applies hand-checked
servings corrections, and produces recipes.csv and recipe_ingredients.csv.

Input: data/source/kerala_source.csv, data/source/servings_corrections.csv
    The Kerala rows of the raw Kaggle dataset (Cuisine == 'Kerala Recipes', with
    recipes whose ingredients are still in Devanagari removed).

Recipes.csv has one row per recipe: recipe_id, dish_name, region, cuisine_raw,
servings (value used for per-serving nutrition), servings_source (value in the
Kaggle dataset), instructions, and source_row_id (Srno in the Kaggle dataset).
    Output: data/final/recipes.csv

Recipe_ingredients.csv consists of a recipe_id attached to each ingredient's original
recipe, the line_no, and raw_line (the found ingredient). raw_line is produced by
comma-splitting the TranslatedIngredients column, which lets us identify each
recipe's individual ingredients, states, qty sizes, etc.
    Output: data/processed/recipe_ingredients.csv
'''

paths.ensure_dirs()  # ensures processed/ folder is created

kerala = pd.read_csv(paths.KERALA_SOURCE)
print(f'{len(kerala)} Kerala recipes read from snapshot')

# recipes.csv construction
recipes = kerala[['recipe_id', 'TranslatedRecipeName', 'Cuisine', 'Servings',
                  'TranslatedInstructions', 'Srno']].copy()
recipes.columns = ['recipe_id', 'dish_name', 'cuisine_raw', 'servings',
                   'instructions', 'source_row_id']
recipes['region'] = 'Kerala'

# apply hand-checked servings corrections
corr = pd.read_csv(paths.SERVINGS_CORRECTIONS)

unknown = set(corr['recipe_id']) - set(recipes['recipe_id'])
assert not unknown, f'corrections for recipe_ids not in the snapshot: {unknown}'

recipes = recipes.merge(corr[['recipe_id', 'servings_original', 'servings_corrected']],
                        on='recipe_id', how='left')

mismatch = recipes[recipes['servings_corrected'].notna()
                   & (recipes['servings'] != recipes['servings_original'])]
assert mismatch.empty, (
    'correction does not match the source value:\n'
    f"{mismatch[['recipe_id', 'dish_name', 'servings', 'servings_original']]}")

recipes['servings_source'] = recipes['servings']
recipes['servings'] = recipes['servings_corrected'].fillna(recipes['servings'])

recipes = recipes[['recipe_id', 'dish_name', 'region', 'cuisine_raw',
                   'servings', 'servings_source', 'instructions', 'source_row_id']]
recipes.to_csv(paths.RECIPES_TABLE, index=False)
print(f'servings corrected for {len(corr)} recipes: {sorted(corr["recipe_id"])}')

# recipe_ingredients.csv construction 
rows = []
for _, r in kerala.iterrows():
    phrases = [p.strip() for p in str(r['TranslatedIngredients']).split(',') if p.strip()]
    for i, raw_line in enumerate(phrases, start=1):
        rows.append({'recipe_id': r['recipe_id'], 'line_no': i, 'raw_line': raw_line})

recipe_ingredients = pd.DataFrame(rows)
recipe_ingredients.to_csv(paths.RECIPE_INGREDIENTS, index=False)

print('recipes.csv:', len(recipes), 'rows')
print('recipe_ingredients.csv:', len(recipe_ingredients), 'rows')
print('avg ingredient-lines per recipe:', round(len(recipe_ingredients) / len(recipes), 2))
print()
print('possible over-split fragments (<4 chars):')
print(recipe_ingredients[recipe_ingredients['raw_line'].str.len() < 4])