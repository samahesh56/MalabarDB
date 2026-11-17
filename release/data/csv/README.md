# KeralaDB: a recipe-level nutrition database for Kerala cuisine linked to the Indian Food Composition Tables (IFCT 2017)

KeralaDB links 131 Kerala recipes (1,582 ingredient lines) to the Indian Food Composition Tables 2017, with gaps filled from the Indian Nutrient Databank (INDB) and USDA FoodData Central. Every ingredient line carries a parsed quantity, unit and gram weight, and a link to a per-100 g nutrient profile, giving per-serving energy, protein, carbohydrate and fat for each recipe. Every linkage and conversion decision records how it was made. Recipe instructions are not redistributed; each recipe links to its source page.

## RESOURCES

| Name | Format | Rows | Size (bytes) | Title |
|---|---|---|---|---|
| recipe_nutrition | csv | 131 | 13528 | Per-serving nutrition by recipe |
| recipes | csv | 131 | 19094 | Recipes |
| recipe_ingredients | csv | 1582 | 138581 | Recipe ingredient lines |
| ingredients | csv | 208 | 26685 | Ingredients and their nutrient links |
| synonyms | csv | 230 | 14624 | Synonym (alias) table |
| unit_conversions | csv | 331 | 29634 | Unit-to-gram conversions |
| nutrients | csv | 131 | 14282 | Nutrient profiles (per 100 g) |

Each `<name>.csv` has a matching `<name>.csv-schema.json` describing its columns. `datapackage.json` describes all of them together, including how the tables join.
