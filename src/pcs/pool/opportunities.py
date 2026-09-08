"""Read-only adapter and deterministic exports for v2 entry opportunities."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import subprocess

import pandas as pd

from pcs.analysis_contracts import CallContext
from pcs.pool.artifacts import _write_atomic
from pcs.pool.underlying_profiles import ProfileDataReader
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.trend.market_structure import analyze_market_structure
from pcs.trend.snapshot import build_trend_snapshot
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.entry.pullback_gate import evaluate_pullback_gate
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.pullback import analyze_pullback
from pcs.trend.selection_models import (
    EntryOpportunity, OpportunityFeatureBar, OpportunityFeatureView,
    OpportunityInput, OpportunityPolicy, OpportunityStateCheckpoint,
    OpportunitySupportFact, SupportFeatureBar, SupportFeatureView,
    SupportZoneInput, SupportZonePolicy,
)
from pcs.trend.support_zones import evaluate_support_zones


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str).encode()).hexdigest()


def _number(value):
    return None if value is None or pd.isna(value) else float(value)


class OpportunityDataReader:
    """Pin verified daily identity and prepare all causal facts for one call."""
    def __init__(self, access=None):
        self.daily = ProfileDataReader(access)
        self.audit = []

    def load(self, context: CallContext, *, policy=None, prior_state=None,
             calendar="XNYS") -> OpportunityInput:
        import exchange_calendars as xc

        policy = policy or OpportunityPolicy()
        day = context.effective_daily_session
        if day is None:
            raise ValueError("OPPORTUNITY_EFFECTIVE_SESSION_REQUIRED")
        daily = self.daily._read(context.symbol, day, policy, calendar)
        benchmark = None
        benchmark_error = None
        try:
            benchmark = self.daily._read("SPY", day, policy, calendar)
        except (ValueError, RuntimeError) as exc:
            benchmark_error = str(exc)
        frame = pd.DataFrame([{"date": b.session, "open": b.open, "high": b.high,
            "low": b.low, "close": b.close, "volume": b.volume} for b in daily.bars])
        benchmark_frame = (pd.DataFrame([{"date": b.session, "open": b.open,
            "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in benchmark.bars]) if benchmark else None)
        if len(frame) < policy.indicator_warmup_sessions:
            raise ValueError("OPPORTUNITY_INDICATOR_WARMUP_INSUFFICIENT")
        config = TrendIndicatorConfig(pivot_left_bars=3, pivot_right_bars=3)
        indicator_frame = frame.copy()
        missing_volume_rows = int(indicator_frame.volume.isna().sum())
        indicators = calculate_base_indicators(indicator_frame, config, allow_missing_volume=True)
        indicators["ema200"] = frame.close.astype(float).ewm(span=200, adjust=False,
                                                               min_periods=1).mean()
        full_structure = analyze_market_structure(indicator_frame, config, as_of_date=day, allow_missing_volume=True)
        swings = full_structure.confirmed_swings

        cal = xc.get_calendar(calendar)
        end_loc = cal.sessions.get_loc(pd.Timestamp(day))
        analysis = [str(s.date()) for s in cal.sessions[
            end_loc-policy.analysis_sessions+1:end_loc+1]]
        future = [str(s.date()) for s in cal.sessions[
            end_loc+1:end_loc+policy.confirmation_sessions+policy.entry_window_sessions+2]]
        expected = analysis + future
        by_date = {str(pd.Timestamp(row.date).date()): i for i, row in frame.iterrows()}

        feature_bars = []
        support_bars = []
        legacy_by_day = {}
        for i, row in frame.iterrows():
            session = str(pd.Timestamp(row.date).date())
            ind = indicators.iloc[i]
            structure = analyze_market_structure(indicator_frame, config, as_of_date=session,
                                                 precomputed_swings=swings, allow_missing_volume=True)
            pullback = analyze_pullback(indicator_frame, indicators, None, structure, config,
                                        as_of_date=session, allow_missing_volume=True)
            health = phase = None
            trend_status = pullback_status = "NOT_EVALUATED"
            trend_result = pullback_result = None
            trend_reasons, pullback_reasons = [], []
            if session in analysis:
                snapshot = build_trend_snapshot(indicator_frame, benchmark_frame, config,
                    as_of_date=session, symbol=context.symbol, benchmark="SPY" if benchmark else None,
                    precomputed_indicators=indicators, precomputed_swings=swings, allow_missing_volume=True)
                interpretation = interpret_trend(snapshot, config)
                # Trend health consumes relative strength.  If that optional
                # source is absent, only this dependent fact remains unknown.
                if interpretation.available and snapshot.relative_strength.available:
                    health = interpretation.trend_health
                phase = (snapshot.market_structure_engine.short_term_phase
                         if snapshot.market_structure_engine and
                         snapshot.market_structure_engine.available else None)
                score = score_trend(snapshot, interpretation, config)
                trend_gate = evaluate_trend_gate(score, interpretation, snapshot)
                trend_status = "EXECUTED" if trend_gate.available else "BLOCKED"
                trend_result = trend_gate.trend_gate_result
                trend_reasons = list(trend_gate.reasons or trend_gate.warnings)
                pullback_gate = evaluate_pullback_gate(trend_gate, snapshot, interpretation)
                pullback_status = "EXECUTED" if pullback_gate.available else "BLOCKED"
                pullback_result = pullback_gate.pullback_gate_result
                pullback_reasons = list(pullback_gate.reasons or pullback_gate.warnings)
            legacy_by_day[session] = {"pullback": pullback,
                "trend_gate_status": trend_status, "trend_gate_result": trend_result,
                "trend_gate_reasons": trend_reasons,
                "pullback_gate_status": pullback_status,
                "pullback_gate_result": pullback_result,
                "pullback_gate_reasons": pullback_reasons}
            feature_bars.append(OpportunityFeatureBar(session=session,
                open=_number(row.open), high=_number(row.high), low=_number(row.low),
                close=_number(row.close), volume=_number(row.volume),
                sma20=_number(ind.sma20), sma50=_number(ind.sma50),
                sma200=_number(ind.sma200), ema200=_number(ind.ema200),
                atr14=_number(ind.atr14), rsi14=_number(ind.rsi14),
                structure_state=structure.structure_state if structure.available else None,
                trend_health=health,
                trend_health_source="pcs.trend.interpretation.interpret_trend" if health else None,
                short_term_phase=phase,
                short_term_phase_source="pcs.trend.market_structure_engine" if phase else None,
                legacy_trend_gate_status=trend_status,
                legacy_trend_gate_result=trend_result,
                legacy_trend_gate_reasons=trend_reasons,
                legacy_pullback_gate_status=pullback_status,
                legacy_pullback_gate_result=pullback_result,
                legacy_pullback_gate_reasons=pullback_reasons,
                legacy_pullback_state=pullback.pullback_state,
                legacy_pullback_reasons=list(pullback.reasons)))
            if session in analysis:
                support_bars.append(SupportFeatureBar(session=session,
                    open=_number(row.open), high=_number(row.high), low=_number(row.low),
                    close=_number(row.close), sma20=_number(ind.sma20),
                    sma50=_number(ind.sma50), atr14=_number(ind.atr14)))

        confirmed = []
        from pcs.trend.selection_models import ConfirmedSwingEvidence
        for swing in swings:
            confirmed_at = str(pd.Timestamp(swing.confirmed_at).date())
            if confirmed_at >= analysis[0]:
                confirmed.append(ConfirmedSwingEvidence(source_id="sha256:"+_hash([
                    context.symbol, str(pd.Timestamp(swing.pivot_date).date()),
                    swing.swing_type, swing.price, confirmed_at, 3, 3]),
                    pivot_date=str(pd.Timestamp(swing.pivot_date).date()),
                    confirmed_at=confirmed_at, swing_type=swing.swing_type,
                    price=float(swing.price)))
        indicator_payload = {"implementation": "pcs.trend.indicators.calculate_base_indicators",
            "projection": "single-computation-causal-projection",
            "config": {"sma20": 20, "sma50": 50, "sma200": 200,
                       "ema200": 200, "atr": 14, "rsi": 14,
                       "pivot_left": 3, "pivot_right": 3},
            "indicator_seed_start": str(pd.Timestamp(frame.date.iloc[0]).date()),
            "daily_source": [daily.source.sha256, daily.source.record_identity],
            "benchmark_source": ([benchmark.source.sha256, benchmark.source.record_identity]
                                 if benchmark else None)}
        indicator_identity = "sha256:"+_hash(indicator_payload)
        support_view = SupportFeatureView(symbol=context.symbol, bars=support_bars,
            confirmed_swings=confirmed, expected_sessions=expected,
            analysis_start=analysis[0],
            indicator_seed_start=str(pd.Timestamp(frame.date.iloc[0]).date()),
            indicator_identity=indicator_identity, source=daily.source,
            price_basis=daily.price_basis,
            corporate_action_version=daily.corporate_action_version,
            input_kind="VERIFIED_CANONICAL", source_timestamp=daily.source_timestamp,
            received_at=daily.received_at)
        support_policy = SupportZonePolicy(analysis_sessions=policy.analysis_sessions,
            indicator_warmup_sessions=policy.indicator_warmup_sessions)
        support_state = None
        support_facts = []
        support_result_ids = {}
        support_results = []
        for session in analysis:
            if session not in by_date:
                continue
            support_context = context.model_copy(update={
                "effective_daily_session": session,
                "requested_as_of": session,
                "request_id": f"{context.request_id}:support:{session}",
                "scope": "SUPPORT_ZONES_FOR_OPPORTUNITY"})
            result = evaluate_support_zones(SupportZoneInput(call_context=support_context,
                feature_view=support_view, effective_policy=support_policy,
                prior_state=support_state))
            support_state = result.next_state
            support_results.append(result)
            support_result_ids[session] = result.result_id
            for zone in result.current_zones + result.archived_zones:
                for test in zone.tests:
                    if test.touch_session <= session:
                        support_facts.append(OpportunitySupportFact(session=session,
                            support_result_id=result.result_id, zone_id=zone.zone_id,
                            test_id=test.test_id, zone_lower=zone.lower,
                            zone_upper=zone.upper, anchor_atr=zone.anchor_atr,
                            invalidation_line=zone.invalidation_line,
                            zone_available_at=zone.available_at,
                            touch_session=test.touch_session,
                            test_status=test.status, first_held_at=test.first_held_at,
                            broken_at=zone.broken_at, zone_state=zone.state,
                            source_ids=list(zone.observed_source_ids),
                            sources=list(zone.observed_sources or zone.creation_sources),
                            reason_codes=list(dict.fromkeys(zone.reason_codes+test.reason_codes))))
        feature = OpportunityFeatureView(symbol=context.symbol, bars=feature_bars,
            expected_sessions=[str(s.date()) for s in cal.sessions[
                end_loc-policy.required_sessions+1:end_loc+1]] + future, analysis_start=analysis[0],
            indicator_seed_start=str(pd.Timestamp(frame.date.iloc[0]).date()),
            indicator_identity=indicator_identity, source=daily.source,
            auxiliary_sources=[benchmark.source] if benchmark else [],
            price_basis=daily.price_basis,
            corporate_action_version=daily.corporate_action_version,
            input_kind="VERIFIED_CANONICAL", source_timestamp=daily.source_timestamp,
            received_at=daily.received_at)
        last_legacy = legacy_by_day.get(day)
        last_pullback = last_legacy["pullback"] if last_legacy else None
        legacy = {"producer": "pcs.trend.pullback.analyze_pullback",
            "execution_status": "EXECUTED" if last_pullback and last_pullback.available else "NOT_EVALUATED",
            "as_of": day,
            "pullback_state": last_pullback.pullback_state if last_pullback else None,
            "reason_codes": list(last_pullback.reasons) if last_pullback else [],
            "trend_gate_execution_status": last_legacy["trend_gate_status"] if last_legacy else "NOT_EVALUATED",
            "trend_gate_result": last_legacy["trend_gate_result"] if last_legacy else None,
            "trend_gate_reason_codes": last_legacy["trend_gate_reasons"] if last_legacy else [],
            "pullback_gate_execution_status": last_legacy["pullback_gate_status"] if last_legacy else "NOT_EVALUATED",
            "pullback_gate_result": last_legacy["pullback_gate_result"] if last_legacy else None,
            "pullback_gate_reason_codes": last_legacy["pullback_gate_reasons"] if last_legacy else [],
            "benchmark_status": "AVAILABLE" if benchmark else "UNAVAILABLE",
            "benchmark_reason": benchmark_error,
            "production_action": "UNCHANGED_NOT_EVALUATED_BY_V2"}
        self.audit.append({"symbol": context.symbol, "requested_as_of": day,
            "physical_verified_rows": len(frame), "analysis_start": analysis[0],
            "analysis_sessions": len(analysis), "indicator_seed_start": feature.indicator_seed_start,
            "indicator_identity": indicator_identity, "confirmed_swings": len(confirmed),
            "support_daily_results": len(support_results),
            "volume_missing_rows": missing_volume_rows,
            "volume_handling": "ORIGINAL_NULL_RETAINED; PRICE_ONLY_VALIDATION_OPT_IN",
            "source": daily.source.model_dump(mode="json")})
        return OpportunityInput(call_context=context, feature_view=feature,
            support_facts=support_facts, support_result_ids=support_result_ids,
            effective_policy=policy, prior_state=prior_state, calendar=calendar,
            legacy_opinion=legacy)

    def verify_unchanged(self):
        return self.daily.verify_unchanged()


def evaluate_pool_opportunity_observation(input: OpportunityInput) -> EntryOpportunity:
    """Explicit pool observation boundary; does not alter pool admission."""
    return evaluate_entry_opportunity(input)


def opportunity_to_ai_view(result: EntryOpportunity):
    return {"result_id": result.result_id, "symbol": result.symbol,
        "family": result.family, "active_families": result.active_families,
        "family_results": [opportunity_to_ai_view(r) for r in result.family_results],
        "economic_events": result.economic_events,
        "breakout_retest": (result.breakout_result.model_dump(mode="json")
                            if result.breakout_result else None),
        "shallow_pullback_timeline": [e.model_dump(mode="json") for e in result.shallow_pullback_timeline],
        "as_of": result.as_of, "state": result.state.value if result.state else None,
        "capability_status": result.status.value,
        "eligible_at_requested_time": result.eligible_at_requested_time,
        "requested_session": result.requested_session,
        "request_time_semantics": result.request_time_semantics,
        "economic_episode_id": result.economic_episode_id,
        "opportunity_id": result.opportunity_id,
        "evaluated_through": result.evaluated_through,
        "entry_permitted_from": result.entry_permitted_from,
        "entry_permitted_until": result.entry_permitted_until,
        "confirmation_deadline_elapsed_at_requested_session": result.confirmation_deadline_elapsed_at_requested_session,
        "entry_window_elapsed_at_requested_session": result.entry_window_elapsed_at_requested_session,
        "upstream_result_ids": result.upstream_result_ids,
        "dates": ({"setup": result.timeline[-1].setup_date,
                   "touch": result.timeline[-1].touch_date,
                   "confirmation": result.timeline[-1].confirmation_date,
                   "confirmation_deadline": result.timeline[-1].confirmation_deadline,
                   "entry_start": result.timeline[-1].entry_start,
                   "entry_end": result.timeline[-1].entry_end}
                  if result.timeline else {}),
        "supporting_evidence": result.supporting_evidence,
        "opposing_evidence": result.opposing_evidence,
        "missing_evidence": result.missing_evidence,
        "current_missing_details": [g.model_dump(mode="json") for g in result.current_missing_details],
        "coverage_missing_evidence": [g.model_dump(mode="json") for g in result.coverage_missing_evidence],
        "next_observation_conditions": result.next_observation_conditions,
        "legacy_opinion": result.legacy_opinion,
        "opinion_differences": result.opinion_differences,
        "detail_refs": {"timeline": "opportunity_timeline.json",
                        "conditions": "opportunity_conditions.json",
                        "transitions": "opportunity_transitions.json"},
        "explanation": result.explanation}


def opportunities_to_markdown(results):
    results = [child for r in results for child in (r.family_results or [r])]
    out = ["# 第4步：机会状态机与健康回调", "",
        "ENTRY_READY仅表示程序按v1.7政策识别到可观察机会；不是下单授权，也不表示已完成期权评估。", "",
        "| 股票 | 行情日 | 能力 | 状态 | 当前可评估 | 触及 | 首次确认 | 确认截止 | 入场窗口 | 缺口 |",
        "|---|---|---|---|---|---|---|---|---|---|"]
    for result in results:
        day = result.timeline[-1] if result.timeline else None
        out.append(f"| {result.symbol} | {result.as_of} | {result.status.value} | "
            f"{result.state.value if result.state else '未知'} | "
            f"{result.eligible_at_requested_time if result.eligible_at_requested_time is not None else '未知'} | "
            f"{day.touch_date if day and day.touch_date else '未发生'} | "
            f"{day.confirmation_date if day and day.confirmation_date else '未发生'} | "
            f"{day.confirmation_deadline if day and day.confirmation_deadline else '不适用'} | "
            f"{(day.entry_start+'—'+day.entry_end) if day and day.entry_start and day.entry_end else '不适用'} | "
            f"当前缺项：{', '.join(result.missing_evidence) or '无'}；"
            f"覆盖未知：{len(result.coverage_missing_evidence)}项；"
            f"{', '.join(result.coverage.reason_codes) or '无阻断缺口'} |")
    for result in results:
        out += ["", f"## {result.symbol} / {result.family} 逐日过程", "",
            "| 日期 | 状态 | 能力 | 可评估 | 事件/原因 |", "|---|---|---|---|---|"]
        for day in result.timeline:
            out.append(f"| {day.session} | {day.state.value if day.state else '未知'} | "
                f"{day.capability_status.value} | {day.eligible if day.eligible is not None else '未知'} | "
                f"{', '.join(day.reason_codes) or '—'} |")
        out += ["", result.explanation, ""]
        if result.coverage_missing_evidence:
            out += ["| 缺项日期 | 条件 | 角色 | 原因 | 影响 |",
                    "|---|---|---|---|---|"]
            for gap in result.coverage_missing_evidence:
                out.append(f"| {gap.session} | {gap.condition_id} | {gap.role} | "
                    f"{', '.join(gap.reason_codes)} | {', '.join(gap.affected_outputs)} |")
        if result.shallow_pullback_timeline:
            out += ["", "| 日期 | 原触及 | 冻结峰值/日期 | 前日ATR/日期 | 累计最低价 | 触及/累计深度ATR | 首次超限 | 资格/缺口 |",
                    "|---|---|---|---|---|---|---|---|"]
            for evidence in result.shallow_pullback_timeline:
                s = evidence.next_state
                out.append(f"| {evidence.session} | {s.touch_session if s else '未发生'} | "
                    f"{str(s.peak_price)+' / '+s.peak_session if s else '未知'} | "
                    f"{str(s.depth_anchor_atr)+' / '+s.depth_anchor_session if s else '未知'} | "
                    f"{s.episode_low if s else '未知'} | "
                    f"{str(s.depth_at_touch)+' / '+str(s.current_depth_atr) if s else '未知'} | "
                    f"{s.first_depth_exceeded if s and s.first_depth_exceeded else '未记录超限'} | "
                    f"{evidence.detected} / {', '.join(evidence.reason_codes)} |")
        if result.breakout_result:
            out += ["", "| 日期 | 阶段 | 突破ID | 回踩 | 确认 | 当前可评估 | 原因 |",
                    "|---|---|---|---|---|---|---|"]
            for day in result.breakout_result.timeline:
                out.append(f"| {day.session} | {day.stage} | {day.breakout_id or '未发生'} | "
                    f"{day.retest_session or '未发生'} | {day.confirmation_session or '未发生'} | "
                    f"{day.eligible if day.eligible is not None else '未知'} | "
                    f"{', '.join(day.reason_codes) or '—'} |")
    return "\n".join(out)


def _csv_view(results):
    results = [child for r in results for child in (r.family_results or [r])]
    stream = io.StringIO(newline="")
    columns = ["symbol", "family", "session", "state", "capability_status", "eligible",
        "economic_episode_id", "opportunity_id", "setup_date", "touch_date",
        "confirmation_deadline", "confirmation_date", "entry_start", "entry_end",
        "support_zone_id", "support_test_id", "reason_codes", "conditions_ref",
        "current_missing_evidence", "coverage_missing_evidence",
        "requested_session", "request_time_semantics", "eligible_at_requested_time"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    for result in results:
        for day in result.timeline:
            writer.writerow({"symbol": result.symbol, "family": result.family, "session": day.session,
                "state": day.state.value if day.state else "", "capability_status": day.capability_status.value,
                "eligible": "" if day.eligible is None else str(day.eligible).lower(),
                "economic_episode_id": day.economic_episode_id or "",
                "opportunity_id": day.opportunity_id or "", "setup_date": day.setup_date or "",
                "touch_date": day.touch_date or "", "confirmation_deadline": day.confirmation_deadline or "",
                "confirmation_date": day.confirmation_date or "", "entry_start": day.entry_start or "",
                "entry_end": day.entry_end or "", "support_zone_id": day.support_zone_id or "",
                "support_test_id": day.support_test_id or "", "reason_codes": ";".join(day.reason_codes),
                "conditions_ref": f"{result.result_id}:{day.session}",
                "current_missing_evidence": json.dumps(
                    [c.condition_id for c in day.conditions if c.role != "DIAGNOSTIC" and c.predicate_value is None]
                    if result.family == "BREAKOUT_RETEST" else result.missing_evidence, ensure_ascii=False),
                "requested_session": result.requested_session,
                "request_time_semantics": result.request_time_semantics,
                "eligible_at_requested_time": "" if result.eligible_at_requested_time is None else str(result.eligible_at_requested_time).lower(),
                "coverage_missing_evidence": json.dumps([g.model_dump(mode="json")
                    for g in result.coverage_missing_evidence if g.session == day.session],
                    ensure_ascii=False)})
    return stream.getvalue()


def write_opportunity_artifacts(output_directory, results, *, audit=None, inputs=None):
    root = Path(output_directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("OPPORTUNITY_OUTPUT_DIRECTORY_NOT_EMPTY")
    root.mkdir(parents=True, exist_ok=True)
    children = [child for r in results for child in (r.family_results or [r])]
    documents = {
        "entry_opportunities.json": [r.model_dump(mode="json") for r in results],
        "entry_opportunities.ai.json": [opportunity_to_ai_view(r) for r in results],
        "family_opportunities.json": [r.model_dump(mode="json") for r in children],
        "shallow_pullback_timeline.json": [e.model_dump(mode="json") for r in children
                                          for e in r.shallow_pullback_timeline],
        "breakout_events.json": [{"symbol": r.symbol, **e.model_dump(mode="json")}
                                  for r in children if r.breakout_result
                                  for e in r.breakout_result.events],
        "breakout_retest_timeline.json": [{"symbol": r.symbol, **d.model_dump(mode="json")}
                                           for r in children if r.breakout_result
                                           for d in r.breakout_result.timeline],
        "opportunity_timeline.json": [{"symbol": r.symbol, "result_id": r.result_id,
            "family": r.family, **d.model_dump(mode="json")} for r in children for d in r.timeline],
        "opportunity_conditions.json": [{"symbol": r.symbol, "result_id": r.result_id,
            "family": r.family, "day": d.session, **c.model_dump(mode="json")} for r in children for d in r.timeline for c in d.conditions],
        "opportunity_transitions.json": [{"symbol": r.symbol, "result_id": r.result_id,
            "family": r.family, **t.model_dump(mode="json")} for r in children for t in r.transitions],
        "opportunity_states.json": [r.next_state.model_dump(mode="json") for r in children],
        "entry_opportunity.schema.json": EntryOpportunity.model_json_schema(),
        "opportunity_input.schema.json": OpportunityInput.model_json_schema(),
        "examples.json": {
            "normal": opportunity_to_ai_view(results[0]) if results else None,
            "missing_input": {"state": None, "last_known_state": None,
                "eligible_at_requested_time": None, "capability_status": "PARTIAL",
                "reason_codes": ["DAILY_BAR_MISSING"],
                "meaning": "能力未知，不等于NO_SETUP或false"}},
        "field_dictionary.json": {
            "state": "NO_SETUP/WATCH/CONFIRMING/ENTRY_READY/EXPIRED/INVALIDATED business state",
            "status": "Capability status; gaps do not create a market state",
            "current_missing_details": "Dated required unknowns affecting the current conclusion; excludes diagnostics",
            "coverage_missing_evidence": "Dated non-diagnostic unknowns across the retained timeline; coverage-only items do not override a conclusive false",
            "coverage.processed_sessions": "Sessions actually attempted in this invocation, including the first unresolved gap",
            "coverage.display_sessions": "Requested presentation range; never truncates state advancement",
            "eligible_at_requested_time": "null when requested time is not evaluable; ENTRY_READY is not authorization",
            "confirmation": "touch+1..touch+3 inclusive; first date is frozen",
            "entry_window": "confirmation+1..confirmation+3; confirmation day excluded",
            "rvol20": "current volume / mean(previous 20 completed sessions); current excluded",
            "upper_wick": "confirmation blocker only under policy v1.7",
            "unknown": "null plus stable reason code; CSV empty is unknown, never false",
            "identity": "call time/run/request/recovery diagnostics excluded from semantic result_id",
            "details": "CSV rows reference complete conditions by result_id and day"},
        "read_audit.json": audit or {},
    }
    from pcs.trend.selection_models import (BreakoutRetestInput, BreakoutRetestResult,
        ShallowPullbackInput, SetupEvidence)
    documents["shallow_pullback_input.schema.json"] = ShallowPullbackInput.model_json_schema()
    documents["setup_evidence.schema.json"] = SetupEvidence.model_json_schema()
    documents["breakout_retest_input.schema.json"] = BreakoutRetestInput.model_json_schema()
    documents["breakout_retest_result.schema.json"] = BreakoutRetestResult.model_json_schema()
    if inputs is not None:
        documents["prepared_opportunity_inputs.json"] = [i.model_dump(mode="json") for i in inputs]
    hashes = {name: _write_atomic(root/name, json.dumps(doc, ensure_ascii=False,
              indent=2, allow_nan=False)) for name, doc in documents.items()}
    hashes["entry_opportunities.csv"] = _write_atomic(root/"entry_opportunities.csv", _csv_view(results))
    hashes["entry_opportunities.zh-CN.md"] = _write_atomic(root/"entry_opportunities.zh-CN.md",
                                                            opportunities_to_markdown(results))
    code_root = Path(__file__).resolve().parents[3]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=code_root,
                                     text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain",
        "--untracked-files=no"], cwd=code_root, text=True).strip())
    _write_atomic(root/"artifact_manifest.json", json.dumps({"module": "entry_opportunity",
        "version": "1.0", "source_commit": commit, "tracked_source_dirty": dirty,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "symbols": [r.symbol for r in results], "result_ids": [r.result_id for r in results],
        "sha256": hashes}, ensure_ascii=False, indent=2))
    return root


def load_opportunity_state(output_directory, symbol):
    root = Path(output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    expected = manifest["sha256"]["opportunity_states.json"]
    if sha256((root/"opportunity_states.json").read_bytes()).hexdigest() != expected:
        raise ValueError("OPPORTUNITY_STATE_HASH_MISMATCH")
    states = [OpportunityStateCheckpoint.model_validate(x) for x in json.loads(
        (root/"opportunity_states.json").read_text(encoding="utf-8"))]
    return next((s for s in states if s.symbol == symbol.strip().upper()), None)


def load_opportunity_resume_evidence(output_directory, symbol):
    """Load hash-verified detailed history kept outside the small checkpoint."""
    root = Path(output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    raw = (root/"entry_opportunities.json").read_bytes()
    if sha256(raw).hexdigest() != manifest["sha256"]["entry_opportunities.json"]:
        raise ValueError("OPPORTUNITY_RESULT_HASH_MISMATCH")
    result = next((EntryOpportunity.model_validate(x) for x in json.loads(raw)
                   if x.get("symbol") == symbol.strip().upper()), None)
    if result is None:
        return None
    return {"prior_state": result.next_state, "prior_timeline": result.timeline,
            "prior_transitions": result.transitions,
            "prior_detections": result.detections,
            "prior_setup_evidence": result.shallow_pullback_timeline,
            "prior_family_results": result.family_results or [result]}


def read_opportunity_bundle(output_directory):
    """Verify one immutable bundle once for a batch; no canonical/provider reads."""
    root = Path(output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    contents = {}
    for name, digest in manifest["sha256"].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("OPPORTUNITY_ARTIFACT_PATH_INVALID")
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != digest:
            raise ValueError(f"OPPORTUNITY_ARTIFACT_HASH_MISMATCH:{name}")
        if name in {"entry_opportunities.json", "prepared_opportunity_inputs.json", "read_audit.json"}:
            contents[name] = json.loads(raw)
    return manifest, contents


def find_shallow_evidence(result, session, test_id=None):
    return [e for r in (result.family_results or [result]) for e in r.shallow_pullback_timeline
            if e.session == session and (test_id is None or e.next_state and e.next_state.test_id == test_id)]


def find_breakout_event(result, breakout_id):
    return next((event for child in (result.family_results or [result])
                 if child.breakout_result for event in child.breakout_result.events
                 if event.breakout_id == breakout_id), None)


def find_breakout_day(result, session, test_id=None):
    return [day for child in (result.family_results or [result])
            if child.breakout_result for day in child.breakout_result.timeline
            if day.session == session and (test_id is None or day.support_test_id == test_id)]


def find_opportunity_episode(result: EntryOpportunity, economic_episode_id: str):
    return next((e for e in result.episodes
                 if e.economic_episode_id == economic_episode_id), None)


def find_opportunity_day(result: EntryOpportunity, session: str):
    return next((d for d in result.timeline if d.session == session), None)


def find_opportunity_condition(result: EntryOpportunity, session: str,
                               condition_id: str):
    day = find_opportunity_day(result, session)
    return next((c for c in day.conditions if c.condition_id == condition_id), None) if day else None


def run_opportunity_command(args):
    from pcs.validation import ValidationRun
    code_root = Path(__file__).resolve().parents[3]
    guard = ValidationRun(code_root, tuple((code_root/"src/pcs/trend").glob("*.py")) +
        (Path(__file__).resolve(), code_root/"src/pcs/trend/selection_models.py"))
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    if not 1 <= len(symbols) <= 8:
        raise ValueError("OPPORTUNITY_SYMBOL_LIMIT_1_TO_8")
    input_directory = getattr(args, "input_directory", None)
    resume_directory = getattr(args, "resume_directory", None)
    render_only = getattr(args, "render_only", False)
    families = list(dict.fromkeys(getattr(args, "families", "HEALTHY_PULLBACK").split(",")))
    if not set(families) <= {"HEALTHY_PULLBACK", "SHALLOW_PULLBACK", "BREAKOUT_RETEST"}:
        raise ValueError("OPPORTUNITY_FAMILY_INVALID")
    saved_inputs, prior_results = {}, {}
    if input_directory:
        saved_manifest, contents = read_opportunity_bundle(input_directory)
        if render_only:
            saved = [EntryOpportunity.model_validate(x) for x in contents["entry_opportunities.json"]
                     if x["symbol"] in symbols]
            write_opportunity_artifacts(args.output_directory, saved,
                audit={"render_only": True, "origin_manifest": saved_manifest})
            guard.add_output(args.output_directory)
            if guard.finish(Path(args.output_directory)/"validation_run.json").value != "VALID":
                raise ValueError("STALE — RERUN REQUIRED")
            print(json.dumps({"output_directory": args.output_directory, "render_only": True}))
            return
        saved_inputs = {x["call_context"]["symbol"]: OpportunityInput.model_validate(x)
            for x in contents.get("prepared_opportunity_inputs.json", [])}
        if not saved_inputs:
            raise ValueError("OPPORTUNITY_PREPARED_INPUTS_NOT_SAVED")
    elif render_only:
        raise ValueError("OPPORTUNITY_RENDER_REQUIRES_INPUT_DIRECTORY")
    if resume_directory:
        _, prior_contents = read_opportunity_bundle(resume_directory)
        prior_results = {x["symbol"]: EntryOpportunity.model_validate(x)
                         for x in prior_contents["entry_opportunities.json"]}
    reader = None if input_directory else OpportunityDataReader()
    results, failures, prepared = [], [], []
    for symbol in symbols:
        context = CallContext(symbol=symbol, requested_as_of=args.as_of,
            effective_daily_session=args.as_of, mode="HISTORICAL",
            run_id=args.run_id, request_id=f"{args.run_id}:{symbol}",
            scope="ENTRY_OPPORTUNITY_V2_OBSERVATION")
        try:
            if input_directory:
                if symbol not in saved_inputs:
                    original = next((f for f in contents.get("read_audit.json", {}).get("failures", [])
                                     if f["symbol"] == symbol), None)
                    raise ValueError(original["reason"] if original else "OPPORTUNITY_SAVED_SYMBOL_MISSING")
                loaded = saved_inputs[symbol]
                if args.as_of > loaded.call_context.effective_daily_session:
                    raise ValueError("OPPORTUNITY_SAVED_INPUT_END_EXCEEDED")
                loaded = loaded.model_copy(update={"call_context": context})
            else:
                loaded = reader.load(context)
            prior = prior_results.get(symbol)
            loaded = loaded.model_copy(update={"enabled_families": families,
                "prior_family_results": (prior.family_results or [prior]) if prior else []})
            prepared.append(loaded)
            results.append(evaluate_entry_opportunity(loaded))
        except (ValueError, RuntimeError) as exc:
            failures.append({"symbol": symbol, "stage": "OPPORTUNITY_READ_OR_EVALUATE",
                             "reason": str(exc)})
    verification = reader.verify_unchanged() if reader else {
        "status": "SAVED_ARTIFACT_HASHES_VERIFIED", "origin_directory": input_directory,
        "origin_manifest": saved_manifest}
    root = write_opportunity_artifacts(args.output_directory, results,
        audit={"reads": reader.audit if reader else [], "source_unchanged": verification,
               "failures": failures, "input_directory": input_directory,
               "resume_directory": resume_directory}, inputs=prepared)
    guard.add_output(root)
    if guard.finish(root/"validation_run.json").value != "VALID":
        raise ValueError("STALE — RERUN REQUIRED")
    print(json.dumps({"output_directory": str(root.resolve()), "results": len(results),
                      "failures": failures}, ensure_ascii=False, indent=2))
