"""Calendar-year audit of the sealed baseline opportunity-episode analysis.

Descriptive only: reads the existing baseline entry/outcome/lifecycle artifacts
and daily date partitions for coverage. It does not replay or alter strategy.
"""
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

BASE = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
EP = Path("research_outputs/opportunity_episode_analysis_20260821")
OUT = EP
ACCESS = PCSDataAccess()

WINDOWS = {"TRAIN": (pd.Timestamp("2020-01-01"), pd.Timestamp("2025-12-31")),
           "VALIDATION": (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-05-31"))}

def baseline(ticker, split):
    c = pd.read_parquet(BASE / f"{ticker}_entry_contract_v2.parquet")
    o = pd.read_parquet(BASE / f"{ticker}_train_validation_outcomes.parquet")
    l = pd.read_parquet(BASE / f"{ticker}_lifecycle_marks.parquet")
    c["entry"] = pd.to_datetime(c.decision_date).dt.normalize()
    l["mark_date"] = pd.to_datetime(l.mark_date).dt.normalize()
    exits = (l[l.exit.fillna(False)].sort_values("mark_date")
             .drop_duplicates("candidate_id")[["candidate_id", "mark_date"]]
             .rename(columns={"mark_date": "exit"}))
    x = c.merge(o[["candidate_id", "pnl"]], on="candidate_id")
    x = x.merge(exits, on="candidate_id", how="left")
    lo, hi = WINDOWS[split]
    return x[x.entry.between(lo, hi)].sort_values(["entry", "candidate_id"]).reset_index(drop=True)

def cycle_rows(x):
    rows, current, end = [], [], None
    for _, r in x.iterrows():
        if current and r.entry > end:
            rows.append(current); current = []
        current.append(r)
        exits = [q.exit for q in current if pd.notna(q.exit)]
        end = max(exits, default=r.entry)
    if current: rows.append(current)
    return [(pd.DataFrame(g).entry.min(), max([q.exit for q in g if pd.notna(q.exit)], default=g[-1].entry)) for g in rows]

def episode_rows(x, gap, sessions):
    if x.empty: return pd.DataFrame(columns=["episode_start", "episode_end", "number_of_entries"])
    idx = {d: i for i, d in enumerate(sessions)}
    groups, current, start = [], [], None
    for _, r in x.iterrows():
        i = idx[r.entry]
        if current and i - start >= gap:
            groups.append(current); current = []
        if not current: start = i
        current.append(r)
    if current: groups.append(current)
    out = []
    for g in groups:
        z = pd.DataFrame(g)
        out.append({"episode_start": z.entry.min(), "episode_end": z.exit.max() if z.exit.notna().any() else z.entry.max(), "number_of_entries": len(z)})
    return pd.DataFrame(out)

def available_dates(ticker, lo, hi):
    daily = ACCESS.read_prices(ticker, start_date=lo, end_date=hi)
    if daily.empty:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(pd.to_datetime(daily.date).dt.normalize().drop_duplicates().sort_values())

def main():
    detail, summary = [], []
    for ticker in ("SPY", "QQQ"):
        for split, (lo, hi) in WINDOWS.items():
            x = baseline(ticker, split)
            years = range(lo.year, hi.year + 1)
            daily = available_dates(ticker, lo, hi)
            for gap in (10, 15, 20):
                sessions = daily[(daily >= x.entry.min()) & (daily <= x.entry.max())] if len(x) else daily
                eps = episode_rows(x, gap, sessions)
                cycles = cycle_rows(x)
                for year in years:
                    yd = daily[daily.year == year]
                    raw_dates = x.loc[x.entry.dt.year == year, "entry"].nunique()
                    trade_count = int((x.entry.dt.year == year).sum())
                    e = eps[eps.episode_start.dt.year == year] if len(eps) else eps
                    c = sum(s.year == year for s, _ in cycles)
                    # Baseline has no admission blocker; every qualifying baseline
                    # episode contains at least one baseline trade by construction.
                    year_sessions = daily[(daily >= max(lo, pd.Timestamp(year, 1, 1))) & (daily <= min(hi, pd.Timestamp(year, 12, 31)))]
                    detail.append({"ticker": ticker, "split": split, "calendar_year": year,
                                   "gap_days": gap, "available_trading_days": len(yd),
                                   "raw_eligible_entry_dates": raw_dates,
                                   "opportunity_episode_count": len(e),
                                   "baseline_exposure_cycle_count": c,
                                   "episodes_that_produced_a_trade": len(e),
                                   "episodes_blocked_by_existing_position": 0,
                                   "year_first_available_date": yd.min() if len(yd) else pd.NaT,
                                   "year_last_available_date": yd.max() if len(yd) else pd.NaT,
                                   "year_data_coverage_rate_pct": round(100 * len(yd) / len(year_sessions), 4) if len(year_sessions) else None})
                    all_eps = len(eps)
                    summary.append({"ticker": ticker, "split": split, "calendar_year": year, "gap_days": gap,
                                    "episode_count": len(e), "baseline_trade_count": trade_count,
                                    "episode_share_of_all_opportunities_pct": round(100 * len(e) / all_eps, 4) if all_eps else 0,
                                    "baseline_trade_share_pct": round(100 * trade_count / len(x), 4) if len(x) else 0,
                                    "highest_episode_year": None, "lowest_episode_year": None,
                                    "train_validation_note": "Annual row includes zero-count years; episode/cycle assigned by start date."})
    d = pd.DataFrame(detail); s = pd.DataFrame(summary)
    for (ticker, split, gap), g in s.groupby(["ticker", "split", "gap_days"]):
        vals = g.episode_count
        s.loc[g.index, "highest_episode_year"] = g.loc[vals.idxmax(), "calendar_year"]
        s.loc[g.index, "lowest_episode_year"] = g.loc[vals.idxmin(), "calendar_year"]
    d.to_csv(OUT / "opportunity_episode_by_year.csv", index=False)
    s.to_csv(OUT / "opportunity_episode_year_summary.csv", index=False)
    print(d.to_string(index=False)); print(s.to_string(index=False))

if __name__ == "__main__": main()
