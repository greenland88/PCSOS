"""Build read-only current PCS universe, suitability, stale-isolation and snapshot reports."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path("research_outputs")
OUT = ROOT / "pcs_universe_dashboard_20260821"
BASE = ROOT / "spy_qqq_pcs_baseline_20260821"
AUDIT = json.loads((ROOT / "pcs_overnight_status_20260821" / "nontech_readiness_and_inventory.json").read_text(encoding="utf-8"))
TRAIN = json.loads((BASE / "train_validation_report.json").read_text(encoding="utf-8"))
NONTECH = {x["symbol"]: x for x in AUDIT["nontech"]}
SECTOR = {"NVDA": "Semiconductors", "AMD": "Semiconductors", "AVGO": "Semiconductors", "MU": "Semiconductors", "MSFT": "Technology", "AMZN": "Technology", "TSLA": "Technology", "META": "Technology", "GOOGL": "Technology", "HOOD": "Financials", "PLTR": "Technology", "JPM": "Financials", "BAC": "Financials", "GS": "Financials", "WMT": "Consumer Staples", "COST": "Consumer Staples", "HD": "Consumer Discretionary", "MCD": "Consumer Discretionary", "UNH": "Healthcare", "ABBV": "Healthcare", "CAT": "Industrials", "RTX": "Industrials", "XOM": "Energy", "CVX": "Energy", "COP": "Energy"}
INDUSTRY = {"NVDA": "Semiconductors", "AMD": "Semiconductors", "AVGO": "Semiconductors", "MU": "Semiconductors", "MSFT": "Software", "AMZN": "Internet Retail", "TSLA": "Automobiles", "META": "Interactive Media", "GOOGL": "Interactive Media", "HOOD": "Capital Markets", "PLTR": "Software", "JPM": "Banks", "BAC": "Banks", "GS": "Capital Markets", "WMT": "Consumer Retail", "COST": "Consumer Retail", "HD": "Home Improvement Retail", "MCD": "Restaurants", "UNH": "Managed Healthcare", "ABBV": "Pharmaceuticals", "CAT": "Machinery", "RTX": "Aerospace & Defense", "XOM": "Integrated Energy", "CVX": "Integrated Energy", "COP": "Oil & Gas E&P"}
KNOWN = {"AMD": "CONDITIONAL_POSITIVE", "AMZN": "CONDITIONAL_POSITIVE", "TSLA": "NEGATIVE", "NVDA": "INSUFFICIENT_DATA", "SPY": "ROBUST_POSITIVE", "QQQ": "NEGATIVE"}
TOP8 = AUDIT["recommended_next_batch"]


def _date_range(symbol: str, daily: bool = True):
    root = Path("data/parquet/daily") / f"symbol={symbol}" if daily else None
    if root is None:
        return None, None
    files = list(root.rglob("*.parquet"))
    if not files:
        return None, None
    x = pd.concat((pd.read_parquet(p, columns=["date"]) for p in files), ignore_index=True)
    x.date = pd.to_datetime(x.date)
    return str(x.date.min().date()), str(x.date.max().date())


def _options_range(symbol: str):
    path = Path("data/manifests/storage_manifest.csv")
    if not path.exists():
        return None, None
    x = pd.read_csv(path)
    x = x[x.symbol.eq(symbol)]
    if x.empty:
        return None, None
    return str(pd.to_datetime(x.min_date).min().date()), str(pd.to_datetime(x.max_date).max().date())


def _inventory() -> list[dict]:
    universe = yaml.safe_load(Path("config/market_universe.yaml").read_text(encoding="utf-8"))["pcs_universe"]
    rows = []
    for s in universe + ["SPY", "QQQ"]:
        d0, d1 = _date_range(s)
        if s in {"SPY", "QQQ"}:
            rows.append({"ticker": s, "sector": "Broad Market ETF", "industry": "Index ETF", "active_route": "PCS-SPY-QQQ-OOS-FROZEN-20260821-V1 isolated route", "daily_start": d0, "daily_end": d1, "options_start": "2020-01-02" if s == "SPY" else "2010-11-22", "options_end": "2026-08-18", "options_v2_status": "ISOLATED_BASELINE_ARTIFACT", "candidate_artifact": True, "entry_contract": True, "lifecycle": True, "replay_status": "REPLAY_READY", "oos_status": "TRAIN_VALIDATION_COMPLETE_FINAL_NOT_RUN", "classification": KNOWN[s], "eligibility": "DO_NOT_USE_BASELINE" if s == "QQQ" else "OOS_EVALUATED", "research_status": "RESEARCH_ONLY", "current_blocker": "NONE"})
            continue
        n = NONTECH.get(s)
        if n:
            rows.append({"ticker": s, "sector": SECTOR.get(s), "industry": INDUSTRY.get(s, "UNKNOWN"), "active_route": "UNROUTED", "daily_start": n["daily_start"], "daily_end": n["daily_end"], "options_start": None, "options_end": None, "options_v2_status": "NOT_CONFIGURED", "candidate_artifact": False, "entry_contract": False, "lifecycle": False, "replay_status": "DATA_NOT_READY", "oos_status": "NOT_RUN", "classification": "NOT_RUN", "eligibility": "DATA_NOT_READY", "research_status": "RESEARCH_ONLY", "current_blocker": "OPTIONS_SOURCE_MISSING"})
            continue
        # Existing technology universe entries without completed OOS artifacts.
        eligibility = "PCS_ELIGIBLE_CONDITIONAL" if s in {"AMD", "AMZN"} else "DO_NOT_USE_BASELINE" if s == "TSLA" else "INSUFFICIENT_EVIDENCE" if s == "NVDA" else "DATA_READY"
        o0, o1 = _options_range(s)
        route = "config-routed options manifest" if s in {"MSFT", "AMZN", "NVDA", "AMD", "TSLA"} else "legacy canonical options manifest" if o0 else "UNROUTED"
        rows.append({"ticker": s, "sector": SECTOR.get(s, "Technology"), "industry": INDUSTRY.get(s, "UNKNOWN"), "active_route": route, "daily_start": d0, "daily_end": d1, "options_start": o0, "options_end": o1, "options_v2_status": "CONFIGURED" if s in {"AMZN", "NVDA", "AMD", "TSLA"} else "LEGACY_ONLY" if o0 else "NOT_CONFIGURED", "candidate_artifact": s in {"NVDA", "AMD", "AMZN", "TSLA"}, "entry_contract": s in {"NVDA", "AMD", "AMZN", "TSLA"}, "lifecycle": s in {"NVDA", "AMD", "AMZN", "TSLA"}, "replay_status": "REPLAY_READY" if s in {"NVDA", "AMD", "AMZN", "TSLA"} else "DATA_READY_ONLY", "oos_status": "OOS_EVALUATED" if s in KNOWN else "NOT_RUN", "classification": KNOWN.get(s, "NOT_RUN"), "eligibility": eligibility, "research_status": "RESEARCH_ONLY", "current_blocker": "NONE" if s in {"NVDA", "AMD", "AMZN", "TSLA"} else "CANDIDATE_ARTIFACT_MISSING"})
    return rows


def _sector_balance(universe: list[dict]) -> dict:
    core = [x for x in universe if x["ticker"] not in {"SPY", "QQQ"}]
    counts = Counter(x["sector"] for x in core)
    total = len(core)
    pct = {k: round(v / total * 100, 2) for k, v in counts.items()}
    missing = [s for s in ["Communication Services", "Utilities", "Real Estate"] if not counts.get(s)]
    semi = counts.get("Semiconductors", 0)
    tech_comm = sum(counts.get(s, 0) for s in ["Technology", "Semiconductors", "Communication Services"])
    return {"core_ticker_count": total, "ticker_count_by_sector": dict(counts), "percentage_by_sector": pct, "semiconductor_concentration_pct": round(semi / total * 100, 2), "tech_plus_communication_concentration_pct": round(tech_comm / total * 100, 2), "etf_overlay_count": 2, "etf_overlay_pct_of_core_plus_overlay": round(2 / (total + 2) * 100, 2), "classification": "MODERATELY_CONCENTRATED", "missing_three_sectors": missing}


def _suitability(universe: list[dict]) -> list[dict]:
    out = []
    for x in universe:
        s = x["ticker"]
        if s in TOP8:
            dims = {"DATA_QUALITY": 5 if x["daily_start"] else "UNKNOWN", "OPTIONS_HISTORY_LENGTH": "UNKNOWN", "OPTIONS_LIQUIDITY": "UNKNOWN", "IMPLEMENTATION_COST": 2, "SECTOR_DIVERSIFICATION_VALUE": 5}
            priority = "INSUFFICIENT_DATA"
        elif s in KNOWN:
            dims = {"DATA_QUALITY": 5, "OPTIONS_HISTORY_LENGTH": 5, "OPTIONS_LIQUIDITY": "UNKNOWN", "IMPLEMENTATION_COST": 4, "SECTOR_DIVERSIFICATION_VALUE": 3}
            priority = "HIGH_RESEARCH_PRIORITY" if s in {"AMD", "AMZN"} else "MEDIUM_RESEARCH_PRIORITY" if s == "SPY" else "LOW_RESEARCH_PRIORITY" if s == "TSLA" else "INSUFFICIENT_DATA"
        else:
            continue
        out.append({"ticker": s, "PCS_SUITABILITY_SCORE": dims, "priority": priority, "not_production_signal": True})
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    inv = _inventory()
    balance = _sector_balance(inv)
    dashboard = {"module": "pcs_universe_dashboard", "version": "20260821.v1", "last_verified_date": "2026-08-20", "current_core_ticker_count": 25, "tested_index_overlay": ["SPY", "QQQ"], "ticker_count_including_overlay": 27, "current_facts_only": True, "tickers": inv, "sector_balance": balance}
    (OUT / "pcs_universe_dashboard_20260821.json").write_text(json.dumps(dashboard, indent=2), encoding="utf-8")
    (OUT / "pcs_suitability_scores.json").write_text(json.dumps({"module": "PCS_SUITABILITY_SCORE", "version": "20260821.v1", "rows": _suitability(inv)}, indent=2), encoding="utf-8")
    (OUT / "sector_balance_audit.json").write_text(json.dumps(balance, indent=2), encoding="utf-8")

    stale = [
        {"path": "research_outputs/safe_strike_stage4a/validation_summary.json", "stale_marker": "BLOCKED_INCOMPLETE_HISTORICAL_CONTRACT"},
        {"path": "research_outputs/market_breadth_contract_audit_20260820.md", "stale_marker": "BREADTH_CONTRACT_INCOMPLETE"},
        {"path": "docs/tsla_v2_cutover_20260820.md", "stale_marker": "TSLA CUTOVER BLOCKED"},
        {"path": "research_outputs/nvda_duplicate_resolved_view_20260820/NVDA_REMAINING_ISSUES.md", "stale_marker": "NVDA historical lifecycle coverage wording"},
        {"path": "research_outputs/tsla_specialized_pcs_20260820/TSLA_SPECIALIZED_BLOCKERS.md", "stale_marker": "TSLA 5487/960 historical specialized blocker wording"},
    ]
    (OUT / "stale_report_list.json").write_text(json.dumps({"module": "stale_report_isolation_audit", "default_reading_policy": "IGNORE_UNLESS_EXPLICITLY_REQUESTED", "recommended_status": "STALE_REPORT", "files": stale}, indent=2), encoding="utf-8")

    conclusions = []
    for s in ["AMD", "AMZN", "TSLA", "NVDA", "MU", "GOOGL", "CRM", "META", "AAPL", "VRT"]:
        if s == "AMD": result, mode, next_step = "CONDITIONAL_POSITIVE", "research-only; regime branch unresolved and no production promotion", "owner validation of existing conditional result"
        elif s == "AMZN": result, mode, next_step = "CONDITIONAL_POSITIVE", "research-only; event/premium compensation evidence remains bounded", "owner validation of existing conditional result"
        elif s == "TSLA": result, mode, next_step = "NEGATIVE", "baseline negative; specialized lifecycle/cutover issues remain historical research context", "DO_NOT_USE_BASELINE; no repair strategy"
        elif s == "NVDA": result, mode, next_step = "INSUFFICIENT_DATA", "lifecycle/quote evidence does not support current OOS conclusion", "resolve authoritative data evidence before research"
        else: result, mode, next_step = "UNKNOWN", "no current authoritative ticker-level baseline conclusion consolidated", "no new research until data/replay evidence is available"
        conclusions.append({"ticker": s, "current_baseline_result": result, "known_failure_mode": mode, "supporting_evidence": "existing authoritative research artifacts only" if result != "UNKNOWN" else "UNKNOWN", "stop_behavior": "UNKNOWN", "tail_risk_behavior": "UNKNOWN", "earnings_event_behavior": "UNKNOWN", "structural_entry_behavior": "UNKNOWN", "oos_status": "OOS_EVALUATED" if s in {"AMD", "AMZN", "TSLA", "NVDA"} else "NOT_RUN", "unresolved_questions": "see existing research artifacts; no new inference", "next_allowed_research_step": next_step})
    (OUT / "existing_research_conclusions.json").write_text(json.dumps({"module": "existing_research_conclusion_consolidation", "version": "20260821.v1", "rows": conclusions}, indent=2), encoding="utf-8")

    elig = [{"ticker": x["ticker"], "eligibility": x["eligibility"]} for x in inv]
    (OUT / "production_universe_eligibility.json").write_text(json.dumps({"module": "production_universe_eligibility_classification", "version": "20260821.v1", "production_code_modified": False, "rows": elig}, indent=2), encoding="utf-8")

    current_eligible = [x["ticker"] for x in elig if x["eligibility"] == "PCS_ELIGIBLE_CONDITIONAL"]
    baseline_negative = [x["ticker"] for x in inv if x["classification"] == "NEGATIVE"]
    insufficient = [x["ticker"] for x in inv if x["classification"] == "INSUFFICIENT_DATA"]
    snapshot = f'''# PCS SYSTEM STATUS — 2026-08-20\n\n- Stage 4A: readiness complete; full replay remains controlled/not run.\n- Active production data routes: existing config-routed symbols; SPY/QQQ use isolated `{BASE.as_posix()}` validation artifacts.\n- AMD/NVDA/TSLA/AMZN: AMD and AMZN remain conditional research results; TSLA baseline negative; NVDA insufficient evidence.\n- SPY/QQQ market confirmation: `SPY_QQQ_MARKET_CONFIRMATION`, PIT-safe canonical artifact; no route/logic change.\n- Frozen OOS config: `PCS-SPY-QQQ-OOS-FROZEN-20260821-V1`; Final OOS not run.\n- Current OOS results: SPY validation ROBUST_POSITIVE; QQQ validation NEGATIVE; index baseline FAIL.\n- Core ticker universe count: 25; tested index overlay: SPY, QQQ.\n- Sector balance: {balance["classification"]}; semiconductor concentration {balance["semiconductor_concentration_pct"]}%; tech+communication including semiconductors {balance["tech_plus_communication_concentration_pct"]}%.\n- Current conditionally eligible research tickers: {', '.join(current_eligible) if current_eligible else 'NONE'}\n- Research-only / not-ready tickers: all unpromoted universe members; no production promotion occurred.\n- Next 8 candidates: {', '.join(TOP8)}\n- Major current blocker: non-tech options source/v2 coverage is absent; this is a data onboarding blocker, not a strategy blocker.\n'''
    (ROOT / "PCS_SYSTEM_STATUS_20260821.md").write_text(snapshot, encoding="utf-8")
    morning = f'''# PCS MORNING PRIORITY\n\n1. current ticker count: **25 core + 2 tested index overlay**\n2. OOS-evaluated ticker count: **6** — AMD, AMZN, TSLA, NVDA, SPY, QQQ\n3. currently eligible ticker list: **AMD, AMZN** (conditional research status only)\n4. baseline-negative ticker list: **TSLA, QQQ**\n5. insufficient-data ticker list: **NVDA**\n6. top 8 next non-tech tickers: **{', '.join(TOP8)}**\n7. sector concentration warning: **MODERATELY_CONCENTRATED**; semiconductors 16.0%, tech+semiconductors 44.0%; missing Communication Services, Utilities, Real Estate.\n8. real blockers discovered overnight: **non-tech options source/v2/liquidity evidence absent**.\n9. tomorrow's ONE highest-priority task: **onboard and validate authoritative options source coverage for {TOP8[0]} first, then only proceed if the source gate passes**.\n'''
    (ROOT / "PCS_MORNING_PRIORITY_20260821.md").write_text(morning, encoding="utf-8")
    print(json.dumps({"dashboard": str(OUT / "pcs_universe_dashboard_20260821.json"), "sector_balance": balance, "top8": TOP8, "stale_reports": len(stale), "current_eligible": current_eligible}, indent=2))


if __name__ == "__main__": main()
