"""Checkpointed, bounded candidate generation using the frozen universe rules."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib, json, os, tempfile, time
from pathlib import Path
from typing import Any

import pandas as pd

from .credit_stop import load_quotes_canonical_index
from .entry_candidate_universe import generate_observable_candidates
from pcs.data.access import PCSDataAccess


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""): h.update(b)
    return h.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def _config_hash(symbol: str, start: str, end: str, daily_path: str | Path, benchmark_path: str | Path) -> str:
    access = PCSDataAccess()
    def input_identity(dataset: str, name: str, path: str | Path) -> str:
        normalized = str(path).replace("\\", "/")
        if normalized.startswith("data/") or "data/raw/" in normalized:
            return access.source_data_identity(dataset, name)
        p = Path(path)
        return _sha(p) if p.exists() else "MISSING"

    payload = {"symbol": symbol.upper(), "start": start, "end": end,
               "producer": "pcs.research.entry_candidate_universe.generate_observable_candidates",
               "daily_identity": input_identity("daily", symbol, daily_path),
               "benchmark_identity": input_identity("daily", "QQQ", benchmark_path),
               "options_identity": access.source_data_identity("options", symbol),
               "code_identity": {str(p): _sha(p) for p in (
                   Path(__file__), Path(__file__).with_name("entry_candidate_universe.py"),
                   Path(__file__).with_name("credit_stop.py"))}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def run_batched_candidates(symbol: str, daily_path: str | Path, benchmark_path: str | Path,
                           start: str, end: str, output_root: str | Path,
                           workers: int = 8, resume: bool = True) -> dict[str, Any]:
    symbol = symbol.upper(); out = Path(output_root); batches = out / "batches"
    state_path = out / "candidate_checkpoint.json"; cfg = _config_hash(symbol, start, end, daily_path, benchmark_path)
    periods = [(p.year, p.quarter) for p in pd.period_range(pd.Timestamp(start), pd.Timestamp(end), freq="Q")]
    state = json.loads(state_path.read_text()) if resume and state_path.exists() else {"symbol": symbol, "config_hash": cfg, "batches": {}}
    if state.get("config_hash") != cfg: raise ValueError("CANDIDATE_CONFIG_HASH_CHANGED")
    for y, q in periods: state["batches"].setdefault(f"{y}Q{q}", {"status": "PENDING"})
    _atomic_json(state_path, state)

    def work(item):
        y, q = item; bid = f"{y}Q{q}"; path = batches / f"{symbol}_{y}_q{q}.parquet"
        prior = state["batches"].get(bid, {})
        if prior.get("status") == "COMMITTED" and path.exists() and _sha(path) == prior.get("output_checksum"):
            return bid, {**prior, "status": "COMMITTED", "reused": True}
        started = time.perf_counter()
        try:
            qstart = max(pd.Timestamp(start), pd.Timestamp(y, q * 3 - 2, 1)).strftime("%Y-%m-%d")
            qend = min(pd.Timestamp(end), pd.Timestamp(y, q * 3, 1) + pd.offsets.QuarterEnd()).strftime("%Y-%m-%d")
            chains, load_meta = load_quotes_canonical_index(symbol, qstart, qend)
            frame, summary = generate_observable_candidates(symbol, daily_path, "", qstart, qend,
                                                             chain_index=chains, benchmark_path=benchmark_path)
            _atomic_parquet(frame, path)
            return bid, {"status": "COMMITTED", "batch": bid, "output_path": str(path), "output_checksum": _sha(path),
                         "rows": len(frame), "summary": asdict(summary), "loader": load_meta,
                         "config_hash": cfg, "duration_seconds": round(time.perf_counter() - started, 3)}
        except Exception as exc:
            return bid, {"status": "FAILED", "batch": bid, "failure_code": type(exc).__name__, "failure_reason": str(exc),
                         "config_hash": cfg, "duration_seconds": round(time.perf_counter() - started, 3)}

    pending = [x for x in periods if state["batches"][f"{x[0]}Q{x[1]}"].get("status") != "COMMITTED"]
    total = len(periods); completed = total - len(pending); started = time.perf_counter(); durations = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [pool.submit(work, x) for x in pending]
        for future in as_completed(futures):
            bid, result = future.result(); state["batches"][bid] = result; _atomic_json(state_path, state)
            if result.get("status") == "COMMITTED": completed += 1; durations.append(result.get("duration_seconds", 0))
            print(json.dumps({"batch": bid, "status": result.get("status"), "completed": completed, "total": total,
                              "rows": result.get("rows", 0), "elapsed_seconds": round(time.perf_counter() - started, 1),
                              "avg_batch_seconds": round(sum(durations) / len(durations), 2) if durations else None}, default=str), flush=True)
    failed = [b for b in state["batches"].values() if b.get("status") != "COMMITTED"]
    if failed: return {"status": "IN_PROGRESS", "completed": completed, "total": total, "failed": failed, "checkpoint": str(state_path)}

    frames = []
    for y, q in sorted(periods):
        path = Path(state["batches"][f"{y}Q{q}"]["output_path"]); 
        if not path.exists() or _sha(path) != state["batches"][f"{y}Q{q}"]["output_checksum"]: raise ValueError(f"BATCH_CHECKSUM_FAILED:{y}Q{q}")
        frames.append(pd.read_parquet(path))
    merged = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if "date" in merged and "expiration" in merged:
        merged = merged.sort_values(["date", "expiration", "short_strike", "long_strike"], kind="mergesort").reset_index(drop=True)
    identity = [c for c in ["ticker", "date", "expiration", "short_strike", "long_strike"] if c in merged]
    if identity and merged.duplicated(identity).any(): raise ValueError("CANDIDATE_IDENTITY_DUPLICATE")
    final = out / "candidate_universe.parquet"; _atomic_parquet(merged, final)
    summary = {"status": "COMPLETE", "symbol": symbol, "rows": len(merged), "batch_count": total,
               "config_hash": cfg, "output_checksum": _sha(final), "created_at": datetime.now(timezone.utc).isoformat()}
    _atomic_json(out / "candidate_manifest.json", summary)
    return summary

