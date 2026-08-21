"""
MalabarDB Phase 1: Full corpus tagging pipeline (regex + ingredient-parser)

Combines two extraction methods (RegEx/Ingredient-parser lib) and uses their
agreement/disagreement as a signal for which rows need manual review

Design summary:
- name/qty/unit: regex is the label source. ingredient-parser's output for these same
  three fields is kept ONLY to generate a disagreement flag 
- state/size/notes: ingredient-parser is the only method available.
  regex was deliberately not extended to these field: their
  vocabulary is too open-ended (chopped/sliced/ground/ripe/...)
- dry_fresh: ingredient-parser has NO output field for this at all.
  A small closed-vocabulary keyword regex covers it independently.

Outputs two kinds of file, not terminal printing:
1. full_corpus_labels.csv: one row per ingredient line, every
   extracted field + every review flag, kept as the working reference.
2. review_queue_*.csv":  filtered subsets of the above, one per
   open review question, with blank columns added for corrections.
   Kept separate so review work isn't lost inside ~1700 mostly-clean
   rows, and so progress can be saved/reopened across sessions"""

import os
import re
import pandas as pd
from ingredient_parser import parse_ingredient
from malabardb import paths

'''1. REGEX EXTRACTOR (qty / unit / name)
Unit vocabulary built by scanning THIS corpus for the word following
a leading quantity -- not a generic English unit list. Words like
"dry"/"green"/"raw"/"small" appear frequently in that position too, but
are adjectives, not units -- confirmed by reading the matches before adding anything here.'''
UNITS = {
    'teaspoon', 'teaspoons', 'tsp', 'tsps', 'tablespoon', 'tablespoons', 'tbsp', 'tbsps',
    'cup', 'cups', 'gram', 'grams', 'gm', 'gms', 'g', 'kg', 'ml', 'liter', 'litre',
    'sprig', 'sprigs', 'inch', 'inches', 'pinch', 'pinches', 'clove', 'cloves',
    'pod', 'pods', 'slice', 'slices', 'piece', 'pieces', 'handful', 'handfuls',
}

# alias map: without this, "tsp"  vs "teaspoon" reads as a disagreement even though it's the same unit
UNIT_ALIASES = {
    'tbsp': 'tablespoon', 'tbsps': 'tablespoon', 'tablespoons': 'tablespoon',
    'tsp': 'teaspoon', 'tsps': 'teaspoon', 'teaspoons': 'teaspoon',
    'cups': 'cup', 'grams': 'gram', 'gm': 'gram', 'gms': 'gram', 'g': 'gram',
    'sprigs': 'sprig', 'cloves': 'clove', 'pods': 'pod', 'slices': 'slice',
    'pieces': 'piece', 'handfuls': 'handful', 'kg': 'kilogram', 'ml': 'milliliter',
}

# Ordered most-specific-first: regex alternation takes the first
# alternative that matches, not the longest, so "2-1/2" (mixed
# fraction) must be tried before "2-3"-style range or it gets mis-sliced
QTY_RE = re.compile(
    r'^\s*('
    r'\d+\s+to\s+\d+'            # "2 to 3"
    r'|\d+\s*-\s*\d+\s*/\s*\d+'  # "2-1/2" / "2 - 1 / 2"
    r'|\d+\s*-\s*\d+'            # "2-3" / "2 - 3"
    r'|\d+\s*/\s*\d+'            # "1/2"
    r'|\d+'                      # "2"
    r')\s*'
)


def regex_extract(raw_line):
    """qty/unit/name via the ordered quantity pattern + unit dictionary.
    name = everything after qty/unit up to the first ' - '"""
    s = str(raw_line)
    qty, rest = '', s
    m = QTY_RE.match(s)
    if m:
        qty = re.sub(r'\s+', '', m.group(1))
        rest = s[m.end():]
    unit = ''
    rest_stripped = rest.strip()
    words = rest_stripped.split(' ', 1)
    if words and words[0].strip('(),.').lower() in UNITS:
        unit = words[0].strip('(),.')
        rest_stripped = words[1] if len(words) > 1 else ''
    name = rest_stripped.split(' - ')[0].strip()
    return pd.Series({'regex_qty': qty, 'regex_unit': unit, 'regex_name': name})


# 2. INGREDIENT-PARSER (name / qty / unit / state / notes)
def run_ip(raw_line):
    """name/qty/unit here exist ONLY for the disagreement check below --
    state/comment/purpose ARE the final source for those fields."""
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


'''3. dry/fresh keyword regex
ingredient-parser has NO output field for dry/raw/frozen...
Small closed vocabulary (4 words, confirmed by scanning the corpus)
confirms dry/fried/fresh/frozen can be extraced using regex'''
DRY_FRESH_RE = re.compile(r'\b(dry|dried|fresh|frozen)\b', re.IGNORECASE)

def dry_fresh_patch(raw_line):
    m = DRY_FRESH_RE.search(str(raw_line))
    return m.group(1).capitalize() if m else ''


'''4. name reattachment
ingredient-parser sometimes truncates multi-term slash-separated
parentheticals, e.g. "Elephant yam (Suran/Senai/Ratalu)" becomes
"Elephant yam Ratalu", dumping "Suran/Senai" into `comment`.
Fix: only override ip_name when its tokens are a STRICT subset of
regex_name's tokens (something is missing, not just phrased differently)'''
def _tokens(s):
    return set(re.findall(r'[a-z]+', str(s).lower()))

def name_patch(regex_name, ip_name):
    t_ip, t_regex = _tokens(ip_name), _tokens(regex_name)
    if t_ip and t_ip < t_regex:
        return regex_name
    return ip_name

'''5. STATE MODIFIER-CLAUSE CHECK
No independent second method exists for `state`, so there's no
disagreement signal the way there is for name/qty/unit. Cheapest
available proxy: does the raw text even have a " - X" clause where
state info could live? 

KNOWN LIMITATION: only catches modifiers written AFTER a dash.
Pre-noun adjectival states ("Cooked rice") are invisible to this.'''
def has_modifier_clause(raw_line):
    parts = str(raw_line).split(' - ', 1)
    return len(parts) > 1 and parts[1].strip() != ''

def get_modifier_text(raw_line):
    parts = str(raw_line).split(' - ', 1)
    return parts[1].strip() if len(parts) > 1 else ''


# helper: write a review queue without destroying prior corrections ---
def write_review_queue(new_queue_df, path, key_cols, correction_cols):
    """Write a review queue CSV without destroying prior corrections.

    If a file already exists at `path`, any row that's STILL flagged
    keeps whatever correction was already typed in for it -- only rows
    that are newly flagged (weren't in the old file) get blank
    corrections. 
    """
    if os.path.exists(path):
        existing = pd.read_csv(path)
        existing_corrections = existing[key_cols + correction_cols]
        new_queue_df = new_queue_df.drop(columns=correction_cols, errors='ignore').merge(
            existing_corrections, on=key_cols, how='left'
        )
        for col in correction_cols:
            if col not in new_queue_df.columns:
                new_queue_df[col] = ''
    new_queue_df.to_csv(path, index=False)


# 6. BUILD THE FULL CORPUS TABLE
ri = pd.read_csv(paths.RECIPE_INGREDIENTS)

ri = pd.concat([ri, ri['raw_line'].apply(regex_extract)], axis=1)
ri = pd.concat([ri, ri['raw_line'].apply(run_ip)], axis=1)
ri['patch_dry_fresh'] = ri['raw_line'].apply(dry_fresh_patch)
ri['ip_patched_name'] = ri.apply(lambda r: name_patch(r['regex_name'], r['ip_name']), axis=1)
ri['has_modifier'] = ri['raw_line'].apply(has_modifier_clause)
ri['modifier_text'] = ri['raw_line'].apply(get_modifier_text)

def _norm(x):
    return '' if pd.isna(x) else str(x).strip().lower()

def _norm_unit(x):
    s = _norm(x)
    return UNIT_ALIASES.get(s, s)


# Disagreement flags for name/qty/unit: regex is the label source;
# ip's version of these three fields exists only to produce this signal.
# unit uses _norm_unit (alias-aware); name/qty use plain _norm since
# they don't have the same "same thing, different spelling" problem.
ri['flag_name_disagree'] = ri.apply(lambda r: _norm(r['regex_name']) != _norm(r['ip_patched_name']), axis=1)
ri['flag_qty_disagree'] = ri.apply(lambda r: _norm(r['regex_qty']) != _norm(r['ip_qty']), axis=1)
ri['flag_unit_disagree'] = ri.apply(lambda r: _norm_unit(r['regex_unit']) != _norm_unit(r['ip_unit']), axis=1)

# State review priority: modifier clause exists but ip found nothing is used as a basic review check 
ri['flag_state_priority_review'] = ri['has_modifier'] & (ri['ip_state'] == '')
ri['flag_state_anomaly'] = (~ri['has_modifier']) & (ri['ip_state'] != '')

ri.to_csv(paths.FULL_CORPUS_LABELS, index=False)

# 7. DERIVE REVIEW QUEUES: separate CSVs, not terminal output, routed
# through write_review_queue() so a rerun doesn't wipe out prior review work.
name_qty_unit_queue = ri[
    ri['flag_name_disagree'] | ri['flag_qty_disagree'] | ri['flag_unit_disagree']
].copy()
name_qty_unit_queue['corrected_name'] = ''
name_qty_unit_queue['corrected_qty'] = ''
name_qty_unit_queue['corrected_unit'] = ''
write_review_queue(
    name_qty_unit_queue[[
        'recipe_id', 'line_no', 'raw_line', 'regex_name', 'ip_patched_name',
        'regex_qty', 'ip_qty', 'regex_unit', 'ip_unit',
        'flag_name_disagree', 'flag_qty_disagree', 'flag_unit_disagree',
        'corrected_name', 'corrected_qty', 'corrected_unit',
    ]],
    paths.REVIEW_QUEUE_NAME_QTY_UNIT,
    key_cols=['recipe_id', 'line_no'],
    correction_cols=['corrected_name', 'corrected_qty', 'corrected_unit'],
)

state_priority_queue = ri[ri['flag_state_priority_review']].copy()
state_priority_queue['corrected_state'] = ''
state_priority_queue['corrected_notes'] = ''
state_priority_queue['note_to_self'] = ''  # e.g. "real miss" vs "correctly belongs in notes"
write_review_queue(
    state_priority_queue[[
        'recipe_id', 'line_no', 'raw_line', 'modifier_text', 'ip_state', 'ip_comment', 'ip_purpose',
        'corrected_state', 'corrected_notes', 'note_to_self',
    ]],
    paths.REVIEW_QUEUE_STATE_PRIORITY,
    key_cols=['recipe_id', 'line_no'],
    correction_cols=['corrected_state', 'corrected_notes', 'note_to_self'],
)

# state_anomaly_queue has no correction columns, just 3 rows for a quick look
state_anomaly_queue = ri[ri['flag_state_anomaly']].copy()
state_anomaly_queue[['recipe_id', 'line_no', 'raw_line', 'ip_state']].to_csv(
    paths.REVIEW_QUEUE_STATE_ANOMALY, index=False)

print('full_corpus_labels.csv:', len(ri), 'rows')
print('review_queue_name_qty_unit.csv:', len(name_qty_unit_queue), 'rows')
print('review_queue_state_priority.csv:', len(state_priority_queue), 'rows')
print('review_queue_state_anomaly.csv:', len(state_anomaly_queue), 'rows')