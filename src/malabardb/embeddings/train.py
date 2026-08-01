"""
train.py - train the v0a skip-gram on the ingredient-list corpus.

Reads data/processed/v0a_kerala_spacy.txt, trains word2vec, saves the vectors.
Run from repo root:  python models/v0a_ingredient_sg/train.py

Each recipe is one "sentence"; its ingredient tokens are each other's neighbours.
Skip-gram learns similar vectors for ingredients that share recipe-mates, i.e. it
learns co-occurrence (complements)

This trains on ingredient LISTS, not the recipe INSTRUCTION text Pellegrini's
food2vec uses. It is the food2vec / ingredient2vec formulation
"""

import json
from pathlib import Path
from malabardb import paths

from gensim.models import Word2Vec

# Frequency floor. min_count=2 keeps 121 types but their vectors are barely
# trained; min_count=5 keeps 67 (86.8% of tokens) and is what we report.
MIN_COUNT = 5

# Gensim's default (5) assumes a huge corpus. 200 is safely past convergence.
EPOCHS = 200

PROBES = ["coconut_oil", "curry_leaves", "mustard_seed", "jaggery"] # ingredients to inspect after training


def load_corpus(path):
    """Read the corpus into the list-of-lists gensim expects: one inner list per
    recipe, one token per ingredient."""
    recipes = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            tokens = line.split()
            if tokens:
                recipes.append(tokens)
    return recipes


def train(recipes):
    """Train skip-gram. The non-default settings below are small-corpus choices."""
    model = Word2Vec(
        sentences=recipes,
        sg=1,                 # skip-gram, not CBOW: better on rare words / small data
        vector_size=50,       # 100 would overparameterise 67 types
        window=15,            # ingredient order is arbitrary, so span the whole recipe
        min_count=MIN_COUNT,
        epochs=EPOCHS,
        workers=1,            # single worker => deterministic (with a fixed seed)
        seed=42,
    )
    return model


def inspect(model):
    """Print top-5 neighbours for a few probe ingredients. These are complements,
    not substitutes — that is the result, not a bug."""
    for word in PROBES:
        if word not in model.wv:
            print(f"  {word}: not in vocabulary (min_count={MIN_COUNT})")
            continue
        neighbors = ", ".join(w for w, _ in model.wv.most_similar(word, topn=5))
        print(f"  {word} -> {neighbors}")


if __name__ == "__main__":
    recipes = load_corpus(paths.CORPUS)
    print(f"loaded {len(recipes)} recipes from {paths.CORPUS.name}")

    model = train(recipes)
    print(f"vocab: {len(model.wv)} types (min_count={MIN_COUNT}, epochs={EPOCHS})")

    paths.ensure_dirs()
    model.wv.save(str(paths.VECTORS))

    # Config travels with the vectors: a neighbour list is meaningless without
    # knowing which corpus and settings produced it.
    config = {
        "corpus": paths.CORPUS.name,
        "n_recipes": len(recipes),
        "sg": 1, "vector_size": 50, "window": 15,
        "min_count": MIN_COUNT, "epochs": EPOCHS, "seed": 42,
        "vocab_size": len(model.wv),
    }
    (paths.V0A_CONFIG).write_text(json.dumps(config, indent=2), encoding="utf-8")

    print(f"saved vectors -> {paths.VECTORS}")
    inspect(model)