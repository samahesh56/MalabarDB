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

## Project structure

The wider project has two tracks, run in sequence because the first produces the
cleaned data the second trains on:

- **Task A — Recipe to nutrition per serving.** Ingredient extraction, unit
  normalization to grams, nutrient lookup against IFCT 2017, aggregation, division
  by servings.
- **Task B — Ingredient embeddings for substitution.** Vector representations of
  ingredients supporting "similar but healthier swap" suggestions.

Work is organised in phases:

| Phase | Focus | Status |
| --- | --- | --- |
| 0 | Onboarding, baseline embedding, complements-vs-substitutes demo | **this repo (v0a)** |
| 1 | Data assembly: normalized recipe table + nutrient lookup | upcoming |
| 2 | Recipe to nutrition pipeline (Task A) | upcoming |
| 3 | Substitution model: contextual or nutrient-constrained (Task B) | upcoming |
| 4 | Evaluation and handoff | upcoming |

---

## Pipeline

Four stages turn the raw recipe table into trained vectors. Each writes an
inspectable artifact, so stages can be run and reviewed independently.

```
extract_names.py    recipe line  ->  candidate ingredient name
normalize_vocab.py  candidate    ->  controlled vocabulary (lemmatized, deduped)
build_corpus.py     vocabulary   ->  training corpus (one recipe per line)
train.py            corpus       ->  skip-gram vectors
```

`extract_names.py` isolates the ingredient name from a recipe line
(`1 tablespoon Red Chilli powder - roasted` becomes `Red Chilli powder`).
`normalize_vocab.py` lemmatizes and deduplicates those names into a controlled
vocabulary, following the normalization scheme of Pellegrini et al. (2021).
`build_corpus.py` maps each recipe onto vocabulary tokens. `train.py` trains the
skip-gram and saves the vectors.

---

## Running it

Requires Python 3.12 or 3.13 (gensim has no prebuilt wheel for 3.14 yet).

```bash
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

*6000+ Indian Food Recipes Dataset* exists at:

```
data/raw/IndianFoodDatasetXLS.xlsx
```

Then run from the repo root, in order:

```bash
python preprocessing/extract_names.py     # -> data/interim/extracted_names.json
python preprocessing/normalize_vocab.py   # -> data/processed/vocabulary_spacy.json
python preprocessing/build_corpus.py      # -> data/processed/v0a_kerala_spacy.txt
python models/v0a_ingredient_sg/train.py  # -> models/v0a_ingredient_sg/vectors.kv
```

Optional checks:

```bash
python preprocessing/audit.py     # corpus sanity checks (WIP)
python harness/visualize.py       # PCA plot of the vector space
```

---

## Data

- **Source:** *6000+ Indian Food Recipes Dataset* (Kaggle).
- **Columns used:** `TranslatedRecipeName`, `TranslatedIngredients`,
  `TranslatedInstructions`, `Servings`, `Cuisine`.
- **Filter for v0a:** `Cuisine == "Kerala Recipes"`, giving 163 recipes with
  non-null ingredients. 145 survive preprocessing.

The corpus is small by design. It is enough to demonstrate the complements-vs-
substitutes phenomenon, which lives on the high-frequency ingredients, but not to
produce high-quality vectors across the full ingredient vocabulary.

---

## Output

`train.py` writes:

- `models/v0a_ingredient_sg/vectors.kv` — the trained vectors (gensim KeyedVectors).
- `models/v0a_ingredient_sg/config.json` — training settings and corpus stats

A quick look at the result:

```python
from gensim.models import KeyedVectors
kv = KeyedVectors.load("models/v0a_ingredient_sg/vectors.kv")
kv.most_similar("coconut_oil", topn=5)
# -> pearl_onion, cumin_seed, turmeric_powder, green_chilli, curry_leaves
```

Those neighbours are the tempering set: ingredients that go into hot oil together at
the start of a Kerala dish. They are what coconut oil is *used with*, not what you
would *replace* it with. 

---

## References

- Pellegrini, C., Özsoy, E., Wintergerst, M., Groh, G. (2021). *Exploiting Food
  Embeddings for Ingredient Substitution.* HEALTHINF.
- Lawo, D., Böhm, L., Esau, M. (2020). *Supporting plant-based diets with
  ingredient2vec.*
- Fatemi, B., Duval, Q., Girdhar, R., Drozdzal, M., Romero-Soriano, A. (2023).
  *Learning to substitute ingredients in recipes (GISMo).* arXiv:2302.07960.
- Shirai, S. S., et al. (2021). *Identifying Ingredient Substitutions Using a
  Knowledge Graph of Food (DIISH).* Frontiers in Artificial Intelligence.
- Indian Food Composition Tables (IFCT) 2017, National Institute of Nutrition, ICMR.