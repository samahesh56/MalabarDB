# MalabarDB — Naive Ingredient Embeddings for Kerala Cuisine (v0a)

> Phase 0 / Task B prototype. This document covers the project framing, the core
> thesis, the preprocessing pipeline, and how to run it.

---

## 1. Project framing

This repository is part of a computational gastronomy project on **ingredient
substitution and nutrition estimation for Kerala cuisine**. The wider project has
two distinct deliverables, done in sequence because the first produces the cleaned
data the second trains on:

- **Task A — Recipe → nutrition per serving.** An NLP-parsing and database-mapping
  problem: extract ingredients, normalize units, look up nutrients (against IFCT
  2017), aggregate, and divide by servings. No embeddings required; most of the
  data-cleaning effort lives here.
- **Task B — Ingredient embeddings for substitution.** Learn vector representations
  of ingredients to support "similar-but-healthier swap" suggestions.

**This repository is the first step of Task B: `v0a`, a naive skip-gram model
trained on ingredient lists.** It is deliberately small-scale. Its purpose is to
*reproduce a known result on our own curated Kerala corpus* and make its failure
mode visible and measurable.

### What v0a sets out to establish

1. **Reproducibility.** That a skip-gram trained on ingredient lists can be built
   end-to-end on a curated Kerala recipe corpus.
2. **The wrong-relation result.** That nearest-neighbour in the resulting embedding
   space returns **complements** (ingredients used *together*), not **substitutes**
   (ingredients that *replace* one another), i.e. that "closest vector = good
   substitute" is a tempting but incorrect assumption.
3. **That the phenomenon holds for Kerala ingredients specifically.** Prior work
   demonstrates this on large English-language corpora; we confirm it reproduces on
   a small, cuisine-specific Indian dataset.

Establishing (2) on our own data is the intellectual core of the project. The naive
model is not a failed attempt at substitution, but rather a **correct demonstration
of why co-occurrence models cannot do substitution.**

---

## 2. How to run

### Requirements

- Python **3.12 or 3.13** (gensim does not yet ship prebuilt wheels for 3.14).
- Dependencies listed in `requirements.txt`.

### Setup

```bash
# from the repo root
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### Data

Place the Kaggle dataset at:

```
data/raw/IndianFoodDatasetXLS.xlsx
```

(The *6000+ Indian Food Recipes Dataset*. Raw data and generated artifacts are
gitignored; regenerate the corpus with the steps below.)

### Pipeline

Run from the repo root, in order:

```bash
# 1. Build the corpus (Stages 1-4): raw table -> data/processed/v0a_kerala.txt
python preprocessing/build_corpus.py

# 2. Audit the corpus (verify before training)
python preprocessing/audit.py

# 3. Train the skip-gram (Stage 5) -> models/v0a_ingredient_sg/vectors.kv
python models/v0a_ingredient_sg/train.py

# 4. (optional) PCA visualization of the vector space
python harness/visualize.py
```

> All scripts assume they are run **from the repo root**, not from inside their own
> folders. Relative paths resolve against the working directory.

---

## 3. Dataset

- **Source:** *6000+ Indian Food Recipes Dataset* (Kaggle), stored locally.
- **Relevant columns:** `TranslatedRecipeName`, `TranslatedIngredients`,
  `TranslatedInstructions`, `Servings`, `Cuisine`.
- **Filter for v0a:** `Cuisine == "Kerala Recipes"` → 163 recipes with non-null
  ingredients.
- **After preprocessing** (see §4): **145 recipes** retained.

> **Corpus-scope note.** "Kerala Recipes" alone is small (163 recipes) and yields a
> sparse corpus. This is sufficient to *demonstrate the phenomenon*  which lives on
> the high-frequency ingredients, but not to produce high-quality vectors.

---

## 4. Preprocessing: from recipe text → training corpus

### 4.1 The problem preprocessing solves

word2vec sees **only token identity**. To it, `"Onion"`, `"onion"`, and `"2 onions"`
are three unrelated things, and `"red chilli powder"` is three separate words. So the
entire job of preprocessing is to reduce each recipe to a **list of canonical
ingredient tokens in which there is one consistent token per ingredient, and nothing else.**

#### Pipeline format note 
The pipeline follows the conventional shape of text-corpus preprocessing: **select**
the data, **split** it into units, **parse** (clean) each unit, **reassemble** into
model input. 

### 4.3 How we developed our approach (and what we took from Pellegrini)

The normalization approach of **Pellegrini et al. (2021)** was studied. Both their
paper's description and their published `RecipeNormalizer` code were examined to see how an
existing food-embedding project cleaned its ingredient text. From their work we
observed the *categories* of cleaning that ingredient text requires:

- stripping parenthetical content,
- discarding quantity, unit, and preparation words,
- normalizing word inflection (i.e. *walks* → *walk*; they lemmatize),
- joining multi-word ingredient names into single tokens (*red chilli powder* →
  `red_chilli_powder`).

These categories were used as a general guideline for preparing recipes for embedding
training. Further data-preparation methods should be explored in future iterations.

**spaCy is not yet included in this implementation** (see §4.5).

### 4.4 The four cleaning decisions

Each recipe's `TranslatedIngredients` string is split on commas into individual
ingredient lines. Each line is passed through four transformations, in order, to
produce one canonical token (or is dropped). Order matters and is itself a decision:
quantity is stripped **before** the parenthetical is harvested, so the harvested
synonym key is a clean ingredient name rather than one still carrying its quantity.

#### Decision A — Drop the preparation qualifier

**What:** everything after `" - "` is removed (`"Onion - thinly sliced"` → `"Onion"`).

**Why:** the text after the dash describes *preparation* (sliced, chopped, to taste),
not *identity*. Two recipes using onion "thinly sliced" vs "chopped" use the same
ingredient; keeping the qualifier would split one ingredient into many tokens. *(This
is a general observation about the dataset's format and should be more closely
verified as coverage expands.)*

#### Decision B — Strip parentheticals, but harvest them as synonyms

**What:** parenthetical content is removed from the token (`"Curd (Dahi / Yogurt)"`
→ token `"curd"`), but before removal it is recorded as a synonym mapping
(`curd → {dahi, yogurt}`), split on `/` and `,`. The collected map is saved to
`data/processed/*_synonyms.json`.

**Why:** the parenthetical is noise *for the token* (it would fragment the name), but
it is **free, high-value data** for the project. These glosses are exactly the
Hindi/Malayalam/English synonym pairs (`brinjal → eggplant/baingan`,
`karela → bitter gourd/pavakkai`, `kokum → malabar tamarind`) that Task A will need
to match ingredients against IFCT's English nutrient tables. Deleting them outright,
as the reference does, would throw away information we know we need downstream. So we
delete from the token **and** keep the mapping.

#### Decision C — Strip leading quantity and unit tokens (front-only)

**What:** the leading run of number tokens (`3`, `1/2`, `1-1/2`) and unit words
(`cup`, `tablespoon`, `sprig`, ...) is removed, stopping at the first "real" word.
`"3 tablespoon Red Chilli powder"` → `"Red Chilli powder"`.

**Why:** a surviving `tablespoon` token would co-occur with nearly every ingredient
and pollute every neighbourhood. 

We strip from the *front* only, stopping at the first real word, for two data-driven reasons established by inspecting the corpus:
(1) 89% of ingredient lines lead with the quantity, so "anywhere" stripping buys
almost nothing; (2) stopping early protects ingredient names that contain a
unit-homonym. The clearest case is **`gram`**: it is both a mass unit (`250 grams
Fish`) and an ingredient word (`Gram flour`, `black gram`, `Bengal Gram Dal`). We
resolve this by treating `gram`/`grams` as a strippable unit **only when it directly
follows a number**. 

#### Decision D — Canonicalize to one consistent token

**What:** lowercase, collapse simple plurals, underscore-join the surviving words into
a single token, and drop any token containing non-Latin characters.
`"Red Chilli powder"` → `"red_chilli_powder"`; `"onions"` and `"onion"` both →
`"onion"`.

**Why (consistency over correctness).** word2vec needs every mention of one ingredient
to become the *same string*; it does not need that string to be a linguistically
correct singular. Our plural rule is crude and sometimes "wrong" (`curry_leaves` →
`curry_leav`), and that is fine, because it is applied uniformly: every mention of
curry leaf becomes the same `curry_leav` token.

**Why drop non-Latin tokens.** The `TranslatedIngredients` column is incompletely
translated: some ingredient text remains in Devanagari (e.g. `नमक`, Hindi for salt).
Left in, these become *separate tokens for ingredients that already exist in English*,
splitting one ingredient's signal across two tokens. For this prototype we drop any
non-ASCII token for simplicity. 

### 4.5 Why we do not lemmatize

**Lemmatization** reduces a word to its dictionary base form (`leaves` → `leaf`,
`baked` → `bake`) using a language model's grammatical knowledge. Pellegrini uses
spaCy's `en_core_web_lg` model to do this.

spaCy is not used in this initial model, because it is not certain how an English
lemmatizer will behave on a Kerala-based ingredient list (terms like *besan, amchur,
karela* were never in its training data), and a simple plural rule is sufficient and
fully transparent for this prototype phase. spaCy (or a NER-based approach) can be
explored in future iterations.

### 4.6 Output

The pipeline (`preprocessing/build_corpus.py`) produces two artifacts in
`data/processed/`:

- **`v0a_kerala.txt`:** the corpus: one recipe per line, space-separated canonical
  tokens. This is what the model trains on. Saving it as an inspectable file
  decouples parsing runs from training runs and makes the corpus diffable.
- **`v0a_kerala_synonyms.json`:** the harvested synonym map from Decision B.

---

## 5. Known limitations (preprocessing)

- **Small corpus.** 145 recipes yield a sparse corpus; only ~68 ingredients appear
  ≥5 times and ~43 appear ≥10 times. This is the *effective vocabulary* which is the set of
  ingredients with enough contexts to earn a trustworthy vector, and the pool any
  probe set must be drawn from.
- **Devanagari drop.** 18 recipes dropped for untranslated ingredient text (see
  Decision D). Documented and revisitable; recovering them is tied to a
  translation/normalization layer that Task A will need regardless.


---

## 6. Audit and Results

WIP 

---

## References

- Pellegrini, C., Özsoy, E., Wintergerst, M., & Groh, G. (2021). *Exploiting Food
  Embeddings for Ingredient Substitution.* HEALTHINF.
- Shirai, S. S., Seneviratne, O., Gordon, M. E., Chen, C.-H., & McGuinness, D. L.
  (2021). *Identifying Ingredient Substitutions Using a Knowledge Graph of Food
  (DIISH).* Frontiers in Artificial Intelligence.
- Indian Food Composition Tables (IFCT) 2017, National Institute of Nutrition, ICMR.