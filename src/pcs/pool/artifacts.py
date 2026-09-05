"""Atomic, auditable U1 pool artifacts."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json
import pandas as pd

from .models import PoolScanResult


def _write_atomic(path: Path, payload: str) -> str:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    digest = sha256(payload.encode("utf-8")).hexdigest()
    temporary.replace(path)
    return digest


def _write_parquet_atomic(path: Path, rows) -> str:
    temporary = path.with_name(path.name + ".tmp")
    pd.DataFrame(rows).to_parquet(temporary, index=False)
    digest = sha256(temporary.read_bytes()).hexdigest()
    temporary.replace(path)
    return digest


def persist_pool_artifacts(result: PoolScanResult, output_directory: str | Path) -> Path:
    """Write the U1 snapshot/results and manifest; later stages are explicit."""
    root = Path(output_directory) / result.snapshot.run_id
    root.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    snapshot = json.dumps(asdict(result.snapshot), default=str, sort_keys=True, indent=2)
    files["universe_snapshot.json"] = _write_atomic(root / "universe_snapshot.json", snapshot)
    files["universe_snapshot.parquet"] = _write_parquet_atomic(root / "universe_snapshot.parquet", [
        {"symbol": row.symbol, "run_id": row.run_id, "as_of": row.as_of,
         "universe_snapshot_id": result.snapshot.universe_snapshot_id}
        for row in result.ticker_results])
    flat_rows = [{key: value for key, value in asdict(row).items()
                  if key != "discovered_contracts"}
                 for row in result.ticker_results]
    rows = json.dumps(flat_rows, default=str, sort_keys=True, indent=2)
    files["static_eligibility.json"] = _write_atomic(root / "static_eligibility.json", rows)
    files["daily_timing.json"] = _write_atomic(root / "daily_timing.json", rows)
    parquet_rows = flat_rows
    files["static_eligibility.parquet"] = _write_parquet_atomic(root / "static_eligibility.parquet", parquet_rows)
    files["daily_timing.parquet"] = _write_parquet_atomic(root / "daily_timing.parquet", parquet_rows)
    empty_schema = {"symbol": [], "run_id": [], "as_of": [], "status": [], "reason_codes": []}
    options_evaluated = sum(row.options_status.value != "NOT_EVALUATED"
                            for row in result.ticker_results)
    option_rows = []
    for candidate in result.discovered_contracts:
        option_rows.append({"run_id": result.snapshot.run_id,
                            "as_of": result.snapshot.as_of,
                            **dict(candidate)})
    for name in ("intraday_overlay.parquet", "final_decisions.parquet"):
        files[name] = _write_parquet_atomic(root / name, empty_schema)
    files["options_shortlist.parquet"] = _write_parquet_atomic(
        root / "options_shortlist.parquet", option_rows if option_rows else empty_schema)
    summary = json.dumps(dict(result.summary), sort_keys=True, indent=2)
    files["aggregate_summary.json"] = _write_atomic(root / "aggregate_summary.json", summary)
    recovery_payload = json.dumps(
        {"summary": dict(result.recovery_summary),
         "by_symbol": dict(result.preparation_results)},
        default=str, sort_keys=True, indent=2)
    files["preparation_recovery.json"] = _write_atomic(
        root / "preparation_recovery.json", recovery_payload)
    transitions = []
    failures = []
    for row in result.ticker_results:
        transitions.append({"symbol": row.symbol, "previous_state": "RAW_UNIVERSE",
                            "new_state": row.final_action.value, "reason_codes": list(row.reason_codes),
                            "as_of": row.as_of, "run_id": row.run_id,
                            "generation_id": row.generation_id, "dataset_fingerprint": row.dataset_fingerprint,
                            "profile_version": row.profile_version, "engine_version": result.snapshot.engine_version})
        if row.final_action.value in {"DATA_FAILED", "TEMP_BLOCKED", "REJECTED"}:
            failures.append({"symbol": row.symbol, "run_id": row.run_id,
                             "reason_codes": list(row.reason_codes), "final_action": row.final_action.value})
    files["state_transitions.jsonl"] = _write_atomic(root / "state_transitions.jsonl",
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in transitions))
    files["failures.jsonl"] = _write_atomic(root / "failures.jsonl",
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in failures))
    report = (f"# PCS pool scan {result.snapshot.run_id}\n\n"
              f"Mode: `{result.snapshot.mode}`  \nAs of: `{result.snapshot.as_of}`  \n"
              f"Symbols: **{len(result.ticker_results)}**  \n\n"
              f"OPTIONS_EVALUATED_COUNT: **{options_evaluated}**  \n"
              f"OPTIONS_DISCOVERED_TICKER_COUNT: **{sum(bool(row.discovered_contracts) for row in result.ticker_results)}**  \n"
              f"DISCOVERED_SPREAD_COUNT: **{len(result.discovered_contracts)}**  \n\n"
              "Stage-B contract discovery is evidence only; Stage-C selection and "
              "approval are not implemented in this run.\n")
    files["human_report.md"] = _write_atomic(root / "human_report.md", report)
    files["ai_decision_packets.jsonl"] = _write_atomic(root / "ai_decision_packets.jsonl",
        "".join(json.dumps({"symbol": row.symbol, "final_action": row.final_action.value,
                            "reason_codes": list(row.reason_codes)}, sort_keys=True) + "\n"
                for row in result.ticker_results))
    manifest = {
        "current": True, "run_id": result.snapshot.run_id, "as_of": result.snapshot.as_of,
        "mode": result.snapshot.mode, "universe_snapshot_id": result.snapshot.universe_snapshot_id,
        "stage_status": {"RAW_UNIVERSE": "COMPLETE", "STATIC_ELIGIBILITY": "COMPLETE",
                          "DAILY_TIMING": "COMPLETE",
                          "OPTIONS_SHORTLIST": "COMPLETE" if options_evaluated else "NOT_RUN",
                          "EVENT_GATE": "COMPLETE" if any(row.event_status != "NOT_EVALUATED" for row in result.ticker_results) else "NOT_RUN",
                          "PORTFOLIO_GATE": "COMPLETE" if any(row.portfolio_status != "NOT_EVALUATED" for row in result.ticker_results) else "NOT_RUN"},
        "input_symbol_count": len(result.ticker_results), "summary": dict(result.summary),
        "counters": dict(result.counters), "recovery_summary": dict(result.recovery_summary),
        "artifact_hashes": files,
    }
    _write_atomic(root / "run_manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    return root
