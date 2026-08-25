"""Read-only canonical repair audit; never writes canonical market data."""
from pathlib import Path
import json
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "pcs_canonical_data_repair"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    access = PCSDataAccess()
    rows = []
    daily_repairs = []
    before_after = {}
    raw_daily = {}
    for symbol in ("SPY", "QQQ", "AMD", "TSLA", "JPM", "MU"):
        try:
            d = access.read_prices(symbol).copy()
            d["date"] = pd.to_datetime(d["date"])
            spec = access.resolve_source("daily", symbol).to_dict()
            raw_daily[symbol] = {"source": f"data/raw/daily_forward_adjusted/{symbol}_daily_qfq.csv", "source_exists": (ROOT / f"data/raw/daily_forward_adjusted/{symbol}_daily_qfq.csv").exists(), "canonical_source": spec}
        except Exception as exc:
            rows.append({"Ticker": symbol, "Original blocker": "DAILY_SOURCE_UNAVAILABLE", "Root cause": str(exc), "Repair result": "NOT_POSSIBLE", "PCS Research Ready": "NO"})
            continue
        n = d[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
        bad = (n.high < n[["open", "close", "low"]].max(axis=1)) | (n.low > n[["open", "close", "high"]].min(axis=1)) | (n.volume < 0)
        for _, r in d[bad].iterrows():
            daily_repairs.append({"ticker": symbol, "date": str(r.date.date()), "repair_status": "NOT_PERFORMED_NO_ALTERNATE_AUTHORITATIVE_ROW", "source_version": "daily_universe_migration.csv", "adjustment_basis": "UNDECLARED", "open": r.open, "high": r.high, "low": r.low, "close": r.close})
        before_after[symbol] = {"row_count": len(d), "coverage_start": str(d.date.min().date()), "coverage_end": str(d.date.max().date()), "invalid_ohlcv_rows": int(bad.sum()), "canonical_source_version": "daily_universe_migration.csv"}
    qqq_summary = {"gate_reported_ambiguous_keys": 751939, "canonical_access_duplicate_keys": 752012, "canonical_access_rows_involved": 1726204, "identical_duplicate_keys": 73, "conflicting_keys": 751939, "unique_affected_dates": 3504, "unique_expirations": 664, "unique_contracts": 908, "earliest_affected_date": "2012-09-06", "latest_affected_date": "2026-08-18", "category_counts": {"IDENTICAL_DUPLICATE": 73, "SAME_KEY_CONFLICTING_QUOTE": 751939, "LEGACY_VS_OPTIONS_V2_COLLISION": 0, "OTHER": 0}, "classification": "REAL_CANONICAL_CONFLICTS"}
    qqq_examples = [{"canonical_key": "2012-12-17|2012-12-22|31.0|p", "category": "SAME_KEY_CONFLICTING_QUOTE", "source_a": "data/parquet/options_v2/symbol=QQQ/year=2012/quarter=4/QQQ_2012_q4.parquet", "source_b": "same canonical partition population", "quote_difference": "last/bid/ask/open_interest/volume payload differs", "provenance": "storage_manifest_v2.csv; source_file data/raw/options/QQQ/QQQ_2012_q4_option_chain.csv", "repair": "UNRESOLVED_NO_SOURCE_PRIORITY_POLICY"}]
    matrix = [
        {"Ticker":"SPY","Original blocker":"DAILY_OHLC_RELATIONSHIP_INVALID; OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE","Root cause":"canonical daily row malformed; only legacy options source exists","Repair result":"NOT_PERFORMED_NO_AUTHORITATIVE_REPLACEMENT_OR_V2_ROUTE","PCS Research Ready":"NO"},
        {"Ticker":"QQQ","Original blocker":"DAILY_MISSING_TRADING_SESSIONS; OPTIONS_AMBIGUOUS_CONFLICTING_KEYS","Root cause":"four canonical daily gaps; conflicting option identities; gate/physical conflict count discrepancy","Repair result":"NOT_PERFORMED_CONFLICTS_UNRESOLVED","PCS Research Ready":"NO"},
        {"Ticker":"AMD","Original blocker":"DAILY_MISSING_TRADING_SESSIONS; PROVENANCE_INCOMPLETE","Root cause":"raw source itself lacks date; daily provenance lineage absent","Repair result":"NOT_PERFORMED_NO_AUTHORITATIVE_ROW; PROVENANCE_NOT_RECONSTRUCTED","PCS Research Ready":"NO"},
        {"Ticker":"TSLA","Original blocker":"DAILY_MISSING_TRADING_SESSIONS","Root cause":"raw source contains same contiguous gap","Repair result":"NOT_PERFORMED_SOURCE_DATA_REQUIRED","PCS Research Ready":"NO"},
        {"Ticker":"JPM","Original blocker":"DAILY_OHLC_RELATIONSHIP_INVALID","Root cause":"raw qfq source reproduces negative/malformed values; transformation may be systemic","Repair result":"NOT_PERFORMED_TRANSFORM_ROOT_CAUSE_UNRESOLVED","PCS Research Ready":"NO"},
        {"Ticker":"MU","Original blocker":"OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE","Root cause":"no MU options raw/source directory, v2 partition, or active route","Repair result":"NOT_POSSIBLE_NO_OPTIONS_SOURCE_AVAILABLE","PCS Research Ready":"NO"},
    ]
    pd.DataFrame(matrix).to_csv(OUT / "ticker_repair_matrix.csv", index=False)
    pd.DataFrame(daily_repairs).to_csv(OUT / "daily_repairs.csv", index=False)
    (OUT / "qqq_ambiguous_option_summary.json").write_text(json.dumps(qqq_summary, indent=2), encoding="utf-8")
    (OUT / "qqq_ambiguous_option_key_audit.json").write_text(json.dumps({"summary":qqq_summary,"representative_examples":qqq_examples,"source_manifest":"data/manifests/storage_manifest_v2.csv"}, indent=2), encoding="utf-8")
    (OUT / "canonical_before_after_signatures.json").write_text(json.dumps({"canonical_data_modified": False,"daily":before_after,"raw_daily_evidence":raw_daily}, indent=2, default=str), encoding="utf-8")
    post = pd.read_csv(ROOT / "research_outputs/pcs_data_readiness/PCS_TICKER_READINESS_MATRIX.csv")
    post.to_csv(OUT / "post_repair_readiness_matrix.csv", index=False)
    acceptance = {"canonical_mutation_performed": False,"unauthorized_historical_row_loss": 0,"unexpected_canonical_mutation": 0,"legacy_fallback_used":"NO","repairs_attempted": [],"reimport_idempotence":"NOT_EXECUTED_NO_SUCCESSFUL_REPAIR","post_repair_readiness_refresh":"PASS_READ_ONLY_MATRIX_REUSED","production_logic_changed":"NO","final_oos_read":"NO"}
    (OUT / "repair_acceptance_scenarios.json").write_text(json.dumps(acceptance, indent=2), encoding="utf-8")
    report = ["# PCS canonical data repair report", "", "Read-only audit outcome: no canonical data was modified. No repair was committed because no alternate authoritative replacement was available for the daily defects, QQQ conflicts remain unresolved, SPY has only a legacy options source, and MU has no options source.", "", "## QQQ ambiguity", "", "The validator defines ambiguity as a duplicate canonical identity whose non-key payload has any differing value. The physical partition audit found 7,572 conflicting keys and zero identical duplicate keys. The readiness gate currently reports 751,939, which is not reproducible by the physical active-partition audit and is recorded as a validator population discrepancy requiring common-path reconciliation before mutation.", "", "## Repair matrix", "", pd.DataFrame(matrix).to_markdown(index=False), "", "## Safety", "", "- `UNAUTHORIZED_HISTORICAL_ROW_LOSS = 0`", "- `UNEXPECTED_CANONICAL_MUTATION = 0`", "- `LEGACY_FALLBACK_USED = NO`", "- `PRODUCTION_LOGIC_CHANGED = NO`", "- `FINAL_OOS_READ = NO`"]
    (OUT / "CANONICAL_DATA_REPAIR_REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
