"""Canonical read adapter and deterministic exports for support-zone evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import subprocess

import pandas as pd

from pcs.analysis_contracts import CallContext
from pcs.pool.artifacts import _write_atomic
from pcs.pool.underlying_profiles import ProfileDataReader
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.trend.market_structure import analyze_market_structure
from pcs.trend.selection_models import (
    ConfirmedSwingEvidence, SupportFeatureBar, SupportFeatureView, SupportZoneInput,
    SupportZonePolicy, SupportZoneResult, SupportZoneState,
)
from pcs.trend.support_zones import evaluate_support_zones


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, default=str).encode()).hexdigest()


class SupportZoneDataReader:
    """Resolve verified daily data once and prepare existing trend facts."""
    def __init__(self, access=None):
        self.daily = ProfileDataReader(access)
        self.audit = []

    def load(self, context: CallContext, *, policy=None, prior_state=None, bound_zone_id=None,
             calendar="XNYS"):
        import exchange_calendars as xc
        policy = policy or SupportZonePolicy()
        day = context.effective_daily_session
        if day is None:
            raise ValueError("SUPPORT_EFFECTIVE_SESSION_REQUIRED")
        daily_view = self.daily._read(context.symbol, day, policy, calendar)
        frame = pd.DataFrame([{"date": b.session, "open": b.open, "high": b.high,
            "low": b.low, "close": b.close, "volume": b.volume} for b in daily_view.bars])
        if len(frame) < policy.indicator_warmup_sessions:
            raise ValueError("SUPPORT_INDICATOR_WARMUP_INSUFFICIENT")
        config = TrendIndicatorConfig(pivot_left_bars=policy.pivot_left_bars,
            pivot_right_bars=policy.pivot_right_bars, atr_period=policy.atr_period)
        indicators = calculate_base_indicators(frame, config)
        structure = analyze_market_structure(frame, config, as_of_date=day)
        cal = xc.get_calendar(calendar)
        end_loc = cal.sessions.get_loc(pd.Timestamp(day))
        analysis_sessions = [str(s.date()) for s in cal.sessions[
            end_loc-policy.analysis_sessions+1:end_loc+1]]
        future = [str(s.date()) for s in cal.sessions[end_loc+1:end_loc+policy.confirmation_sessions+1]]
        by_date = {str(pd.Timestamp(row.date).date()): i for i, row in frame.iterrows()}
        bars = []
        for session in analysis_sessions:
            if session not in by_date:
                continue
            i = by_date[session]
            row, ind = frame.iloc[i], indicators.iloc[i]
            def number(value):
                return None if pd.isna(value) else float(value)
            bars.append(SupportFeatureBar(session=session,
                **{k: number(row[k]) for k in ("open", "high", "low", "close")},
                **{k: number(ind[k]) for k in ("sma20", "sma50", "atr14")}))
        swings = [ConfirmedSwingEvidence(source_id="sha256:"+_hash([
                context.symbol, str(pd.Timestamp(s.pivot_date).date()), s.swing_type, s.price,
                str(pd.Timestamp(s.confirmed_at).date()), policy.pivot_left_bars, policy.pivot_right_bars]),
            pivot_date=str(pd.Timestamp(s.pivot_date).date()),
            confirmed_at=str(pd.Timestamp(s.confirmed_at).date()), swing_type=s.swing_type,
            price=float(s.price)) for s in structure.confirmed_swings
            if str(pd.Timestamp(s.confirmed_at).date()) >= analysis_sessions[0]]
        indicator_payload = {"implementation": "pcs.trend.indicators.calculate_base_indicators",
            "config": {"sma20": 20, "sma50": 50, "atr": policy.atr_period,
                "pivot_left": policy.pivot_left_bars, "pivot_right": policy.pivot_right_bars},
            "indicator_seed_start": str(frame.date.iloc[0]),
            "daily_source": [daily_view.source.sha256, daily_view.source.record_identity]}
        feature = SupportFeatureView(symbol=context.symbol, bars=bars,
            confirmed_swings=swings, expected_sessions=analysis_sessions+future,
            analysis_start=analysis_sessions[0], indicator_seed_start=str(frame.date.iloc[0]),
            indicator_identity="sha256:"+_hash(indicator_payload), source=daily_view.source,
            price_basis=daily_view.price_basis,
            corporate_action_version=daily_view.corporate_action_version,
            input_kind="VERIFIED_CANONICAL", source_timestamp=daily_view.source_timestamp,
            received_at=daily_view.received_at)
        self.audit.append({"symbol": context.symbol, "effective_session": day,
            "physical_verified_rows": len(frame), "analysis_start": analysis_sessions[0],
            "analysis_sessions": len(analysis_sessions), "analysis_bars": len(bars),
            "indicator_seed_start": feature.indicator_seed_start,
            "indicator_identity": feature.indicator_identity,
            "confirmed_swings_in_view": len(swings), "source": daily_view.source.model_dump(mode="json")})
        return SupportZoneInput(call_context=context, feature_view=feature,
            effective_policy=policy, prior_state=prior_state, bound_zone_id=bound_zone_id)

    def verify_unchanged(self):
        return self.daily.verify_unchanged()


def support_zone_to_ai_view(result):
    return {"result_id": result.result_id, "symbol": result.symbol, "as_of": result.as_of,
        "status": result.status.value,
        "current_zones": [{"zone_id": z.zone_id, "bounds": [z.lower, z.upper],
            "invalidation_line": z.invalidation_line, "state": z.state,
            "evidence_grade": z.evidence_grade, "held_tests": sum(t.status == "HELD" for t in z.tests),
            "reason_codes": z.reason_codes} for z in result.current_zones],
        "archived_zones": [{"zone_id": z.zone_id, "broken_at": z.broken_at,
            "archive_reason": z.archive_reason,
            "past_held_tests": sum(t.status == "HELD" for t in z.tests)} for z in result.archived_zones],
        "selections": [s.model_dump(mode="json") for s in result.selections],
        "coverage": result.coverage.model_dump(mode="json"), "reason_codes": result.reason_codes,
        "detail_refs": {"zones": "support_zones.json", "tests": "support_tests.json",
            "history": "support_history.json"}}


def support_zones_to_markdown(results):
    out = ["# 可验证支撑区域", "", "区域、建区ATR与失效线均在形成时冻结；本报告是研究/描述证据，不是交易许可。", ""]
    for r in results:
        out += [f"## {r.symbol} · {r.as_of} · {r.status.value}", "",
            f"结果ID：`{r.result_id}`；分析 {len(r.coverage.expected_sessions)} 个交易日，实际 {len(r.coverage.actual_sessions)} 根，评估至 {r.coverage.evaluated_through or '未评估'}。",
            "", "| 区域ID | 类型 | 下沿–上沿 | 建区ATR | 失效线 | 首次可知 | 状态 | HELD/全部测试 | 来源数 |", "|---|---|---:|---:|---:|---|---|---:|---:|"]
        zones = r.current_zones+r.archived_zones
        for z in zones:
            held = sum(t.status == "HELD" for t in z.tests)
            out.append(f"| `{z.zone_id}` | {z.zone_type} | {z.lower:.6g}–{z.upper:.6g} | {z.anchor_atr:.6g} | {z.invalidation_line:.6g} | {z.available_at} | {z.state} | {held}/{len(z.tests)} | {len(z.creation_sources)} |")
        if not zones:
            out.append("| 未形成 | — | — | — | — | — | — | — | — |")
        out += ["", "| 区域ID | 触及 | 截止 | 结果 | 首次HELD | 累计低点 | 反弹ATR | 穿透ATR | 离开日期 |", "|---|---|---|---|---|---:|---:|---:|---|"]
        for z in zones:
            for t in z.tests:
                out.append(f"| `{z.zone_id}` | {t.touch_session} | {t.confirmation_deadline} | {t.status} | {t.first_held_at or '—'} | {t.cumulative_low:.6g} | {t.rebound_atr if t.rebound_atr is not None else '—'} | {t.penetration_atr:.6g} | {t.departure_session or '—'} |")
        if not any(z.tests for z in zones):
            out.append("| 无测试 | — | — | — | — | — | — | — | — |")
        out += ["", "选择角色：" + "；".join(f"{s.role}={s.zone_id or s.status}" for s in r.selections),
            "", "缺口/原因：" + ("、".join(r.reason_codes) if r.reason_codes else "无"), ""]
    return "\n".join(out)


def find_support_zone(result, zone_id):
    return next((z for z in result.current_zones+result.archived_zones if z.zone_id == zone_id), None)


def find_support_test(result, test_id):
    return next((t for z in result.current_zones+result.archived_zones for t in z.tests if t.test_id == test_id), None)


def write_support_zone_artifacts(output_directory, results, *, audit=None):
    root = Path(output_directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("SUPPORT_OUTPUT_DIRECTORY_NOT_EMPTY")
    root.mkdir(parents=True, exist_ok=True)
    zones = [{"symbol": r.symbol, **z.model_dump(mode="json")} for r in results for z in r.current_zones+r.archived_zones]
    tests = [{"symbol": r.symbol, "zone_id": z.zone_id, **t.model_dump(mode="json")}
             for r in results for z in r.current_zones+r.archived_zones for t in z.tests]
    history = [{"symbol": r.symbol, **h.model_dump(mode="json")} for r in results for h in r.support_history]
    docs = {"support_zone_results.json": [r.model_dump(mode="json") for r in results],
        "support_zones.json": zones, "support_tests.json": tests, "support_history.json": history,
        "support_states.json": [r.next_state.model_dump(mode="json") for r in results],
        "support_zones.ai.json": [support_zone_to_ai_view(r) for r in results],
        "support_zone_result.schema.json": SupportZoneResult.model_json_schema(),
        "support_zone_input.schema.json": SupportZoneInput.model_json_schema(),
        "field_dictionary.json": {"zone_width": "anchor ± 0.175 * formation ATR; total 0.35 ATR",
            "invalidation": "lower - 0.35 * formation ATR; close below is BROKEN",
            "held": "touch+1 through touch+3: close >= upper and rebound from cumulative low >= 0.5 formation ATR",
            "retest": "after ended test, close >= upper+0.5 formation ATR, then later intersection",
            "intraday": "low below invalidation is recorded separately from close break",
            "missing": "null or PARTIAL with stable reason code; no imputation"},
        "read_audit.json": audit or {}}
    hashes = {name: _write_atomic(root/name, json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False))
              for name, doc in docs.items()}
    hashes["support_zones.zh-CN.md"] = _write_atomic(root/"support_zones.zh-CN.md", support_zones_to_markdown(results))
    code_root = Path(__file__).resolve().parents[3]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=code_root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=code_root, text=True).strip())
    _write_atomic(root/"artifact_manifest.json", json.dumps({"module": "support_zones", "version": "1.0",
        "source_commit": commit, "tracked_source_dirty": dirty,
        "created_at": datetime.now(timezone.utc).isoformat(), "symbols": [r.symbol for r in results],
        "result_ids": [r.result_id for r in results], "sha256": hashes}, indent=2))
    return root


def load_support_zone_state(output_directory, symbol):
    root = Path(output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    expected = manifest["sha256"]["support_states.json"]
    if sha256((root/"support_states.json").read_bytes()).hexdigest() != expected:
        raise ValueError("SUPPORT_STATE_HASH_MISMATCH")
    states = [SupportZoneState.model_validate(x) for x in json.loads(
        (root/"support_states.json").read_text(encoding="utf-8"))]
    return next((s for s in states if s.symbol == symbol.upper()), None)


def run_support_zone_command(args):
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    if not 1 <= len(symbols) <= 8:
        raise ValueError("SUPPORT_SYMBOL_LIMIT_1_TO_8")
    reader, results, failures = SupportZoneDataReader(), [], []
    for symbol in symbols:
        ctx = CallContext(symbol=symbol, requested_as_of=args.as_of,
            effective_daily_session=args.as_of, mode="HISTORICAL", run_id=args.run_id,
            request_id=f"{args.run_id}:{symbol}", scope="SUPPORT_ZONES")
        try:
            results.append(evaluate_support_zones(reader.load(ctx)))
        except (ValueError, RuntimeError) as exc:
            failures.append({"symbol": symbol, "stage": "SUPPORT_READ_OR_EVALUATE", "reason": str(exc)})
    verification = reader.verify_unchanged()
    root = write_support_zone_artifacts(args.output_directory, results,
        audit={"reads": reader.audit, "source_unchanged": verification, "failures": failures})
    print(json.dumps({"output_directory": str(root.resolve()), "results": len(results),
                      "failures": failures}, indent=2))
