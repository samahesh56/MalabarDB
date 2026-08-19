import pandas as pd
from ingredient_parser import parse_ingredient

df = pd.read_csv('parser_eval_sample.csv')

rows = []
for _, r in df.iterrows():
    parsed = parse_ingredient(r['raw_line'])
    amt = parsed.amount[0] if parsed.amount else None
    rows.append({
        'raw_line': r['raw_line'],
        'gold_name': r['name'], 'pred_name': '; '.join(n.text for n in parsed.name),
        'gold_qty': r['qty'], 'pred_qty': str(amt.quantity) if amt else '',
        'gold_unit': r['unit'], 'pred_unit': str(amt.unit) if amt else '',
        'gold_state': r['state'], 'pred_preparation': parsed.preparation.text if parsed.preparation else '',
        'gold_size': r['size'], 'pred_size': parsed.size.text if parsed.size else '',
        'gold_dry_fresh': r['dry_fresh'],
        'gold_notes': r['notes'],
        'pred_comment': parsed.comment.text if parsed.comment else '',
        'pred_purpose': parsed.purpose.text if parsed.purpose else '',
    })

out = pd.DataFrame(rows)
out.to_csv('parser_comparison_raw.csv', index=False)
print(out[['raw_line','gold_name','pred_name','gold_qty','pred_qty','gold_unit','pred_unit']].to_string())