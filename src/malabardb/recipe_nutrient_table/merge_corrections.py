"""
Merge base extraction + manual review corrections -> final recipe_ingredients

Run this AFTER filling in the corrected_* columns in the review queue
CSVs. Safe to rerun at any point, including mid-review -- rows you
haven't gotten to yet keep their automatic extraction and stay marked
'needs_review'.

Output: recipe_ingredients_final.csv: this becomes the new input to build_db.py
"""

import pandas as pd
from malabardb import paths

base = pd.read_csv(paths.FULL_CORPUS_LABELS)
nqu = pd.read_csv(paths.REVIEW_QUEUE_NAME_QTY_UNIT)
state_pri = pd.read_csv(paths.REVIEW_QUEUE_STATE_PRIORITY)

KEY = ['recipe_id', 'line_no']


def apply_corrections(base_df, review_df, mapping):
    """mapping: {base_column: corrected_column}. Overrides base_column
    with corrected_column's value ONLY where corrected_column is filled
    in. Blank means 'not reviewed yet', not 'clear this field'."""
    merged = base_df.merge(review_df[KEY + list(mapping.values())], on=KEY, how='left')
    for base_col, corr_col in mapping.items():
        has_correction = merged[corr_col].notna() & (merged[corr_col].astype(str).str.strip() != '')
        merged.loc[has_correction, base_col] = merged.loc[has_correction, corr_col]
    return merged.drop(columns=list(mapping.values()))


final = apply_corrections(base, nqu, {
    'regex_name': 'corrected_name', 'regex_qty': 'corrected_qty', 'regex_unit': 'corrected_unit',
})
final = apply_corrections(final, state_pri, {
    'ip_state': 'corrected_state',
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
split_row_keys = set()  # track the actual (recipe_id, line_no) keys created below
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
            'regex_qty': vals['qty'], 'regex_unit': vals['unit'], 'regex_name': vals['name'],
            'ip_state': '', 'patch_dry_fresh': vals.get('dry_fresh', ''),
            'flag_name_disagree': False, 'flag_qty_disagree': False, 'flag_unit_disagree': False,
            'flag_state_priority_review': False, 'flag_state_anomaly': False,
        })
if new_rows:
    final = pd.concat([final, pd.DataFrame(new_rows)], ignore_index=True)
    final['line_no'] = final['line_no'].astype(int)

# parse_status: 'reviewed' if a correction was actually entered or
# handled by one of the fixes above; 'needs_review' if still flagged
# and untouched; 'clean' otherwise 
reviewed_keys = set(map(tuple, nqu.loc[nqu['corrected_name'].notna(), KEY].values)) | \
                set(map(tuple, state_pri.loc[state_pri['corrected_state'].notna(), KEY].values)) | \
                inch_keys | set(BLIND_SPOT_FIXES.keys()) | split_row_keys

final['reviewed'] = final.apply(lambda r: (r['recipe_id'], r['line_no']) in reviewed_keys, axis=1)
final['parse_status'] = final.apply(
    lambda r: 'reviewed' if r['reviewed'] else ('needs_review' if
        (r.get('flag_name_disagree') or r.get('flag_qty_disagree') or r.get('flag_unit_disagree')
         or r.get('flag_state_priority_review') or r.get('flag_state_anomaly')) else 'clean'),
    axis=1
)

output_cols = ['recipe_id', 'line_no', 'raw_line', 'regex_qty', 'regex_unit', 'regex_name',
               'ip_state', 'patch_dry_fresh', 'parse_status']
final[output_cols].rename(columns={
    'regex_qty': 'qty', 'regex_unit': 'unit', 'regex_name': 'name_raw',
    'ip_state': 'state', 'patch_dry_fresh': 'dry_fresh',
}).to_csv(paths.RECIPE_INGREDIENTS_FINAL, index=False)

print('recipe_ingredients_final.csv:', len(final), 'rows')
print(final['parse_status'].value_counts())