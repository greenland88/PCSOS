"""Bounded, source-grounded evidence views for downstream AI readers.

This module only reshapes persisted scan evidence.  It never calls a data
provider, recomputes a strategy indicator, or changes a deterministic verdict.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from datetime import datetime, timezone
from hashlib import sha256
import csv
import io
import json

from pcs.analysis_contracts import (
    CallContext, CapabilityStatus, EvidenceValue, ExecutionStatus,
    ExplanationInput, RuleEvaluation, SelectionExplanation,
    SelectionExplanationData, SourceReference, SourceStatus,
)


UNKNOWN_REASON = "NOT_SAVED_IN_SOURCE_ARTIFACT"


def _plain(value):
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def unknown(reason: str = UNKNOWN_REASON) -> dict[str, str]:
    return {"status": "UNKNOWN", "reason": reason}


def provided(value, *, source: str | None = None) -> dict[str, Any]:
    result = {"status": "PROVIDED", "value": _plain(value)}
    if source:
        result["source"] = source
    return result


def _value(row, key, source: str | None = None):
    value = row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)
    return unknown() if value is None else provided(value, source=source)


def _status(row, key):
    value = row.get(key) if isinstance(row, Mapping) else getattr(row, key, None)
    return value.value if hasattr(value, "value") else str(value) if value is not None else "NOT_EVALUATED"


def _path_status(row, key):
    value = _status(row, key)
    return {"stage": key, "status": value,
            "reason_codes": list(_plain(row.get("reason_codes", ()))) if key == "final_action"
            else list(_plain(row.get(key.replace("_status", "_reason_codes"), ())))}


def _outcome_class(row):
    action = _status(row, "final_action")
    eligibility = _status(row, "eligibility_status")
    if eligibility == "DATA_BLOCKED" or action == "DATA_FAILED":
        return "DATA_BLOCKED"
    if action == "REJECTED":
        return "STRATEGY_REJECTED"
    if action == "WATCH":
        return "WATCH"
    if action == "WAIT":
        return "WAIT"
    if action == "PCS_TRADE_READY":
        return "PCS_TRADE_READY"
    return "NOT_EXECUTED"


def build_ai_evidence_packet(row, snapshot: Mapping[str, Any], *, evidence_window: int = 60) -> dict[str, Any]:
    """Build one packet from a TickerScanResult or its persisted dictionary."""
    row = _plain(row)
    state = row.get("candidate_state") or {}
    trend = state.get("trend_evidence") or {}
    series = state.get("price_indicator_series")
    if not series:
        series_value = unknown()
    else:
        series_value = provided(series[-evidence_window:], source="candidate_state.price_indicator_series")
    last_series = series[-1] if series else {}
    rules = state.get("applicable_rules") or {}
    engine = trend.get("market_structure_engine") or {}
    market_structure = trend.get("market_structure") or {}
    confirmed_swings = market_structure.get("confirmed_swings") if isinstance(market_structure, Mapping) else None
    support = trend.get("support") or {}
    selection = row.get("selected_contract") or row.get("selection_result") or row.get("discovered_contracts")
    phase = row.get("short_term_phase")
    setup_type = ("TREND_CONTINUATION" if phase == "CONTINUATION" else
                  "PULLBACK" if phase == "HEALTHY_PULLBACK" else
                  "RECOVERY_RECLAIM" if phase and "RECLAIM" in str(phase) else unknown())
    packet = {
        "schema": "pcs.ai_evidence_packet",
        "schema_version": "1",
        "symbol": row.get("symbol"),
        "as_of": row.get("as_of"),
        "system_verdict": {
            "eligibility_status": _status(row, "eligibility_status"),
            "timing_status": _status(row, "timing_status"),
            "options_status": _status(row, "options_status"),
            "event_status": _status(row, "event_status"),
            "portfolio_status": _status(row, "portfolio_status"),
            "final_action": _status(row, "final_action"),
            "reason_codes": list(row.get("reason_codes", ())),
        },
        "outcome_class": _outcome_class(row),
        "selection_basis": ("PCS_TRADE_READY" if _status(row, "final_action") == "PCS_TRADE_READY"
                            else "TIMING_ENTRY_READY" if _status(row, "timing_status") == "TIMING_ENTRY_READY"
                            else "NOT_SELECTED"),
        "decision_path": {
            "eligibility": {"status": _status(row, "eligibility_status"),
                             "reason_codes": list(row.get("reason_codes", ()))},
            "timing": {"status": _status(row, "timing_status"),
                       "reason_codes": list(row.get("timing_reason_codes", state.get("timing_reason_codes", ()))),
                       "actual_values": {key: _value(state, key, "candidate_state")
                                         for key in ("close", "atr")},
                       "applicable_rules": {key: rules[key] for key in rules if key.startswith("pullback_") or key.startswith("support_")}},
            "options": {"status": _status(row, "options_status"),
                        "reason_codes": list(row.get("selection_reason_codes", ())) or
                                        list(row.get("reason_codes", ())),
                        "evidence": provided(selection, source="selected/discovered contract fields") if selection else unknown()},
            "event": {"status": _status(row, "event_status"), "reason_codes": []},
            "portfolio": {"status": _status(row, "portfolio_status"), "reason_codes": []},
            "final": {"status": _status(row, "final_action"),
                      "reason_codes": list(row.get("reason_codes", ()))},
        },
        "opportunity_context": {
            "setup_type": provided(setup_type, source="short_term_phase") if isinstance(setup_type, str) else setup_type,
            "structural_trend": _value(row, "structural_trend", "ticker_result"),
            "short_term_phase": _value(row, "short_term_phase", "ticker_result"),
            "trend_gate_reasons": provided(row.get("trend_gate_reasons", ()), source="ticker_result")
                                  if row.get("trend_gate_reasons") else unknown(),
            "pullback_gate_reasons": provided(row.get("pullback_gate_reasons", ()), source="ticker_result")
                                     if row.get("pullback_gate_reasons") else unknown(),
        },
        "price_and_indicators": {
            "window_sessions": evidence_window,
            "sequence": series_value,
            "current_values": {key: _value(state, key, "candidate_state")
                               for key in ("close", "atr")},
        },
        "structure": {
            "snapshot": provided(trend.get("market_structure"), source="trend_snapshot.market_structure")
                        if trend.get("market_structure") else unknown(),
            "confirmed_points": provided(confirmed_swings, source="market_structure.confirmed_swings")
                                if confirmed_swings else unknown("NO_CONFIRMED_SWINGS_SAVED"),
            "failure_evidence": provided(engine.get("reason_codes"), source="market_structure_engine.reason_codes")
                                if engine.get("reason_codes") else unknown("STRUCTURE_FAILURE_CONDITIONS_NOT_SAVED"),
        },
        "support_resistance": {
            "support_snapshot": provided(support, source="trend_snapshot.support") if support else unknown(),
            "nearest_support": _value(support, "nearest_support", "trend_snapshot.support"),
            "distance_pct": _value(support, "nearest_support_distance_pct", "trend_snapshot.support"),
            "distance_atr": _value(support, "nearest_support_distance_atr", "trend_snapshot.support"),
            "resistance": unknown("RESISTANCE_NOT_COMPUTED_BY_EXISTING_PATH"),
        },
        "trend_engine_evidence": provided(trend.get("market_structure_engine"), source="trend_snapshot.market_structure_engine")
                                 if trend.get("market_structure_engine") else unknown(),
        "volume": {
            "rvol20": _value(engine, "rvol20", "market_structure_engine"),
            "sequence_included": bool(series and any("volume" in item for item in series)),
        },
        "overheat": {
            "rsi14": _value(last_series, "rsi14", "candidate_state.price_indicator_series"),
            "thresholds": {key: rules[key] for key in ("rsi_overheated", "rsi_hard_block") if key in rules},
            "status": ("OVERHEATED" if isinstance(last_series.get("rsi14"), (int, float)) and
                       "rsi_overheated" in rules and last_series["rsi14"] >= rules["rsi_overheated"]
                       else "NOT_OVERHEATED" if isinstance(last_series.get("rsi14"), (int, float)) else "UNKNOWN"),
        },
        "rule_context": provided(state.get("applicable_rules"), source="candidate_state.applicable_rules")
                        if state.get("applicable_rules") else unknown("RULE_CONTEXT_NOT_SAVED"),
        "market_and_industry": {
            "market_regime": unknown("MARKET_CONTEXT_NOT_SAVED"),
            "industry_trend": unknown("INDUSTRY_CONTEXT_NOT_SAVED"),
            "relative_strength": provided(trend.get("relative_strength"), source="trend_snapshot.relative_strength")
                                if trend.get("relative_strength") else unknown("RELATIVE_STRENGTH_NOT_SAVED"),
        },
        "instrument_and_events": {
            "event_status": _value(row, "event_status", "ticker_result"),
            "company_or_etf_type": unknown("INSTRUMENT_METADATA_NOT_SAVED"),
            "industry": unknown("INSTRUMENT_METADATA_NOT_SAVED"),
            "event_detail": unknown("EVENT_DATA_NOT_SAVED"),
        },
        "trade_evidence": provided(selection, source="ticker_result contract fields") if selection else unknown(),
        "data_identity": {
            key: _value(row, key, "ticker_result")
            for key in ("generation_id", "dataset_fingerprint", "profile_version", "feature_max_date", "effective_daily_session")
        },
        "calculation_identity": {
            key: _value(state, key, "candidate_state")
            for key in ("code_identity", "rules_identity", "daily_identity", "options_identity", "timing_computed_at")
        },
        "not_evaluated": [stage for stage in ("timing_status", "options_status", "event_status", "portfolio_status")
                          if _status(row, stage) == "NOT_EVALUATED"],
        "ai_boundary": {
            "system_verdict_is_authoritative": True,
            "ai_opinion": None,
            "ai_may_not_change_hard_gates": True,
            "full_history_injected": False,
        },
    }
    return packet


def _compact_summary(packet: Mapping[str, Any], detail_file: str, line_number: int) -> dict[str, Any]:
    verdict = packet["system_verdict"]
    return {"symbol": packet["symbol"], "as_of": packet["as_of"],
            "outcome_class": packet["outcome_class"],
            "eligibility_status": verdict["eligibility_status"],
            "timing_status": verdict["timing_status"],
            "final_action": verdict["final_action"],
            "key_reason_codes": verdict["reason_codes"][:4],
            "data_completeness": "PARTIAL" if any(v["status"] == "UNKNOWN" for v in packet["data_identity"].values()) else "COMPLETE",
            "detail": {"file": detail_file, "line_number": line_number, "symbol": packet["symbol"]}}


def build_ai_artifacts(rows, snapshot, *, evidence_window: int = 60):
    packets = [build_ai_evidence_packet(row, snapshot, evidence_window=evidence_window) for row in rows]
    detail_file = "ai_evidence_packets.jsonl"
    summary = [_compact_summary(packet, detail_file, index + 1) for index, packet in enumerate(packets)]
    selected = [p["symbol"] for p in packets if
                p["system_verdict"]["final_action"] == "PCS_TRADE_READY" or
                p["system_verdict"]["timing_status"] == "TIMING_ENTRY_READY"]
    watched = [p["symbol"] for p in packets if p["outcome_class"] == "WATCH"]
    near_miss = [p["symbol"] for p in packets if
                 p["system_verdict"]["timing_status"] == "TIMING_ENTRY_READY" and
                 p["system_verdict"]["final_action"] != "PCS_TRADE_READY"]
    contradictions = [p["symbol"] for p in packets if
                      p["opportunity_context"]["structural_trend"].get("value") == "STRUCTURAL_UPTREND" and
                      p["opportunity_context"]["short_term_phase"].get("value") in {"FAILED_FOLLOW_THROUGH", "SUPPORT_BREAKDOWN"}]
    index = {p["symbol"]: {"file": detail_file, "line_number": n + 1} for n, p in enumerate(packets)}
    return {
        "full_pool_summary": summary,
        "focus_index": {"selected": selected, "user_attention_not_selected": [],
                         "near_miss": near_miss, "contradictions": contradictions,
                         "classification_basis": "existing evidence only; no score added"},
        "ticker_index": index,
        "packets": packets,
    }


def write_ai_artifacts(root: Path, rows, snapshot, *, evidence_window: int = 60) -> dict[str, str]:
    """Write new views beside compatible legacy artifacts and return hashes."""
    from .artifacts import _write_atomic
    from hashlib import sha256
    built = build_ai_artifacts(rows, snapshot, evidence_window=evidence_window)
    hashes = {}
    for name, payload in {
        "full_pool_summary.json": built["full_pool_summary"],
        "focus_index.json": built["focus_index"],
        "ai_evidence_index.json": built["ticker_index"],
    }.items():
        hashes[name] = _write_atomic(root / name, json.dumps(payload, default=str, sort_keys=True, indent=2))
    hashes["ai_evidence_packets.jsonl"] = _write_atomic(
        root / "ai_evidence_packets.jsonl",
        "".join(json.dumps(packet, default=str, sort_keys=True) + "\n" for packet in built["packets"]))
    return hashes


def read_ai_evidence(root: str | Path, symbol: str) -> dict[str, Any] | None:
    """Read one packet using the persisted index; callers can avoid full-pool injection."""
    from hashlib import sha256
    root = Path(root)
    manifest = json.loads((root / "run_manifest.json").read_text(encoding="utf-8"))
    expected = manifest.get("artifact_hashes", {}).get("ai_evidence_packets.jsonl")
    expected_index = manifest.get("artifact_hashes", {}).get("ai_evidence_index.json")
    packet_path = root / "ai_evidence_packets.jsonl"
    index_path = root / "ai_evidence_index.json"
    if (not expected or not expected_index or not packet_path.exists() or not index_path.exists()
            or sha256(packet_path.read_bytes()).hexdigest() != expected
            or sha256(index_path.read_bytes()).hexdigest() != expected_index):
        raise ValueError("AI_EVIDENCE_STALE_OR_HASH_INVALID")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = index.get(str(symbol).upper())
    if not entry:
        return None
    with (root / entry["file"]).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number == entry["line_number"]:
                return json.loads(line)
    return None


def read_ai_evidence_batch(
    root: str | Path,
    symbols: Iterable[str],
    *,
    validated_manifest: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read a bounded symbol set with one hash check and one JSONL traversal.

    ``validated_manifest`` is accepted only from the adapter after
    :func:`pcs.pool.artifacts._load_pool_run` has validated every artifact.
    Standalone callers get the same two-file hash validation here.
    """
    root = Path(root)
    wanted = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
    if not wanted:
        return {}
    manifest = dict(validated_manifest or json.loads((root / "run_manifest.json").read_text(encoding="utf-8")))
    packet_path = root / "ai_evidence_packets.jsonl"
    index_path = root / "ai_evidence_index.json"
    expected = manifest.get("artifact_hashes", {}).get(packet_path.name)
    expected_index = manifest.get("artifact_hashes", {}).get(index_path.name)
    if not expected or not expected_index or not packet_path.exists() or not index_path.exists():
        raise ValueError("AI_EVIDENCE_STALE_OR_HASH_INVALID")
    if validated_manifest is None:
        if (sha256(packet_path.read_bytes()).hexdigest() != expected or
                sha256(index_path.read_bytes()).hexdigest() != expected_index):
            raise ValueError("AI_EVIDENCE_STALE_OR_HASH_INVALID")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    expected_symbols = wanted.intersection(index)
    found: dict[str, dict[str, Any]] = {}
    with packet_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            packet = json.loads(line)
            symbol = str(packet.get("symbol", "")).upper()
            if symbol in expected_symbols:
                entry = index[symbol]
                if entry.get("file") != packet_path.name or entry.get("line_number") != line_number:
                    raise ValueError("AI_EVIDENCE_INDEX_MISMATCH")
                found[symbol] = packet
                if len(found) == len(expected_symbols):
                    break
    if set(found) != expected_symbols:
        raise ValueError("AI_EVIDENCE_INDEX_MISMATCH")
    return found


def _evidence(
    metric_id: str,
    value: Any,
    *,
    source_status: SourceStatus,
    producer: str,
    source_field: str | None,
    definition_ref: str,
    unit: str | None = None,
    data_time: str | None = None,
    execution_status: ExecutionStatus = ExecutionStatus.EXECUTED,
    evidence_refs: Iterable[str] = (),
    consumed: bool | None = None,
    notes: Iterable[str] = (),
) -> EvidenceValue:
    return EvidenceValue(
        metric_id=metric_id, value=_plain(value), unit=unit,
        source_status=source_status, execution_status=execution_status,
        producer=producer, source_field=source_field, data_time=data_time,
        definition_ref=definition_ref, evidence_refs=list(evidence_refs),
        consumed_in_source_run=consumed, notes=list(notes),
    )


def _recorded_metric(
    metric_id: str,
    container: Mapping[str, Any],
    key: str,
    *,
    source_status: SourceStatus,
    producer: str,
    source_field: str,
    definition_ref: str,
    unit: str | None = None,
    data_time: str | None = None,
) -> EvidenceValue:
    value = container.get(key)
    return _evidence(
        metric_id, value,
        source_status=source_status if value is not None else SourceStatus.NOT_RECORDED,
        producer=producer, source_field=source_field, definition_ref=definition_ref,
        unit=unit, data_time=data_time,
        execution_status=(ExecutionStatus.EXECUTED if value is not None
                          else ExecutionStatus.NOT_EVALUATED),
        evidence_refs=[source_field] if value is not None else [],
        notes=[] if value is not None else ["旧产物未保存该字段；未从原因文字反推。"],
    )


def _gate_outcome(reasons: list[str], *, passed: str, failed: str) -> str:
    if passed in reasons:
        return "PASS"
    if failed in reasons:
        return "FAIL"
    return "UNKNOWN"


def _next_conditions(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    reasons = list(row.get("reason_codes") or ())
    conditions: list[dict[str, Any]] = []
    if "DAILY_STALE" in reasons:
        conditions.append({
            "condition_id": "FRESH_CANONICAL_DAILY_REQUIRED",
            "description_zh": "取得覆盖目标交易日且身份验证通过的日线后重新评估。",
            "source_status": SourceStatus.DERIVED.value,
            "derived_from": ["ticker_result.reason_codes:DAILY_STALE"],
        })
    if "waiting_for_qualified_pullback" in reasons:
        conditions.append({
            "condition_id": "QUALIFIED_PULLBACK_REQUIRED",
            "description_zh": "等待生产回调门记录合格回调、足够支撑、牛市结构与健康趋势同时成立。",
            "source_status": SourceStatus.DERIVED.value,
            "derived_from": ["ticker_result.pullback_gate_reasons"],
        })
    if "trend_gate_not_pass" in reasons:
        conditions.append({
            "condition_id": "TREND_GATE_PASS_REQUIRED",
            "description_zh": "等待后续已完成日线使生产趋势门明确通过。",
            "source_status": SourceStatus.DERIVED.value,
            "derived_from": ["ticker_result.trend_gate_reasons"],
        })
    if "trend_gate_reject" in reasons or "UNDERLYING_STRUCTURAL_REJECT" in reasons:
        conditions.append({
            "condition_id": "STRUCTURAL_REJECTION_MUST_CLEAR",
            "description_zh": "后续已完成日线必须使结构性拒绝解除，之后才可重新检查回调门。",
            "source_status": SourceStatus.DERIVED.value,
            "derived_from": ["ticker_result.trend_gate_reasons", "ticker_result.reason_codes"],
        })
    if any(str(code).startswith("OPTIONS_") for code in reasons):
        conditions.append({
            "condition_id": "VERIFIED_OPTIONS_INPUT_REQUIRED",
            "description_zh": "仅期权下游需要：取得目标报价日的 verified options 输入后继续期权阶段。",
            "source_status": SourceStatus.DERIVED.value,
            "derived_from": ["ticker_result.reason_codes"],
        })
    if not conditions and row.get("final_action") != "PCS_TRADE_READY":
        conditions.append({
            "condition_id": "NEXT_DETERMINISTIC_EVALUATION_REQUIRED",
            "description_zh": "等待下一次合法输入由确定性引擎重新评估；旧产物未记录更具体的条件。",
            "source_status": SourceStatus.NOT_RECORDED.value,
            "derived_from": [],
        })
    return conditions


def _score_provenance(scoring_executed: bool, data_time: str | None) -> list[EvidenceValue]:
    execution = ExecutionStatus.EXECUTED if scoring_executed else ExecutionStatus.NOT_EVALUATED
    common_note = ([] if scoring_executed else
                   ["源码可达不等于本票已执行；来源run未保存DecisionEngine评分结果。"])
    return [
        _evidence("candidate.business_quality", 80, source_status=SourceStatus.CONFIGURED_ASSUMPTION,
                  producer="pcs_status._candidate", source_field="business_quality=80",
                  definition_ref="src/pcs/pcs_status.py::_candidate", unit="SCORE_0_100",
                  data_time=data_time, execution_status=execution, consumed=scoring_executed,
                  notes=["固定候选输入，不是公司质量实测。", *common_note]),
        _evidence("candidate.support_score", 0, source_status=SourceStatus.CONFIGURED_ASSUMPTION,
                  producer="pcs_status._candidate", source_field="support_score=0",
                  definition_ref="src/pcs/pcs_status.py::_candidate", unit="SCORE_0_100",
                  data_time=data_time, execution_status=execution, consumed=scoring_executed,
                  notes=["缺少实测支撑分的占位输入，不表示支撑实测为差。", *common_note]),
        _evidence("candidate.price_confirmation", 0, source_status=SourceStatus.CONFIGURED_ASSUMPTION,
                  producer="pcs_status._candidate", source_field="price_confirmation=0",
                  definition_ref="src/pcs/pcs_status.py::_candidate", unit="SCORE_0_100",
                  data_time=data_time, execution_status=execution, consumed=scoring_executed,
                  notes=["固定候选输入，不表示价格确认实测失败；若执行会占trend score的30%。", *common_note]),
        _evidence("candidate.sector_alignment", 80, source_status=SourceStatus.CONFIGURED_ASSUMPTION,
                  producer="pcs_status._candidate", source_field="sector_alignment=80",
                  definition_ref="src/pcs/pcs_status.py::_candidate", unit="SCORE_0_100",
                  data_time=data_time, execution_status=execution, consumed=False,
                  notes=["字段被填入，但当前ScoreBreakdown与OpportunityScorer不消费该字段。", *common_note]),
        _evidence("decision.iv_premium", None, source_status=SourceStatus.NOT_RECORDED,
                  producer="DecisionEngine.evaluate_candidate", source_field=None,
                  definition_ref="min(100, credit / max(short_strike - long_strike, 1) * 500)",
                  unit="SCORE_0_100", data_time=data_time, execution_status=execution,
                  consumed=scoring_executed,
                  notes=["兼容字段名；实际语义是净信用/价差宽度衍生分，不是真实IV premium。",
                         "真实IV诊断未保存在本run的评分轨迹中。", *common_note]),
    ]


def _entrypoint_differences(source_run_executed_pool: bool) -> list[dict[str, Any]]:
    return [
        {
            "difference_id": "PIVOT_CONFIRMATION_WINDOW",
            "production_pool": {"value": "left=3,right=3", "source": "TrendIndicatorConfig"},
            "opportunity_replay": {"value": "left=2,right=2", "source": "opportunity_engine._confirmed_pivot_*"},
            "per_symbol_comparison_status": "IMPLEMENTATION_DIFFERENCE_ONLY",
            "production_pool_executed_in_source_run": source_run_executed_pool,
            "opportunity_replay_executed_in_source_run": False,
        },
        {
            "difference_id": "ATR_IMPLEMENTATION",
            "production_pool": {"value": "TA-Lib ATR(14)", "source": "trend.indicators.calculate_base_indicators"},
            "opportunity_replay": {"value": "rolling mean of true range(14)", "source": "trend.opportunity_engine"},
            "per_symbol_comparison_status": "IMPLEMENTATION_DIFFERENCE_ONLY",
            "production_pool_executed_in_source_run": source_run_executed_pool,
            "opportunity_replay_executed_in_source_run": False,
        },
        {
            "difference_id": "LONG_MOVING_AVERAGE",
            "production_pool": {"value": "SMA200", "source": "trend.indicators/moving_averages"},
            "opportunity_replay": {"value": "EMA200", "source": "trend.opportunity_engine"},
            "per_symbol_comparison_status": "IMPLEMENTATION_DIFFERENCE_ONLY",
            "production_pool_executed_in_source_run": source_run_executed_pool,
            "opportunity_replay_executed_in_source_run": False,
        },
        {
            "difference_id": "ENTRY_READY_ROUTE",
            "production_pool": {"value": "trend_gate + pullback_gate", "source": "pool.runner"},
            "market_context": {"value": "short_term_phase direct mapping", "source": "market_context.build_market_context"},
            "opportunity_replay": {"value": "opportunity state machine", "source": "trend.opportunity_engine"},
            "per_symbol_comparison_status": "ALTERNATE_RESULTS_NOT_RECORDED",
            "production_pool_executed_in_source_run": source_run_executed_pool,
            "market_context_executed_in_source_run": False,
            "opportunity_replay_executed_in_source_run": False,
        },
    ]


def _semantic_result_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def explain_selection(input: ExplanationInput) -> SelectionExplanation:
    """Explain one persisted selection result without reading data or running strategy code."""
    ctx = input.call_context
    row = _plain(input.ticker_result)
    if str(row.get("symbol", "")).upper() != ctx.symbol:
        raise ValueError("EXPLANATION_SYMBOL_MISMATCH")
    if row.get("run_id") and row.get("run_id") != ctx.run_id:
        raise ValueError("EXPLANATION_RUN_ID_MISMATCH")

    state = row.get("candidate_state") or {}
    trend = state.get("trend_evidence") or {}
    engine = trend.get("market_structure_engine") or {}
    support = trend.get("support") or {}
    daily_time = row.get("feature_max_date") or ctx.effective_daily_session
    timing_executed = row.get("timing_status") != "NOT_EVALUATED"
    options_stage_reached = row.get("options_status") != "NOT_EVALUATED"
    contract_evaluation_status = state.get("contract_evaluation_status", "NOT_EVALUATED")
    selection_result = row.get("selection_result") or {}
    scoring_executed = bool(selection_result and
                            (selection_result.get("decision") or selection_result.get("scores")))

    measurements = [
        _recorded_metric("close", state, "close", source_status=SourceStatus.OBSERVED,
                         producer="pool.runner persisted timing evidence",
                         source_field="ticker_result.candidate_state.close",
                         definition_ref="canonical completed daily close", unit="USD_PER_SHARE",
                         data_time=daily_time),
        _recorded_metric("atr14", state, "atr", source_status=SourceStatus.DERIVED,
                         producer="trend.indicators.calculate_base_indicators",
                         source_field="ticker_result.candidate_state.atr",
                         definition_ref="TA-Lib ATR(14)", unit="USD_PER_SHARE",
                         data_time=daily_time),
        _recorded_metric("structural_trend", row, "structural_trend", source_status=SourceStatus.DERIVED,
                         producer="trend.market_structure_engine",
                         source_field="ticker_result.structural_trend",
                         definition_ref="market-structure-engine", data_time=daily_time),
        _recorded_metric("trend_health", {}, "trend_health", source_status=SourceStatus.DERIVED,
                         producer="trend.interpretation", source_field="not saved as a field",
                         definition_ref="trend.interpretation", data_time=daily_time),
        _recorded_metric("short_term_phase", row, "short_term_phase", source_status=SourceStatus.DERIVED,
                         producer="trend.market_structure_engine",
                         source_field="ticker_result.short_term_phase",
                         definition_ref="market-structure-engine", data_time=daily_time),
        _recorded_metric("pullback_depth", engine, "pullback_depth_atr", source_status=SourceStatus.DERIVED,
                         producer="trend.market_structure_engine",
                         source_field="candidate_state.trend_evidence.market_structure_engine.pullback_depth_atr",
                         definition_ref="market-structure-engine", unit="ATR", data_time=daily_time),
        _recorded_metric("nearest_support", support, "nearest_support", source_status=SourceStatus.DERIVED,
                         producer="trend.support.analyze_support",
                         source_field="candidate_state.trend_evidence.support.nearest_support",
                         definition_ref="trend.support", unit="USD_PER_SHARE", data_time=daily_time),
        _recorded_metric("support_distance_atr", support, "nearest_support_distance_atr",
                         source_status=SourceStatus.DERIVED, producer="trend.support.analyze_support",
                         source_field="candidate_state.trend_evidence.support.nearest_support_distance_atr",
                         definition_ref="trend.support", unit="ATR", data_time=daily_time),
        _recorded_metric("support_confluence", support, "support_confluence_state",
                         source_status=SourceStatus.DERIVED, producer="trend.support.analyze_support",
                         source_field="candidate_state.trend_evidence.support.support_confluence_state",
                         definition_ref="trend.support", data_time=daily_time),
        _recorded_metric("follow_through_confirmed", engine, "follow_through_confirmed",
                         source_status=SourceStatus.DERIVED, producer="trend.market_structure_engine",
                         source_field="candidate_state.trend_evidence.market_structure_engine.follow_through_confirmed",
                         definition_ref="market-structure-engine", unit="BOOLEAN", data_time=daily_time),
    ]
    by_id = {item.metric_id: item for item in measurements}
    source_reasons = list(row.get("reason_codes") or ())
    primary = source_reasons[-1] if source_reasons else None
    secondary = source_reasons[:-1]
    metadata_unknowns = [
        "instrument_type", "industry", "business_quality_evidence",
        "static_liquidity_evidence", "event_detail",
    ]
    next_conditions = _next_conditions(row)
    score_provenance = _score_provenance(scoring_executed, daily_time)
    trend_reasons = list(row.get("trend_gate_reasons") or ())
    pullback_reasons = list(row.get("pullback_gate_reasons") or ())
    rule_evaluations = [
        RuleEvaluation(
            rule_id="pool.static_eligibility", rule_version="legacy-pool",
            rule_role="ADMISSION", execution_status=ExecutionStatus.EXECUTED,
            gate_outcome=("PASS" if row.get("eligibility_status") == "PCS_ELIGIBLE" else
                          "FAIL" if row.get("eligibility_status") in {"HARD_EXCLUDED", "TEMP_INELIGIBLE"}
                          else "UNKNOWN"),
            predicate_value=(True if row.get("eligibility_status") == "PCS_ELIGIBLE" else
                             False if row.get("eligibility_status") in {"HARD_EXCLUDED", "TEMP_INELIGIBLE"}
                             else None),
            input_refs=["ticker_result.eligibility_status"], reason_codes=source_reasons,
        ),
        RuleEvaluation(
            rule_id="pool.trend_gate", rule_version="source-run",
            rule_role="ADMISSION",
            execution_status=(ExecutionStatus.EXECUTED if timing_executed else ExecutionStatus.NOT_EVALUATED),
            gate_outcome=_gate_outcome(trend_reasons, passed="trend_gate_pass", failed="trend_gate_reject"),
            input_refs=["ticker_result.trend_gate_reasons"], reason_codes=trend_reasons,
        ),
        RuleEvaluation(
            rule_id="pool.pullback_gate", rule_version="source-run",
            rule_role="ADMISSION",
            execution_status=(ExecutionStatus.EXECUTED if timing_executed else ExecutionStatus.NOT_EVALUATED),
            gate_outcome=("PASS" if row.get("timing_status") == "TIMING_ENTRY_READY" else
                          "UNKNOWN" if timing_executed else "NOT_EVALUATED"),
            input_refs=["ticker_result.pullback_gate_reasons"], reason_codes=pullback_reasons,
            policy_ref="candidate_state.applicable_rules",
        ),
    ]
    entities = []
    if support:
        entities.append({
            "entity_id": f"{ctx.symbol}:{ctx.effective_daily_session}:support_snapshot",
            "entity_type": "SUPPORT_SNAPSHOT", "source_status": SourceStatus.DERIVED.value,
            "nearest_support": support.get("nearest_support"),
            "support_type": support.get("nearest_support_type"),
            "all_saved_supports": support.get("supports", []),
            "evidence_ref": "candidate_state.trend_evidence.support",
        })

    status = ("COMPLETED" if row.get("eligibility_status") == "PCS_ELIGIBLE" and timing_executed
              else "PARTIAL")
    data_quality_missing = [item.metric_id for item in measurements
                            if item.source_status in {SourceStatus.MISSING, SourceStatus.NOT_RECORDED}]
    selection_explanation = {
        "basic_eligibility": {
            "status": row.get("eligibility_status", "NOT_RECORDED"),
            "display_zh": ("基础筛选通过" if row.get("eligibility_status") == "PCS_ELIGIBLE"
                           else "基础筛选未通过或资料受阻"),
            "does_not_prove": ["业务质量", "静态流动性", "事件安全"],
        },
        "static_metadata_status": "UNKNOWN",
        "daily_session": ctx.effective_daily_session,
        "stock_structure": by_id["structural_trend"].model_dump(mode="json"),
        "trend_health": by_id["trend_health"].model_dump(mode="json"),
        "short_term_phase": by_id["short_term_phase"].model_dump(mode="json"),
        "pullback_depth": by_id["pullback_depth"].model_dump(mode="json"),
        "support": {
            "level": by_id["nearest_support"].model_dump(mode="json"),
            "distance_atr": by_id["support_distance_atr"].model_dump(mode="json"),
            "confluence": by_id["support_confluence"].model_dump(mode="json"),
        },
        "confirmation": by_id["follow_through_confirmed"].model_dump(mode="json"),
        "legacy_timing": {"status": row.get("timing_status", "NOT_RECORDED"),
                          "execution_status": (ExecutionStatus.EXECUTED.value if timing_executed
                                               else ExecutionStatus.NOT_EVALUATED.value)},
        "options_stage": {
            "status": row.get("options_status", "NOT_RECORDED"),
            "stage_reached": options_stage_reached,
            "quote_read_status": state.get("verified_read_status", "NOT_RECORDED"),
            "contract_evaluation_status": contract_evaluation_status,
            "decision_scoring_executed": scoring_executed,
        },
        "score_validity": ("RECORDED_EXECUTION" if scoring_executed else "NOT_EVALUATED"),
        "iv_diagnostics": {"status": "NOT_RECORDED",
                           "note": "来源run未保存真实IV评分诊断；iv_premium字段名不代表已测IV。"},
        "final_action_unchanged": row.get("final_action"),
        "primary_reason": primary,
        "secondary_reasons": secondary,
        "next_conditions": next_conditions,
    }
    effective_policy = {
        "source_run_applicable_rules": state.get("applicable_rules") or {},
        "score_weights": {
            "market_regime": .15, "underlying_quality": .12, "trend": .12,
            "support": .12, "liquidity": .12, "rollability": .08,
            "strike_buffer": .12, "iv_premium": .08,
            "portfolio_capacity": .06, "news_risk": .03,
        },
        "score_weights_source": "config/pcs_rules.yaml:scoring.weights",
        "candidate_factory_assumptions": {
            "business_quality": 80, "support_score": 0,
            "price_confirmation": 0, "sector_alignment": 80,
        },
        "input_policy": input.effective_policy,
    }
    entrypoint_disagreements = _entrypoint_differences(timing_executed)
    semantic = {
        "module": "selection_explanation", "version": "1.0", "symbol": ctx.symbol,
        "as_of": ctx.requested_as_of, "source_run_id": ctx.run_id,
        "source_artifacts": [{
            "source_kind": ref.source_kind, "schema_version": ref.schema_version,
            "sha256": ref.sha256, "record_identity": ref.record_identity,
        } for ref in input.source_references],
        "selection_explanation": selection_explanation,
        "measurements": [item.model_dump(mode="json") for item in measurements],
        "rules": [item.model_dump(mode="json") for item in rule_evaluations],
        "policy": effective_policy,
        "score_provenance": [item.model_dump(mode="json") for item in score_provenance],
        "entrypoint_disagreements": entrypoint_disagreements,
    }
    result_id = _semantic_result_id(semantic)
    capabilities = {
        "stock_explanation": {"status": CapabilityStatus.COMPLETED.value
                              if timing_executed else CapabilityStatus.PARTIAL.value,
                              "required_missing": data_quality_missing},
        "score_provenance": {"status": CapabilityStatus.COMPLETED.value,
                             "execution_status": (ExecutionStatus.EXECUTED.value if scoring_executed
                                                  else ExecutionStatus.NOT_EVALUATED.value)},
        "options_evaluation": {"status": (CapabilityStatus.COMPLETED.value
                                           if contract_evaluation_status == "EVALUATED"
                                           else CapabilityStatus.NOT_EVALUATED.value)},
    }
    data = SelectionExplanationData(
        identity={"result_id": result_id, "scope": ctx.scope,
                  "source_run_id": ctx.run_id, "source_final_action": row.get("final_action")},
        time_context={"requested_as_of": ctx.requested_as_of,
                      "effective_daily_session": ctx.effective_daily_session,
                      "source_data_timestamp": None,
                      "source_data_timestamp_status": "NOT_RECORDED",
                      "completed_daily_bar": bool(row.get("feature_max_date")),
                      "calculated_at": datetime.now(timezone.utc).isoformat()},
        capabilities=capabilities, measurements=measurements, entities=entities,
        rule_evaluations=rule_evaluations,
        assessment={"conclusion": row.get("final_action"), "scope": "EXPLANATION_ONLY",
                    "primary_reason": primary, "secondary_reasons": secondary,
                    "supporting_evidence_refs": [x.source_field for x in measurements if x.value is not None],
                    "contrary_evidence_refs": [],
                    "unresolved_conflicts": ["入口实现存在差异；替代入口未在来源run执行。"]},
        effective_policy=effective_policy,
        data_quality={"missing_fields": data_quality_missing,
                      "not_recorded_is_not_zero": True,
                      "source_artifacts_hash_validated": all(ref.validated for ref in input.source_references)},
        next_conditions=next_conditions, provenance=input.source_references,
        detail_index=[{"kind": "source_ai_evidence", "symbol": ctx.symbol,
                       "record_identity": ref.record_identity, "source_id": ref.source_id}
                      for ref in input.source_references if ref.source_kind == "AI_EVIDENCE_PACKET"],
        selection_explanation=selection_explanation,
        entrypoint_disagreements=entrypoint_disagreements,
        metadata_unknowns=metadata_unknowns, score_provenance=score_provenance,
    )
    return SelectionExplanation(
        symbol=ctx.symbol, as_of=ctx.requested_as_of, status=status,
        data_timestamp=None, run_id=ctx.run_id, request_id=ctx.request_id,
        reason_codes=source_reasons, data=data,
        explanation=(f"{ctx.symbol}：{selection_explanation['basic_eligibility']['display_zh']}；"
                     f"旧决策保持为 {row.get('final_action')}，评分执行状态为 "
                     f"{'EXECUTED' if scoring_executed else 'NOT_EVALUATED'}。"),
    )


def load_selection_explanation_inputs(
    run_directory: str | Path,
    symbols: Iterable[str],
    *,
    request_id: str,
) -> list[ExplanationInput]:
    """Trusted adapter: validate one saved run, then prepare bounded typed inputs."""
    from .artifacts import _load_pool_run

    root = Path(run_directory)
    manifest_path = root / "run_manifest.json"
    loaded = _load_pool_run(manifest_path)
    if loaded is None:
        raise ValueError("POOL_ARTIFACT_NOT_CURRENT_OR_HASH_INVALID")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha = sha256(manifest_path.read_bytes()).hexdigest()
    ordered = list(dict.fromkeys(str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()))
    rows = {str(row.get("symbol", "")).upper(): row for row in loaded["ticker_results"]}
    missing = [symbol for symbol in ordered if symbol not in rows]
    if missing:
        raise ValueError("SELECTION_SYMBOL_NOT_IN_RUN:" + ",".join(missing))
    packets = read_ai_evidence_batch(root, ordered, validated_manifest=manifest)
    snapshot = loaded["snapshot"]
    inputs = []
    for symbol in ordered:
        row = rows[symbol]
        packet = packets.get(symbol)
        refs = [
            SourceReference(
                source_id=str(manifest_path.resolve()), source_kind="POOL_RUN_MANIFEST",
                schema_version=str(manifest.get("schema_version") or manifest.get("version") or "legacy"),
                sha256=manifest_sha, record_identity=manifest.get("run_id"), validated=True,
            ),
            SourceReference(
                source_id=str((root / "daily_timing.json").resolve()), source_kind="TICKER_RESULT",
                sha256=manifest["artifact_hashes"]["daily_timing.json"],
                record_identity=f"{manifest['run_id']}:{symbol}", validated=True,
            ),
        ]
        if packet is not None:
            refs.append(SourceReference(
                source_id=str((root / "ai_evidence_packets.jsonl").resolve()),
                source_kind="AI_EVIDENCE_PACKET", schema_version=str(packet.get("schema_version")),
                sha256=manifest["artifact_hashes"]["ai_evidence_packets.jsonl"],
                record_identity=f"{manifest['run_id']}:{symbol}", validated=True,
            ))
        inputs.append(ExplanationInput(
            call_context=CallContext(
                symbol=symbol, requested_as_of=str(row.get("as_of") or snapshot.get("as_of")),
                effective_daily_session=(row.get("effective_daily_session") or
                                         snapshot.get("effective_daily_session")),
                mode=str(snapshot.get("mode")), run_id=str(manifest["run_id"]),
                request_id=request_id,
            ),
            ticker_result=row, ai_evidence=packet,
            effective_policy={"source": "candidate_state.applicable_rules",
                              "values": (row.get("candidate_state") or {}).get("applicable_rules") or {}},
            source_references=refs,
        ))
    return inputs


def build_selection_explanations(
    run_directory: str | Path,
    symbols: Iterable[str],
    *,
    request_id: str,
) -> list[SelectionExplanation]:
    return [explain_selection(item) for item in
            load_selection_explanation_inputs(run_directory, symbols, request_id=request_id)]


def selection_explanation_to_ai_view(result: SelectionExplanation) -> dict[str, Any]:
    detail = result.data.selection_explanation
    return {
        "schema": "pcs.selection_explanation.ai_view", "schema_version": "1",
        "result_id": result.data.identity["result_id"], "symbol": result.symbol,
        "as_of": result.as_of, "authoritative_action": detail["final_action_unchanged"],
        "summary": result.explanation,
        "key_facts": {
            "structure": detail["stock_structure"], "phase": detail["short_term_phase"],
            "support": detail["support"], "confirmation": detail["confirmation"],
        },
        "primary_reason": detail["primary_reason"],
        "secondary_reasons": detail["secondary_reasons"],
        "missing": result.data.data_quality["missing_fields"],
        "next_conditions": result.data.next_conditions,
        "score_provenance": [item.model_dump(mode="json") for item in result.data.score_provenance],
        "ai_boundary": {"may_change_authoritative_action": False,
                        "new_inferences_must_be_separate": True},
    }


def selection_explanation_to_markdown(result: SelectionExplanation) -> str:
    detail = result.data.selection_explanation
    def show(value: Mapping[str, Any]) -> str:
        actual = value.get("value")
        return ("未记录" if actual is None else str(actual)) + f" ({value.get('source_status')})"
    lines = [
        f"## {result.symbol}", "",
        f"- 结论：{detail['basic_eligibility']['display_zh']}；原决策 **{detail['final_action_unchanged']}**（未改写）",
        f"- 结果身份：`{result.data.identity['result_id']}`",
        f"- 数据日：{detail['daily_session']}；请求时刻：{result.as_of}",
        f"- 结构：{show(detail['stock_structure'])}",
        f"- 趋势健康：{show(detail['trend_health'])}",
        f"- 短期阶段：{show(detail['short_term_phase'])}",
        f"- 回调深度：{show(detail['pullback_depth'])}",
        f"- 支撑：{show(detail['support']['level'])}；距离 {show(detail['support']['distance_atr'])}",
        f"- 确认：{show(detail['confirmation'])}",
        f"- 期权阶段：状态 {detail['options_stage']['status']}；合约评估 {detail['options_stage']['contract_evaluation_status']}；评分执行 {detail['options_stage']['decision_scoring_executed']}",
        f"- 主要原因：{detail['primary_reason'] or '无'}",
        "", "下一观察条件：", "",
    ]
    lines.extend(f"- {item['description_zh']} [{item['source_status']}]"
                 for item in result.data.next_conditions)
    lines.extend(["", "评分来源：", "",
                  "| 字段 | 值 | 来源状态 | 本次执行 | 本次消费 | 含义 |",
                  "|---|---:|---|---|---|---|"])
    for item in result.data.score_provenance:
        lines.append(f"| {item.metric_id} | {item.value if item.value is not None else 'null'} | "
                     f"{item.source_status.value} | {item.execution_status.value} | "
                     f"{item.consumed_in_source_run} | {' '.join(item.notes)} |")
    lines.extend(["", "入口差异：", "",
                  "| 差异 | 来源run实际执行 | 对照结果状态 |",
                  "|---|---|---|"])
    for item in result.data.entrypoint_disagreements:
        lines.append(f"| {item['difference_id']} | {item['production_pool_executed_in_source_run']} | "
                     f"{item['per_symbol_comparison_status']} |")
    lines.append("")
    return "\n".join(lines)


def selection_explanations_to_csv(results: Iterable[SelectionExplanation]) -> str:
    fields = ["symbol", "as_of", "result_id", "eligibility_status", "static_metadata_status",
              "structural_trend", "trend_health", "short_term_phase", "pullback_depth_atr",
              "nearest_support", "support_distance_atr", "confirmation", "timing_status",
              "options_status", "contract_evaluation_status", "decision_scoring_executed",
              "final_action", "primary_reason", "missing_fields"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for result in results:
        d = result.data.selection_explanation
        writer.writerow({
            "symbol": result.symbol, "as_of": result.as_of,
            "result_id": result.data.identity["result_id"],
            "eligibility_status": d["basic_eligibility"]["status"],
            "static_metadata_status": d["static_metadata_status"],
            "structural_trend": d["stock_structure"]["value"],
            "trend_health": d["trend_health"]["value"],
            "short_term_phase": d["short_term_phase"]["value"],
            "pullback_depth_atr": d["pullback_depth"]["value"],
            "nearest_support": d["support"]["level"]["value"],
            "support_distance_atr": d["support"]["distance_atr"]["value"],
            "confirmation": d["confirmation"]["value"],
            "timing_status": d["legacy_timing"]["status"],
            "options_status": d["options_stage"]["status"],
            "contract_evaluation_status": d["options_stage"]["contract_evaluation_status"],
            "decision_scoring_executed": d["options_stage"]["decision_scoring_executed"],
            "final_action": d["final_action_unchanged"], "primary_reason": d["primary_reason"],
            "missing_fields": ";".join(result.data.data_quality["missing_fields"]),
        })
    return output.getvalue()


def selection_explanation_field_dictionary() -> dict[str, Any]:
    return {
        "schema": "pcs.selection_explanation.field_dictionary", "version": "1.0",
        "source_status": {item.value: item.name for item in SourceStatus},
        "execution_status": {item.value: item.name for item in ExecutionStatus},
        "important_fields": {
            "data.selection_explanation": "逐票中文/程序共用的决策路径摘要；不改变原决策。",
            "data.measurements": "带单位、来源、时间、定义和缺失语义的事实。",
            "data.score_provenance": "评分输入来源、消费关系与实际执行状态。",
            "data.entrypoint_disagreements": "源码入口实现差异及来源run是否实际执行。",
            "data.metadata_unknowns": "旧产物未提供的静态metadata，不因PCS_ELIGIBLE而推定。",
            "data.next_conditions": "由已记录状态确定性生成的下一观察条件。",
        },
        "units": {"USD_PER_SHARE": "美元/股", "ATR": "以ATR为单位的距离",
                  "SCORE_0_100": "0到100的评分输入或分项", "BOOLEAN": "布尔值"},
    }


def write_selection_explanation_artifacts(
    output_directory: str | Path,
    results: Iterable[SelectionExplanation],
) -> Path:
    """Persist four deterministic views and their hashes in an isolated directory."""
    from .artifacts import _write_atomic

    root = Path(output_directory)
    root.mkdir(parents=True, exist_ok=True)
    ordered = sorted(results, key=lambda item: item.symbol)
    payloads = {
        "selection_explanations.json": json.dumps(
            [item.model_dump(mode="json") for item in ordered], ensure_ascii=False,
            sort_keys=True, indent=2, allow_nan=False),
        "selection_explanations.csv": selection_explanations_to_csv(ordered),
        "selection_explanations.zh-CN.md": (
            "# PCS 逐票解释（v1.4 第1步）\n\n"
            "本报告只解释保存证据；不改变原交易判定。\n\n" +
            "\n".join(selection_explanation_to_markdown(item) for item in ordered)),
        "selection_explanations.ai.json": json.dumps(
            [selection_explanation_to_ai_view(item) for item in ordered], ensure_ascii=False,
            sort_keys=True, indent=2, allow_nan=False),
        "selection_explanation.schema.json": json.dumps(
            {"input": ExplanationInput.model_json_schema(),
             "output": SelectionExplanation.model_json_schema()},
            ensure_ascii=False, sort_keys=True, indent=2),
        "field_dictionary.json": json.dumps(
            selection_explanation_field_dictionary(), ensure_ascii=False, sort_keys=True, indent=2),
    }
    hashes = {name: _write_atomic(root / name, content) for name, content in payloads.items()}
    manifest = {
        "schema": "pcs.selection_explanation.export", "schema_version": "1",
        "module_version": "selection-explanation-v1.4-step1.1",
        "record_count": len(ordered), "symbols": [item.symbol for item in ordered],
        "result_ids": [item.data.identity["result_id"] for item in ordered],
        "artifact_hashes": hashes,
        "source_run_ids": sorted({item.run_id for item in ordered}),
        "view_invariant": "All views are generated from the same SelectionExplanation objects.",
    }
    _write_atomic(root / "manifest.json", json.dumps(manifest, ensure_ascii=False,
                                                      sort_keys=True, indent=2))
    return root


def upgrade_current_pool_artifacts(run_directory: str | Path, *, evidence_window: int = 60) -> Path:
    """Add AI views to one hash-valid current run without scanning or reading data."""
    from .artifacts import _load_pool_run, _write_atomic
    from hashlib import sha256
    root = Path(run_directory)
    loaded = _load_pool_run(root / "run_manifest.json")
    if loaded is None:
        raise ValueError("POOL_ARTIFACT_NOT_CURRENT_OR_HASH_INVALID")
    hashes = write_ai_artifacts(root, loaded["ticker_results"], loaded["snapshot"],
                                evidence_window=evidence_window)
    manifest_path = root / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.setdefault("artifact_hashes", {}).update(hashes)
    manifest["ai_evidence"] = {"schema": "pcs.ai_evidence_packet", "version": "1",
                                "window_sessions": evidence_window,
                                "summary": "full_pool_summary.json",
                                "index": "ai_evidence_index.json",
                                "detail": "ai_evidence_packets.jsonl"}
    _write_atomic(manifest_path, json.dumps(manifest, default=str, sort_keys=True, indent=2))
    return root
