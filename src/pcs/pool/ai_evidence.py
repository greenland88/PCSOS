"""Bounded, source-grounded evidence views for downstream AI readers.

This module only reshapes persisted scan evidence.  It never calls a data
provider, recomputes a strategy indicator, or changes a deterministic verdict.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping
import json


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
