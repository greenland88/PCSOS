from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'research_outputs' / 'nvda_research_agent' / 'round14_stop_type_features_20260824'
OUT.mkdir(parents=True, exist_ok=True)

features = pd.read_parquet(ROOT / 'research_outputs/nvda_research_agent/round11_train_diagnostic_20260824/train_outcomes_with_entry_features.parquet')
recon = pd.read_parquet(ROOT / 'research_outputs/nvda_research_agent/round13_stop_reconstruction_20260824/stopped_trade_reconstruction.parquet')
stops = recon[['candidate_id', 'classification', 'post_stop_min_pnl', 'short_breach', 'long_breach']]
d = features.merge(stops, on='candidate_id', how='inner')
d = d[d['classification'].isin(['MIXED', 'PREMATURE_STOP'])].copy()

cols = {
    'underlying': 'underlying',
    'atr': 'atr',
    'support_distance_atr': 'nearest_support_distance_atr',
    'ma20_distance_atr': 'pullback.distance_to_sma20_atr',
    'ma50_distance_atr': 'pullback.distance_to_sma50_atr',
    'atr_pct': 'atr_pct',
    'credit': 'credit',
    'credit_width': 'credit_width',
    'dte': 'dte',
}
rows = []
for year, g in d.groupby('year', sort=True):
    for label, col in cols.items():
        if col not in g:
            continue
        for cls, x in g.groupby('classification'):
            v = pd.to_numeric(x[col], errors='coerce').dropna()
            if len(v):
                rows.append({'year': int(year), 'classification': cls, 'feature': label,
                             'n': len(v), 'mean': v.mean(), 'median': v.median()})
summary = pd.DataFrame(rows)
summary.to_csv(OUT / 'stop_type_feature_summary.csv', index=False)

counts = d.groupby(['year', 'classification']).size().rename('n').reset_index()
counts.to_csv(OUT / 'stop_type_counts.csv', index=False)

report = '''# NVDA Round 14 — TRAIN-only stop-type entry features

This is descriptive only. It compares entry-time features for stopped trades
classified by the post-stop path reconstruction. No production rule or threshold
was changed, and no validation or final OOS data was read.

Definitions:
- PREMATURE_STOP: recovered after the stop without a deeper post-stop adverse mark.
- MIXED: recovered after the stop but first reached a worse P&L after the stop.

The detailed machine-readable outputs are `stop_type_feature_summary.csv` and
`stop_type_counts.csv`. Differences are exploratory and must not be promoted
without chronological validation.
'''
(OUT / 'round14_report.md').write_text(report, encoding='utf-8')
(OUT / 'round14_manifest.json').write_text(json.dumps({
    'round': 14, 'train_only': '2020-01-02..2023-12-31',
    'source_rounds': [11, 13], 'rows': int(len(d)),
    'validation_read': False, 'final_oos_read': False,
    'production_changed': False, 'thresholds_changed': False,
}, indent=2), encoding='utf-8')
print(counts.to_string(index=False))
