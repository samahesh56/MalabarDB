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

# --- raw inputs ---------------------------------------------------------
IFCT_INDEX = RAW / "IFCT_index.csv"
RECIPES = RAW / "IndianFoodDatasetCSV.csv"

# --- interim ------------------------------------------------------------
EXTRACTED_NAMES = INTERIM / "extracted_names.json"

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

# --- review (human-owned) -----------------------------------------------
REVIEW_QUEUE = REVIEW / "review_queue.csv"
LINKAGE_TABLE = REVIEW / "linkage_table.csv"


def ensure_dirs() -> None:
    '''Create output directories. Safe to call repeatedly.'''
    for d in (INTERIM, PROCESSED, REVIEW, MODEL_V0A, FIGURES):
        d.mkdir(parents=True, exist_ok=True)