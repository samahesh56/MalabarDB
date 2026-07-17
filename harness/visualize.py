"""
visualize.py — project the v0a vectors to 2D with PCA and scatter them.
A LOOK, not evidence: helps you SEE the co-occurrence clustering. Distances are
approximate (2D shadow of 50D), so read groupings, not exact positions.
Run from repo root:  python harness/visualize.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
from gensim.models import KeyedVectors
from sklearn.decomposition import PCA

REPO_ROOT = Path(__file__).resolve().parent.parent
VECTORS_PATH = REPO_ROOT / "models" / "v0a_ingredient_sg" / "vectors.kv"
OUT_PATH = REPO_ROOT / "harness" / "v0a_pca.png"

# TODO: how many ingredients to plot? Plotting ALL ~200 is an unreadable mess of
#   labels, and half are rare (noisy vectors). Plotting the most frequent N keeps
#   it legible AND restricts to vectors we trust. Pick N (30-50 is sensible) and
#   say why in a comment.
TOP_N = 40


def main():
    wv = KeyedVectors.load(str(VECTORS_PATH))

    # wv.index_to_key is ordered by frequency (most common first), so the first
    # TOP_N are exactly our high-confidence, high-frequency ingredients.
    words = wv.index_to_key[:TOP_N]
    vectors = wv[words]                      # shape (TOP_N, 50)

    # PCA: 50 dims -> 2. fit_transform returns the 2D coordinates.
    coords = PCA(n_components=2, random_state=42).fit_transform(vectors)

    # scatter + label every point (labels are what make this readable)
    plt.figure(figsize=(14, 10))
    plt.scatter(coords[:, 0], coords[:, 1], s=30)
    for (x, y), word in zip(coords, words):
        plt.annotate(word, (x, y), fontsize=8,
                     xytext=(4, 2), textcoords="offset points")

    # TODO (optional): the PCA axes have no inherent meaning, but you can label
    #   them with the fraction of variance each captures, which tells you how
    #   much structure the 2D view actually preserves:
    #   pca = PCA(n_components=2, ...).fit(vectors)
    #   plt.xlabel(f"PC1 ({pca.explained_variance_ratio_[0]:.0%} var)")
    #   ... (requires keeping the fitted pca object, not just coords)

    plt.title(f"v0a ingredient vectors (top {TOP_N} by frequency), PCA to 2D")
    plt.tight_layout()
    plt.savefig(OUT_PATH, dpi=150)
    print(f"saved plot -> {OUT_PATH}")
    plt.show()   # opens a window; close it to end the script


if __name__ == "__main__":
    main()