'''
paths.py - single source of truth for every filesystem location.

Paths are resolved from THIS FILE's location, not the working directory, so
scripts behave identically whether run from the repo root. (import from here)
'''

from pathlib import Path

# src/malabardb/paths.py -> parents[2] is the repo root
ROOT = Path(__file__).resolve().parents[2]

DATA = ROOT / "data"
RAW = DATA / "raw"              # untouched downloads; never written to
INTERIM = DATA / "interim"      # intermediate, disposable, regenerable
PROCESSED = DATA / "processed"  # pipeline outputs consumed by later stages
REVIEW = DATA / "review"        # human-edited files; NEVER overwritten by code
FINAL = DATA / "final"

# --- raw inputs ---------------------------------------------------------
IFCT_INDEX = RAW / "IFCT_index.csv"
RECIPES = RAW / "IndianFoodDatasetCSV.csv"

# --- models ---
MODELS    = ROOT / "models"
MODEL_V0A = MODELS / "v0a_ingredient_sg"
VECTORS   = MODEL_V0A / "vectors.kv"
V0A_CONFIG = MODEL_V0A / "config.json"

# --- figures ---
FIGURES = ROOT / "reports" / "figures"
PCA_PLOT = FIGURES / "v0a_pca.png"

# --- processed ----------------------------------------------------------
VOCAB_SPACY = PROCESSED / "vocabulary_spacy.json"
VOCAB_RULES = PROCESSED / "vocabulary_rules.json"
IFCT_NORMALIZED = PROCESSED / "ifct_normalized.csv"
CANDIDATE_TABLE = PROCESSED / "candidate_table.csv"
CORPUS = PROCESSED / "v0a_kerala_spacy.txt"
EXTRACTED_NAMES = PROCESSED / "extracted_names.json"
RECIPE_INGREDIENTS = PROCESSED / "recipe_ingredients.csv" 
FULL_CORPUS_LABELS = PROCESSED / "full_corpus_labels.csv"  # regex + ingredient-parser candidate labels

# --- review (human-owned) -----------------------------------------------
REVIEW_QUEUE_NAME_QTY_UNIT = REVIEW / "review_queue_name_qty_unit.csv"
REVIEW_QUEUE_STATE_PRIORITY = REVIEW / "review_queue_state_priority.csv"
REVIEW_QUEUE_STATE_ANOMALY = REVIEW / "review_queue_state_anomaly.csv"
REVIEW_QUEUE_IFCT_AMBIGUOUS = REVIEW / "review_queue_ifct_ambiguous.csv"
REVIEW_QUEUE_IFCT_UNMATCHED = REVIEW / "review_queue_ifct_unmatched.csv"

# --- final --------------------------------------------------------------
INGREDIENTS = FINAL / "ingredients.csv"
RECIPES_TABLE = FINAL / "recipes.csv"  
RECIPE_INGREDIENTS_FINAL = FINAL / "recipe_ingredients_final.csv"
MALABARDB = FINAL / "malabardb.db"


def ensure_dirs() -> None:
    '''Create output directories. Safe to call repeatedly.'''
    for d in (INTERIM, PROCESSED, REVIEW, MODEL_V0A, FIGURES):
        d.mkdir(parents=True, exist_ok=True)