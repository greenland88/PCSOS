from pathlib import Path
import json
import duckdb
import pandas as pd
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch, summarize_replay
from pcs.data.access import PCSDataAccess

OUT = Path("research_outputs/oos_walk_forward_20260821")
OUT.mkdir(parents=True, exist_ok=True)
POLICY = ReplayPolicy()
SPLITS = Path("research_outputs/oos_splits_20260821")
ARTIFACTS = {
    "AMD": Path("research_outputs/safe_strike_stage4a/candidate_inputs/AMD.parquet"),
    "AMZN": Path("research_outputs/safe_strike_stage4a/authoritative_amzn_794_entry_contract_v2.parquet"),
    "TSLA": Path("research_outputs/safe_strike_stage4a/candidate_inputs/TSLA.parquet"),
    "NVDA": Path("research_outputs/safe_strike_stage4a/candidate_inputs/NVDA.parquet"),
}
FIELDS = ["date", "ticker", "expiration", "short_strike", "long_strike", "dte", "atr", "atr_distance", "credit", "spread_width", "credit_width_ratio", "planned_loss", "theoretical_max_loss", "short_delta", "trend_state", "pullback_state", "support_state", "population", "subgroup", "baseline_pullback", "variant_pullback", "earnings_date", "days_to_earnings", "expected_management_window"]

def load_candidates(ticker):
    d = pd.read_parquet(ARTIFACTS[ticker])
    return d[[c for c in FIELDS if c in d.columns]].copy()

def quote_index(ticker, cands):
    con = duckdb.connect(); start = pd.to_datetime(cands.date).min().date(); end = (pd.to_datetime(cands.date).max() + pd.Timedelta(days=POLICY.max_quote_days)).date()
    exps = sorted(pd.to_datetime(cands.expiration).dt.date.unique()); strikes = sorted(set(cands.short_strike.astype(float)) | set(cands.long_strike.astype(float)))
    exp_sql = ",".join("DATE '" + str(x) + "'" for x in exps); strike_sql = ",".join(str(float(x)) for x in strikes)
    spec = PCSDataAccess().resolve_source("options", ticker, start, end)
    parquet_input = spec.path.split(";") if ";" in spec.path else spec.path
    q = f'''SELECT trade_date AS "Trade Date", expiration_date AS "Expiry Date", strike AS "Strike", bid AS "Bid Price", ask AS "Ask Price", open_interest AS "Open Interest", volume AS "Volume", delta AS "Delta" FROM read_parquet(?, hive_partitioning=true) WHERE trade_date BETWEEN DATE '{start}' AND DATE '{end}' AND expiration_date IN ({exp_sql}) AND strike IN ({strike_sql}) AND lower(call_put)='p' ORDER BY trade_date'''
    frame = con.execute(q, [parquet_input]).fetchdf(); con.close(); frame["Trade Date"] = pd.to_datetime(frame["Trade Date"]); frame["Expiry Date"] = pd.to_datetime(frame["Expiry Date"])
    return {(e.normalize(), float(s)): g.sort_values("Trade Date").copy() for (e, s), g in frame.groupby(["Expiry Date", "Strike"], sort=False)}, len(frame)

def replay(cands, idx):
    def run_chunk(chunk):
        out = []
        for _, c in chunk.iterrows():
            r = c.to_dict(); exp = pd.Timestamp(r["expiration"]).normalize(); day = pd.Timestamp(r["date"]).normalize(); s = idx.get((exp, float(r["short_strike"]))); l = idx.get((exp, float(r["long_strike"])))
            if s is None or l is None: r.update(status="UNAVAILABLE", exit_reason="ENTRY_QUOTES_MISSING", entry_available=False)
            else:
                sm = s[s["Trade Date"].eq(day)]; lm = l[l["Trade Date"].eq(day)]
                if len(sm) != 1 or len(lm) != 1: r.update(status="UNAVAILABLE", exit_reason="ENTRY_QUOTES_MISSING", entry_available=False)
                else:
                    sr, lr = sm.iloc[0], lm.iloc[0]; r.update(credit=float(sr["Bid Price"] - lr["Ask Price"]), entry_available=True, short_bid=float(sr["Bid Price"]), short_ask=float(sr["Ask Price"]), long_bid=float(lr["Bid Price"]), long_ask=float(lr["Ask Price"])); r.update(_replay_lifecycle_batch(r, idx, POLICY))
            out.append(r)
        return out
    chunks = [cands.iloc[i:i + max(1, len(cands) // 8)] for i in range(0, len(cands), max(1, len(cands) // 8))]
    with ThreadPoolExecutor(max_workers=max(1, min(8, len(chunks)))) as pool:
        parts = list(pool.map(run_chunk, chunks))
    return pd.DataFrame([row for part in parts for row in part])
def yearly(frame):
    if frame.empty: return {}
    x = frame.copy(); x["year"] = pd.to_datetime(x["date"]).dt.year
    return {str(int(y)): summarize_replay(g).to_dict("records")[0] for y, g in x.groupby("year")}

def run_ticker(ticker):
    c = load_candidates(ticker); idx, quote_rows = quote_index(ticker, c); r = replay(c, idx); r.to_parquet(OUT / f"{ticker}_train_validation_replay.parquet", index=False)
    split = json.loads((SPLITS / f"{ticker}.json").read_text())
    result = {"ticker": ticker, "frozen_config_id": "PCS-OOS-FROZEN-20260821-V1", "config_hash": "CBAF956586AF43AFE0F0E0?"}
    result["config_hash"] = "CBAF956586AF43AFEAF0E3512E4B491D625613464392FD4440800BDBC026793B"
    result["quote_rows_scanned"] = quote_rows; result["splits"] = {}
    for part in split["splits"][:2]:
        mask = pd.to_datetime(r["date"]).between(part["start"], part["end"]); g = r.loc[mask].copy(); summary = summarize_replay(g, test_start_date=part["start"], test_end_date=part["end"])
        rec = summary.to_dict("records")[0] if not summary.empty else {}; rec["yearly_breakdown"] = yearly(g); rec["split_candidate_count"] = int(len(g)); rec["stop_rate"] = rec.get("stop_frequency"); rec["return_on_planned_risk"] = rec.get("annualized_return_on_average_planned_loss"); result["splits"][part["name"]] = rec
    result["final_oos"] = {"status": "NOT_RUN"}; result["classification"] = "PENDING_STABILITY_COMPARISON"
    (OUT / f"{ticker}_validation.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return ticker, result

if __name__ == "__main__":
    all_results = {}
    with ProcessPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(run_ticker, ticker): ticker for ticker in ("AMD", "AMZN", "TSLA", "NVDA")}
        for future in as_completed(futures):
            ticker, result = future.result()
            all_results[ticker] = result
            print(json.dumps({"ticker": ticker, "status": "COMPLETE"}), flush=True)
    (OUT / "system_summary.json").write_text(json.dumps({"frozen_config_id":"PCS-OOS-FROZEN-20260821-V1", "config_hash":"CBAF956586AF43AFEAF0E3512E4B491D625613464392FD4440800BDBC026793B", "tickers":all_results, "final_oos":"NOT_RUN", "parameter_search":"NOT_RUN"}, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"completed": sorted(all_results), "workers": 8}, indent=2))
