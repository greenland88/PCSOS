"""Atomic, auditable U1 pool artifacts."""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import json
import pandas as pd

from .models import PoolScanResult


class CandidateCheckpoints:
    """Run-local, hashed recovery evidence, including interrupted runs.

    These files never confer data readiness: callers must independently resolve
    the current canonical inputs and compare identities before using a row.
    """
    def __init__(self, output_directory, run_id):
        self.root = Path(output_directory) if output_directory is not None else None
        self.run_id = run_id
        self.previous = {}
        if self.root is not None and self.root.exists():
            paths = sorted(self.root.glob("*/candidate_checkpoints/*.json"),
                           key=lambda p: p.stat().st_mtime_ns, reverse=True)
            for path in paths:
                try:
                    envelope = json.loads(path.read_text(encoding="utf-8"))
                    payload = envelope["payload"]
                    if sha256(self._encode(payload).encode()).hexdigest() != envelope["sha256"]:
                        continue
                    row = payload["row"]
                    if payload["version"] != 1 or row["run_id"] != path.parent.parent.name:
                        continue
                    self.previous.setdefault(row["symbol"], payload)
                except (OSError, ValueError, KeyError, TypeError):
                    continue

    @staticmethod
    def _encode(value):
        return json.dumps(value, sort_keys=True, default=str)

    def save(self, row, identity):
        if self.root is None:
            return
        payload = {"version": 1, "identity": identity, "row": asdict(row)}
        envelope = {"payload": payload,
                    "sha256": sha256(self._encode(payload).encode()).hexdigest()}
        directory = self.root / self.run_id / "candidate_checkpoints"
        directory.mkdir(parents=True, exist_ok=True)
        # Use a digest filename; symbols must never become filesystem paths.
        path = directory / (sha256(row.symbol.encode()).hexdigest() + ".json")
        _write_atomic(path, self._encode(envelope))

    def resume(self, symbol, identity):
        from .models import (TickerScanResult, EligibilityStatus, TimingStatus,
                             OptionsStatus, FinalAction)
        payload = self.previous.get(symbol)
        if not payload or payload["identity"] != identity:
            return None
        try:
            values = dict(payload["row"])
            for field, enum in (("eligibility_status", EligibilityStatus),
                                ("timing_status", TimingStatus), ("options_status", OptionsStatus),
                                ("final_action", FinalAction)):
                values[field] = enum(values[field])
            for field in ("reason_codes", "warnings", "trend_gate_reasons", "pullback_gate_reasons",
                          "selection_reason_codes", "preparation_reason_codes", "reentry_conditions",
                          "discovered_contracts"):
                values[field] = tuple(values.get(field, ()))
            row = TickerScanResult(**values)
            required = {"close", "atr", "timing_reason_codes", "timing_computed_at", "daily_identity"}
            if row.timing_status != TimingStatus.TIMING_ENTRY_READY or not required <= row.candidate_state.keys():
                return None
            return row
        except (ValueError, TypeError, KeyError):
            return None

    def preparation_evidence(self, symbol, requirements):
        """Provider backoff survives a timing/code invalidation for the same need."""
        payload = self.previous.get(symbol)
        if not payload:
            return {}, None
        row = payload["row"]
        state = row.get("candidate_state", {})
        if self._encode(state.get("requirements")) != self._encode(requirements):
            return {}, None
        keys = {"preparation_receipt", "source_query_started_at", "source_query_completed_at",
                "attempt_budget_per_run", "preparation_attempt_run_id", "source_check_status"}
        return {k: v for k, v in state.items() if k in keys}, row.get("next_review_at")


def _load_pool_run(manifest_path: Path):
    """Load a completed run only when its manifest and payload hashes agree."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("current") is not True or manifest.get("run_id") != manifest_path.parent.name:
        return None
    if manifest.get("stage_status", {}).get("DAILY_TIMING") != "COMPLETE":
        return None
    for name, expected in (manifest.get("artifact_hashes") or {}).items():
        path = manifest_path.parent / name
        if not path.exists():
            return None
        actual = sha256(path.read_bytes()).hexdigest()
        # JSON hashes are payload hashes; parquet hashes are byte hashes.
        if name.endswith(".json") or name.endswith(".jsonl"):
            payload = path.read_text(encoding="utf-8")
            actual = sha256(payload.encode("utf-8")).hexdigest()
        if actual != expected:
            return None
    snapshot_path = manifest_path.parent / "universe_snapshot.json"
    timing_path = manifest_path.parent / "daily_timing.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("run_id") != manifest.get("run_id"):
        return None
    ticker_results = json.loads(timing_path.read_text(encoding="utf-8"))
    return {"snapshot": snapshot, "ticker_results": ticker_results}


def _previous_pool_run(root: Path, current_run_id: str, *, baseline_run_id: str | None = None):
    """Load an explicitly named baseline, or an untrusted observation candidate."""
    if baseline_run_id:
        path = root / baseline_run_id / "run_manifest.json"
        try:
            loaded = _load_pool_run(path)
        except (OSError, ValueError, TypeError):
            return None
        return (0.0, baseline_run_id, loaded) if loaded is not None else None
    candidates = []
    for manifest_path in root.glob("*/run_manifest.json"):
        if manifest_path.parent.name == current_run_id:
            continue
        try:
            loaded = _load_pool_run(manifest_path)
            if loaded is None:
                continue
            candidates.append((manifest_path.stat().st_mtime, manifest_path.parent.name,
                               loaded))
        except (OSError, ValueError, TypeError):
            continue
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def _write_atomic(path: Path, payload: str) -> str:
    temporary = path.with_name(path.name + ".tmp")
    # Write bytes so the digest is identical on Windows and POSIX (text mode
    # newline translation would otherwise invalidate the recorded identity).
    encoded = payload.encode("utf-8")
    temporary.write_bytes(encoded)
    digest = sha256(encoded).hexdigest()
    temporary.replace(path)
    return digest


def _write_parquet_atomic(path: Path, rows) -> str:
    temporary = path.with_name(path.name + ".tmp")
    pd.DataFrame(rows).to_parquet(temporary, index=False)
    digest = sha256(temporary.read_bytes()).hexdigest()
    temporary.replace(path)
    return digest


def persist_pool_artifacts(result: PoolScanResult, output_directory: str | Path,
                           *, baseline_run_id: str | None = None,
                           recovery_run_id: str | None = None,
                           evidence_window: int = 60) -> Path:
    """Write the U1 snapshot/results and manifest; later stages are explicit."""
    output_root = Path(output_directory)
    root = output_root / result.snapshot.run_id
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
    parquet_rows = [{**row, "candidate_state": json.dumps(row.get("candidate_state", {}), default=str, sort_keys=True)}
                    for row in flat_rows]
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
    previous = _previous_pool_run(output_root, result.snapshot.run_id,
                                  baseline_run_id=baseline_run_id)
    if previous is None:
        reconciliation = {"status": "BASELINE_NOT_FOUND" if baseline_run_id else "NO_COMPARABLE_PREVIOUS_RUN",
                          "comparable": False, "baseline_run_id": baseline_run_id}
    else:
        from .runner import reconcile_pool_scan_results
        _, previous_run_id, before = previous
        after = {"snapshot": asdict(result.snapshot),
                 "ticker_results": [asdict(row) for row in result.ticker_results]}
        reconciliation = {"status": "COMPARED", "previous_run_id": previous_run_id,
                          **reconcile_pool_scan_results(before, after,
                              result.preparation_results),
                          "baseline_run_id": baseline_run_id or previous_run_id,
                          "recovery_run_id": recovery_run_id,
                          "recovery_evidence_source": "CURRENT_PREPARATION_RESULTS" if result.preparation_results else
                                                      ("EXPLICIT_RECOVERY_RUN" if recovery_run_id else "NONE")}
    files["reconciliation.json"] = _write_atomic(
        root / "reconciliation.json", json.dumps(reconciliation, default=str,
                                                   sort_keys=True, indent=2))
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
    from .ai_evidence import write_ai_artifacts
    files.update(write_ai_artifacts(root, result.ticker_results,
                                    asdict(result.snapshot), evidence_window=evidence_window))
    manifest["artifact_hashes"] = files
    manifest["ai_evidence"] = {"schema": "pcs.ai_evidence_packet", "version": "1",
                                "window_sessions": evidence_window,
                                "summary": "full_pool_summary.json",
                                "index": "ai_evidence_index.json",
                                "detail": "ai_evidence_packets.jsonl"}
    _write_atomic(root / "run_manifest.json", json.dumps(manifest, sort_keys=True, indent=2))
    return root
