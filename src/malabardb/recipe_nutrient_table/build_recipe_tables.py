import pandas as pd
import re

from malabardb import paths

'''
STEP 1: Inputs the raw kaggle dataset, filters by kerala cuisine, and produces a recipes.csv table, and a recipe_ingredients.csv table

Recipes.csv assigns a recipe_id for each recipe, with attributes dish_name, region, cuisine_raw, servings, instructions, and source_row_id (tracking)
    Output: data/final/recipes.csv 

Recipe_ingredients.csv consists of a recipe_id attached to each ingredient's original recipe, the line_no, and raw_line (the found ingredient)
raw_line is produced using comma-splitting the TranslatedIngredients column, which will allow us to identify each recipes' individual ingredients, states, qty sizes, etc. 
    Output: data/processed/recipe_ingredients.csv'''

df = pd.read_csv(paths.RECIPES)
kerala = df[df['Cuisine'] == 'Kerala Recipes'].copy()

def has_devanagari(s):
    return bool(re.search(r'[\u0900-\u097F]', str(s)))

kerala = kerala[~kerala['TranslatedIngredients'].apply(has_devanagari)].reset_index(drop=True)
kerala['recipe_id'] = range(1, len(kerala) + 1)

# recipes.csv construction: contains data specific to kerala cuisine from raw dataset
recipes = kerala[['recipe_id', 'TranslatedRecipeName', 'Cuisine', 'Servings',
                   'TranslatedInstructions', 'Srno']].copy()
recipes.columns = ['recipe_id', 'dish_name', 'cuisine_raw', 'servings',
                    'instructions', 'source_row_id']
recipes['region'] = 'Kerala'
recipes = recipes[['recipe_id', 'dish_name', 'region', 'cuisine_raw',
                    'servings', 'instructions', 'source_row_id']]
recipes.to_csv(paths.RECIPES_TABLE, index=False)

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