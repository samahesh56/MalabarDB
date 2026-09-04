"""
Merge base extraction + manual review corrections -> final recipe_ingredients

Run this AFTER filling in the corrected_* columns in review_queue_parsing.csv.
Safe to rerun at any point, including mid-review -- rows not yet reached keep
their automatic extraction and stay marked 'needs_review'.

Only the four corrected_* columns are read back. The queue's context columns
(modifier_text, ip_state, ip_comment, ip_purpose) exist to help a reviewer
judge a flagged line and are never merged.

Output: recipe_ingredients_final.csv, the input to build_db.py
"""

import pandas as pd

from malabardb import paths

KEY = ['recipe_id', 'line_no']

CORRECTION_COLS = ['corrected_name', 'corrected_qty', 'corrected_unit',
                   'corrected_state']

base = pd.read_csv(paths.FULL_CORPUS_LABELS)
queue = pd.read_csv(paths.REVIEW_QUEUE_PARSING)


def nonblank(s):
    """True where a cell holds real text. Covers NaN and the literal string
    'nan', which appears whenever a blank cell has been through a CSV
    round-trip via astype(str)."""
    return s.astype(str).str.strip().replace({'nan': '', 'NaN': ''}).ne('')


def apply_corrections(base_df, review_df, mapping):
    """mapping: {base_column: corrected_column}. Overrides base_column with
    corrected_column ONLY where the correction is filled in. Blank means
    'not reviewed yet', not 'clear this field'."""
    merged = base_df.merge(review_df[KEY + list(mapping.values())],
                           on=KEY, how='left')
    for base_col, corr_col in mapping.items():
        has_correction = nonblank(merged[corr_col])
        merged.loc[has_correction, base_col] = merged.loc[has_correction, corr_col]
    return merged.drop(columns=list(mapping.values()))


final = apply_corrections(base, queue, {
    'regex_name': 'corrected_name',
    'regex_qty':  'corrected_qty',
    'regex_unit': 'corrected_unit',
    'ip_state':   'corrected_state',
})

# Fix 1: bulk-resolve the 'inch' rows. Regex was already right,
# ip is structurally blind to this unit, decision already verified.
inch_keys = set(zip(final.loc[final['regex_unit'] == 'inch', 'recipe_id'],
                    final.loc[final['regex_unit'] == 'inch', 'line_no']))
print(f'bulk-resolving {len(inch_keys)} "inch" rows as regex-correct')

# Fix 2: the 3 known blind-spot rows
BLIND_SPOT_FIXES = {
    (23, 8): {'qty': '1', 'unit': 'pinch'},    # Salt - a pinch
    (75, 5): {'qty': '1', 'unit': 'pinch'},    # turmeric powder - a pinch
    (101, 2): {'qty': '1', 'unit': 'pinch'},   # turmeric powder - a pinch
}
for (rid, ln), vals in BLIND_SPOT_FIXES.items():
    mask = (final['recipe_id'] == rid) & (final['line_no'] == ln)
    final.loc[mask, 'regex_qty'] = vals['qty']
    final.loc[mask, 'regex_unit'] = vals['unit']

# Fix 3: split known merged-ingredient rows into two rows
MERGED_ROW_SPLITS = {
    (11, 4): [   # "1 tsp active dry yeast - + 1/2 cup lukewarm water"
        {'qty': '1', 'unit': 'tsp', 'name': 'active dry yeast', 'dry_fresh': 'Dry'},
        {'qty': '1/2', 'unit': 'cup', 'name': 'lukewarm water'},
    ],
    (66, 4): [   # "1 teaspoon Active dry yeast -  + 1/2 cup of luke warm water"
        {'qty': '1', 'unit': 'teaspoon', 'name': 'Active dry yeast', 'dry_fresh': 'Dry'},
        {'qty': '1/2', 'unit': 'cup', 'name': 'luke warm water'},
    ],
}
new_rows = []
split_row_keys = set()   # the (recipe_id, line_no) keys created below
for (rid, ln), split_rows in MERGED_ROW_SPLITS.items():
    orig_mask = (final['recipe_id'] == rid) & (final['line_no'] == ln)
    orig_raw_line = final.loc[orig_mask, 'raw_line'].values[0] if orig_mask.any() else ''
    final = final[~orig_mask]
    next_line_no = int(final.loc[final['recipe_id'] == rid, 'line_no'].max()) + 1
    for i, vals in enumerate(split_rows):
        new_line_no = next_line_no + i
        split_row_keys.add((rid, new_line_no))
        new_rows.append({
            'recipe_id': rid, 'line_no': new_line_no, 'raw_line': orig_raw_line,
            'regex_qty': vals['qty'], 'regex_unit': vals['unit'],
            'regex_name': vals['name'],
            'ip_state': '', 'patch_dry_fresh': vals.get('dry_fresh', ''),
            # Hand-authored, so no flag fires: blank review_flags keeps these
            # rows out of 'needs_review' below.
            'review_flags': '',
            'flag_name': False, 'flag_qty': False,
            'flag_unit': False, 'flag_state': False,
        })
if new_rows:
    final = pd.concat([final, pd.DataFrame(new_rows)], ignore_index=True)
    final['line_no'] = final['line_no'].astype(int)

# parse_status:
#   reviewed     a correction was entered, or one of the fixes above applied
#   needs_review still flagged and untouched
#   clean        no flag ever fired
corrected = queue.loc[queue[CORRECTION_COLS].apply(nonblank).any(axis=1), KEY]
reviewed_keys = (set(map(tuple, corrected.values))
                 | inch_keys | set(BLIND_SPOT_FIXES) | split_row_keys)

final['parse_status'] = final.apply(
    lambda r: 'reviewed' if (r['recipe_id'], r['line_no']) in reviewed_keys
    else ('needs_review' if str(r['review_flags']).strip() not in ('', 'nan')
          else 'clean'),
    axis=1)

output_cols = ['recipe_id', 'line_no', 'raw_line', 'regex_qty', 'regex_unit',
               'regex_name', 'ip_state', 'patch_dry_fresh', 'parse_status']
final[output_cols].rename(columns={
    'regex_qty': 'qty', 'regex_unit': 'unit', 'regex_name': 'name_raw',
    'ip_state': 'state', 'patch_dry_fresh': 'dry_fresh',
}).to_csv(paths.RECIPE_INGREDIENTS_FINAL, index=False)

print('recipe_ingredients_final.csv:', len(final), 'rows')
print(final['parse_status'].value_counts())