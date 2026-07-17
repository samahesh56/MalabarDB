"""
audit.py — verify the v0a corpus BEFORE training.
Reads data/processed/v0a_kerala.txt and runs three checks:
  1. split-vote  — did plural collapse (Decision D) work?
  2. wreckage    — are there malformed tokens the parser mangled?
  3. frequency   — do common/rare tokens look sane?
Run from the repo root:  python preprocessing/audit.py
"""

from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = REPO_ROOT / "data" / "processed" / "v0a_kerala.txt"


def load_tokens(path):
    """Read the saved corpus back into memory.
    Each LINE of the file is one recipe; tokens are space-separated (that's how
    save_corpus wrote them). We return both the list of recipes AND a flat
    Counter of every token, because the checks need token frequencies."""
    recipes = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            tokens = line.split()
            if tokens:
                recipes.append(tokens)
    counts = Counter(tok for recipe in recipes for tok in recipe)
    return recipes, counts


def check_split_votes(counts):
    """CHECK 1 — do singular/plural pairs still exist as SEPARATE tokens?
    For each token, test whether an obvious inflected variant (token+'s',
    token+'es', or token minus a trailing 's') is ALSO in the vocabulary.
    If a pair shows up, the two spellings didn't collapse and word2vec will
    treat one ingredient as two. Empty list = Decision D worked."""
    vocab = set(counts)
    seen = set()
    print("=== CHECK 1: split-votes (want: none) ===")
    found = False
    for tok in sorted(vocab):
        variants = {tok + "s", tok + "es", tok[:-1] if tok.endswith("s") else None}
        for v in variants:
            if v and v in vocab and (tok, v) not in seen and (v, tok) not in seen:
                seen.add((tok, v))
                print(f"   {tok} ({counts[tok]})  <->  {v} ({counts[v]})")
                found = True
    if not found:
        print("   none — plurals collapsed cleanly")
    print()


def check_wreckage(counts):
    """CHECK 2 — malformed tokens, i.e. parser bugs rather than real names.
    We hunt three known bad shapes: a leading 'grams_'/'gram_' (unit leak),
    a leading 'to_' (range leftover), and any '/' (fraction fragment).
    After the grams fix, this count should drop toward zero."""
    print("=== CHECK 2: wreckage (want: none / a small accepted few) ===")
    suspects = sorted(
        t for t in counts
        if t.startswith(("grams_", "gram_", "to_")) or "/" in t or t[:1].isdigit()
    )
    for t in suspects:
        print(f"   {t}  ({counts[t]})")
    print(f"   -> {len(suspects)} suspect tokens")
    print()


def check_frequency(counts):
    """CHECK 3 — is the distribution sane?
    The TOP tokens should be Kerala staples (salt, curry leaves, coconut...).
    The TAIL — hapaxes, tokens appearing exactly once — should be rare
    INGREDIENTS, not parser junk. The >=N counts tell you your EFFECTIVE
    vocabulary: only tokens seen several times get a usable vector, and your
    probe set must be drawn from those."""
    print("=== CHECK 3: frequency ===")
    print(f"   vocab size: {len(counts)}   total tokens: {sum(counts.values())}")
    hapax = sum(1 for c in counts.values() if c == 1)
    print(f"   hapaxes (appear once): {hapax} ({100 * hapax // len(counts)}% of vocab)")
    for k in (2, 3, 5, 10):
        print(f"   appear >= {k}x: {sum(1 for c in counts.values() if c >= k)}")
    print("   top 25:")
    for tok, c in counts.most_common(25):
        print(f"      {c:3d}  {tok}")
    print()


if __name__ == "__main__":
    recipes, counts = load_tokens(CORPUS_PATH)
    print(f"loaded {len(recipes)} recipes\n")
    check_split_votes(counts)
    check_wreckage(counts)
    check_frequency(counts)