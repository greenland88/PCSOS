"""Configurable current-strategy replay adapter.

This module is intentionally research-only: rule values are supplied by the
ResearchSpec and production configuration is never mutated.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json
from typing import Any
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.executable_boundary import resolve_executable_start_date
from pcs.entry.contract_v2 import nearby_strikes, later_expirations
from pcs.entry.gates import EventGate, LiquidityGate, RegimeGate, SafeStrikeGate, CreditEfficiencyGate, DTEGate
from pcs.entry.support_contract import SupportState
from pcs.engine.decision_engine import load_rules
from pcs.regime.market_regime import MarketRegimeEngine
from pcs.models.trade import TradeCandidate
from pcs.research.entry_candidate_universe import build_historical_setup_context_table, _atr14
from pcs.research.stage4a_full_replay import canonical_market_state_factory
from pcs.research.stage4a_lifecycle import Stage4ALifecycleReplayAdapter, LifecycleAdapterError
from pcs.research.scheduled_event_calendar import load_calendar
from pcs.research.ticker_readiness import assert_research_ready
from pcs.research.variant_b_replay import ReplayPolicy, summarize_replay, _replay_lifecycle_batch, _load_replay_calendar


def _identity(ticker, day, expiry, short, long):
    raw = "|".join([ticker, str(pd.Timestamp(day).date()), str(pd.Timestamp(expiry).date()), f"{float(short):.15g}", f"{float(long):.15g}"])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _session_horizon_end(start_dates, sessions, count: int) -> pd.Timestamp:
    """Return the latest date covering ``count`` authoritative sessions."""
    calendar = pd.DatetimeIndex(pd.to_datetime(sessions)).normalize().drop_duplicates().sort_values()
    if len(calendar) == 0:
        raise LifecycleAdapterError("SESSION_CALENDAR_UNAVAILABLE")
    ends = []
    for start in pd.to_datetime(start_dates):
        position = int(calendar.searchsorted(pd.Timestamp(start).normalize(), side="left"))
        if position < len(calendar):
            ends.append(calendar[min(position + count, len(calendar) - 1)])
    if not ends:
        raise LifecycleAdapterError("SESSION_CALENDAR_HORIZON_UNAVAILABLE")
    return max(ends)


def _candidate(row, ctx, chain, ticker):
    exp = row["expiration"]; typ = chain[chain.call_put.eq("p")]
    return TradeCandidate(ticker=str(ticker).upper(), expiration=str(pd.Timestamp(exp).date()),
        short_strike=float(row.short_strike), long_strike=float(row.long_strike),
        underlying_price=float(row.close), credit=float(row.credit), dte=int(row.dte),
        short_delta=float(row.short_delta) if pd.notna(row.short_delta) else 0.0,
        expected_move=float(row.expected_move), expected_move_1d=float(row.expected_move),
        support_level=float(ctx["snapshot"].support.nearest_support or row.close),
        option_volume=int(row.short_volume), open_interest=int(row.short_oi),
        bid_ask_pct=float(row.bid_ask_pct), nearby_strikes=int(row.nearby_strikes),
        later_expirations=int(row.later_expirations), business_quality=0.0,
        trend_score=float(getattr(ctx["trend_score"], "score", 0) if hasattr(ctx["trend_score"], "score") else 0),
        support_score=0.0, sector_alignment=0.0, price_confirmation=0.0,
        atr=float(row.atr), long_option_volume=int(row.long_volume),
        long_open_interest=int(row.long_oi), bid=float(row.short_bid), ask=float(row.short_ask),
        long_bid=float(row.long_bid), long_ask=float(row.long_ask), entry_date=str(pd.Timestamp(row.date).date()),
        trend_snapshot=ctx["snapshot"], trend_interpretation=ctx["interpretation"], trend_score_result=ctx["trend_score"])


def build_lifecycle_quote_rows(quotes: pd.DataFrame, candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve exactly one canonical put quote per leg/date."""
    required = {"symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask"}
    missing = required - set(quotes.columns)
    if missing:
        raise LifecycleAdapterError("CANONICAL_QUOTE_SCHEMA_MISSING:" + ",".join(sorted(missing)))
    q = quotes.copy()
    q["trade_date"] = pd.to_datetime(q.trade_date).dt.normalize()
    q["expiration_date"] = pd.to_datetime(q.expiration_date).dt.normalize()
    entry = pd.Timestamp(candidate["date"]).normalize()
    expiry = pd.Timestamp(candidate["expiration"]).normalize()
    q = q[(q.symbol.astype(str).str.upper() == str(candidate["ticker"]).upper()) &
          (q.trade_date > entry) & (q.trade_date <= expiry) &
          (q.expiration_date == expiry) & q.call_put.astype(str).str.lower().eq("p") &
          q.strike.isin([float(candidate["short_strike"]), float(candidate["long_strike"])])].copy()
    rows = []
    for day, group in q.groupby("trade_date", sort=True):
        legs = {}
        for strike in (float(candidate["short_strike"]), float(candidate["long_strike"])):
            leg = group[group.strike.eq(strike)]
            if len(leg) > 1:
                raise LifecycleAdapterError("CANONICAL_PUT_LEG_MATCH_FAILURE:DUPLICATE")
            if len(leg) == 0:
                legs = None
                break
            legs[strike] = leg.iloc[0]
        if legs is None:
            continue
        short = legs[float(candidate["short_strike"])]
        long = legs[float(candidate["long_strike"])]
        rows.append({"ticker": str(candidate["ticker"]).upper(), "candidate_id": candidate["candidate_id"], "option_type": "p",
                     "mark_date": day, "expiration": expiry,
                     "short_strike": float(candidate["short_strike"]), "long_strike": float(candidate["long_strike"]),
                     "short_bid": short.bid, "short_ask": short.ask,
                     "long_bid": long.bid, "long_ask": long.ask})
    if not rows:
        raise LifecycleAdapterError("CANONICAL_PUT_LIFECYCLE_QUOTES_MISSING")
    return rows


def validate_lifecycle_corporate_action(candidate: dict[str, Any], price_basis_service=None) -> None:
    """Fail closed when a lifecycle crosses an action without mapping evidence."""
    if price_basis_service is None:
        return
    action = price_basis_service.crossing_action(candidate["ticker"], candidate["date"], candidate["expiration"])
    if action is not None and not candidate.get("contract_mapping_available", False):
        raise LifecycleAdapterError("CORPORATE_ACTION_CONTRACT_MAPPING_UNAVAILABLE")


def run_current_strategy_replay(spec, *, output_dir: str | Path = "research_outputs", data_access=None, price_basis_service=None) -> dict[str, Any]:
    rules = {"dte_min": 30, "dte_max": 45, "safe_strike_atr": 2.3, "allowed_widths": [5, 10, 2],
             "width_mode": "ALL", "min_credit_width_ratio": .10, "trend_gate": True,
             "pullback_gate": True, "support_gate": True, "regime_gate": False,
             "event_gate": True, "liquidity_gate": True, "predictability_gate": True}
    rules.update(spec.rules)
    # Track A research semantics: the frozen opportunity episode supplies
    # setup eligibility; delayed dates are checked only by execution gates.
    track_a_execution_only = bool(spec.signal_definition.get("track_a_execution_only", False))
    access = data_access or PCSDataAccess()
    assert_research_ready(spec.ticker, access=access)
    configured_start = pd.Timestamp(spec.date_range.get("start")) if spec.date_range.get("start") else None
    boundary_start = pd.Timestamp(resolve_executable_start_date(spec.ticker, access.source_routes))
    requested_start = max(configured_start, boundary_start) if configured_start is not None else boundary_start
    daily_source = access.resolve_source("daily", spec.ticker)
    option_source = access.resolve_source("options", spec.ticker)
    requested_end = pd.Timestamp(spec.date_range["end"]) if spec.date_range.get("end") else pd.Timestamp(daily_source.last_date)
    split_train_end = pd.Timestamp(spec.split_policy["train_end"]) if spec.split_policy.get("train_end") else requested_end
    train_end = min(split_train_end, requested_end, pd.Timestamp(daily_source.last_date))
    execution_dates = spec.signal_definition.get("execution_dates") or []
    for value in execution_dates:
        day = pd.Timestamp(value).normalize()
        if day < requested_start or day > train_end:
            raise ValueError(f"RESEARCH_BOUNDARY_VIOLATION: execution date {day.date()} outside {requested_start.date()}..{train_end.date()}")
    feature_start = max(pd.Timestamp(daily_source.first_date), requested_start - pd.Timedelta(days=300))
    daily = access.read_prices(spec.ticker, start_date=feature_start)
    daily["date"] = pd.to_datetime(daily.date).dt.normalize(); daily = daily.sort_values("date").reset_index(drop=True)
    requested_start = requested_start or pd.Timestamp(daily.date.min())
    # Keep the canonical feature warm-up, but do not load pre-scope history
    # into the replay process. The clean population remains the sole signal
    # population; this is only an I/O boundary for the requested period.
    train_start = max(pd.Timestamp(daily.date.min()), requested_start - pd.Timedelta(days=300))
    train = daily[daily.date.between(train_start, train_end)].copy()
    if execution_dates:
        allowed = {pd.Timestamp(x).normalize() for x in execution_dates}
        execution_date_set = allowed
    benchmark_symbol = spec.signal_definition.get("benchmark_symbol")
    if not benchmark_symbol:
        raise ValueError("SPEC_INCOMPLETE: benchmark_symbol")
    benchmark = access.read_prices(benchmark_symbol, train.date.min(), train.date.max())
    train["atr"] = _atr14(train)
    option_last_date = pd.Timestamp(option_source.last_date)
    if execution_dates:
        # Frozen transfer/replay dates are already fixed by the ResearchSpec.
        # Read only each requested decision window (plus lifecycle horizon)
        # instead of materializing the ticker's entire options history.
        requested = sorted({pd.Timestamp(x).normalize() for x in execution_dates})
        windows = [(day, day) for day in requested]
        opts = access.read_quotes_for_windows(spec.ticker, windows)
        if len(opts):
            key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
            if opts.duplicated(key, keep=False).any():
                raise LifecycleAdapterError("CANONICAL_QUOTE_DUPLICATE_IDENTITY")
    else:
        opts = access.read_quotes(spec.ticker, max(train.date.min(), pd.Timestamp(option_source.first_date)), min(train.date.max(), option_last_date))
    # The canonical reader returns the full options schema.  The replay only
    # needs these fields; retaining Greeks and provenance columns through the
    # per-day grouping otherwise multiplies memory use without changing any
    # strategy calculation.
    opts = opts[["symbol", "trade_date", "expiration_date", "strike", "call_put",
                 "bid", "ask", "delta", "volume", "open_interest"]]
    opts.trade_date = pd.to_datetime(opts.trade_date).dt.normalize(); opts.expiration_date = pd.to_datetime(opts.expiration_date).dt.normalize()
    # Use only the validated executable quote subset from the authoritative
    # clean research population.  Invalid physical rows and same-day expiry
    # records are not eligible contract quotes and must not poison replay.
    opts = opts[opts.bid.notna() & opts.ask.notna() & (opts.bid >= 0) & (opts.ask >= opts.bid)
                & opts.expiration_date.gt(opts.trade_date)].copy()
    by_day = {d: g.copy() for d, g in opts.groupby("trade_date")}
    rules_cfg = load_rules(); gate_rules = json.loads(json.dumps(rules_cfg)); gate_rules["entry"]["hard_dte_min"] = int(rules["dte_min"]); gate_rules["entry"]["hard_dte_max"] = int(rules["dte_max"]); gate_rules["entry"]["safe_strike_atr"] = float(rules["safe_strike_atr"]); gate_rules["entry"]["min_credit_width_ratio"] = float(rules["min_credit_width_ratio"]); market_factory = canonical_market_state_factory(); regime_engine = MarketRegimeEngine(gate_rules)
    calendar = _load_replay_calendar("data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv")
    setup_rows = []; candidates = []; context_rows = []; event_cache = {}; rejected = {k: 0 for k in ["TREND","PULLBACK","SUPPORT","PREDICTABILITY","REGIME","EVENT","DTE","SAFE_STRIKE","LIQUIDITY","CREDIT_WIDTH","LIFECYCLE_QUOTES_MISSING"]}
    feature_ready = 0; setup_eligible = 0; market_state_missing = 0
    context_table = build_historical_setup_context_table(train, benchmark, train.date, spec.ticker, benchmark_symbol)
    broad_new_entry = spec.research_mode.value == "NEW_ENTRY"
    for day in train.date:
        if pd.Timestamp(day) < requested_start:
            continue
        if execution_dates and pd.Timestamp(day).normalize() not in execution_date_set:
            continue
        ctx = context_table[pd.Timestamp(day).normalize()]
        if not ctx.get("available"): continue
        feature_ready += 1
        tg = getattr(ctx.get("trend_gate_result"), "trend_gate_result", None); pg = getattr(ctx.get("pullback_gate_result"), "pullback_gate_result", None)
        clean = getattr(ctx.get("snapshot").cleanliness, "available", False)
        checks = [] if (track_a_execution_only or broad_new_entry) else [("TREND", rules["trend_gate"], tg == "PASS"), ("PULLBACK", rules["pullback_gate"], pg == "PASS"),
                  ("SUPPORT", rules["support_gate"], getattr(ctx.get("snapshot").support, "support_confluence_state", None) in {"moderate", "strong"}),
                  ("PREDICTABILITY", rules["predictability_gate"], clean)]
        failed = False
        for name, enabled, ok in checks:
            if enabled and not ok: rejected[name] += 1; failed = True
        if failed: continue
        setup_eligible += 1
        context_rows.append({"decision_date": day, "setup_date": day, "setup_id": f"{spec.ticker.upper()}_{day.date().isoformat()}",
                             "entry_type": "first_entry", **{k: getattr(ctx.get("snapshot"), k, None) for k in []},
                             "trend_state": ctx.get("trend_state"),
                             "trend_gate": tg, "trend_score": getattr(ctx.get("trend_score"), "score", None),
                             "pullback_state": ctx.get("pullback_state"),
                             "pullback_gate": pg, "setup_state": ctx.get("pullback_state"),
                             "support_state": getattr(ctx.get("snapshot").support, "support_confluence_state", None),
                             "support_level": getattr(ctx.get("snapshot").support, "nearest_support", None),
                             "support_distance_atr": getattr(ctx.get("snapshot").support, "support_distance_atr", None),
                             "support_strength": getattr(ctx.get("snapshot").support, "support_strength", None),
                             "predictability_state": ctx.get("predictability_state"),
                             "predictability_available": clean, "market_regime": None, "market_regime_available": False})
        chain = by_day.get(day, pd.DataFrame())
        if chain.empty: continue
        close = float(train.loc[train.date.eq(day), "close"].iloc[0]); atr = float(train.loc[train.date.eq(day), "atr"].iloc[0]) if pd.notna(train.loc[train.date.eq(day), "atr"].iloc[0]) else None
        if not atr or atr <= 0: continue
        puts = chain[chain.call_put.eq("p")].copy(); puts["dte"] = (puts.expiration_date - day).dt.days
        for exp, group in puts[puts.dte.between(int(rules["dte_min"]), int(rules["dte_max"]))].groupby("expiration_date"):
            for _, short in group.iterrows():
                comparison_short = (price_basis_service.to_comparison_strike(spec.ticker, day, short.strike)
                                    if price_basis_service is not None else float(short.strike))
                if (close - comparison_short) / atr < float(rules["safe_strike_atr"]): continue
                for width in [float(x) for x in rules["allowed_widths"]]:
                    long = group[group.strike.eq(float(short.strike) - width)]
                    if long.empty: continue
                    long = long.iloc[0]
                    credit = float(short.bid - long.ask); ratio = credit / width if width else 0
                    if credit <= 0 or ratio < float(rules["min_credit_width_ratio"]): rejected["CREDIT_WIDTH"] += 1; continue
                    rec = {"date": day, "ticker": spec.ticker, "close": close, "atr": atr, "expiration": exp, "short_strike": float(short.strike), "comparison_short_strike": comparison_short, "long_strike": float(long.strike), "dte": int((exp-day).days), "short_delta": short.get("delta", 0), "credit": credit, "spread_width": width, "short_bid": float(short.bid), "short_ask": float(short.ask), "long_bid": float(long.bid), "long_ask": float(long.ask), "short_volume": int(short.volume), "short_oi": int(short.open_interest), "long_volume": int(long.volume), "long_oi": int(long.open_interest), "bid_ask_pct": float((short.ask-short.bid)/max((short.ask+short.bid)/2, 1e-12)), "nearby_strikes": nearby_strikes(chain, exp, "p", short.strike), "later_expirations": later_expirations(chain, exp, "p",), "expected_move": atr}
                    tc = _candidate(pd.Series(rec), ctx, chain, spec.ticker)
                    tc.trading_sessions = train.date
                    gate_results = []
                    if int(rules["dte_min"]) > rec["dte"] or int(rules["dte_max"]) < rec["dte"]:
                        rejected["DTE"] += 1; continue
                    try:
                        market_state = market_factory({"date": day})
                        regime, _, _ = regime_engine.classify(market_state)
                        if rules["regime_gate"]:
                            gate_results.append(RegimeGate(gate_rules).evaluate(regime, ctx["entry_context"]))
                    except ValueError as exc:
                        if "MARKET_STATE_PIT_UNAVAILABLE" not in str(exc): raise
                        market_state_missing += 1
                        if rules["regime_gate"]: raise
                    event_gate_available = bool(calendar.attrs.get("event_data_available") and calendar.attrs.get("event_pit_verified"))
                    if rules["event_gate"] and event_gate_available:
                        event_key = (pd.Timestamp(day).normalize(), pd.Timestamp(exp).normalize())
                        if event_key not in event_cache:
                            event_cache[event_key] = EventGate().evaluate(tc, calendar)
                        gate_results.append(event_cache[event_key])
                    gate_results.append(DTEGate(gate_rules).evaluate(tc))
                    gate_results.append(SafeStrikeGate(gate_rules, price_basis_service).evaluate(tc))
                    if rules["liquidity_gate"]: gate_results.append(LiquidityGate(gate_rules).evaluate(tc))
                    gate_results.append(CreditEfficiencyGate(gate_rules).evaluate(tc))
                    if any(g.status.value == "FAIL" for g in gate_results):
                        for g in gate_results:
                            if g.status.value == "FAIL": rejected["REGIME" if g.gate == "regime" else "EVENT" if g.gate == "event" else "SAFE_STRIKE" if g.gate == "safe_strike" else "LIQUIDITY" if g.gate == "liquidity" else "CREDIT_WIDTH"] += 1
                        continue
                    rec["candidate_id"] = _identity(spec.ticker, day, exp, short.strike, long.strike); rec["initial_credit"] = credit; candidates.append(rec)
    frame = pd.DataFrame(candidates)
    if broad_new_entry and len(frame):
        width_order = {float(width): rank for rank, width in enumerate(rules["allowed_widths"])}
        frame["_width_rank"] = frame["spread_width"].map(width_order).fillna(999)
        frame = (frame.sort_values(["date", "_width_rank", "dte", "credit"], ascending=[True, True, True, False])
                      .drop_duplicates("date", keep="first").drop(columns=["_width_rank"]))
    # Build only requested lifecycle quotes through PCSDataAccess, then use the approved adapter.
    life_rows = []
    lifecycle_quotes = pd.DataFrame()
    if len(frame):
        lifecycle_quote_end = min(
            _session_horizon_end(frame["date"], train.date, ReplayPolicy().max_quote_days),
            option_last_date,
        )
        lifecycle_quotes = access.read_quotes(
            spec.ticker,
            frame["date"].min(),
            lifecycle_quote_end,
        )
        key = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
        if lifecycle_quotes.duplicated(key, keep=False).any():
            raise LifecycleAdapterError("CANONICAL_QUOTE_DUPLICATE_IDENTITY")
        lifecycle_quotes = lifecycle_quotes[["symbol", "trade_date", "expiration_date",
                                             "strike", "call_put", "bid", "ask"]]
        lifecycle_quotes["trade_date"] = pd.to_datetime(lifecycle_quotes["trade_date"]).dt.normalize()
        lifecycle_quotes["expiration_date"] = pd.to_datetime(lifecycle_quotes["expiration_date"]).dt.normalize()
    eligible_candidate_ids = set()
    for r in frame.to_dict("records"):
        quote_end = min(
            _session_horizon_end([r["date"]], train.date, ReplayPolicy().max_quote_days),
            pd.Timestamp(r["expiration"]),
        )
        q = lifecycle_quotes[(lifecycle_quotes.trade_date >= pd.Timestamp(r["date"])) &
                             (lifecycle_quotes.trade_date <= quote_end) &
                             (lifecycle_quotes.expiration_date == pd.Timestamp(r["expiration"])) &
                             (lifecycle_quotes.strike.isin([r["short_strike"], r["long_strike"]]))]
        try:
            validate_lifecycle_corporate_action(r, price_basis_service)
            life_rows.extend(build_lifecycle_quote_rows(q, r))
            eligible_candidate_ids.add(str(r["candidate_id"]))
        except LifecycleAdapterError as exc:
            if str(exc) == "CORPORATE_ACTION_CONTRACT_MAPPING_UNAVAILABLE":
                continue
            if str(exc) == "CANONICAL_PUT_LIFECYCLE_QUOTES_MISSING":
                # A missing lifecycle series invalidates this candidate only;
                # preserve the canonical fail-closed lifecycle admission while
                # allowing other valid selected contracts to complete replay.
                rejected["LIFECYCLE_QUOTES_MISSING"] += 1
                continue
            raise
    adapter = Stage4ALifecycleReplayAdapter(pd.DataFrame(life_rows), ReplayPolicy()) if life_rows else None
    results = []
    for r in frame.to_dict("records"):
        if adapter is None or str(r["candidate_id"]) not in eligible_candidate_ids: continue
        x = adapter(r); results.append({**r, **x})
    result_frame = pd.DataFrame(results); out = Path(output_dir) / spec.research_id; out.mkdir(parents=True, exist_ok=True)
    if len(frame): frame.to_parquet(out / "candidates.parquet", index=False)
    if len(result_frame): result_frame.to_parquet(out / "lifecycle_results.parquet", index=False)
    if context_rows: pd.DataFrame(context_rows).drop_duplicates("decision_date").to_parquet(out / "entry_context.parquet", index=False)
    summary = summarize_replay(result_frame) if len(result_frame) else pd.DataFrame()
    metrics = summary.iloc[0].to_dict() if len(summary) else {}
    yearly_metrics = []
    if len(result_frame):
        result_frame["entry_year"] = pd.to_datetime(result_frame["entry_date"], errors="coerce").dt.year
        for year, group in result_frame.groupby("entry_year", dropna=True, sort=True):
            pnl = pd.to_numeric(group["realized_pnl"], errors="coerce").dropna()
            wins = pnl[pnl > 0]
            losses = pnl[pnl < 0]
            yearly_metrics.append({
                "year": int(year), "trade_count": int(len(pnl)),
                "total_realized_pnl": float(pnl.sum()),
                "expectancy": float(pnl.mean()) if len(pnl) else None,
                "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() else None,
                "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
                "stop_rate": float(group["stop_triggered"].fillna(False).astype(bool).mean()),
            })
    (out / "yearly_metrics.json").write_text(json.dumps(yearly_metrics, indent=2), encoding="utf-8")
    decision_days = train[train.date >= requested_start]
    final_oos_start = spec.split_policy.get("final_oos_start") or spec.split_policy.get("final_oos", {}).get("start")
    max_read_date = pd.to_datetime(opts.trade_date).max() if len(opts) else None
    final_oos_read = bool(final_oos_start and max_read_date is not pd.NaT and pd.notna(max_read_date) and max_read_date >= pd.Timestamp(final_oos_start))
    event_available = bool(calendar.attrs.get("event_data_available"))
    event_pit_verified = bool(calendar.attrs.get("event_pit_verified"))
    event_gate_applied = bool(rules["event_gate"] and event_available and event_pit_verified)
    report = {"module":"pcs.research.current_strategy_replay", "version":"1.1", "ticker":spec.ticker, "research_mode":spec.research_mode.value, "population_semantics":"FULL_PIT_FEATURE_READY_CALENDAR" if spec.research_mode.value == "NEW_ENTRY" else "LEGACY_CURRENT_STRATEGY", "old_strategy_reference":"FORBIDDEN_FOR_NEW_ENTRY" if spec.research_mode.value == "NEW_ENTRY" else "LEGACY_RESEARCH_ONLY", "rules":rules, "event_data_available":event_available, "event_pit_verified":event_pit_verified, "event_gate_applied":event_gate_applied, "event_gate_result":"APPLIED" if event_gate_applied else "NOT_AVAILABLE", "event_reason":calendar.attrs.get("event_reason", "NO_TICKER_EVENT_ROWS"), "funnel":{"TRADING_DAYS":len(decision_days),"FEATURE_READY_DAYS":feature_ready,"SETUP_ELIGIBLE_DAYS":setup_eligible,"CONTRACT_CANDIDATES":len(frame),"SELECTED_ENTRIES":len(frame),"LIFECYCLES_COMPLETED":len(result_frame), **{f+"_REJECTED":v for f,v in rejected.items()}}, "market_state_missing_count":market_state_missing, "regime_used_as_blocker":bool(rules["regime_gate"]), "metrics":metrics, "max_options_read_date":str(max_read_date.date()) if pd.notna(max_read_date) else None,"final_oos_read":final_oos_read,"old_474_used_as_input":False,"production_logic_changed":False,"production_config_changed":False,"frozen_artifact_changed":False}
    (out / "replay_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
