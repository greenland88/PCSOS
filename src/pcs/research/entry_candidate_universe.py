"""Research-only reconstruction of a PCS Entry v1.0 candidate universe.

This module deliberately does not call ``select_pair`` or consume selected
trade outputs.  It preserves every observable spread candidate and reports
unavailable production layers instead of inventing replacements.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pcs.data.access import PCSDataAccess

from pcs.entry.context import build_entry_context
from pcs.entry.pullback_gate import evaluate_pullback_gate
from pcs.entry.strike_gate import evaluate_short_strike
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.indicators import calculate_base_indicators
from pcs.trend.models import TrendIndicatorValidationError


FROZEN_SAFE_STRIKE_ATR = 2.3
FROZEN_DTE_MIN = 30
FROZEN_DTE_MAX = 45
FROZEN_CREDIT_WIDTH_MIN = 0.10
_CHAIN_CACHE: dict[tuple[str, str, str], dict[pd.Timestamp, pd.DataFrame]] = {}


@dataclass(frozen=True)
class DryRunSummary:
    ticker: str
    start: str
    end: str
    trading_dates_inspected: int
    dates_with_raw_chains: int
    dates_with_underlying_atr: int
    dates_passing_deterministic_setup: int
    eligible_expirations: int
    short_strikes_before_safe_strike: int
    short_strikes_after_safe_strike: int
    candidates_after_long_leg: int
    candidates_after_liquidity: int
    candidates_after_credit: int
    final_candidate_spreads: int
    unique_entry_opportunities: int
    timestamp_validation: str = "UNAVAILABLE: source has trade dates but no quote timestamps"


def _daily(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    # Known repository daily paths are compatibility inputs only. Resolve
    # their ticker through the canonical access boundary so candidate
    # discovery cannot silently consume a raw export. Temporary fixture paths
    # remain supported for isolated tests/onboarding.
    if "data" in path.parts and ("raw" in path.parts or "parquet" in path.parts):
        stem = path.stem
        ticker = stem.removesuffix("_daily_qfq").upper()
        if ticker and ticker != stem.upper():
            return PCSDataAccess().read_prices(ticker).copy()
    if path.suffix.lower() == ".parquet":
        d = pd.read_parquet(path)
    else:
        d = pd.read_csv(path)
    d = d.rename(columns={"日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low", "收盘价": "close", "成交量": "volume"})
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    return d


def _atr14(d: pd.DataFrame) -> pd.Series:
    prev = d.close.shift(1)
    tr = pd.concat([(d.high - d.low), (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(14, min_periods=14).mean()


def _quote_ok(row: pd.Series) -> bool:
    return pd.notna(row["Bid Price"]) and pd.notna(row["Ask Price"]) and row["Bid Price"] > 0 and row["Ask Price"] >= row["Bid Price"]


def build_historical_setup_context(daily: pd.DataFrame, benchmark: pd.DataFrame | None,
                                  day: object, symbol: str, benchmark_symbol: str,
                                  config: TrendIndicatorConfig | None = None,
                                  precomputed_indicators: pd.DataFrame | None = None) -> dict[str, Any]:
    """Build setup context through the production deterministic functions.

    This is research-only orchestration.  The persisted trend-history artifact
    is intentionally not consulted.  Both inputs are truncated before the
    runtime snapshot is built, providing an explicit point-in-time boundary.
    """
    config = config or TrendIndicatorConfig()
    day = pd.Timestamp(day).normalize()
    stock = daily[daily.date <= day].copy()
    if benchmark is None or benchmark.empty:
        return {"available": False, "reason_codes": ["BENCHMARK_DATA_UNAVAILABLE"]}
    bench = benchmark[benchmark.date <= day].copy()
    if bench.empty:
        return {"available": False, "reason_codes": ["BENCHMARK_DATA_UNAVAILABLE"]}
    if len(stock) < config.sma_long_period or len(bench) < config.sma_long_period:
        return {"available": False, "reason_codes": ["INSUFFICIENT_LOOKBACK"]}
    try:
        indicators = None
        if precomputed_indicators is not None:
            indicators = precomputed_indicators.loc[stock.index]
        snapshot = build_trend_snapshot(stock, bench, config, as_of_date=day,
                                       symbol=symbol, benchmark=benchmark_symbol,
                                       precomputed_indicators=indicators)
        interpretation = interpret_trend(snapshot, config)
        trend_score = score_trend(snapshot, interpretation, config)
        trend_gate = evaluate_trend_gate(trend_score, interpretation, snapshot)
        pullback_gate = evaluate_pullback_gate(trend_gate, snapshot, interpretation)
        # Context construction requires a strike result.  This boundary strike
        # is diagnostic only; candidate strikes are evaluated later by the
        # universe generator and are never selected by this placeholder.
        close = float(snapshot.pullback.current_close)
        atr = float(snapshot.support.current_atr)
        boundary_strike = close - FROZEN_SAFE_STRIKE_ATR * atr
        strike_gate = evaluate_short_strike(boundary_strike, snapshot, interpretation,
                                            trend_gate, pullback_gate, config)
        entry_context = build_entry_context(trend_gate, pullback_gate, strike_gate)
    except (TrendIndicatorValidationError, AttributeError, TypeError, ValueError) as exc:
        return {"available": False, "reason_codes": ["HISTORICAL_CONTEXT_UNAVAILABLE"],
                "error": str(exc)}

    reasons: list[str] = []
    warnings = list(getattr(snapshot, "warnings", ()) or ())
    if not snapshot.available:
        reasons.append("HISTORICAL_CONTEXT_UNAVAILABLE")
    if trend_gate.trend_gate_result == "REJECT":
        reasons.append("TREND_FAIL")
    if getattr(snapshot.market_structure, "breakdown_confirmed", False):
        reasons.append("BREAKDOWN_CONFIRMED")
    if pullback_gate.pullback_gate_result == "WAIT":
        reasons.append("PULLBACK_WAIT")
    elif pullback_gate.pullback_gate_result == "REJECT":
        reasons.append("PULLBACK_REJECT")
    if not getattr(snapshot.cleanliness, "available", False):
        reasons.append("PREDICTABILITY_FAIL")
    support_state = getattr(snapshot.support, "support_confluence_state", None)
    if support_state in {None, "none"}:
        reasons.append("SUPPORT_FAIL")
    if not reasons and entry_context.entry_context_state == "READY":
        reasons.append("SETUP_PASS")
    return {
        "available": bool(snapshot.available), "snapshot": snapshot,
        "interpretation": interpretation, "trend_score": trend_score,
        "trend_gate_result": trend_gate, "pullback_gate_result": pullback_gate,
        "strike_gate_result": strike_gate, "entry_context": entry_context,
        "trend_state": getattr(trend_score, "trend_state", None),
        "pullback_state": getattr(snapshot.pullback, "pullback_state", None),
        "support_state": support_state,
        "predictability_state": getattr(snapshot.cleanliness, "cleanliness_state", None),
        "reason_codes": list(dict.fromkeys(reasons)), "warnings": warnings,
    }


def build_historical_setup_context_table(
    daily: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    dates: pd.Series | list[object],
    symbol: str,
    benchmark_symbol: str,
    config: TrendIndicatorConfig | None = None,
) -> dict[pd.Timestamp, dict[str, Any]]:
    """Build PIT-safe entry-date contexts once for replay and research reuse.

    Only deterministic information available through each date is cached.
    Indicators are computed on the complete bounded frame once; each context
    still receives an as-of slice, preserving the original PIT boundary.
    """
    config = config or TrendIndicatorConfig()
    stock = daily.copy()
    stock["date"] = pd.to_datetime(stock["date"]).dt.normalize()
    stock = stock.sort_values("date").reset_index(drop=True)
    indicators = calculate_base_indicators(stock, config)
    result: dict[pd.Timestamp, dict[str, Any]] = {}
    for raw_day in dates:
        day = pd.Timestamp(raw_day).normalize()
        result[day] = build_historical_setup_context(
            stock, benchmark, day, symbol, benchmark_symbol, config,
            precomputed_indicators=indicators,
        )
    return result


def evaluate_intended_pullback_variant(context: dict[str, Any]) -> dict[str, Any]:
    """Research-only semantic comparator; never used by production eligibility.

    Variant B deliberately reuses existing deterministic states and fields.  It
    does not introduce numeric thresholds: any active MA/swing support is
    usable, while the existing healthy-pullback, trend-health, and structure
    classifications provide the available stabilization evidence.
    """
    tg = context.get("trend_gate_result")
    snapshot = context.get("snapshot")
    interpretation = context.get("interpretation")
    if any(value is None or not getattr(value, "available", False)
           for value in (tg, snapshot, interpretation)):
        return {"result": None, "reason_codes": ["HISTORICAL_CONTEXT_UNAVAILABLE"]}
    pullback = snapshot.pullback
    support = snapshot.support
    gate = getattr(tg, "trend_gate_result", None)
    health = getattr(interpretation, "trend_health", None)
    direction = getattr(interpretation, "trend_direction", None)
    structure = getattr(snapshot.market_structure, "structure_state", None)
    pullback_state = getattr(pullback, "pullback_state", None)
    active_supports = [item for item in getattr(support, "supports", []) if item.get("active")]
    reasons = []
    if gate == "REJECT" or direction == "bearish" or health == "broken":
        return {"result": "REJECT", "reason_codes": ["TREND_OR_BREAKDOWN_REJECT"]}
    if structure in {"bearish", "deteriorating"} or pullback_state == "breakdown":
        return {"result": "REJECT", "reason_codes": ["BREAKDOWN_CONFIRMED"]}
    if pullback_state == "unstable_pullback":
        return {"result": "REJECT", "reason_codes": ["ACCELERATING_OR_UNSTABLE_SELLING"]}
    if gate != "PASS":
        return {"result": "WAIT", "reason_codes": ["TREND_NOT_ACCEPTABLE"]}
    if pullback_state not in {"healthy_pullback", "shallow_pullback"}:
        return {"result": "WAIT", "reason_codes": ["RETRACEMENT_NOT_CONFIRMED"]}
    if not active_supports:
        return {"result": "WAIT", "reason_codes": ["SUPPORT_NOT_INTACT"]}
    if structure != "bullish" or health not in {"strong", "healthy"}:
        return {"result": "WAIT", "reason_codes": ["STABILIZATION_NOT_CONFIRMED"]}
    reasons.extend(["trend_acceptable", pullback_state, "active_support_present",
                    "bullish_structure", "trend_health_not_broken"])
    return {"result": "PASS", "reason_codes": reasons}


def setup_only_validation(tickers: dict[str, tuple[str | Path, str | Path]], start: str,
                          end: str, benchmark_symbols: dict[str, str] | None = None,
                          config: TrendIndicatorConfig | None = None) -> dict[str, Any]:
    """Run deterministic setup validation without reading option chains."""
    benchmark_symbols = benchmark_symbols or {ticker: "QQQ" for ticker in tickers}
    outputs: dict[str, Any] = {}
    for ticker, (daily_path, benchmark_path) in tickers.items():
        stock = _daily(daily_path)
        benchmark = _daily(benchmark_path)
        rows = []
        for day in stock.loc[stock.date.between(start, end), "date"]:
            ctx = build_historical_setup_context(stock, benchmark, day, ticker,
                                                  benchmark_symbols.get(ticker, "QQQ"), config)
            tg = ctx.get("trend_gate_result")
            pg = ctx.get("pullback_gate_result")
            rows.append({"date": str(day.date()), "ticker": ticker,
                         "context_available": ctx.get("available", False),
                         "trend_state": ctx.get("trend_state"),
                         "trend_gate": getattr(tg, "trend_gate_result", None),
                         "pullback_state": ctx.get("pullback_state"),
                         "pullback_gate": getattr(pg, "pullback_gate_result", None),
                         "support_state": ctx.get("support_state"),
                         "predictability_state": ctx.get("predictability_state"),
                         "reason_codes": ctx.get("reason_codes", [])})
        frame = pd.DataFrame(rows)
        outputs[ticker] = {"rows": frame, "summary": {
            "dates_inspected": len(frame),
            "context_available": int(frame.context_available.sum()) if not frame.empty else 0,
            "context_unavailable": int((~frame.context_available).sum()) if not frame.empty else 0,
            "trend_pass": int(frame.trend_gate.eq("PASS").sum()) if not frame.empty else 0,
            "pullback_pass": int(frame.pullback_gate.eq("PASS").sum()) if not frame.empty else 0,
            "pullback_wait": int(frame.pullback_gate.eq("WAIT").sum()) if not frame.empty else 0,
            "pullback_reject": int(frame.pullback_gate.eq("REJECT").sum()) if not frame.empty else 0,
            "setup_pass": int(frame.reason_codes.map(lambda x: "SETUP_PASS" in x).sum()) if not frame.empty else 0,
        }}
    return outputs


def _cached_chains(option_root: str | Path, start: str, end: str) -> tuple[dict[pd.Timestamp, pd.DataFrame], dict[str, Any]]:
    """Load and clean a ticker's active canonical route once, then index by trade date."""
    key = (str(option_root), str(start), str(end))
    if key in _CHAIN_CACHE:
        return _CHAIN_CACHE[key], {"cache_hit": True, "files_opened": 0, "rows_loaded": sum(map(len, _CHAIN_CACHE[key].values()))}
    ticker = Path(option_root).name.upper()
    from .credit_stop import load_quotes_canonical
    cleaned, meta = load_quotes_canonical(ticker, pd.Timestamp(start), pd.Timestamp(end))
    indexed = {day: frame.copy() for day, frame in cleaned.groupby("Trade Date")}
    _CHAIN_CACHE[key] = indexed
    return indexed, {"cache_hit": False, "files_opened": meta.get("quarter_files_opened", 0), "rows_loaded": meta.get("option_rows_loaded", len(cleaned)), "duplicates_removed": meta.get("duplicate_rows_deduped", 0), "ambiguous_excluded": meta.get("ambiguous_quote_rows_excluded", 0)}


def generate_observable_candidates(ticker: str, daily_path: str | Path, option_root: str | Path,
                                   start: str, end: str, trend_history: pd.DataFrame | None = None,
                                   chain_index: dict[pd.Timestamp, pd.DataFrame] | None = None,
                                   benchmark_path: str | Path | None = None,
                                   benchmark_symbol: str = "QQQ",
                                   trend_config: TrendIndicatorConfig | None = None) -> tuple[pd.DataFrame, DryRunSummary]:
    # Universal admission boundary: candidate discovery is strategy research
    # and may not run against an unready ticker.
    from .ticker_readiness import assert_research_ready
    assert_research_ready(ticker)
    daily = _daily(daily_path)
    daily["atr14_point_in_time"] = _atr14(daily)
    dates = daily.loc[daily.date.between(start, end), "date"]
    benchmark = _daily(benchmark_path) if benchmark_path is not None else None
    records: list[dict[str, Any]] = []
    raw_dates = 0; usable_dates = 0; setup_dates = 0; expirations = 0
    before_safe = after_safe = after_long = after_liq = after_credit = 0
    opportunity_dates: set[tuple[str, str]] = set()
    for day in dates:
        dayrow = daily[daily.date.eq(day)].iloc[0]
        atr = dayrow.atr14_point_in_time
        if chain_index is not None:
            chain = chain_index.get(day, pd.DataFrame())
        else:
            from .credit_stop import load_quotes_canonical
            chain = load_quotes_canonical(ticker, day, day)[0]
        if chain.empty:
            continue
        raw_dates += 1
        if pd.isna(atr) or atr <= 0:
            continue
        usable_dates += 1
        context = build_historical_setup_context(daily, benchmark, day, ticker,
                                                  benchmark_symbol, trend_config)
        setup_pass = context.get("available", False) and context.get("entry_context").entry_context_state == "READY"
        if not setup_pass:
            continue
        setup_dates += 1
        puts = chain[chain["Call/Put"].eq("p")].copy()
        puts["DTE"] = (puts["Expiry Date"] - day).dt.days
        expirations += int(puts.loc[puts.DTE.between(FROZEN_DTE_MIN, FROZEN_DTE_MAX), "Expiry Date"].nunique())
        for expiry, exp in puts[puts.DTE.between(FROZEN_DTE_MIN, FROZEN_DTE_MAX)].groupby("Expiry Date"):
            shorts = exp[exp.Strike < float(dayrow.close)].copy()
            before_safe += len(shorts)
            safe = shorts[(float(dayrow.close) - shorts.Strike) / float(atr) >= FROZEN_SAFE_STRIKE_ATR]
            after_safe += len(safe)
            for _, short in safe.iterrows():
                longs = exp[exp.Strike < short.Strike]
                for _, long in longs.iterrows():
                    if not (_quote_ok(short) and _quote_ok(long)):
                        continue
                    after_long += 1
                    short_spread = (short["Ask Price"] - short["Bid Price"]) / ((short["Ask Price"] + short["Bid Price"]) / 2)
                    long_spread = (long["Ask Price"] - long["Bid Price"]) / ((long["Ask Price"] + long["Bid Price"]) / 2)
                    if short["Open Interest"] < 500 or short["Volume"] < 100 or short_spread > .18 or long_spread > .18:
                        continue
                    after_liq += 1
                    credit = float(short["Bid Price"] - long["Ask Price"])
                    width = float(short.Strike - long.Strike)
                    ratio = credit / width if width > 0 else 0.0
                    if credit <= 0 or ratio < FROZEN_CREDIT_WIDTH_MIN:
                        continue
                    after_credit += 1
                    opportunity_dates.add((ticker, str(day.date())))
                    records.append({"date": str(day.date()), "ticker": ticker, "underlying_price": float(dayrow.close), "atr14": float(atr), "trend_state": context.get("trend_state"), "pullback_state": context.get("pullback_state"), "pullback_gate": context["pullback_gate_result"].pullback_gate_result, "trend_gate": context["trend_gate_result"].trend_gate_result, "reason_codes": context.get("reason_codes", []), "expiration": str(expiry.date()), "dte": int((expiry-day).days), "short_strike": float(short.Strike), "long_strike": float(long.Strike), "spread_width": width, "atr_distance": float((dayrow.close-short.Strike)/atr), "short_bid": float(short["Bid Price"]), "short_ask": float(short["Ask Price"]), "short_volume": int(short["Volume"]), "short_oi": int(short["Open Interest"]), "short_delta": float(short["Delta"]) if pd.notna(short["Delta"]) else None, "long_bid": float(long["Bid Price"]), "long_ask": float(long["Ask Price"]), "long_volume": int(long["Volume"]), "long_oi": int(long["Open Interest"]), "credit": credit, "credit_width_ratio": ratio, "event_state": "NOT_INCLUDED", "portfolio_state": "NOT_INCLUDED", "timestamp_validation": "UNAVAILABLE"})
    summary = DryRunSummary(ticker, start, end, len(dates), raw_dates, usable_dates, setup_dates, expirations, before_safe, after_safe, after_long, after_liq, after_credit, len(records), len(opportunity_dates))
    return pd.DataFrame(records), summary


def dry_run(tickers: dict[str, tuple[str | Path, str | Path]], start: str, end: str,
            histories: dict[str, pd.DataFrame] | None = None,
            benchmark_paths: dict[str, str | Path] | None = None) -> dict[str, Any]:
    outputs = {}
    for ticker, (daily_path, option_root) in tickers.items():
        chains, load_meta = _cached_chains(option_root, start, end)
        benchmark_path = (benchmark_paths or {}).get(ticker)
        if benchmark_path is None:
            candidate = Path(daily_path).with_name("QQQ_daily_qfq.csv")
            benchmark_path = candidate if candidate.exists() else None
        candidates, summary = generate_observable_candidates(
            ticker, daily_path, option_root, start, end,
            (histories or {}).get(ticker), chains,
            benchmark_path=benchmark_path,
        )
        outputs[ticker] = {"summary": asdict(summary), "candidates": candidates, "loader": load_meta}
    return outputs
