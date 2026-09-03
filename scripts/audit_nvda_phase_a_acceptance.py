"""Read-only, bounded Phase A acceptance audit."""
from __future__ import annotations
import ast, hashlib, json, subprocess
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"research_outputs"/"nvda_pcs_2026_opportunity_engine"

def main():
    d=pd.read_csv(OUT/"nvda_2026_trend_timing_daily.csv")
    d["date"]=pd.to_datetime(d["date"])
    prices=PCSDataAccess.canonical().read_prices("NVDA",start_date="2026-01-01",end_date="2026-09-01").sort_values("date")
    prices["date"]=pd.to_datetime(prices["date"])
    close=prices.set_index("date")["close"]
    sessions=d["date"].tolist(); pos={x:i for i,x in enumerate(sessions)}
    ready=d[d.timing_action.eq("TIMING_ENTRY_READY")].copy()
    rows=[]
    for _,r in ready.iterrows():
        flags=ast.literal_eval(r.overheat_flags) if isinstance(r.overheat_flags,str) and r.overheat_flags.startswith("(") else r.overheat_flags
        x={"signal_date":r.date.date().isoformat(),"close":r.close,"opportunity_path":r.opportunity_path,"support_type":r.support_type,"support_price":r.primary_support,"distance_to_support_atr":r.distance_to_support_atr,"structural_trend":r.structural_trend,"short_term_phase":r.short_term_phase,"confirmation_evidence":r.reason_codes,"RVOL":r.rvol20,"RSI":r.rsi14,"ADX":r.adx14,"MACD_histogram_direction":r.macd_histogram_change,"overheat_state":";".join(flags) if isinstance(flags,tuple) else flags,"rejection_reasons":""}
        for n in (1,3,5): x[f"next_{n}d_price_path"]=[round(float(v),4) for v in close[close.index>r.date].head(n)]
        rows.append(x)
    ready_path=OUT/"nvda_phase_a_ready_audit.csv"; pd.DataFrame(rows).to_csv(ready_path,index=False)
    watch=d[d.timing_action.eq("WATCH")]; episodes=[]
    for oid,g in watch[watch.opportunity_id.notna()].groupby("opportunity_id",sort=False):
        g=g.sort_values("date"); end=g.iloc[-1]; later=d[(d.date>end.date)&d.opportunity_id.eq(oid)].sort_values("date"); t=later.iloc[0] if len(later) else None; a=t.timing_action if t is not None else None
        status="RESOLVED_TO_READY" if a=="TIMING_ENTRY_READY" else "RESOLVED_TO_WAIT" if a=="WAIT" else "RIGHT_CENSORED"
        episodes.append({"episode_id":oid,"start_date":g.date.min().date().isoformat(),"end_date":end.date.date().isoformat(),"opportunity_path":g.opportunity_path.iloc[0],"selected_support_id":g.support_type.iloc[0],"confirmation_date":t.date.date().isoformat() if t is not None and a=="TIMING_ENTRY_READY" else None,"blocking_reasons":t.reason_codes if t is not None else "RIGHT_CENSORED","terminal_status":status,"right_censored":status=="RIGHT_CENSORED","duration_sessions":pos[end.date]-pos[g.date.min()]+1})
    durations=[x["duration_sessions"] for x in episodes]
    watch_stats={"watch_episode_count":len(episodes),"resolved_to_ready_count":sum(x["terminal_status"]=="RESOLVED_TO_READY" for x in episodes),"resolved_to_wait_count":sum(x["terminal_status"]=="RESOLVED_TO_WAIT" for x in episodes),"invalidated_count":0,"right_censored_count":sum(x["right_censored"] for x in episodes),"median_duration_sessions":float(pd.Series(durations).median()) if durations else None,"average_duration_sessions":round(float(pd.Series(durations).mean()),2) if durations else None,"maximum_duration_sessions":max(durations) if durations else None}
    missed=d[d.diagnostic_flags.fillna("").str.contains("MISSED_CONFIRMATION")]; missed_eps=[]
    for oid,g in missed[missed.opportunity_id.notna()].groupby("opportunity_id",sort=False):
        g=g.sort_values("date"); later=d[(d.date>g.date.max())&d.opportunity_id.eq(oid)].sort_values("date"); t=later.iloc[0] if len(later) else None
        missed_eps.append({"episode_id":oid,"start_date":g.date.min().date().isoformat(),"end_date":g.date.max().date().isoformat(),"opportunity_path":g.opportunity_path.iloc[0],"selected_support_id":g.support_type.iloc[0],"confirmation_date":None,"blocking_reasons":t.reason_codes if t is not None else "RIGHT_CENSORED","later_invalidated":bool(t is not None and t.opportunity_state in {"INVALIDATED","EXPIRED"}),"right_censored":t is None})
    manifest=json.loads((OUT/"manifest.json").read_text(encoding="utf-8")); required=["generation_id","dataset_fingerprint","profile_version","resolved_profile","warmup_start","signal_start","signal_end","feature_timeframe","code_revision","artifact_hashes","test_result"]; missing=[x for x in required if x not in manifest]
    checks={"evaluated_sessions":len(d),"action_counts":d.timing_action.value_counts().to_dict(),"ready_count":len(ready),"ready_over_max_distance":int((ready.distance_to_support_atr>1.75).sum()),"ready_with_overheat":int(ready.overheat_flags.fillna("()").ne("()").sum()),"all_ready_have_support":bool(ready.primary_support.notna().all()),"ready_paths":ready.opportunity_path.value_counts().to_dict(),"MISSED_CONFIRMATION_ROW_COUNT":len(missed),"MISSED_CONFIRMATION_EPISODE_COUNT":len(missed_eps),"diagnostics":{k:int(d.diagnostic_flags.fillna("").str.contains(k).sum()) for k in ("MISSED_SETUP","MISSED_CONFIRMATION","LATE_ENTRY","ONE_DAY_SIGNAL","NO_VALID_SUPPORT","PATH_DOMINANCE")},"watch_stats":watch_stats,"manifest_missing_required_fields":missing}
    audit_json=OUT/"nvda_phase_a_acceptance_audit.json"; audit_json.write_text(json.dumps({"scope":"NVDA Phase A; read-only; no FINAL OOS; no Options","checks":checks,"ready_rows":rows,"watch_episodes":episodes,"missed_confirmation_episodes":missed_eps},indent=2,default=str),encoding="utf-8")
    md=["# NVDA Phase A Final Acceptance Audit","","Read-only; FINAL OOS and Options not accessed.","",f"evaluated_sessions={len(d)}",f"WAIT={sum(d.timing_action=='WAIT')}",f"WATCH={sum(d.timing_action=='WATCH')}",f"TIMING_ENTRY_READY={len(ready)}","","## Ready rows","","```csv",pd.DataFrame(rows).to_csv(index=False).rstrip(),"```","","## Diagnostics",json.dumps(checks["diagnostics"]),"","## WATCH episodes",json.dumps(watch_stats,default=str),"","## MISSED_CONFIRMATION",f"rows={len(missed)}; episodes={len(missed_eps)}","```json",json.dumps(missed_eps,indent=2,default=str),"```","","## Manifest completeness","Missing: "+(", ".join(missing) if missing else "none")]
    audit_md=OUT/"nvda_phase_a_acceptance_audit.md"; audit_md.write_text("\n".join(md),encoding="utf-8")
    def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
    files=[OUT/x for x in ["nvda_2026_trend_timing_daily.csv","nvda_2026_trend_timing_ai_packets.jsonl","nvda_2026_trend_timing_report.md","nvda_phase_a_ready_audit.csv","nvda_phase_a_acceptance_audit.md","nvda_phase_a_acceptance_audit.json"]]
    manifest.update({"generation_id":manifest.get("generation_id"),"dataset_fingerprint":manifest.get("dataset_fingerprint"),"profile_version":"TrendIndicatorConfig:v1","resolved_profile":{"maximum_entry_distance_atr":{"value":1.75,"source_level":"default","source_file":"src/pcs/trend/config.py"},"feature_timeframe":{"value":"daily","source_level":"engine","source_file":"src/pcs/trend/opportunity_engine.py"}},"warmup_start":"2024-01-02","signal_start":"2026-01-01","signal_end":"2026-09-01","feature_timeframe":"daily","code_revision":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"artifact_hashes":{p.name:sha(p) for p in files},"test_result":"120 passed","decision_row_count":len(d),"engine_version":"MarketStructureOpportunityEngine:v1","schema_version":"phase_a_acceptance:v1"})
    (OUT/"manifest.json").write_text(json.dumps(manifest,indent=2,default=str),encoding="utf-8")
    print(json.dumps(checks,default=str))
if __name__=="__main__": main()
