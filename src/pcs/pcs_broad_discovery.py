"""Broad daily PCS discovery over the authoritative Pool 2 universe."""
from __future__ import annotations

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed, wait
import json
from pathlib import Path
from typing import Any

import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.engine.decision_engine import load_rules
from pcs.entry.pullback_gate import evaluate_pullback_gate
from pcs.entry.trend_gate import evaluate_trend_gate
from pcs.pcs_status import evaluate_pcs_status, _event_calendar
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.interpretation import interpret_trend
from pcs.trend.scoring import score_trend
from pcs.trend.snapshot import build_trend_snapshot


POOL = Path("research_outputs/global_pcs_base_universe/pool_2_options/pcs_base_pool_ranked.parquet")
POOL1 = Path("research_outputs/global_pcs_base_universe/pool_1_underlying/all_symbols_status.parquet")


def _isolated_default_execute(item: dict[str, Any], as_of: str, recover: bool) -> dict[str, Any]:
    """Run one production ticker in an independently killable worker."""
    access = PCSDataAccess()
    result = evaluate_pcs_status(item["symbol"], as_of, mode="eod", data_access=access,
                                 event_calendar=_event_calendar(),
                                 full_research_readiness=False, auto_recover=recover)
    return result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)


def _trend_prescreen(symbol: str, as_of: str, access: PCSDataAccess) -> tuple[bool, list[str]]:
    """Reuse the production trend/pullback gates before loading option chains."""
    daily = access.read_prices(symbol, end_date=as_of).sort_values("date")
    benchmark = access.read_prices("QQQ", end_date=as_of).sort_values("date")
    snapshot = build_trend_snapshot(daily, benchmark, as_of_date=pd.Timestamp(as_of).normalize(),
                                    symbol=symbol, benchmark="QQQ", config=TrendIndicatorConfig())
    interpretation = interpret_trend(snapshot)
    score = score_trend(snapshot, interpretation)
    trend = evaluate_trend_gate(score, interpretation, snapshot)
    pullback = evaluate_pullback_gate(trend, snapshot, interpretation)
    reasons = list(dict.fromkeys([*trend.reasons, *pullback.reasons]))
    return (trend.trend_gate_result == "PASS" and
            pullback.pullback_gate_result == "PASS"), reasons


def _prescreen(as_of: str, pool_path: str | Path = POOL,
               pool1_path: str | Path = POOL1) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use only existing infrastructure quality tiers and session coverage."""
    pool = pd.read_parquet(pool_path).copy()
    pool["symbol"] = pool.symbol.astype(str).str.upper()
    underlying = pd.read_parquet(pool1_path)[["symbol", "coverage_end"]].drop_duplicates("symbol")
    underlying["symbol"] = underlying.symbol.astype(str).str.upper()
    frame = pool.merge(underlying, on="symbol", how="left", validate="one_to_one")
    frame["coverage_end"] = pd.to_datetime(frame.coverage_end, errors="coerce").dt.normalize()
    cutoff = pd.Timestamp(as_of).normalize()
    frame["prescreen_pass"] = frame.tier.astype(str).isin({"TIER_A", "TIER_B"}) & frame.coverage_end.ge(cutoff)
    frame["prescreen_reason"] = "PASS"
    frame.loc[~frame.tier.astype(str).isin({"TIER_A", "TIER_B"}), "prescreen_reason"] = "EXISTING_POOL_TIER_C"
    frame.loc[frame.coverage_end.lt(cutoff) | frame.coverage_end.isna(), "prescreen_reason"] = "LATEST_COMPLETED_SESSION_MISSING"
    return frame, frame[frame.prescreen_pass].sort_values(["pool_rank", "symbol"]).copy()


def execute_broad_pcs_discovery(as_of: str, *, output_dir: str | Path =
                                "research_outputs/global_pcs_base_universe/daily_discovery",
                                pool_path: str | Path = POOL, pool1_path: str | Path = POOL1,
                                evaluator=evaluate_pcs_status, data_access=None,
                                max_workers: int = 4, resume: bool = True,
                                execution_timeout_seconds: float = 120.0,
                                apply_trend_prescreen: bool = True,
                                reuse_prescreen: bool = True) -> dict[str, Any]:
    """Execute every prescreen survivor; there is deliberately no top-N limit."""
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    prior_summary = out / "summary.json"
    prior_prescreen = out / "prescreen.csv"
    reusable = False
    if reuse_prescreen and prior_summary.exists() and prior_prescreen.exists():
        try:
            reusable = json.loads(prior_summary.read_text(encoding="utf-8")).get("as_of") == as_of
        except (ValueError, OSError):
            reusable = False
    if reusable:
        universe = pd.read_csv(prior_prescreen)
        universe["coverage_end"] = pd.to_datetime(universe.coverage_end, errors="coerce").dt.normalize()
        survivors = universe[universe.prescreen_pass.astype(bool)].sort_values(["pool_rank", "symbol"]).copy()
    else:
        universe, survivors = _prescreen(as_of, pool_path, pool1_path)
    access = data_access or PCSDataAccess()
    if apply_trend_prescreen and not reusable:
        trend_results = {}
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as workers:
            futures = {workers.submit(_trend_prescreen, item.symbol, as_of, access): item.symbol
                       for item in survivors.itertuples(index=False)}
            for future in as_completed(futures):
                symbol = futures[future]
                try: trend_results[symbol] = future.result()
                except Exception as exc: trend_results[symbol] = (False, [f"TREND_PRESCREEN_UNAVAILABLE:{type(exc).__name__}"])
        for symbol, (passed, reasons) in trend_results.items():
            mask = universe.symbol.eq(symbol)
            if not passed:
                universe.loc[mask, "prescreen_pass"] = False
                universe.loc[mask, "prescreen_reason"] = ";".join(reasons) or "EXISTING_TREND_PULLBACK_GATE_REJECTED"
        survivors = universe[universe.prescreen_pass].sort_values(["pool_rank", "symbol"]).copy()
    calendar = _event_calendar()
    checkpoint = out / "broad_pcs_results.jsonl"
    rows = []
    if resume and checkpoint.exists():
        try:
            rows = pd.read_json(checkpoint, lines=True).to_dict("records")
        except (ValueError, TypeError):
            rows = []
    survivor_symbols = set(survivors.symbol.astype(str).str.upper())
    rows = [row for row in rows if str(row.get("symbol", "")).upper() in survivor_symbols]
    completed = {str(row.get("symbol", "")).upper() for row in rows
                 if str(row.get("system_status", "")) != "BLOCKED"}
    rows = [row for row in rows if str(row.get("system_status", "")) != "BLOCKED"]
    pending = [item for item in survivors.itertuples(index=False) if item.symbol not in completed]

    def execute(item, *, recover=False):
        try:
            result = evaluator(item.symbol, as_of, mode="eod", data_access=access,
                               event_calendar=calendar, full_research_readiness=False,
                               auto_recover=recover)
            payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        except Exception as exc:
            # One ticker's dependency failure must remain a completed,
            # auditable BLOCKED row and must not abort the global scan.
            payload = {"system_status": "BLOCKED", "strategy_status": "NOT_RUN",
                       "action": "DATA_BLOCKED", "strategy_evaluated": False,
                       "reason_codes": ["EXECUTOR_EXCEPTION"],
                       "detail": f"{type(exc).__name__}: {exc}"}
        return {"symbol": item.symbol, "pool_rank": int(item.pool_rank),
                "pool_score": float(item.pool_score), "tier": item.tier, **payload}

    executor_cls = ProcessPoolExecutor if evaluator is evaluate_pcs_status and data_access is None else ThreadPoolExecutor
    # Submit only one worker-sized batch at a time.  A single global wait over
    # the full survivor set makes queued work look like timed-out strategy
    # execution (for example 765 symbols with four workers and a 120-second
    # limit).  The timeout is therefore applied to each executable batch; no
    # survivor is silently discarded merely because it was queued behind one.
    batch_size = max(1, int(max_workers))

    def run_batches(items, *, recover=False):
        for start in range(0, len(items), batch_size):
            batch = items[start:start + batch_size]
            workers = executor_cls(max_workers=batch_size)
            if executor_cls is ProcessPoolExecutor:
                futures = {workers.submit(_isolated_default_execute, {
                    "symbol": item.symbol, "pool_rank": int(item.pool_rank),
                    "pool_score": float(item.pool_score), "tier": item.tier}, as_of, recover): item
                           for item in batch}
            else:
                futures = {workers.submit(execute, item, recover=recover): item for item in batch}
            done, unfinished = wait(futures, timeout=max(0.0, float(execution_timeout_seconds)))
            for future in done:
                item = futures[future]
                try:
                    payload = future.result()
                    if executor_cls is ProcessPoolExecutor:
                        row = {"symbol": item.symbol, "pool_rank": int(item.pool_rank),
                               "pool_score": float(item.pool_score), "tier": item.tier, **payload}
                    else:
                        row = payload
                except Exception as exc:
                    row = {"symbol": item.symbol, "pool_rank": int(item.pool_rank),
                           "pool_score": float(item.pool_score), "tier": item.tier,
                           "system_status": "BLOCKED", "strategy_status": "NOT_RUN",
                           "action": "DATA_BLOCKED", "strategy_evaluated": False,
                           "contract_selection_evaluated": False,
                           "reason_codes": ["EXECUTOR_EXCEPTION"],
                           "detail": f"{type(exc).__name__}: {exc}"}
                rows.append(row)
                pd.DataFrame([row]).to_json(checkpoint, orient="records", lines=True,
                                            date_format="iso", mode="a")
            for future in unfinished:
                item = futures[future]
                future.cancel()
                row = {"symbol": item.symbol, "pool_rank": int(item.pool_rank),
                       "pool_score": float(item.pool_score), "tier": item.tier,
                       "system_status": "BLOCKED", "strategy_status": "NOT_RUN",
                       "action": "DATA_BLOCKED", "strategy_evaluated": False,
                       "contract_selection_evaluated": False,
                       "reason_codes": ["RECOVERY_TIMEOUT" if recover else "EXECUTOR_TIMEOUT"],
                       "detail": f"timeout_seconds={execution_timeout_seconds}"}
                rows.append(row)
                pd.DataFrame([row]).to_json(checkpoint, orient="records", lines=True,
                                            date_format="iso", mode="a")
            workers.shutdown(wait=False, cancel_futures=True)

    run_batches(pending)
    # Canonical promotions share one repository manifest transaction. Keep
    # recovery serial, then return to parallel read-only evaluation on the
    # next run. This prevents cross-ticker rollback/snapshot interference.
    refresh_codes = {"OPTION_CHAIN_REFRESH_REQUIRED", "ROUTE_MISSING_RECOVERABLE",
                     "OPTION_STALE", "DATASET_GAP", "EVENT_CALENDAR_REFRESH_REQUIRED"}
    by_symbol = {item.symbol: item for item in survivors.itertuples(index=False)}
    retry_symbols = [str(row.get("symbol", "")).upper() for row in rows
                     if row.get("system_status") == "BLOCKED" and
                     refresh_codes.intersection(set(row.get("reason_codes") or [])) and
                     # Do not repeat provider/recovery work for terminal
                     # coverage, source, corruption, or generation failures.
                     str((row.get("decision") or {}).get("data_reason", "")).upper()
                     not in {"SOURCE_UNAVAILABLE", "GENERATION_MISMATCH", "CORRUPTED", "TARGET_WINDOW_MISSING"}]
    if retry_symbols:
        rows = [row for row in rows if str(row.get("symbol", "")).upper() not in set(retry_symbols)]
        # Recovery is bounded to one retry per symbol, but must not serialize
        # hundreds of independent provider/readiness operations.  Keep the
        # worker count bounded and checkpoint each completed retry.
        run_batches([by_symbol[symbol] for symbol in retry_symbols], recover=True)
    detail = pd.DataFrame(rows)
    if detail.empty:
        detail = pd.DataFrame(columns=["symbol", "pool_rank", "pool_score", "tier",
                                       "system_status", "strategy_status", "action",
                                       "strategy_evaluated", "decision_engine_executed"])
    else:
        detail = detail.drop_duplicates("symbol", keep="last").sort_values(["pool_rank", "symbol"])
        # Timeout/exception envelopes intentionally contain only the fields
        # needed to prove non-execution.  Normalize optional counters before
        # producing the aggregate report.
        for column in ("strategy_evaluated", "contract_selection_evaluated",
                       "decision_engine_executed", "auto_recovered"):
            if column not in detail:
                detail[column] = False
    detail.to_json(checkpoint, orient="records", lines=True, date_format="iso")
    universe[["symbol", "pool_rank", "pool_score", "tier", "coverage_end",
              "prescreen_pass", "prescreen_reason"]].to_csv(out / "prescreen.csv", index=False)
    blocked = detail.system_status.eq("BLOCKED") if len(detail) else pd.Series(dtype=bool)
    reasons = detail.loc[blocked, "reason_codes"].explode() if len(detail) else pd.Series(dtype=str)
    terminal_blocked = int(blocked.sum()) if len(detail) else 0
    decision_count = int(detail.decision_engine_executed.fillna(False).sum()) if len(detail) else 0
    summary = {
        "module": "pcs.pcs_broad_discovery", "version": "1.0", "as_of": as_of,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_total": int(len(universe)),
        "data_eligible": int(universe.coverage_end.ge(pd.Timestamp(as_of).normalize()).sum()),
        "prescreen_rejected": int((~universe.prescreen_pass).sum()),
        "prescreen_passed": int(len(survivors)), "executor_attempted": int(len(detail)),
        "executor_completed": int(detail.strategy_evaluated.fillna(False).sum()) if len(detail) else 0,
        "auto_recovered": int(detail.auto_recovered.fillna(False).sum()) if len(detail) else 0,
        "terminal_blocked": terminal_blocked,
        "blocked_by_event_source": int(reasons.astype(str).str.startswith("EVENT_").sum()),
        "contract_selection_executed": int(detail.contract_selection_evaluated.fillna(False).sum()) if len(detail) else 0,
        "decision_engine_executed": decision_count,
        "open": int(detail.action.eq("OPEN").sum()) if len(detail) else 0,
        "wait": int(detail.action.eq("WAIT").sum()) if len(detail) else 0,
        "scan_complete": bool(len(detail) == len(survivors)),
        # An empty survivor set can mean the upstream data/session gate
        # rejected the entire pool; it is not successful strategy coverage.
        "strategy_coverage_complete": bool(len(survivors) > 0 and
                                             terminal_blocked == 0 and
                                             decision_count == len(survivors)),
    }
    # Do not silently discard eligible symbols from the machine-readable
    # discovery result.  Presentation clients may paginate this complete set.
    opens = detail[detail.action.eq("OPEN")].sort_values(["pool_score", "pool_rank"], ascending=[False, True]) if len(detail) else detail
    summary["top_opportunities"] = opens.to_dict("records")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


__all__ = ["execute_broad_pcs_discovery"]
