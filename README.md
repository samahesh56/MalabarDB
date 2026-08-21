# MalabarDB - Ingredient Embeddings for Kerala Cuisine

Computational gastronomy for Kerala / Malabar food: nutrition estimation and
ingredient substitution. This repository holds **v0a**, the first model in the
substitution track: a naive skip-gram trained on Kerala recipe ingredient lists.

v0a exists to establish a baseline and demonstrate its limitation. A skip-gram
trained on ingredient lists learns which ingredients appear *together*
(complements), not which ingredients *replace* one another (substitutes). Nearest
neighbour in the embedding space is therefore a good "what pairs with this" model
and a poor "what can I swap this for" model. Making that failure visible and
measurable on a Kerala corpus is the point of v0a, and the starting point for the
fixes that follow.

---

## Setup

Requires Python 3.12 or 3.13 (gensim has no prebuilt wheel for 3.14 yet).

```bash
git clone https://github.com/samahesh56/MalabarDB.git
cd MalabarDB

python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate

pip install -e .
python -m spacy download en_core_web_sm
```

`pip install -e .` installs the dependencies from `requirements.txt` **and** puts
`src/` on the import path, which is what makes `python -m malabardb....` work from
any directory.
### Data files

Two inputs are required in `data/raw/`:

| File | Source |
| --- | --- |
| `IndianFoodDatasetXLS.xlsx` | [6000+ Indian Food Recipes Dataset](https://www.kaggle.com/datasets/kanishk307/6000-indian-food-recipes-dataset) (Kaggle) |
| `IFCT_index.csv` | Indian Food Composition Tables 2017, National Institute of Nutrition |

---

## Pipeline

```
extract_names.py      recipe line   ->  candidate ingredient name
normalize_vocab.py    candidate     ->  controlled vocabulary (lemmatized, deduped)
  └─ --ifct           IFCT names    ->  normalized IFCT keys
build_corpus.py       vocabulary    ->  training corpus (one recipe per line)
train.py              corpus        ->  skip-gram vectors
```

`normalize_vocab.py` runs in two modes: the **same** normalizer produces both the recipe vocabulary
and the IFCT keys (to match recipe ingredents and IFCT terms). A vocabulary key and an IFCT-name key are therefore comparable
by construction. 

Run from anywhere, in order:

```bash
python -m malabardb.ingred_vocab.extract_names          # -> data/interim/extracted_names.json
python -m malabardb.ingred_vocab.normalize_vocab        # -> data/processed/vocabulary_{spacy,rules}.json
python -m malabardb.ingred_vocab.normalize_vocab --ifct # -> data/processed/ifct_normalized.csv
python -m malabardb.embeddings.build_corpus             # -> data/processed/v0a_kerala_{spacy,rules}.txt
python -m malabardb.embeddings.train                    # -> models/v0a_ingredient_sg/vectors.kv
```

The `--ifct` step belongs to Task A and does not feed `build_corpus`; it produces
the normalized nutrient-table keys the linkage step consumes.

Optional:

```bash
python -m malabardb.ingred_vocab.audit   # corpus sanity checks (WIP)
python harness.visualize             # PCA plot -> reports/figures/v0a_pca.png
```

---

## Phase 1: Normalized recipe table

Where v0a's ingredient vocabulary comes from, and Task A's foundation: turning raw
recipe text into a table where every ingredient in every recipe has a known
quantity, unit, name, and (where available) preparation state.

No single tool gets this right alone, so two independent extraction methods run
on every ingredient line: a **regex extractor** tuned to this dataset's specific
quantity/unit formats, and the general-purpose **`ingredient-parser`** library.
Wherever the two disagree, the row is flagged for a person to check. That
disagreement is the review signal, instead of trusting either method blindly or
reading all lines by hand.

```
build_recipe_tables.py   raw Kaggle data        ->  recipes table (final)
                                                      recipe_ingredients, structure only
                                                      (recipe_id, line_no, raw_line -- no parsing yet)

build_corpus_labels.py   recipe_ingredients     ->  candidate labels + disagreement flags
                                                      review queues (flagged rows only)

  [ manual review: fill in the flagged rows ]

merge_corrections.py     labels + corrections   ->  recipe_ingredients table (final)

build_db.py              final tables           ->  malabardb.db
```

Run from anywhere, in order:

```bash
python -m malabardb.recipe_nutrient_table.build_recipe_tables
python -m malabardb.recipe_nutrient_table.build_corpus_labels
# review the flagged rows in the review queue files here
python -m malabardb.recipe_nutrient_table.merge_corrections
python -m malabardb.recipe_nutrient_table.build_db
```

### What's in `malabardb.recipe_nutrient_table`

| File | Does |
| --- | --- |
| `build_recipe_tables.py` | Filters the raw dataset to Kerala recipes, splits each recipe's ingredient text into one row per ingredient line. |
| `build_corpus_labels.py` | Runs both extraction methods, flags disagreements, writes the review queues. |
| `merge_corrections.py` | Applies manual corrections on top of the automatic extraction to produce the final table. |
| `build_db.py` | Loads the final CSVs into `malabardb.db`. |

## Data

- **Source:** *6000+ Indian Food Recipes Dataset* (Kaggle).
- **Columns used:** `TranslatedRecipeName`, `TranslatedIngredients`,
  `TranslatedInstructions`, `Servings`, `Cuisine`.
- **Filter for v0a:** `Cuisine == "Kerala Recipes"`, giving 163 recipes with
  non-null ingredients. 145 survive preprocessing.
- **Nutrient reference:** IFCT 2017, 542 food rows, normalized to 512 distinct
  keys. The four collisions are two variety spreads (brinjal, green chilli) and
  two genuine name clashes IFCT itself contains (cat fish and crab each appear as
  both a marine and a freshwater entry).

The corpus is small by design. It is enough to demonstrate the complements-vs-
substitutes phenomenon, which lives on the high-frequency ingredients, but not to
produce high-quality vectors across the full ingredient vocabulary.

---

## Output

`train.py` writes:

- `models/v0a_ingredient_sg/vectors.kv` — the trained vectors (gensim KeyedVectors).
- `models/v0a_ingredient_sg/config.json` — training settings and corpus stats.

A quick look at the result:

```python
from gensim.models import KeyedVectors
from malabardb import paths

kv = KeyedVectors.load(str(paths.VECTORS))
kv.most_similar("coconut_oil", topn=5)
# -> pearl_onion, cumin_seed, turmeric_powder, green_chilli, curry_leaves
```

Those neighbours are the tempering set: ingredients that go into hot oil together at
the start of a Kerala dish. They are what coconut oil is *used with*, not what you
would *replace* it with.

---

## References
ss
- Pellegrini, C., Özsoy, E., Wintergerst, M., Groh, G. (2021). *Exploiting Food
  Embeddings for Ingredient Substitution.* HEALTHINF.
- Lawo, D., Böhm, L., Esau, M. (2020). *Supporting plant-based diets with
  ingredient2vec.*
- Fatemi, B., Duval, Q., Girdhar, R., Drozdzal, M., Romero-Soriano, A. (2023).
  *Learning to substitute ingredients in recipes (GISMo).* arXiv:2302.07960.
- Shirai, S. S., et al. (2021). *Identifying Ingredient Substitutions Using a
  Knowledge Graph of Food (DIISH).* Frontiers in Artificial Intelligence.
- Indian Food Composition Tables (IFCT) 2017, National Institute of Nutrition, ICMR.