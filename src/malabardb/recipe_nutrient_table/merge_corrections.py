"""
Step 3: combine the machine-labeled corpus with human input to produce the final
recipe_ingredients table.

Three inputs, applied in this order:
    full_corpus_labels.csv    every line, machine-extracted          (step 2 output)
    review_queue_parsing.csv  field corrections for FLAGGED lines    (hand-edited)
    manual_fixes.csv          edits for lines no flag could catch    (hand-edited)

Stage 1  overlay the review queue        blank correction = keep the machine value
Stage 2  apply manual fixes              set / drop / split (see manual_fixes.csv)
Stage 3  canonicalize qty and unit       '1 1/2' -> '1-1/2', 'cups' -> 'cup'
Stage 4  assign parse_status             reviewed | needs_review | auto

Output: recipe_ingredients_final.csv, the input to build_db.py
"""

import pandas as pd

from malabardb import paths
from malabardb.recipe_nutrient_table.review_queue_parsing import (
    UNIT_ALIASES, normalize_qty, find_moved_lines)

KEY = ['recipe_id', 'line_no']

# The queue's four editable columns, and the corpus column each one overrides.
CORRECTION_MAP = {
    'regex_name': 'corrected_name',
    'regex_qty': 'corrected_qty',
    'regex_unit': 'corrected_unit',
    'ip_state': 'corrected_state',
}
CORRECTION_COLS = list(CORRECTION_MAP.values())

OUTPUT_COLS = ['recipe_id', 'line_no', 'raw_line', 'regex_qty', 'regex_unit',
               'regex_name', 'ip_state', 'patch_dry_fresh', 'parse_status']
OUTPUT_NAMES = {'regex_qty': 'qty', 'regex_unit': 'unit', 'regex_name': 'name_raw',
                'ip_state': 'state', 'patch_dry_fresh': 'dry_fresh'}


def nonblank(s):
    """True where a cell holds real text. NaN, '' and '   ' are all blank."""
    return s.fillna('').astype(str).str.strip().ne('')


# Stage 1: overlay the review queue 
def apply_corrections(corpus, queue):
    """Override machine columns with the reviewer's corrected_* values.

    Only non-blank corrections are applied: blank means 'not reviewed yet',
    never 'clear this field'. Corrections are stripped on the way in."""
    merged = corpus.merge(queue[KEY + CORRECTION_COLS], on=KEY, how='left')
    for machine_col, corrected_col in CORRECTION_MAP.items():
        filled = nonblank(merged[corrected_col])
        value = merged[corrected_col].astype(str).str.strip()
        merged.loc[filled, machine_col] = value.loc[filled]
    return merged.drop(columns=CORRECTION_COLS)


#  Stage 2: manual fixes 
def load_manual_fixes(corpus):
    """Read manual_fixes.csv and check every key exists in the corpus."""

    fixes = pd.read_csv(paths.MANUAL_FIXES, dtype=str, keep_default_na=False)
    fixes = fixes.astype({'recipe_id': int, 'line_no': int})

    unknown = set(fixes['action']) - {'set', 'drop', 'split'}
    if unknown:
        raise SystemExit(f'manual_fixes.csv: unknown action(s) {sorted(unknown)}')

    corpus_keys = set(map(tuple, corpus[KEY].values))
    missing = [k for k in map(tuple, fixes[KEY].values) if k not in corpus_keys]
    if missing:
        raise SystemExit(
            f'manual_fixes.csv targets {len(missing)} line(s) that are not in '
            f'full_corpus_labels.csv: {sorted(set(missing))}')
    return fixes


def apply_set(final, rows):
    """Overwrite qty/unit/name on lines both extractors got wrong."""
    for _, fix in rows.iterrows():
        at = (final['recipe_id'] == fix.recipe_id) & (final['line_no'] == fix.line_no)
        final.loc[at, 'regex_qty'] = fix.qty
        final.loc[at, 'regex_unit'] = fix.unit
        if fix['name']:
            final.loc[at, 'regex_name'] = fix['name']
    return final, set(map(tuple, rows[KEY].values))


def apply_drop(final, rows):
    """Remove lines that are not ingredients: instruction fragments and
    orphaned modifiers left behind by the source's comma placement."""
    for _, fix in rows.iterrows():
        at = (final['recipe_id'] == fix.recipe_id) & (final['line_no'] == fix.line_no)
        final = final[~at]
    return final


def apply_split(final, rows):
    """Replace one line that packs two ingredients with one row per ingredient.
    New rows are appended with fresh line_no values rather than renumbering the
    recipe. Both new rows keep the parent's raw_line as provenance."""

    new_rows, new_keys = [], set()
    for (recipe_id, line_no), group in rows.groupby(KEY, sort=False):
        at = (final['recipe_id'] == recipe_id) & (final['line_no'] == line_no)
        parent_raw_line = final.loc[at, 'raw_line'].values[0] if at.any() else ''
        final = final[~at]

        next_line_no = int(final.loc[final['recipe_id'] == recipe_id, 'line_no'].max()) + 1
        for offset, (_, fix) in enumerate(group.iterrows()):
            key = (recipe_id, next_line_no + offset)
            new_keys.add(key)
            new_rows.append({
                'recipe_id': recipe_id, 'line_no': next_line_no + offset,
                'raw_line': parent_raw_line,
                'regex_qty': fix.qty, 'regex_unit': fix.unit, 'regex_name': fix['name'],
                'ip_state': '', 'patch_dry_fresh': fix.dry_fresh,
                # Hand-authored, so no flag fires: blank review_flags keeps these
                # out of 'needs_review' in stage 4.
                'review_flags': '',
                'flag_name': False, 'flag_qty': False,
                'flag_unit': False, 'flag_state': False,
            })
    if new_rows:
        final = pd.concat([final, pd.DataFrame(new_rows)], ignore_index=True)
        final['line_no'] = final['line_no'].astype(int)
    return final, new_keys


def build():
    corpus = pd.read_csv(paths.FULL_CORPUS_LABELS)
    queue = pd.read_csv(paths.REVIEW_QUEUE_PARSING,
                        dtype={c: str for c in CORRECTION_COLS})

    # Every queue row must still describe the same source line as the corpus.
    moved = find_moved_lines(queue, corpus)
    if len(moved):
        raise SystemExit(
            f'{len(moved)} queue rows do not match full_corpus_labels.csv; '
            f'rerun review_queue_parsing, or undo edits to raw_line:\n'
            f'{moved.to_string(index=False)}')

    fixes = load_manual_fixes(corpus)

    # corrections win over the machine extraction.
    final = apply_corrections(corpus, queue)

    # edits the flagging process structurally cannot produce.
    final, set_keys = apply_set(final, fixes[fixes['action'] == 'set'])
    n_before_drop = len(final)
    final = apply_drop(final, fixes[fixes['action'] == 'drop'])
    n_dropped = n_before_drop - len(final)
    final, split_keys = apply_split(final, fixes[fixes['action'] == 'split'])

    # Stage 3: one written form per value, so later stages can key on them.
    # qty: '1 1/2' -> '1-1/2'.  unit: the same alias map the flagging uses
    final['regex_qty'] = final['regex_qty'].astype(str).apply(normalize_qty)
    unit = final['regex_unit'].fillna('').astype(str).str.strip().str.lower()
    canonical_unit = unit.map(lambda u: UNIT_ALIASES.get(u, u))
    n_canonicalized = int((unit != canonical_unit).sum())
    final['regex_unit'] = canonical_unit

    # Stage 4: provenance of every row's values.
    #   reviewed      a human supplied at least one value on this line
    #   needs_review  still flagged, nobody has looked at it yet
    #   auto          no flag ever fired; machine extraction, unverified
    corrected = queue.loc[queue[CORRECTION_COLS].apply(nonblank).any(axis=1), KEY]
    reviewed_keys = set(map(tuple, corrected.values)) | set_keys | split_keys
    final['parse_status'] = final.apply(
        lambda r: 'reviewed' if (r['recipe_id'], r['line_no']) in reviewed_keys
        else ('needs_review' if str(r['review_flags']).strip() not in ('', 'nan')
              else 'auto'),
        axis=1)

    final[OUTPUT_COLS].rename(columns=OUTPUT_NAMES).to_csv(
        paths.RECIPE_INGREDIENTS_FINAL, index=False)

    report(final, fixes, n_canonicalized, n_dropped, set_keys, split_keys)
    return final


def report(final, fixes, n_canonicalized, n_dropped, set_keys, split_keys):
    """Print the accounting a reader needs to audit this stage."""
    n = len(final)
    print(f'recipe_ingredients_final.csv: {n} rows')
    print(f'unit values canonicalized: {n_canonicalized}')
    print()
    print(f'{"parse_status":<14}{"rows":>7}{"% lines":>9}')
    for status, count in final['parse_status'].value_counts().items():
        print(f'{status:<14}{count:>7}{count / n * 100:>8.1f}%')

    n_touched = len(set_keys) + n_dropped + len(split_keys)
    print(f'\nmanual fixes ({n_touched} rows, {n_touched / n * 100:.2f}% of corpus)')
    print(f'{"action":<8}{"entries":>9}{"rows":>7}')
    for action, rows in [('set', len(set_keys)), ('drop', n_dropped),
                         ('split', len(split_keys))]:
        n_entries = int((fixes['action'] == action).sum())
        print(f'{action:<8}{n_entries:>9}{rows:>7}')
    print('\nevery manual fix and its justification:')
    print(fixes[fixes['note'].str.strip().ne('')][
        ['recipe_id', 'line_no', 'action', 'note']].to_string(index=False))


if __name__ == '__main__':
    build()