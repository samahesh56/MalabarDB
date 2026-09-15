# MalabarDB pipeline.
#
# A rule says "TARGET is produced from PREREQS by RECIPE", and Make 
# re-runs the recipe only when TARGET is missing or older than
# any PREREQ. Every rule below therefore lists two things as prerequisites: 
# the data files the script reads, and the script itself. Editing either one
# invalidates everything downstream.
#
#   make db            build everything through data/final/malabardb.db
#   make parse-queue   stop after the parsing review queue (human step)
#   make ingred-queue  stop after the ingredient review queue (human step)
#   make -n db         show what would run, without running it
#   make clean         remove regenerable outputs (never touches data/review)
#
# Requires GNU Make >= 4.3 for grouped targets (the `&:` rules). macOS ships
# 3.81; `brew install make` provides it as `gmake`. On Windows use WSL.

PYTHON ?= python
export PYTHONPATH := src
PY  := $(PYTHON) -m
PKG := malabardb.recipe_nutrient_table
S   := src/malabardb/recipe_nutrient_table

# --- directories: mirror paths.py ------------------------------------------
RAW       := data/raw
PROCESSED := data/processed
REVIEW    := data/review
FINAL     := data/final

# --- raw inputs: never targets ---------------------------------------------
KAGGLE     := $(RAW)/IndianFoodDatasetCSV.csv
IFCT_INDEX := $(RAW)/IFCT_index.csv
LOCAL_NUTR := $(RAW)/local_nutrients.csv
MANUAL_FIXES := $(RAW)/manual_fixes.csv
FALLBACK_MAP := $(RAW)/fallback_map.csv     

# --- review queues ----------------------------------------------------------
# Written by a script, then hand-edited, then read downstream. They ARE
# targets because a clean clone must be able to produce them; the scripts read
# the prior queue and carry every correction forward, and abort before writing
# if a corrected line no longer matches the corpus.
QUEUE_PARSING := $(REVIEW)/review_queue_parsing.csv
QUEUE_INGRED  := $(REVIEW)/review_queue_ingredients.csv

# --- machine outputs ---------------------------------------------------------
RECIPES_TABLE   := $(FINAL)/recipes.csv
RECIPE_INGRED   := $(PROCESSED)/recipe_ingredients.csv
CORPUS_LABELS   := $(PROCESSED)/full_corpus_labels.csv
RI_FINAL        := $(FINAL)/recipe_ingredients_final.csv
IFCT_NUTRIENTS  := $(FINAL)/ifct_nutrients.csv
IFCT_NORMALIZED := $(PROCESSED)/ifct_normalized.csv
RECIPE_KEYS     := $(PROCESSED)/recipe_keys.csv
INGRED_MAP      := $(FINAL)/ingredient_ifct_map.csv
DB              := $(FINAL)/malabardb.db

# shared modules: a change here invalidates both tracks
NORMALIZE := src/malabardb/normalize.py
PATHS     := src/malabardb/paths.py

.PHONY: all db parse-queue ingred-queue check clean distclean help
.DEFAULT_GOAL := help

# If a recipe fails part-way, delete its (possibly half-written) target so the
# next run does not mistake it for fresh output. The review queues are exempt:
# their scripts fail BEFORE writing, and the file on disk is the human's work.
.DELETE_ON_ERROR:
.PRECIOUS: $(QUEUE_PARSING) $(QUEUE_INGRED)

all: db

# =============================================================================
# Recipe track
# =============================================================================

# One script, two outputs. `&:` (grouped target) tells Make a single run
# produces both; without it Make would run the script twice.
$(RECIPES_TABLE) $(RECIPE_INGRED) &: $(KAGGLE) $(S)/build_recipe_tables.py $(PATHS)
	$(PY) $(PKG).build_recipe_tables

# Two extractors, disagreement -> queue. Regenerating is safe: corrections in
# the existing queue are carried forward, moved lines abort the run.
$(CORPUS_LABELS) $(QUEUE_PARSING) &: $(RECIPE_INGRED) $(S)/review_queue_parsing.py
	$(PY) $(PKG).review_queue_parsing

$(RI_FINAL): $(CORPUS_LABELS) $(QUEUE_PARSING) $(MANUAL_FIXES) $(S)/merge_corrections.py
	$(PY) $(PKG).merge_corrections

# =============================================================================
# IFCT track  (independent of the recipe track)
# =============================================================================

$(IFCT_NUTRIENTS): $(IFCT_INDEX) $(LOCAL_NUTR) $(S)/build_ifct_nutrients.py
	$(PY) $(PKG).build_ifct_nutrients

$(IFCT_NORMALIZED): $(IFCT_INDEX) $(NORMALIZE)
	$(PY) malabardb.normalize --ifct

# =============================================================================
# Linkage  (the tracks meet here)
# =============================================================================

$(RECIPE_KEYS): $(RI_FINAL) $(NORMALIZE) $(S)/match_to_ifct.py
	$(PY) $(PKG).match_to_ifct prepare

$(QUEUE_INGRED): $(RECIPE_KEYS) $(IFCT_NORMALIZED) $(S)/build_ingred_review.py
	$(PY) $(PKG).build_ingred_review

$(INGRED_MAP): $(QUEUE_INGRED) $(FALLBACK_MAP) $(S)/ingredient_ifct_map.py
	$(PY) $(PKG).ingredient_ifct_map

# =============================================================================
# Unit conversion  (next phase; slots in between linkage and the database)
# =============================================================================
# INDB_UNITS     := $(RAW)/indb/Units.xlsx
# INDB_RULES     := $(REVIEW)/indb_category_rules.csv        # hand-transcribed from INDB.do
# UNIT_REFERENCE := $(PROCESSED)/unit_reference.csv
# QUEUE_UNITS    := $(REVIEW)/review_queue_units.csv
# UNIT_MAP       := $(FINAL)/unit_conversions.csv
#
# $(UNIT_REFERENCE): $(INDB_UNITS) $(INDB_RULES) $(S)/build_unit_reference.py
# 	$(PY) $(PKG).build_unit_reference
# $(QUEUE_UNITS): $(RI_FINAL) $(INGRED_MAP) $(UNIT_REFERENCE) $(S)/build_unit_review.py
# 	$(PY) $(PKG).build_unit_review
# $(UNIT_MAP): $(QUEUE_UNITS) $(S)/unit_conversions_map.py
# 	$(PY) $(PKG).unit_conversions_map
# ... then add $(UNIT_MAP) to the $(DB) prerequisites.

# =============================================================================
# Database  (the only writer; runs last)
# =============================================================================

$(DB): $(RECIPES_TABLE) $(RI_FINAL) $(IFCT_NUTRIENTS) $(INGRED_MAP) $(RECIPE_KEYS) $(S)/build_db.py
	$(PY) $(PKG).build_db

db: $(DB)  ## build everything through the SQLite database

# =============================================================================
# Human checkpoints
# =============================================================================

parse-queue: $(QUEUE_PARSING)  ## build up to the parsing review queue, then stop
	@echo "review $(QUEUE_PARSING), then run: make db"

ingred-queue: $(QUEUE_INGRED)  ## build up to the ingredient review queue, then stop
	@echo "review $(QUEUE_INGRED), then run: make db"

check: $(RI_FINAL) $(QUEUE_INGRED)  ## report rows still awaiting a human decision
	@$(PYTHON) -c "import pandas as pd; \
	  ri = pd.read_csv('$(RI_FINAL)'); \
	  q  = pd.read_csv('$(QUEUE_INGRED)', dtype=str, keep_default_na=False); \
	  print('parsing  needs_review :', int((ri.parse_status == 'needs_review').sum())); \
	  print('ingred   status blank :', int((q.get('status', '').str.strip() == '').sum()))"

# =============================================================================
# Housekeeping
# =============================================================================

clean:  ## remove regenerable outputs. Never touches data/raw or data/review.
	rm -f $(PROCESSED)/*.csv $(PROCESSED)/*.json $(PROCESSED)/*.txt $(DB)

distclean: clean  ## also remove committed machine outputs in data/final (git restores them)
	rm -f $(RECIPES_TABLE) $(RI_FINAL) $(IFCT_NUTRIENTS) $(INGRED_MAP)

help:  ## list targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'
