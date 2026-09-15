"""
Step 2: verify that every ingredient line parsed correctly.

Checking 1739 lines by hand is not viable, so two independent extractors parse 
every line and their disagreement selects the rows a human looks at:
    regex             qty / unit / name : the label source
    ingredient-parser qty / unit / name : second opinion, used only to disagree
    ingredient-parser state             : sole source
    keyword regex     dry_fresh         : sole source

State disagreement is checked by observing if  a line has a " - X" modifier where state info would live but was undetected 

OUTPUTS
    full_corpus_labels.csv    every line, every extracted field, every flag. Do not hand-edit.
    review_queue_parsing.csv  only the flagged lines, with blank corrected_* columns. Edit this file. 

merge_corrections.py folds the queue back over the full table in the next step, after edits are complete.
"""

import os
import re
 
import pandas as pd
from ingredient_parser import parse_ingredient
 
from malabardb import paths
 
# Unit vocabulary derived by scanning the corpus for the word following a leading quantity
UNITS = {
    'teaspoon', 'teaspoons', 'tsp', 'tsps', 'tablespoon', 'tablespoons', 'tbsp', 'tbsps',
    'cup', 'cups', 'gram', 'grams', 'gm', 'gms', 'g', 'kg', 'ml', 'liter', 'litre',
    'sprig', 'sprigs', 'inch', 'inches', 'pinch', 'pinches', 'clove', 'cloves',
    'pod', 'pods', 'slice', 'slices', 'piece', 'pieces', 'handful', 'handfuls',
}
 
# Without this, "tsp" vs "teaspoon" registers as a disagreement.
UNIT_ALIASES = {
    'tbsp': 'tablespoon', 'tbsps': 'tablespoon', 'tablespoons': 'tablespoon',
    'tsp': 'teaspoon', 'tsps': 'teaspoon', 'teaspoons': 'teaspoon',
    'cups': 'cup', 'grams': 'gram', 'gm': 'gram', 'gms': 'gram', 'g': 'gram',
    'sprigs': 'sprig', 'cloves': 'clove', 'pods': 'pod', 'slices': 'slice',
    'pieces': 'piece', 'handfuls': 'handful', 'kg': 'kilogram', 'ml': 'milliliter',
}
 
# Alternation returns the FIRST match, not the longest, so every compound form must be tried 
# before the bare integer that is its prefix: "2-1/2" before the range "2-3" (else it slices as "2-1")
QTY_RE = re.compile(
    r'^\s*('
    r'\d+\s+to\s+\d+'             # 2 to 3
    r'|\d+\s*-\s*\d+\s*/\s*\d+'   # 1-1/2
    r'|\d+\s+\d+\s*/\s*\d+'       # 3 1/2  
    r'|\d+\s*-\s*\d+'             # 2-3
    r'|\d+\s*/\s*\d+'             # 1/2
    r'|\d+\.\d+'                   # 1.5
    r'|\d+'                         # 3
    r')\s*'
)

_SPACED_MIXED = re.compile(r'\d+\s+\d+\s*/\s*\d+')


def normalize_qty(qty):
    """Canonical written form of a matched quantity."""
    
    qty = str(qty).strip()
    if _SPACED_MIXED.fullmatch(qty):
        whole, frac = qty.split(None, 1)
        qty = f"{whole}-{frac}"
    return re.sub(r'\s+', '', qty)
 
DRY_FRESH_RE = re.compile(r'\b(dry|dried|fresh|frozen)\b', re.IGNORECASE)
 
# CONTEXT columns (read-only, shown so a human can decide) come before
# CORRECTION columns (typed in, and the only ones merge_corrections.py reads back).
QUEUE_COLUMNS = [
    'recipe_id', 'line_no', 'raw_line', 'review_flags',
    'regex_name', 'ip_patched_name',
    'regex_qty', 'ip_qty',
    'regex_unit', 'ip_unit',
    'modifier_text', 'ip_state', 'ip_comment', 'ip_purpose',
    'corrected_name', 'corrected_qty', 'corrected_unit', 'corrected_state',
]
 
CORRECTION_COLUMNS = ['corrected_name', 'corrected_qty', 'corrected_unit',
                      'corrected_state']
 
KEY_COLUMNS = ['recipe_id', 'line_no']
 
 
def regex_extract(raw_line):
    """Split a line into qty / unit / name using the quantity pattern and unit
    dictionary. Name is everything after qty and unit, up to the first ' - '."""
    s = str(raw_line)
    qty, rest = '', s
    m = QTY_RE.match(s)
    if m:
        qty = normalize_qty(m.group(1))
        rest = s[m.end():]
 
    unit = ''
    rest = rest.strip()
    words = rest.split(' ', 1)
    if words and words[0].strip('(),.').lower() in UNITS:
        unit = words[0].strip('(),.')
        rest = words[1] if len(words) > 1 else ''
 
    return pd.Series({'regex_qty': qty, 'regex_unit': unit,
                      'regex_name': rest.split(' - ')[0].strip()})
 
 
def run_ip(raw_line):
    """Parse a line with ingredient-parser. qty/unit/name are the second
    opinion only. State is this method's sole contribution. 
    comment/purpose are kept as review context only """
    r = parse_ingredient(str(raw_line))
    amt = r.amount[0] if r.amount else None
    return pd.Series({
        'ip_name': '; '.join(n.text for n in r.name),
        'ip_qty': str(amt.quantity) if amt else '',
        'ip_unit': str(amt.unit) if amt else '',
        'ip_state': r.preparation.text if r.preparation else '',
        'ip_comment': r.comment.text if r.comment else '',
        'ip_purpose': r.purpose.text if r.purpose else '',
    })
 
 
def dry_fresh_patch(raw_line):
    """Extract dry/fresh state. ingredient-parser has no field for this; the
    vocabulary is small and closed enough for a keyword match."""
    
    m = DRY_FRESH_RE.search(str(raw_line))
    return m.group(1).capitalize() if m else ''
 
 
def _tokens(s):
    return set(re.findall(r'[a-z]+', str(s).lower()))
 
 
def name_patch(regex_name, ip_name):
    """Restore terms ingredient-parser drops from slash-separated parentheticals: 
    "Elephant yam (Suran/Senai/Ratalu)" becomes "Elephant yam Ratalu", with the rest pushed into comment. """

    t_ip, t_regex = _tokens(ip_name), _tokens(regex_name)
    return regex_name if t_ip and t_ip < t_regex else ip_name
 
 
def get_modifier_text(raw_line):
    """Return the ' - X' clause, where state information usually sits."""

    parts = str(raw_line).split(' - ', 1)
    return parts[1].strip() if len(parts) > 1 else ''
 
 
def _norm(x):
    return '' if pd.isna(x) else str(x).strip().lower()
 
 
def _norm_unit(x):
    return UNIT_ALIASES.get(_norm(x), _norm(x))
 
 
def has_correction(df):
    """True for rows where a human typed at least one corrected_* value."""
    return (df[CORRECTION_COLUMNS].fillna('').astype(str)
            .apply(lambda col: col.str.strip().ne('')).any(axis=1))


def find_moved_lines(review_rows, corpus):
    """Rows whose (recipe_id, line_no) no longer holds the same raw_line in the
    corpus, including lines that no longer exist. Corrections are matched by
    position, so this confirms each position still means the same line."""
    m = review_rows[KEY_COLUMNS + ['raw_line']].merge(
        corpus[KEY_COLUMNS + ['raw_line']], on=KEY_COLUMNS, how='left',
        suffixes=('_review', '_corpus'))
    return m[m['raw_line_review'] != m['raw_line_corpus']]


def write_review_queue(ri, path):
    """Write the queue: every flagged line, plus every line that already holds a
    correction, flagged or not. A correction is never dropped: if its line is no
    longer flagged it stays (review_flags blank), and if its line changed or
    disappeared the run stops before the file is touched.
    Blank always means 'not reviewed', never 'clear this field'."""
    keep = ri['review_flags'] != ''
    prior = None
    if os.path.exists(path):
        prior = pd.read_csv(path)
        done = prior[has_correction(prior)]

        moved = find_moved_lines(done, ri)
        if len(moved):
            raise SystemExit(
                f'{len(moved)} corrected rows no longer match the source line '
                f'(queue left unchanged):\n{moved.to_string(index=False)}')

        was_corrected = ri.set_index(KEY_COLUMNS).index.isin(
            done.set_index(KEY_COLUMNS).index)
        n_unflagged = int((was_corrected & ~keep).sum())
        if n_unflagged:
            print(f'kept {n_unflagged} corrected rows that are no longer flagged')
        keep = keep | was_corrected

    queue = ri[keep].copy()
    if prior is not None:
        queue = queue.merge(prior[KEY_COLUMNS + CORRECTION_COLUMNS],
                            on=KEY_COLUMNS, how='left')
    for col in CORRECTION_COLUMNS:
        if col not in queue.columns:
            queue[col] = ''
    queue[QUEUE_COLUMNS].to_csv(path, index=False)
    return queue
 
 
def build():
    ri = pd.read_csv(paths.RECIPE_INGREDIENTS)
 
    ri = pd.concat([ri, ri.raw_line.apply(regex_extract)], axis=1)
    ri = pd.concat([ri, ri.raw_line.apply(run_ip)], axis=1)
    ri['patch_dry_fresh'] = ri.raw_line.apply(dry_fresh_patch)
    ri['ip_patched_name'] = ri.apply(
        lambda r: name_patch(r.regex_name, r.ip_name), axis=1)
    ri['modifier_text'] = ri.raw_line.apply(get_modifier_text)
 
    # One flag per reviewable field. Name/qty/unit compare two methods; state
    # has only one, so its flag is structural (clause present, nothing parsed).
    ri['flag_name'] = ri.apply(
        lambda r: _norm(r.regex_name) != _norm(r.ip_patched_name)
              or bool(re.search(r'\d', str(r.regex_name))), axis=1)
    ri['flag_qty'] = ri.apply(
        lambda r: _norm(r.regex_qty) != _norm(r.ip_qty), axis=1)
    ri['flag_unit'] = ri.apply(
        lambda r: _norm_unit(r.regex_unit) != _norm_unit(r.ip_unit), axis=1)
    ri['flag_state'] = (ri.modifier_text != '') & (ri.ip_state == '')
    
 
    flag_cols = ['flag_name', 'flag_qty', 'flag_unit', 'flag_state']
 
    # Human-readable reason column: "name;unit" beats four boolean columns
    # when sorting a spreadsheet by what actually needs attention.
    ri['review_flags'] = ri[flag_cols].apply(
        lambda r: ';'.join(c.removeprefix('flag_') for c in flag_cols if r[c]),
        axis=1)
 
    ri.to_csv(paths.FULL_CORPUS_LABELS, index=False)

    # write the queue 
    queue = write_review_queue(ri, paths.REVIEW_QUEUE_PARSING) 
 
    print(f'full_corpus_labels.csv   {len(ri)} lines')
    print(f'review_queue_parsing.csv {len(queue)} lines '
          f'({len(queue) / len(ri) * 100:.1f}% of corpus)\n')
    print(f'{"field":<8}{"flagged":>9}{"% lines":>9}')
    for col in flag_cols:
        n = int(ri[col].sum())
        print(f'{col.removeprefix("flag_"):<8}{n:>9}{n / len(ri) * 100:>8.1f}%')
 
    if os.path.exists(paths.REVIEW_QUEUE_PARSING):
        # fillna BEFORE astype(str): pandas 3 leaves NaN as NaN rather than
        # rendering it 'nan', so without this every blank cell counts as filled.
        done = queue[CORRECTION_COLUMNS].fillna('').astype(str).apply(
            lambda r: r.str.strip().ne('').any(), axis=1).sum()
        print(f'\ncarried forward: {done} of {len(queue)} rows already corrected')
 
    return ri
 
 
if __name__ == '__main__':
    build()