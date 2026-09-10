"""
Step 2: verify that every ingredient line parsed correctly.

Checking 1739 lines by hand is not viable, so two independent extractors parse 
every line and their DISAGREEMENT selects the rows a human looks at:
    regex             qty / unit / name : the label source
    ingredient-parser qty / unit / name : second opinion, used only to disagree
    ingredient-parser state             : sole source
    keyword regex     dry_fresh         : sole source

Where the two agree on a field, that field is accepted. Where they disagree,
the row enters the review queue. State disagreement is checked by observing if 
a line has a " - X" modifier where state info would live but was undetected 

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
 
# Unit vocabulary derived by scanning the corpus for the word following a
# leading quantity, not from a generic English unit list. 
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
 
# Alternation returns the FIRST match, not the longest, so every compound form
# must be tried before the bare integer that is its prefix: "2-1/2" before the
# range "2-3" (else it slices as "2-1"), "3 1/2" and "1.5" before "\d+" (else
# they slice as "3" and "1", stranding the rest of the number in the NAME).
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
# ip_comment/ip_purpose exist to explain a state miss
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
    """Restore terms ingredient-parser drops from slash-separated
    parentheticals: "Elephant yam (Suran/Senai/Ratalu)" becomes "Elephant yam
    Ratalu", with the rest pushed into comment. """

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
 
 
def write_review_queue(queue, path):
    """Write the queue, preserving corrections already typed into an earlier
    version. A row still flagged keeps its correction; newly flagged rows
    arrive blank. Blank always means 'not reviewed', never 'clear this field'."""
    if os.path.exists(path):
        prior = pd.read_csv(path)[KEY_COLUMNS + CORRECTION_COLUMNS]
        queue = queue.drop(columns=CORRECTION_COLUMNS, errors='ignore').merge(
            prior, on=KEY_COLUMNS, how='left')
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
 
    queue = ri[ri['review_flags'] != ''].copy()
    for col in CORRECTION_COLUMNS:
        queue[col] = ''
    queue = write_review_queue(queue, paths.REVIEW_QUEUE_PARSING)
 
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