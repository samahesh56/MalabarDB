# MalabarDB - Recipe to Nutrition for Kerala Cuisine

MalabarDB maps Kerala recipes to the Indian Food Composition Tables (IFCT 2017)
so that each ingredient in each recipe carries a per-100 g
nutritional profile. This is the foundation for
per-serving nutrition estimation and, later, for nutrient-aware ingredient
substitution. NOTE: Gram normalization is incomplete and required for substitution. 

The problem is a language one, solved using NLP methods. Recipe text says `2 sprigs curry leaves` or
`1/2 tsp turmeric powder haldi`; IFCT says `Curry leaves` (G010) or
`Turmeric powder` (G033). Getting from one to the other means parsing free text
into quantity, unit and name, normalizing the name, and matching it to the
right IFCT row. No single tool does this reliably, so the pipeline runs two
independent methods at each step and sends only their disagreements to a human.
That keeps the manual work small and the machine's decisions auditable.

---

## Setup

**Requires Python 3.12** and **GNU Make 4.3 or newer**. Everything else is
installed for you.

Python 3.13 also works. 3.14 does not - several dependencies have no
prebuilt wheels for it yet.

### Changing the Python version

If `python` on your machine is not 3.12, the build stops with a message saying
so. Open the `Makefile` and edit the `BOOTSTRAP` line near the top:

```make
BOOTSTRAP ?= py -3.12        # Windows
BOOTSTRAP ?= python3.12      # macOS / Linux
```
---

### Installing Make on Windows

Open VS Code as administrator (so its terminal runs as administrator), then:

```powershell
choco install make
```

`winget install -e --id ezwinports.make` also works. Check it is installed correclly by running `make --version`. 
On macOS, `brew install make` installs it as `gmake`; use that in place of `make` below.

### Build

```bash
git clone -b main https://github.com/samahesh56/MalabarDB.git
cd MalabarDB
make db
```

`make db` creates a virtual environment, installs dependencies, downloads the
spaCy model, and runs every pipeline stage. The result is `data/final/malabardb.db`. 

## Pipeline
 
```
data/raw/IndianFoodDatasetCSV.csv                   data/raw/IFCT_index.csv
              |                                              |
   build_recipe_tables.py                          build_ifct_nutrients.py
              |  recipe_ingredients.csv                       |  ifct_nutrients.csv
              v                                               |
   review_queue_parsing.py ---> review_queue_parsing.csv  [HUMAN]
              |  full_corpus_labels.csv                       |
              v                                               |
   merge_corrections.py  (+ manual_fixes.csv)                 |
              |  recipe_ingredients_final.csv                 |
              v                                               v
   match_to_ifct.py  ------------------> normalize.py <--------
              |  recipe_keys.csv                    ifct_normalized.csv
              v
   build_ingred_review.py ---> review_queue_ingredients.csv  [HUMAN]
              |
              v
   ingredient_ifct_map.py  (+ fallback_map.csv)
              |  ingredient_ifct_map.csv
              v
   build_db.py  --->  data/final/malabardb.db
```
 
Two independent tracks. The left one turns recipe text into a clean table of
parsed ingredient lines; the right one turns IFCT 2017 into a per-100 g
nutrient table. They meet at `normalize.py`, which reduces a food name to a
comparison key. The same normalizer runs on both sides, so keys from either
are comparable by construction.
 
Matching then walks a ladder of progressively looser string-overlap rules and
records which rung each ingredient reached, so every linkage decision carries
its own provenance. 
 
### Human Review

Both review queues are committed in their reviewed state, so a fresh clone
builds with no input from you. You only need them if you change something
upstream and lines get re-flagged, or if you want to revisit a decision:

```bash
make parse-queue     # regenerate the parsing queue and stop
make ingred-queue    # regenerate the ingredient queue and stop
make check           # count rows still awaiting a decision
```

Edit the queue, then `make db` again; only the affected stages rerun.
Corrections already in a queue are carried forward when it regenerates. A
blank cell always means "not reviewed", never "clear this field". 

---

## Data

| File | Source |
| --- | --- |
| `data/raw/IndianFoodDatasetCSV.csv` | [6000+ Indian Food Recipes Dataset](https://www.kaggle.com/datasets/kanishk307/6000-indian-food-recipes-dataset) (Kaggle) |
| `data/raw/IFCT_index.csv` | Indian Food Composition Tables 2017, National Institute of Nutrition, ICMR |

6,871 recipes in the source; 163 labelled Kerala; **145** after dropping rows
with untranslated ingredients. Those give **1,737** ingredient lines and
**227** distinct ingredient keys. IFCT contributes **544** nutrient rows.

The CSVs in `data/final/` and `data/review/` are committed because they hold
hand-reviewed decisions that re-running the code cannot reproduce. 
---

## Output

`data/final/malabardb.db`, five tables:

| Table | Rows | Holds |
| --- | --- | --- |
| `recipes` | 145 | dish name, servings, instructions |
| `recipe_ingredients` | 1,737 | one row per ingredient line: quantity, unit, name, `ingredient_id` |
| `ingredients` | 227 | each distinct ingredient, its IFCT code, and how it was linked |
| `ifct_nutrients` | 544 | per-100 g energy and macronutrients from IFCT |
| `unit_conversions` | 0 | grams per unit per ingredient - next phase |

The next stage - unit to grams, then per-serving aggregation - is what turns
these per-100 g values into a nutrition estimate for the whole dish.

---

## References

- Batra, D., et al. (2020). *RecipeDB: A Resource for Exploring Recipes.* -
  database model and per-line ingredient schema.
- Kalra, J., Batra, D., Diwan, N., Bagler, G. (2020). *Nutritional Profile
  Estimation in Cooking Recipes.* ICDEW. - linkage method.
- Eftimov, T., Korošec, P., Koroušić Seljak, B. (2017). *StandFood.* -
  semi-automatic matching design.
- Pellegrini, C., Özsoy, E., Wintergerst, M., Groh, G. (2021). *Exploiting Food
  Embeddings for Ingredient Substitution.* HEALTHINF. - normalizer; Task B.
- Indian Food Composition Tables (IFCT) 2017, National Institute of Nutrition,
  ICMR.