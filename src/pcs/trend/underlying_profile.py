"""Causal descriptive measurements. Pure public API; no data or provider reads.

TA-Lib ATR preserves the production indicator definition, with an explicitly
bounded seed. The legacy adaptive research resolver is deliberately untouched.
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

from pcs.analysis_contracts import CapabilityStatus, ExecutionStatus, SourceStatus
from pcs.trend.relative_strength import _safe_return
from pcs.trend.selection_models import (
    DailyFeatureView, DrawdownEpisode, DrawdownObservation, GapObservation,
    ProfileCoverage, ProfileInput, ProfileMetric, ProfilePolicy, ProfileTimeContext,
    SurvivalPoint, UnderlyingProfile,
)


def content_id(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def profile_sessions(as_of: str, policy: ProfilePolicy, calendar: str = "XNYS") -> list[str]:
    """Declare the merged observation/prefix/ATR history before reading data."""
    import exchange_calendars as xc
    cal = xc.get_calendar(calendar)
    if not cal.is_session(as_of):
        raise ValueError("PROFILE_ASOF_NOT_SESSION")
    end = cal.sessions.get_loc(pd.Timestamp(as_of))
    start = end - policy.required_sessions + 1
    if start < 0:
        raise ValueError("PROFILE_CALENDAR_COVERAGE_MISSING")
    return [str(s.date()) for s in cal.sessions[start:end + 1]]


def _finite(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def _frame(view: DailyFeatureView, sessions: list[str], symbol: str):
    if view.symbol != symbol:
        raise ValueError("PROFILE_SYMBOL_MISMATCH")
    if not view.price_basis or not view.corporate_action_version:
        raise ValueError("PROFILE_PRICE_IDENTITY_MISSING")
    if view.input_kind == "VERIFIED_CANONICAL":
        if not view.source.validated or not view.source.sha256 or not view.source.record_identity:
            raise ValueError("PROFILE_UNVERIFIED_SOURCE")
    elif view.source.source_kind != "TEST":
        raise ValueError("PROFILE_TEST_IDENTITY_REQUIRED")
    # Exclude future/past-outside-prefix rows BEFORE numeric checks and indicators.
    bars = [b for b in view.bars if sessions[0] <= b.session.isoformat() <= sessions[-1]]
    dates = [b.session.isoformat() for b in bars]
    if dates != sorted(set(dates)):
        raise ValueError("PROFILE_DATES_NOT_UNIQUE_SORTED")
    if any(s not in sessions for s in dates):
        raise ValueError("PROFILE_NON_SESSION_BAR")
    records = [{"session": b.session.isoformat(), **{k: _finite(getattr(b, k))
                for k in ("open", "high", "low", "close", "volume")}} for b in bars]
    frame = pd.DataFrame(records, columns=["session", "open", "high", "low", "close", "volume"])
    frame = frame.set_index("session").reindex(sessions).astype(float)
    invalid = {}
    for field in ("open", "high", "low", "close", "volume"):
        bad = frame[field].isna() | (frame[field] < 0 if field == "volume" else frame[field] <= 0)
        invalid[field] = frame.index[bad].tolist()
        frame.loc[bad, field] = np.nan
    bad_ohlc = ((frame.high < frame.low) | (frame.high < frame.open) |
                (frame.high < frame.close) | (frame.low > frame.open) | (frame.low > frame.close))
    if bad_ohlc.any():
        # Corrupt range cannot supply ATR; independently valid closes still supply returns.
        for field in ("high", "low"):
            invalid[field] = sorted(set(invalid[field]) | set(frame.index[bad_ohlc]))
            frame.loc[bad_ohlc, field] = np.nan
    return frame, dates, invalid, content_id(records)


def _atr(frame, period):
    import talib
    values = np.full(len(frame), np.nan)
    good = frame[["high", "low", "close"]].notna().all(axis=1).to_numpy()
    seeds = []
    start = 0
    while start < len(frame):
        if not good[start]:
            start += 1
            continue
        end = start + 1
        while end < len(frame) and good[end]:
            end += 1
        seeds.append(str(frame.index[start]))
        if end - start > period:
            block = frame.iloc[start:end]
            values[start:end] = talib.ATR(*(block[k].to_numpy(dtype=float)
                                           for k in ("high", "low", "close")), timeperiod=period)
        start = end
    return pd.Series(values, index=frame.index), seeds


def _episodes(close, policy, observation_start, symbol):
    """One outstanding episode; a rolling peak can never close it.

    Prefix/unknown-onset events remain visible but are excluded from duration
    estimates. Missing closes censor the ongoing event; recovery cannot be
    asserted across unobserved sessions.
    """
    active = None
    result = []
    segment_start = 0
    boundary_unknown = True

    def finish(end, recovery=None, gap=False):
        nonlocal active
        if active is None:
            return
        start = active["index"]
        left = active["uncertain"] or start < observation_start
        if end >= observation_start:
            elapsed = end - start
            result.append(DrawdownEpisode(
                episode_id="sha256:" + content_id([symbol, str(close.index[start]), active["peak"], policy.model_dump()]),
                start_session=None if active["uncertain"] else str(close.index[start]),
                first_observed_session=str(close.index[max(start, observation_start)]),
                frozen_peak_session=str(close.index[active["peak_index"]]), frozen_peak_close=active["peak"],
                peak_history_complete=not active["uncertain"],
                trough_session=str(close.index[active["trough_index"]]), trough_close=active["trough"],
                max_depth=1 - active["trough"] / active["peak"],
                recovered=recovery is not None, recovery_session=recovery,
                observed_sessions=end - max(start, observation_start),
                recovery_sessions=elapsed if recovery is not None and not left else None,
                left_truncated=left, right_censored=recovery is None,
                censor_session=str(close.index[end]) if recovery is None else None,
                reason_codes=(["LEFT_TRUNCATED"] if left else []) +
                             (["RIGHT_CENSORED"] if recovery is None else []) +
                             (["MISSING_CLOSE_INTERRUPTS_FOLLOWUP"] if gap else []),
            ))
        active = None

    for i, price in enumerate(close):
        if pd.isna(price):
            finish(i - 1, gap=True)
            segment_start = i + 1
            boundary_unknown = True
            continue
        if active is not None:
            if price < active["trough"]:
                active["trough"], active["trough_index"] = float(price), i
            if price >= active["peak"]:
                finish(i, str(close.index[i]))
                boundary_unknown = False
            continue
        if i == segment_start:
            continue
        prefix = close.iloc[max(segment_start, i - policy.peak_lookback):i]
        peak_index = close.index.get_loc(prefix.idxmax())
        peak = float(prefix.max())
        complete = len(prefix) == policy.peak_lookback
        # A full known prefix ending in a new high provides a clean local origin.
        if complete and price >= peak:
            boundary_unknown = False
        if price <= peak * (1 - policy.episode_threshold):
            active = {"index": i, "peak": peak, "peak_index": peak_index,
                      "trough": float(price), "trough_index": i,
                      "uncertain": not complete or boundary_unknown}
    finish(len(close) - 1)
    return result


def _survival(episodes):
    cohort = [e for e in episodes if not e.left_truncated]
    points = []
    survival = 1.0
    for t in sorted({e.observed_sessions for e in cohort}):
        risk = sum(e.observed_sessions >= t for e in cohort)
        recovered = sum(e.observed_sessions == t and e.recovered for e in cohort)
        censored = sum(e.observed_sessions == t and not e.recovered for e in cohort)
        survival *= 1 - recovered / risk
        points.append(SurvivalPoint(elapsed_sessions=t, at_risk=risk, recoveries=recovered,
                                    censored=censored, survival=survival))
    return points


def measure_underlying_profile(input: ProfileInput) -> UnderlyingProfile:
    """Measure independently from a trusted, PIT-bounded DailyFeatureView."""
    import exchange_calendars as xc
    ctx, policy = input.call_context, input.effective_policy
    day = ctx.effective_daily_session
    if day is None:
        raise ValueError("PROFILE_EFFECTIVE_SESSION_REQUIRED")
    cal = xc.get_calendar(input.calendar)
    sessions = profile_sessions(day, policy, input.calendar)
    request = pd.Timestamp(ctx.requested_as_of)
    close_time = pd.Timestamp(cal.session_close(day))
    if request.tzinfo is None:
        if len(ctx.requested_as_of) != 10 or ctx.mode != "HISTORICAL":
            raise ValueError("PROFILE_REQUEST_TIMEZONE_REQUIRED")
        if request.date() < pd.Timestamp(day).date():
            raise ValueError("PROFILE_FUTURE_EVIDENCE")
    elif request < close_time:
        raise ValueError("PROFILE_DAILY_BAR_NOT_COMPLETED")
    frame, actual, invalid, input_hash = _frame(input.feature_view, sessions, ctx.symbol)
    obs_start = len(sessions) - policy.analysis_sessions
    observation = sessions[obs_start:]
    benchmark = None
    benchmark_hash = None
    if input.benchmark is not None:
        if input.benchmark.price_basis != input.feature_view.price_basis:
            raise ValueError("PROFILE_BENCHMARK_PRICE_BASIS_MISMATCH")
        benchmark, _, _, benchmark_hash = _frame(input.benchmark, sessions, input.benchmark.symbol)
    atr, seeds = _atr(frame, policy.atr_period)
    measures = []
    refs = [input.feature_view.source.source_id]

    def metric(name, value, unit, formula, window, count, *, reasons=(), reference=None, parameters=None,
               partial=False, coverage=None):
        value = _finite(value)
        missing = value is None
        status = CapabilityStatus.MISSING if missing else CapabilityStatus.PARTIAL if partial else CapabilityStatus.COMPLETED
        measures.append(ProfileMetric(
            metric_id=name, value=value, unit=unit,
            source_status=SourceStatus.MISSING if missing else SourceStatus.DERIVED,
            execution_status=ExecutionStatus.BLOCKED if missing else ExecutionStatus.EXECUTED,
            producer="pcs.trend.underlying_profile", source_field=None, data_time=day,
            definition_ref=f"underlying-profile-v1:{name}", formula=formula,
            window_sessions=window, sample_count=int(count), evidence_refs=reference or refs,
            status=status, as_of=day, window_start=sessions[-window], window_end=day,
            coverage=min(1.0, count / window) if coverage is None else coverage,
            parameters=parameters or {}, reason_codes=list(reasons) if missing or partial else [],
        ))

    dollars = (frame.close * frame.volume).iloc[-20:]
    currency = input.feature_view.currency
    metric("dollar_volume_median_20", dollars.median() if dollars.notna().all() and currency else None,
           f"{currency or 'UNKNOWN_CURRENCY'}/session", "median(close * volume); price/share * shares/session", 20,
           dollars.notna().sum(), reasons=["VOLUME_OR_PRICE_MISSING"] if currency else ["CURRENCY_NOT_RECORDED"])
    returns = frame.close.pct_change(fill_method=None)
    for w in (20, 60):
        r = returns.iloc[-w:]
        metric(f"realized_volatility_{w}", r.std(ddof=1) * math.sqrt(policy.annualization_sessions)
               if r.notna().all() else None, "annualized_fraction", "std(simple close returns, ddof=1) * sqrt(annualization_sessions)",
               w, r.notna().sum(), reasons=["INSUFFICIENT_RETURN_HISTORY"],
               parameters={"annualization_sessions": policy.annualization_sessions, "ddof": 1})
        rs = None
        count = 0
        if benchmark is not None:
            aligned = pd.concat([frame.close.iloc[-w-1:], benchmark.close.iloc[-w-1:]], axis=1)
            count = max(0, int(aligned.notna().all(axis=1).sum()) - 1)
            if aligned.notna().all().all():
                rs = _safe_return(frame.close.iloc[-1], frame.close.iloc[-w-1]) - _safe_return(
                    benchmark.close.iloc[-1], benchmark.close.iloc[-w-1])
        metric(f"relative_strength_{w}", rs, "return_difference_fraction", "stock_close_return(w) - benchmark_close_return(w); same exchange sessions",
               w, count, reasons=input.benchmark_reason_codes or ["BENCHMARK_MISSING" if benchmark is None else "BENCHMARK_OR_CLOSE_WINDOW_MISSING"],
               reference=refs + ([input.benchmark.source.source_id] if input.benchmark else []),
               parameters={"benchmark": input.benchmark.symbol if input.benchmark else "MISSING"})
    ratio = atr.iloc[-1] / frame.close.iloc[-1]
    metric("atr_over_price", ratio, "fraction", "TA-Lib Wilder ATR(period) / current close", 1,
           int(pd.notna(ratio)), reasons=["ATR_WARMUP_OR_PRICE_MISSING"], parameters={"atr_period": policy.atr_period})

    gaps = []
    previous_close, previous_atr = frame.close.shift(1), atr.shift(1)
    for i in range(obs_start, len(frame)):
        p, a, o = previous_close.iloc[i], previous_atr.iloc[i], frame.open.iloc[i]
        valid = pd.notna(p) and pd.notna(a) and a > 0 and pd.notna(o)
        gaps.append(GapObservation(session=sessions[i], previous_session=sessions[i-1],
            previous_close=_finite(p), open=_finite(o), atr_previous_session=_finite(a),
            up_gap_atr=max(0, o-p)/a if valid else None,
            down_gap_atr=max(0, p-o)/a if valid else None,
            reason_codes=[] if valid else ["PREVIOUS_ATR_OR_GAP_INPUT_MISSING"]))
    for w in sorted({20, 60, policy.analysis_sessions}):
        for direction in ("up", "down"):
            values = pd.Series([getattr(g, f"{direction}_gap_atr") for g in gaps[-w:]], dtype=float)
            count = int(values.notna().sum())
            for q in (0.5, 0.9, 0.95):
                metric(f"gap_{direction}_q{int(q*100)}_{w}", values.quantile(q) if count == w else None,
                       "ATR_previous_session", f"quantile(max(0, {'open-previous_close' if direction == 'up' else 'previous_close-open'}) / previous_ATR, q); includes zero gaps",
                       w, count, reasons=["INCOMPLETE_GAP_WINDOW"], parameters={"q": q, "atr_period": policy.atr_period})
            for threshold in (1.0, 1.5):
                for kind in ("count", "frequency"):
                    value = int(values.ge(threshold).sum()) if kind == "count" else float(values.ge(threshold).mean())
                    metric(f"gap_{direction}_ge_{threshold:g}_{kind}_{w}", value if count == w else None,
                           "sessions" if kind == "count" else "fraction", f"{'count' if kind == 'count' else 'mean'}(directional_gap_atr >= threshold)",
                           w, count, reasons=["INCOMPLETE_GAP_WINDOW"], parameters={"threshold_atr": threshold})

    peak = frame.close.shift(1).rolling(policy.peak_lookback, min_periods=policy.peak_lookback).max()
    depth = (1 - frame.close / peak).clip(lower=0)
    depths = depth.iloc[obs_start:]
    observations = [DrawdownObservation(session=s, close=_finite(frame.close.loc[s]),
                    reference_peak_close=_finite(peak.loc[s]), depth=_finite(depth.loc[s])) for s in observation]
    for q in (0.5, 0.9, 0.95):
        metric(f"drawdown_depth_q{int(q*100)}", depths.quantile(q) if depths.notna().all() else None,
               "fraction", "quantile(max(0, 1-close/max(previous peak_lookback closes)), q); includes zero depths",
               policy.analysis_sessions, depths.notna().sum(), reasons=["INCOMPLETE_DRAWDOWN_WINDOW"],
               parameters={"q": q, "peak_lookback": policy.peak_lookback})
    metric("current_rolling_drawdown", depth.iloc[-1], "fraction", "max(0, 1-close/max(previous peak_lookback closes)); NOT episode recovery",
           policy.peak_lookback, frame.close.iloc[-policy.peak_lookback-1:-1].notna().sum(),
           reasons=["PEAK_OR_CLOSE_MISSING"], parameters={"peak_lookback": policy.peak_lookback})
    metric("current_drawdown_percentile", float(depths.le(depth.iloc[-1]).mean()) if depths.notna().all() else None,
           "fraction", "count(historical rolling depth <= current rolling depth) / observation sessions",
           policy.analysis_sessions, depths.notna().sum(), reasons=["INCOMPLETE_DRAWDOWN_WINDOW"])
    episodes = _episodes(frame.close, policy, obs_start, ctx.symbol)
    survival = _survival(episodes)
    recovered = [e.recovery_sessions for e in episodes if e.recovery_sessions is not None]
    observed_coverage = float(frame.close.iloc[obs_start:].notna().mean())
    metric("recovered_conditional_median", float(np.median(recovered)) if recovered else None,
           "trading_sessions", "median(trigger-to-first-close>=frozen_peak duration | observed recovered, not left truncated)",
           policy.analysis_sessions, len(recovered), reasons=["NO_COMPLETE_RECOVERY_SAMPLES"],
           parameters={"sample_unit": "recovered_episodes"}, coverage=observed_coverage)
    km_median = next((p.elapsed_sessions for p in survival if p.survival <= .5), None)
    metric("recovery_km_median", km_median, "trading_sessions", "first k where product(1-recovered_at_k/at_risk_at_k)<=0.5; recovery before censor at ties; excludes left truncated",
           policy.analysis_sessions, sum(not e.left_truncated for e in episodes), reasons=["KM_MEDIAN_NOT_IDENTIFIABLE"],
           parameters={"sample_unit": "non_left_truncated_episodes"}, coverage=observed_coverage)
    for name, value in (("unrecovered_count", sum(not e.recovered for e in episodes)),
                        ("left_truncated_count", sum(e.left_truncated for e in episodes)),
                        ("episode_count", len(episodes))):
        metric(name, value if observed_coverage else None, "episodes", "count(observed episodes matching field); actual covered history only",
               policy.analysis_sessions, len(episodes), partial=observed_coverage < 1,
               reasons=["INCOMPLETE_EPISODE_OBSERVATION_WINDOW"], coverage=observed_coverage,
               parameters={"sample_unit": "episodes", "threshold_fraction": policy.episode_threshold,
                           "peak_lookback": policy.peak_lookback})
    reasons = sorted({code for m in measures for code in m.reason_codes})
    if len(actual) < len(sessions):
        reasons.append("PROFILE_HISTORY_PARTIAL")
    if any(e.left_truncated for e in episodes):
        reasons.append("LEFT_TRUNCATED_EPISODES_EXCLUDED_FROM_RECOVERY")
    status = CapabilityStatus.PARTIAL if reasons else CapabilityStatus.COMPLETED
    if not actual:
        status = CapabilityStatus.MISSING
    provenance = [input.feature_view.source] + ([input.benchmark.source] if input.benchmark else [])
    result = UnderlyingProfile(symbol=ctx.symbol, as_of=ctx.requested_as_of, status=status, profile_status=status,
        data_timestamp=input.feature_view.source_timestamp, run_id=ctx.run_id, request_id=ctx.request_id,
        result_id="pending", reason_codes=reasons, call_context=ctx,
        time_context=ProfileTimeContext(requested_as_of=ctx.requested_as_of, effective_daily_session=day,
            calendar=input.calendar, exchange_timezone=str(cal.tz), completed_daily_bar=day in actual,
            known_at_lower_bound=close_time.isoformat(), source_timestamp=input.feature_view.source_timestamp,
            received_at=input.feature_view.received_at),
        effective_policy=policy, policy_sha256=content_id(policy.model_dump()), measurements=measures,
        episodes=episodes, gap_observations=gaps, drawdown_observations=observations, recovery_survival=survival,
        coverage=ProfileCoverage(calendar=input.calendar, requested_sessions=sessions, analysis_sessions=observation,
            actual_sessions=actual, missing_sessions=sorted(set(sessions)-set(actual)),
            missing_analysis_sessions=sorted(set(observation)-set(actual)), prefix_required=obs_start,
            prefix_available=sum(s < observation[0] for s in actual), indicator_seed_start=seeds[0] if seeds else None,
            atr_segment_seeds=seeds, legal_input_sha256=input_hash, benchmark_input_sha256=benchmark_hash,
            invalid_fields=invalid), provenance=provenance,
        explanation="描述历史波动、跳空与回撤；不产生交易评分或开仓许可。恢复中位数仅描述有完整起点的样本，缺失值不使用默认值。")
    semantic = result.model_dump(mode="json", exclude={"run_id", "request_id", "result_id", "call_context", "provenance"})
    # Content identity excludes invocation and physical paths, includes source content identities.
    semantic["sources"] = [(s.source_kind, s.sha256, s.record_identity) for s in provenance]
    return result.model_copy(update={"result_id": "sha256:" + content_id(semantic)})


__all__ = ["ProfileInput", "UnderlyingProfile", "measure_underlying_profile", "profile_sessions"]
