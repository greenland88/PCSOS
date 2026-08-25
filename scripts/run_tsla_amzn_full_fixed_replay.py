"""Compare frozen TSLA/AMZN candidates against old canonical and complete v2."""
from pathlib import Path
import json
import duckdb
import pandas as pd
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch, summarize_replay

ART = Path('data/parquet/research/variant_b_full')
OUT = Path('data/parquet/research/vendor_txt_full_replay_20260820')
POLICY = ReplayPolicy()
FIELDS = ['date','ticker','expiration','short_strike','long_strike','dte','atr','atr_distance','credit','spread_width','credit_width_ratio','planned_loss','theoretical_max_loss','short_delta','trend_state','pullback_state','support_state','population','subgroup','baseline_pullback','variant_pullback','earnings_date','days_to_earnings','expected_management_window']

def candidates(symbol):
    frame = pd.read_parquet(ART / f'{symbol}_full_post2020_2d.parquet')
    return frame[[c for c in FIELDS if c in frame.columns]].copy()

def quote_index(symbol, cands, root):
    con = duckdb.connect()
    start = pd.to_datetime(cands.date).min().date()
    end = (pd.to_datetime(cands.date).max() + pd.Timedelta(days=POLICY.max_quote_days)).date()
    exps = sorted(pd.to_datetime(cands.expiration).dt.date.unique())
    strikes = sorted(set(cands.short_strike.astype(float)) | set(cands.long_strike.astype(float)))
    exp_sql = ','.join("DATE '" + str(x) + "'" for x in exps)
    strike_sql = ','.join(str(float(x)) for x in strikes)
    glob = str(root / f'symbol={symbol}' / '**' / '*.parquet').replace('\\', '/')
    sql = (f'''SELECT trade_date AS "Trade Date", expiration_date AS "Expiry Date", strike AS "Strike", bid AS "Bid Price", ask AS "Ask Price", open_interest AS "Open Interest", volume AS "Volume", delta AS "Delta" FROM read_parquet('{glob}') WHERE trade_date BETWEEN DATE '{start}' AND DATE '{end}' AND expiration_date IN ({exp_sql}) AND strike IN ({strike_sql}) AND lower(call_put)='p' ORDER BY trade_date''')
    frame = con.execute(sql).fetchdf(); con.close()
    frame['Trade Date'] = pd.to_datetime(frame['Trade Date']); frame['Expiry Date'] = pd.to_datetime(frame['Expiry Date'])
    return {(e, s): g.sort_values('Trade Date') for (e, s), g in frame.groupby(['Expiry Date', 'Strike'], sort=False)}, len(frame)

def run(cands, index, source):
    rows = []
    for _, candidate in cands.iterrows():
        row = candidate.to_dict(); day = pd.Timestamp(row['date']).normalize(); exp = pd.Timestamp(row['expiration']).normalize()
        short = index.get((exp, float(row['short_strike']))); long = index.get((exp, float(row['long_strike'])))
        sm = short[short['Trade Date'].eq(day)] if short is not None else pd.DataFrame()
        lm = long[long['Trade Date'].eq(day)] if long is not None else pd.DataFrame()
        if len(sm) != 1 or len(lm) != 1:
            row.update(status='UNAVAILABLE', exit_reason='ENTRY_QUOTES_MISSING', entry_available=False)
        else:
            sr, lr = sm.iloc[0], lm.iloc[0]
            row.update(credit=float(sr['Bid Price'] - lr['Ask Price']), entry_available=True, short_bid=float(sr['Bid Price']), short_ask=float(sr['Ask Price']), long_bid=float(lr['Bid Price']), long_ask=float(lr['Ask Price']))
            row.update(_replay_lifecycle_batch(row, index, POLICY))
        row['source'] = source; rows.append(row)
    return pd.DataFrame(rows)

def differences(old, new):
    key = ['date','short_strike','long_strike','expiration']; merged = old.merge(new, on=key, suffixes=('_old','_v2'), how='outer', indicator=True); both = merged[merged._merge.eq('both')]
    out = {'candidate_identity_differences': int((merged._merge != 'both').sum())}
    for field in ['entry_available','credit','mark_count','status','exit_date','exit_reason','mae','mfe','realized_pnl','premium_capture']:
        left, right = both[f'{field}_old'], both[f'{field}_v2']
        if field == 'exit_date': left, right = pd.to_datetime(left, errors='coerce').dt.date.astype(str), pd.to_datetime(right, errors='coerce').dt.date.astype(str)
        elif field in {'credit','mae','mfe','realized_pnl','premium_capture'}:
            out[field] = int((pd.to_numeric(left, errors='coerce').fillna(-999999).sub(pd.to_numeric(right, errors='coerce').fillna(-999999)).abs() > 1e-8).sum()); continue
        out[field] = int((left.fillna('__NA__').astype(str) != right.fillna('__NA__').astype(str)).sum())
    out['trade_impacting_differences'] = sum(out[k] for k in ['candidate_identity_differences','entry_available','credit','status','exit_date','exit_reason','realized_pnl'])
    return out

def main():
    OUT.mkdir(parents=True, exist_ok=True); results = []
    for symbol in ('TSLA', 'AMZN'):
        cands = candidates(symbol); old_idx, old_rows = quote_index(symbol, cands, Path('data/parquet/options')); v2_idx, v2_rows = quote_index(symbol, cands, Path('data/parquet/options_v2'))
        old, v2 = run(cands, old_idx, 'old_canonical'), run(cands, v2_idx, 'options_v2'); diff = differences(old, v2)
        old.to_parquet(OUT / f'{symbol}_old_full_replay.parquet', index=False); v2.to_parquet(OUT / f'{symbol}_v2_full_replay.parquet', index=False)
        results.append({'ticker': symbol, 'fixed_candidates': len(cands), 'old_quote_rows': old_rows, 'v2_quote_rows': v2_rows, 'old_summary': summarize_replay(old).to_dict('records'), 'v2_summary': summarize_replay(v2).to_dict('records'), 'differences': diff, 'replay_pass': diff['trade_impacting_differences'] == 0})
    Path('data/manifests/tsla_amzn_full_fixed_replay_20260820.json').write_text(json.dumps(results, indent=2, default=str), encoding='utf-8'); print(json.dumps(results, indent=2, default=str))

if __name__ == '__main__': main()
