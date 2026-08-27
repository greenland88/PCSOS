from pathlib import Path
import csv
import pandas as pd

root = Path(__file__).resolve().parents[1]
path = root / 'research_outputs/nvda_research_agent/research_log.csv'
header = ['timestamp', 'round', 'hypothesis_id', 'description', 'features',
          'sample_size', 'years_tested', 'result', 'verdict',
          'reason_rejected', 'artifact_path']
raw_rows = []
with path.open(encoding='utf-8', newline='') as fh:
    for fields in csv.reader(fh):
        if fields == header:
            continue
        if not fields:
            continue
        if len(fields) > len(header):
            # Repair the one legacy row whose reason contained an unquoted comma.
            fields = fields[:9] + [','.join(fields[9:-1]), fields[-1]]
        if len(fields) == len(header):
            raw_rows.append(dict(zip(header, fields)))
log = pd.DataFrame(raw_rows, columns=header)
rows = [
    {'timestamp': '2026-08-24T15:30:00+00:00', 'round': 13, 'hypothesis_id': 'NVDA_STOP_RECONSTRUCTION',
     'description': 'TRAIN-only reconstruction of post-stop option paths for stopped trades.',
     'features': 'post-stop P&L path; recovery; short/long strike breach', 'sample_size': 120,
     'years_tested': '2020-2023', 'result': '93_MIXED_27_PREMATURE_STOP; 0_UNRECOVERED',
     'verdict': 'STOP_PATH_MIXED', 'reason_rejected': 'Descriptive reconstruction only; stop rule unchanged',
     'artifact_path': 'research_outputs/nvda_research_agent/round13_stop_reconstruction_20260824'},
    {'timestamp': '2026-08-24T15:31:00+00:00', 'round': 14, 'hypothesis_id': 'NVDA_STOP_TYPE_ENTRY_FEATURES',
     'description': 'Descriptive comparison of entry-time features for MIXED versus PREMATURE_STOP trades.',
     'features': 'underlying; ATR; support and MA distances; ATR%; credit; width; DTE', 'sample_size': 120,
     'years_tested': '2020-2023', 'result': 'DESCRIPTIVE_ARTIFACT_CREATED', 'verdict': 'CONTINUE_RESEARCH',
     'reason_rejected': 'No production threshold or rule tested',
     'artifact_path': 'research_outputs/nvda_research_agent/round14_stop_type_features_20260824'},
]
for row in rows:
    if not ((log['round'] == row['round']).any()):
        log = pd.concat([log, pd.DataFrame([row])], ignore_index=True)
log.to_csv(path, index=False)
print(log.tail(2).to_string(index=False))
