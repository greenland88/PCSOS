from pathlib import Path
import json
import pandas as pd

ROOT = Path('research_outputs/qqq_entry_discovery_agent_v1')
ART = ROOT / 'artifacts'

def metrics(x):
    pnl = x.realized_pnl
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    return {'episodes': len(x), 'trades': len(x), 'total_pnl': float(pnl.sum()),
            'expectancy': float(pnl.mean()),
            'profit_factor': float(wins.sum()/abs(losses.sum())) if len(losses) else None,
            'win_rate': float((pnl > 0).mean()),
            'stop_rate': float(x.stopped.astype(bool).mean()),
            'avg_win': float(wins.mean()) if len(wins) else None,
            'avg_loss': float(losses.mean()) if len(losses) else None,
            'worst_trade': float(pnl.min())}

def main():
    d = pd.read_parquet(ART/'qqq_pit_feature_outcome_table_train_2020_2023.parquet').sort_values('trade_date').copy()
    d.trade_date = pd.to_datetime(d.trade_date)
    d['gap'] = d.trade_date.diff().dt.days.fillna(99)
    d['episode_id'] = (d.gap > 4).cumsum()
    first = d.groupby('episode_id', as_index=False).first()
    out = {'module':'pcs.research.episode_quality_qqq_v1','status':'COMPLETED',
           'data_source':'PCS_CANONICAL_DATA',
           'broad_executable_population':metrics(d),
           'one_entry_per_independent_episode':metrics(first),
           'episode_definition':'selected executable dates separated by more than four calendar days',
           'final_oos_read':False,'validation_read':False,'production_changes':False}
    out['year_metrics'] = {str(y):metrics(g) for y,g in d.assign(year=d.trade_date.dt.year).groupby('year')}
    out['one_entry_year_metrics'] = {str(y):metrics(g) for y,g in first.assign(year=first.trade_date.dt.year).groupby('year')}
    (ART/'episode_quality_summary.json').write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))

if __name__ == '__main__': main()
