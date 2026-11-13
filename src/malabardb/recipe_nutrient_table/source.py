import pandas as pd
import re
from malabardb import paths

def has_devanagari(s):
    return bool(re.search(r'[\u0900-\u097F]', str(s)))

df = pd.read_csv(paths.RECIPES)
kerala = df[df['Cuisine'] == 'Kerala Recipes'].copy()
kerala = kerala[~kerala['TranslatedIngredients'].apply(has_devanagari)].reset_index(drop=True)
kerala.insert(0, 'recipe_id', range(1, len(kerala) + 1))
kerala.to_csv(paths.KERALA_SOURCE, index=False)

# check: ids must match what the pipeline has been using
old = pd.read_csv(paths.RECIPES_TABLE)
same = (old.set_index('recipe_id').dish_name
        == kerala.set_index('recipe_id').TranslatedRecipeName)
print(len(kerala), 'rows; ids match:', same.all())