"""Update and classify every global PCS pool symbol at a fixed EOD as-of.

The script is deliberately per-symbol and fail-isolated: one failed provider
request cannot move the pool's effective as-of date backwards for other
symbols. All writes go through the market-data control plane.
"""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import exchange_calendars as xc
import pandas as pd

from pcs.data.access import PCSDataAccess
from pcs.data.control_plane import MarketDataControlPlane
from pcs.pcs_broad_discovery import _trend_prescreen


DEFAULT_AS_OF = "2026-09-01"
POOL = Path("research_outputs/global_pcs_base_universe/pool_2_options/pcs_base_pool_ranked.parquet")
OUT = Path("research_outputs/global_pcs_base_universe")


def last_completed_session(as_of: str) -> str:
    calendar = xc.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(pd.Timestamp("2026-08-18"), pd.Timestamp(as_of))
    if len(sessions) == 0:
        raise ValueError("NO_NYSE_SESSION_IN_REQUEST_WINDOW")
    return sessions[-1].date().isoformat()


def _classify(row: dict[str, Any], *, access: PCSDataAccess, target: str) -> dict[str, Any]:
    symbol = row["symbol"]
    row["latest_daily_session"] = None
    row["options_session"] = None
    row["daily_active_generation"] = None
    row["missing_sessions"] = []
    try:
        daily = access.read_prices(symbol)
        if daily.empty:
            row.update(status="SOURCE_UNAVAILABLE", reason_codes=["DAILY_SOURCE_UNAVAILABLE"])
            return row
        daily["date"] = pd.to_datetime(daily["date"], errors="coerce").dt.normalize()
        latest = daily["date"].dropna().max()
        row["latest_daily_session"] = latest.date().isoformat() if pd.notna(latest) else None
        row["daily_row_count"] = int(len(daily))
        expected = set(x.date().isoformat() for x in xc.get_calendar("XNYS").sessions_in_range(
            pd.Timestamp("2026-08-18"), pd.Timestamp(target)))
        observed = set(daily["date"].dropna().dt.date.astype(str))
        row["missing_sessions"] = sorted(expected - observed)
        manifest = access._read_manifest(access.manifest_path)
        if not manifest.empty and "dataset" in manifest.columns:
            mr = manifest[(manifest.dataset.astype(str).eq("daily")) &
                          manifest.symbol.astype(str).str.upper().eq(symbol) &
                          manifest.status.astype(str).str.upper().eq("SUCCESS")]
            if not mr.empty:
                mr = mr.sort_values([c for c in ("max_date", "import_timestamp") if c in mr.columns])
                row["daily_active_generation"] = str(mr.iloc[-1].get("active_generation") or
                                                       mr.iloc[-1].get("promoted_generation_id") or
                                                       "LEGACY_NO_GENERATION")
        if row["latest_daily_session"] != target:
            row.update(status="STALE_WITH_LATEST_DATE", reason_codes=["DAILY_NOT_CURRENT_TO_AS_OF"])
            return row
        try:
            options = access.read_quotes(symbol, target, target)
        except Exception as exc:
            row.update(status="CURRENT_TO_2026_09_01", options_status="OPTIONS_DATA_UNAVAILABLE",
                       reason_codes=["OPTIONS_DATA_UNAVAILABLE"],
                       options_detail=f"{type(exc).__name__}: {exc}")
            return row
        if options.empty:
            row.update(status="CURRENT_TO_2026_09_01", options_status="OPTIONS_DATA_UNAVAILABLE",
                       reason_codes=["OPTIONS_DATA_UNAVAILABLE"])
            return row
        options["trade_date"] = pd.to_datetime(options["trade_date"], errors="coerce").dt.normalize()
        row["options_session"] = options["trade_date"].dropna().max().date().isoformat()
        row["options_row_count"] = int(len(options))
        row.update(status="CURRENT_TO_2026_09_01", options_status="READY", reason_codes=[])
        return row
    except Exception as exc:
        row.update(status="DATA_QUALITY_BLOCKED", reason_codes=["READBACK_OR_VALIDATION_FAILED"],
                   detail=f"{type(exc).__name__}: {exc}")
        return row


def run(as_of: str = DEFAULT_AS_OF) -> dict[str, Any]:
    target = last_completed_session(as_of)
    pool = pd.read_parquet(POOL)
    symbols = sorted(pool["symbol"].astype(str).str.upper().unique())
    access = PCSDataAccess.canonical()
    control = MarketDataControlPlane(access=access)
    OUT.mkdir(parents=True, exist_ok=True)
    checkpoint = OUT / f"pool_update_{as_of.replace('-', '')}.jsonl"
    rows: list[dict[str, Any]] = []
    if checkpoint.exists():
        try:
            rows = pd.read_json(checkpoint, lines=True).to_dict("records")
        except (ValueError, TypeError):
            rows = []
    completed = {str(row.get("symbol", "")).upper() for row in rows}
    for symbol in symbols:
        if symbol in completed:
            continue
        record: dict[str, Any] = {"symbol": symbol, "requested_as_of": as_of,
                                  "target_session": target, "provider_called": False,
                                  "fetch_row_count": 0, "promoted_generation": None}
        try:
            result = control.ensure_market_data({
                "symbol": symbol, "datasets": ("daily",),
                "start": "2026-08-19", "end": target,
                "required_start": "2026-08-19", "required_end": target,
                "decision_as_of": target, "consumer": "GLOBAL_POOL_EOD_UPDATE",
            }, symbol=symbol)
            payload = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            record["provider_called"] = bool(payload.get("selected_source") or payload.get("provider_coverage") or payload.get("import_outcomes"))
            record["fetch_row_count"] = int(sum(int(x.get("row_count", 0) or 0) for x in payload.get("provider_coverage", ()) if isinstance(x, dict)))
            promoted = payload.get("promoted_partitions", ())
            record["promoted_generation"] = promoted
            record["control_plane_status"] = payload.get("status")
            record["control_plane_reasons"] = payload.get("reason_codes", ())
        except Exception as exc:
            record.update(control_plane_status="BLOCKED", control_plane_reasons=["CONTROL_PLANE_EXCEPTION"],
                          detail=f"{type(exc).__name__}: {exc}")
        classified = _classify(record, access=access, target=target)
        rows.append(classified)
        pd.DataFrame([classified]).to_json(checkpoint, orient="records", lines=True, date_format="iso", mode="a")
        if len(rows) % 25 == 0 or len(rows) == len(symbols):
            print(json.dumps({"progress": len(rows), "total": len(symbols),
                              "status": classified.get("status"), "symbol": symbol}), flush=True)
    detail = pd.DataFrame(rows).sort_values("symbol")
    # Strategy selection is deliberately downstream of the target-session
    # options gate.  No selector is invoked for a row without an exact
    # target-session chain; retain an explicit non-execution envelope so the
    # entry artifact cannot be mistaken for a partially evaluated candidate.
    detail["selector_evaluated"] = False
    detail["selector_candidate_count"] = 0
    detail["action"] = "DATA_BLOCKED"
    detail["data_reason"] = detail.apply(
        lambda r: "TARGET_SESSION_OPTIONS_MISSING" if r.get("options_status") != "READY"
        else "NOT_RUN", axis=1)
    # Evaluate the daily-only gates from the newly read canonical session.
    # Options remain a separate exact-session gate and are never substituted
    # with an older chain.
    current = detail[detail.status.eq("CURRENT_TO_2026_09_01")].copy()
    def daily_gates(symbol: str) -> dict[str, Any]:
        try:
            prices = access.read_prices(symbol, end_date=target).sort_values("date")
            if prices.empty:
                return {"symbol": symbol, "volume_gate": False, "trend_gate": False,
                        "volume_reason": "DAILY_EMPTY", "trend_reason": "DAILY_EMPTY"}
            recent = prices.tail(20)
            volume_pass = float(recent["volume"].mean()) >= 100_000
            try:
                trend_pass, trend_reasons = _trend_prescreen(symbol, target, access)
            except Exception as exc:
                trend_pass, trend_reasons = False, [f"TREND_GATE_UNAVAILABLE:{type(exc).__name__}"]
            return {"symbol": symbol, "volume_gate": volume_pass,
                    "trend_gate": bool(trend_pass),
                    "volume_reason": "PASS" if volume_pass else "LOW_SHARE_VOLUME",
                    "trend_reason": ";".join(trend_reasons) if trend_reasons else "PASS"}
        except Exception as exc:
            return {"symbol": symbol, "volume_gate": False, "trend_gate": False,
                    "volume_reason": f"DAILY_READ_FAILED:{type(exc).__name__}",
                    "trend_reason": f"DAILY_READ_FAILED:{type(exc).__name__}"}
    gate_rows = []
    with ThreadPoolExecutor(max_workers=8) as workers:
        futures = [workers.submit(daily_gates, str(symbol)) for symbol in current.symbol]
        for future in as_completed(futures):
            gate_rows.append(future.result())
    if gate_rows:
        gates = pd.DataFrame(gate_rows)
        detail = detail.merge(gates, on="symbol", how="left", validate="one_to_one")
    else:
        detail["volume_gate"] = False; detail["trend_gate"] = False
        detail["volume_reason"] = "NOT_CURRENT"; detail["trend_reason"] = "NOT_CURRENT"
    trend_path = OUT / f"trend_audit_{as_of.replace('-', '')}.json"
    if trend_path.exists():
        audited = pd.DataFrame(json.loads(trend_path.read_text(encoding="utf-8")).get("sample", []))
        # The full audit is authoritative when present; merge all rows from
        # its sibling machine-readable detail if a future audit emits one.
        detail_path = trend_path.with_suffix(".rows.jsonl")
        if detail_path.exists():
            audited = pd.read_json(detail_path, lines=True)
        if not audited.empty and "symbol" in audited:
            cols = [c for c in ("symbol", "trend_state", "phase", "structural_trend",
                                "trend_gate", "pullback_gate", "feature_max_date",
                                "history_rows", "trend_reasons", "pullback_reasons") if c in audited]
            detail = detail.merge(audited[cols].drop_duplicates("symbol"), on="symbol", how="left")
    detail["stock_selection_status"] = detail.apply(
        lambda r: "DAILY_DATA_BLOCKED" if r.get("status") != "CURRENT_TO_2026_09_01"
        else "SELECTED" if r.get("trend_gate") == "PASS"
        else "WATCH" if r.get("trend_gate") == "WATCH"
        else "REJECTED", axis=1)
    detail["timing_status"] = detail.apply(
        lambda r: "ENTRY_READY" if r.get("pullback_gate") == "PASS" else
        "WATCH_PULLBACK" if r.get("pullback_gate") == "WAIT" else
        "TREND_WEAK" if r.get("trend_gate") == "REJECT" else "WAIT_CONFIRMATION", axis=1)
    detail["contract_status"] = detail.apply(
        lambda r: "CONTRACT_READY" if r.get("options_status") == "READY" else
        "OPTIONS_DATA_BLOCKED", axis=1)
    stem = f"pool_selection_manual_validation_{as_of.replace('-', '')}"
    detail.to_csv(OUT / f"{stem}.csv", index=False)
    detail.to_parquet(OUT / f"{stem}.parquet", index=False)
    summary = {"as_of": as_of, "market_calendar": "XNYS", "target_session": target,
               "universe_total": len(detail), "status_counts": detail.status.value_counts().to_dict(),
               "options_status_counts": detail.get("options_status", pd.Series(dtype=str)).fillna("NOT_CHECKED").value_counts().to_dict(),
               "current_symbols": detail.loc[detail.status.eq("CURRENT_TO_2026_09_01"), "symbol"].tolist(),
               "stale_symbols": detail.loc[detail.status.eq("STALE_WITH_LATEST_DATE"), ["symbol", "latest_daily_session"]].to_dict("records"),
               "source_unavailable_symbols": detail.loc[detail.status.eq("SOURCE_UNAVAILABLE"), "symbol"].tolist(),
               "data_quality_blocked_symbols": detail.loc[detail.status.eq("DATA_QUALITY_BLOCKED"), "symbol"].tolist()}
    summary.update({
        "volume_trend_selector_counts": {
            "volume": int(detail.volume_gate.fillna(False).sum()),
            "trend": int(detail.trend_gate.fillna(False).sum()),
            "selector": int(detail.selector_evaluated.fillna(False).sum()),
        },
        "action_counts": detail.action.value_counts().to_dict(),
        "open_symbols": [], "wait_symbols": [], "blocked_symbols": detail.symbol.tolist(),
        "selection_note": "Selector was not called without exact target-session options coverage.",
    })
    (OUT / f"{stem}.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    pcs_stem = f"pcs_entry_manual_validation_{as_of.replace('-', '')}"
    detail[detail.status.eq("CURRENT_TO_2026_09_01")].to_parquet(OUT / f"{pcs_stem}.parquet", index=False)
    detail[detail.status.eq("CURRENT_TO_2026_09_01")].to_csv(OUT / f"{pcs_stem}.csv", index=False)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=DEFAULT_AS_OF)
    args = parser.parse_args()
    print(json.dumps(run(args.as_of), indent=2, default=str))
