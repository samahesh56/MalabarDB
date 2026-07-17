"""
train.py: train the v0a skip-gram on the ingredient-list corpus.
Reads data/processed/v0a_kerala.txt, trains word2vec, saves the vectors.
Run from repo root:  python models/v0a_ingredient_sg/train.py

skip-gram learns vectors by trying to predict each
token's NEIGHBORS from the token itself. In our corpus a "sentence" is one
recipe's ingredient list, so a token's neighbors are its RECIPE-MATES. Ingredients
that share recipe-mates get similar vectors, i.e. the model learns CO-OCCURRENCE (complements)
"""

from pathlib import Path

from gensim.models import Word2Vec

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_PATH = REPO_ROOT / "data" / "processed" / "v0a_kerala.txt"
MODEL_DIR = REPO_ROOT / "models" / "v0a_ingredient_sg"


def load_corpus(path):
    """Read the saved corpus back into the list-of-lists gensim expects.

    Each LINE = one recipe; each SPACE-separated token = one ingredient. So one
    inner list is one "sentence" (recipe), and its tokens are the co-occurring
    ingredients word2vec will treat as each other's neighbors."""
    recipes = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            tokens = line.split()
            if tokens: # skip blank lines 
                recipes.append(tokens)
    return recipes


def train(recipes):
    """Train skip-gram. Every keyword below is a deliberate small-corpus choice
    (most are the OPPOSITE of Pellegrini's large-corpus defaults)."""
    model = Word2Vec(
        sentences=recipes,
        sg=1,                 # skip-gram: better for rare words / small data (Pelligrini uses CBOW)
        vector_size=50,       # small dims for small data 
        window=15,            # window spans a whole recipe (important for maintaining context per recipe)
        min_count=2,          # drop tokens that appear only once (no co-occurrence info to learn from)
        epochs=60,            # many passes compensate for fewer recipe data 
        workers=1,            # keep 1 for reproducibility (multi-thread = nondeterministic)
        seed=42,              # fixed seed so runs are repeatable
    )
    return model


def inspect(model):
    """Print top-5 neighbors for a few high-frequency probe ingredients.
    Neighbors will be COMPLEMENTS, not substitutes"""
    # 3-4 probe ingredients from >=10x list prints top 5 neighbors
    for word in ["coconut_oil", "curry_leav", "mustard_seed"]:
        print(word, "->", model.wv.most_similar(word, topn=5))


if __name__ == "__main__":
    recipes = load_corpus(CORPUS_PATH)
    print(f"loaded {len(recipes)} recipes")
    model = train(recipes)

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model.wv.save(str(MODEL_DIR / "vectors.kv"))   # save just the vectors (KeyedVectors)
    print(f"saved vectors -> {MODEL_DIR / 'vectors.kv'}")

    inspect(model)