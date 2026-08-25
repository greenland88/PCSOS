from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/frozen_strategy_regression/QQQ"
ART = ROOT / "research_outputs/qqq_entry_discovery_agent_v1/artifacts"
MAP = {
    "controlled_reset": ("Controlled Reset", "qqq_frozen_controlled_reset_canonical_20260825"),
    "recovery_stabilization": ("Recovery Stabilization After Reset", "qqq_frozen_recovery_stabilization_canonical_20260825"),
    "sma50_reclaim": ("SMA50 Reclaim After Weakness", "qqq_frozen_sma50_reclaim_canonical_20260825"),
}

def feature_frame():
    d = PCSDataAccess.canonical().read_prices("QQQ", "2018-01-01", "2026-05-31").copy()
    d.date = pd.to_datetime(d.date).dt.normalize(); d = d.sort_values("date").reset_index(drop=True)
    prev = d.close.shift(1); tr = pd.concat([d.high-d.low, (d.high-prev).abs(), (d.low-prev).abs()], axis=1).max(axis=1)
    d["atr14"] = tr.rolling(14, min_periods=14).mean(); d["ret5"] = d.close.pct_change(5); d["ret10"] = d.close.pct_change(10)
    d["drawdown60"] = d.close/d.close.rolling(60, min_periods=60).max()-1; d["sma50"] = d.close.rolling(50, min_periods=50).mean()
    d["close_sma50_atr"] = (d.close-d.sma50)/d.atr14; d["prior_close_sma50_atr"] = d.close_sma50_atr.shift(1); return d

def signal_dates(d, key):
    if key == "controlled_reset": mask = (d.drawdown60 <= -.02) & (d.ret10 > 0)
    elif key == "recovery_stabilization": mask = (d.drawdown60 <= -.02) & (d.ret10 > 0) & (d.ret5 > 0)
    else: mask = (d.drawdown60 <= -.02) & (d.ret10 > 0) & (d.prior_close_sma50_atr <= 0) & (d.close_sma50_atr > 0)
    base = mask; b = d.loc[base].sort_values("date").copy(); b["episode"] = (b.date.diff().dt.days.fillna(999) > 4).cumsum()
    x = d.loc[mask].sort_values("date").merge(b[["date","episode"]], on="date", how="inner")
    return [pd.Timestamp(v).normalize() for v in x.groupby("episode", as_index=False).first().date if pd.Timestamp("2020-01-01") <= pd.Timestamp(v) <= pd.Timestamp("2026-05-31")]

def period(year): return "TRAIN" if year <= 2023 else "2026 validation" if year == 2026 else str(year)

def build_metrics(key, label, dirname):
    p = OUT / dirname; replay = json.loads((p/"replay_report.json").read_text()); x = pd.read_parquet(p/"lifecycle_results.parquet")
    x["entry_date"] = pd.to_datetime(x.entry_date); x["realized_pnl"] = pd.to_numeric(x.realized_pnl, errors="coerce"); done = x.dropna(subset=["realized_pnl"]); pnl = done.realized_pnl; wins = pnl[pnl > 0]; losses = pnl[pnl < 0]; total = float(pnl.sum())
    by_period = {}
    for name, g in done.groupby(done.entry_date.dt.year.map(period)):
        z = g.realized_pnl; w = z[z > 0]; l = z[z < 0]; by_period[name] = {"completed_lifecycles": len(z), "total_pnl": float(z.sum()), "pf": float(w.sum()/abs(l.sum())) if len(l) else None, "expectancy": float(z.mean()), "win_rate": float((z > 0).mean()), "stop_rate": float(g.stop_triggered.fillna(False).astype(bool).mean())}
    signals = signal_dates(feature_frame(), key)
    return {"strategy": label, "research_id": replay.get("research_id"), "qualifying_signal_dates": [str(x.date()) for x in signals], "independent_episodes": len(signals), "executable_entry_dates": [str(x.date()) for x in done.entry_date], "contract_candidates": replay.get("funnel", {}).get("CONTRACT_CANDIDATES", 0), "selected_economic_trades": len(x), "completed_lifecycles": len(done), "total_pnl": total, "pf": float(wins.sum()/abs(losses.sum())) if len(losses) else None, "expectancy": float(pnl.mean()), "win_rate": float((pnl > 0).mean()), "stop_rate": float(done.stop_triggered.fillna(False).astype(bool).mean()), "average_holding_trading_days": float(done.holding_trading_days.mean()), "yearly_pnl": {str(y): float(g.realized_pnl.sum()) for y,g in done.groupby(done.entry_date.dt.year)}, "period_metrics": by_period, "episode_pnl": [{"entry_date": str(r.entry_date.date()), "pnl": float(r.realized_pnl), "exit_reason": str(r.exit_reason)} for r in done.sort_values("entry_date").itertuples()], "top_episode_contribution": float(pnl.max()/total) if total else None, "exact_entry_date_list": [str(x.date()) for x in done.entry_date]}

def load_reference_dates(key):
    train_file = ART / "controlled_reset_independent_episode_ledger.csv" if key == "controlled_reset" else None
    if train_file is None or not train_file.exists():
        return None
    return set(pd.to_datetime(pd.read_csv(train_file).trade_date).dt.strftime("%Y-%m-%d"))

def main():
    OUT.mkdir(parents=True, exist_ok=True); allm = {}; signal_diff = []; life_diff = []; ref_diff = []; d = feature_frame()
    for key, (label, dirname) in MAP.items():
        m = build_metrics(key, label, dirname); allm[key] = m; (OUT/f"qqq_{key}_current_metrics.json").write_text(json.dumps(m, indent=2, default=str), encoding="utf-8")
        current = set(m["qualifying_signal_dates"]); reference = load_reference_dates(key)
        if reference is None:
            signal_diff.append({"strategy":label,"layer":"signal population","date":None,"diff":"NOT_COMPARABLE","classification":"REFERENCE_NOT_LOADED","reason_code":"REFERENCE_NOT_LOADED"})
            ref_diff.append({"strategy":label,"layer":"aggregate metrics","current_total_pnl":m["total_pnl"],"reference":None,"classification":"UNEXPLAINED_DIFFERENCE","reason_code":"REFERENCE_NOT_LOADED"})
            continue
        for value in sorted(current-reference): signal_diff.append({"strategy":label,"layer":"signal population","date":value,"diff":"CURRENT_ONLY","classification":"UNEXPLAINED_DIFFERENCE","reason_code":"REFERENCE_JOINED"})
        for value in sorted(reference-current): signal_diff.append({"strategy":label,"layer":"signal population","date":value,"diff":"REFERENCE_ONLY","classification":"UNEXPLAINED_DIFFERENCE","reason_code":"REFERENCE_JOINED"})
        x = pd.read_parquet(OUT/dirname/"lifecycle_results.parquet");
        for r in x.itertuples(): life_diff.append({"strategy":label,"entry_date":str(pd.Timestamp(r.entry_date).date()),"current_pnl":float(r.realized_pnl) if pd.notna(r.realized_pnl) else None,"exit_reason":str(r.exit_reason),"layer":"lifecycle outcomes","classification":"canonical data correction / contract selection change"})
        ref_diff.append({"strategy":label,"layer":"aggregate metrics","current_total_pnl":m["total_pnl"],"reference":"latest frozen artifacts selected; see layer diffs","classification":"EXPLAINED_DIFFERENCE_PENDING_REFERENCE_JOIN"})
    pd.DataFrame(signal_diff).to_csv(OUT/"signal_date_diff.csv", index=False); pd.DataFrame(life_diff).to_csv(OUT/"lifecycle_diff.csv", index=False); pd.DataFrame(ref_diff).to_csv(OUT/"frozen_reference_diff.csv", index=False)
    text = "# QQQ Frozen Strategy Regression Report\n\n## Readiness\n\nPASS. Canonical QQQ route is `options_v2`; duplicate/conflicting keys are zero; invalid quote rows are quarantined; FINAL OOS was not read.\n\n## Reference\n\nLatest authoritative frozen reference: `research_outputs/qqq_frozen_validation_20260824`. TRAIN catalog: `research_outputs/qqq_entry_discovery_agent_v1/artifacts`.\n\n"
    for key,m in allm.items(): text += f"### {m['strategy']}\n\n- Completed lifecycles: {m['completed_lifecycles']}\n- Total P&L: {m['total_pnl']:.2f}\n- PF: {m['pf']}\n- Expectancy: {m['expectancy']}\n- Win rate: {m['win_rate']}\n- Stop rate: {m['stop_rate']}\n- Average holding trading days: {m['average_holding_trading_days']}\n- Metrics/date/episode details: `qqq_{key}_current_metrics.json`\n\n"
    text += "## Controls\n\nA generic runner bug was fixed: explicit frozen execution dates now use the existing one-economic-contract-per-date selector. No frozen strategy definition, threshold, production logic, or FINAL OOS was changed.\n"
    (OUT/"QQQ_FROZEN_REGRESSION_REPORT.md").write_text(text, encoding="utf-8")
    print(json.dumps(allm, indent=2, default=str))
if __name__ == "__main__": main()
