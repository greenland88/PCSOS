"""Read-only canonical adapter and deterministic views for underlying profiles."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import subprocess

import pandas as pd

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.pool.artifacts import _write_atomic
from pcs.pool.runtime import ManifestSnapshot
from pcs.trend.selection_models import DailyBar, DailyFeatureView, ProfileInput, ProfilePolicy, UnderlyingProfile
from pcs.trend.underlying_profile import measure_underlying_profile, profile_sessions


class ProfileDataReader:
    """One invocation pins a manifest; shared benchmark is read only once.

    The caller supplies PCSDataAccess (canonical by default). No ensure/import
    or provider operation is reachable from this adapter.
    """
    def __init__(self, access=None):
        from pcs.data.access import PCSDataAccess
        self.access = access or PCSDataAccess.canonical()
        if self.access.routing_mode != "canonical":
            raise ValueError("PROFILE_CANONICAL_ACCESS_REQUIRED")
        self.snapshot = ManifestSnapshot.capture(self.access)
        self.manifest_sha256 = sha256(Path(self.access.manifest_path).read_bytes()).hexdigest()
        self.cache = {}
        self.file_hashes = {}
        self.audit = []

    def _read(self, symbol, day, policy, calendar):
        from pcs.data.strategy_readiness import resolve_active_verified_daily_handle
        sessions = profile_sessions(day, policy, calendar)
        key = (symbol, day, policy.required_sessions, calendar)
        self.snapshot.assert_current()
        if key in self.cache:
            return self.cache[key]
        handle = resolve_active_verified_daily_handle(symbol, day, policy.required_sessions,
            data_access=self.access, manifest_snapshot=self.snapshot, allow_partial_history=True)
        paths = [Path(p).resolve() for p in handle.canonical_paths]
        for path in paths:
            digest = sha256(path.read_bytes()).hexdigest()
            if str(path) in self.file_hashes and self.file_hashes[str(path)] != digest:
                raise ValueError("PROFILE_SOURCE_CHANGED")
            self.file_hashes[str(path)] = digest
        start = max(sessions[0], str(pd.Timestamp(handle.min_date).date()))
        frame = self.access.read_verified_dataset(handle, start_date=start, end_date=day,
                                                  manifest_snapshot=self.snapshot)
        def value(v):
            return None if pd.isna(v) else float(v)
        source = SourceReference(source_id=f"daily:{symbol}:{handle.dataset_fingerprint}",
            source_kind="VERIFIED_CANONICAL_DAILY", schema_version=handle.schema_version,
            sha256=handle.checksum, record_identity=handle.generation_id, validated=True,
            detail={"dataset_fingerprint": handle.dataset_fingerprint, "canonical_paths": [str(p) for p in paths],
                "file_sha256": {str(p): self.file_hashes[str(p)] for p in paths},
                "partitions": list(handle.partitions), "physical_row_count": handle.row_count,
                "physical_min_date": handle.min_date, "physical_max_date": handle.max_date,
                "manifest_path": str(Path(self.access.manifest_path).resolve()),
                "manifest_identity": self.snapshot.identity,
                "price_basis": handle.price_basis, "corporate_action_version": handle.corporate_action_version,
                "logical_read_start": start, "logical_read_end": day,
                "currency": "USD", "currency_source": "CONFIGURED_ASSUMPTION_US_EQUITIES"})
        bars = [DailyBar(session=pd.Timestamp(row.date).date(), **{k: value(getattr(row, k, None))
                     for k in ("open", "high", "low", "close", "volume")}) for row in frame.itertuples()]
        view = DailyFeatureView(symbol=symbol, bars=bars, source=source, price_basis=handle.price_basis,
            corporate_action_version=handle.corporate_action_version, currency="USD", input_kind="VERIFIED_CANONICAL")
        self.audit.append({"symbol": symbol, "requested_start": sessions[0], "requested_end": day,
            "requested_sessions": len(sessions), "returned_rows": len(frame), "source": source.model_dump(mode="json")})
        self.cache[key] = view
        return view

    def load(self, context: CallContext, *, policy: ProfilePolicy | None = None,
             benchmark_symbol: str | None = "SPY", calendar="XNYS") -> ProfileInput:
        policy = policy or ProfilePolicy()
        if context.effective_daily_session is None:
            raise ValueError("PROFILE_EFFECTIVE_SESSION_REQUIRED")
        view = self._read(context.symbol, context.effective_daily_session, policy, calendar)
        benchmark, reasons = None, []
        if benchmark_symbol:
            try:
                benchmark = self._read(benchmark_symbol.strip().upper(), context.effective_daily_session, policy, calendar)
            except (ValueError, RuntimeError) as exc:
                reasons = ["BENCHMARK_UNAVAILABLE", str(exc)]
        else:
            reasons = ["BENCHMARK_NOT_REQUESTED"]
        return ProfileInput(call_context=context, feature_view=view, benchmark=benchmark,
                            benchmark_reason_codes=reasons, effective_policy=policy, calendar=calendar)

    def verify_unchanged(self):
        self.snapshot.assert_current()
        if sha256(Path(self.access.manifest_path).read_bytes()).hexdigest() != self.manifest_sha256:
            raise ValueError("PROFILE_MANIFEST_CHANGED")
        for path, digest in self.file_hashes.items():
            if sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("PROFILE_SOURCE_CHANGED")
        return {"status": "PASS", "manifest_identity": self.snapshot.identity,
                "manifest_path": str(Path(self.access.manifest_path).resolve()), "manifest_sha256": self.manifest_sha256,
                "file_count": len(self.file_hashes), "file_sha256": self.file_hashes}


def underlying_profile_to_ai_view(result: UnderlyingProfile) -> dict:
    return {"result_id": result.result_id, "symbol": result.symbol, "status": result.status.value,
            "as_of": result.time_context.effective_daily_session,
            "measurements": [m.model_dump(mode="json") for m in result.measurements],
            "episodes": [e.model_dump(mode="json") for e in result.episodes],
            "reason_codes": result.reason_codes, "explanation": result.explanation,
            "detail_ref": "underlying_profiles.json", "policy_sha256": result.policy_sha256}


def underlying_profiles_to_markdown(results: list[UnderlyingProfile]) -> str:
    out = ["# 股票风险特点档案", "", "数值均为描述性历史统计。比例使用小数（0.05=5%）；成交额USD为美国股票配置口径。", ""]
    for r in results:
        out += [f"## {r.symbol} · {r.time_context.effective_daily_session} · {r.status.value}", "",
                f"结果身份：`{r.result_id}`。实际读取 {len(r.coverage.actual_sessions)} 根；观察窗口 {len(r.coverage.analysis_sessions)} 个交易日。",
                "", "| 指标 | 数值 | 单位 | 状态 | 有效样本 | 缺失原因 |", "|---|---:|---|---|---:|---|"]
        for m in r.measurements:
            value = "缺失" if m.value is None else f"{m.value:.8g}"
            out.append(f"| {metric_label(m.metric_id)} (`{m.metric_id}`) | {value} | {m.unit} | {m.status.value} | {m.sample_count} | {', '.join(m.reason_codes) or '—'} |")
        out += ["", "| 回撤开始 | 冻结高点日期/价格 | 最低点日期/价格 | 最大深度 | 恢复日期 | 已观察交易日 | 左截断 | 右删失 |",
                "|---|---|---|---|---|---|---:|---|---|"]
        for e in r.episodes:
            out.append(f"| {e.start_session or '未记录'} | {e.frozen_peak_session} / {e.frozen_peak_close:.8g} | {e.trough_session} / {e.trough_close:.8g} | {e.max_depth:.6g} | {e.recovery_session or '未恢复'} | {e.observed_sessions} | {e.left_truncated} | {e.right_censored} |")
        if not r.episodes:
            out.append("| 本次覆盖中未识别到事件 | — | — | — | — | — | — | — |")
        out += ["", r.explanation, ""]
    return "\n".join(out)


def metric_label(name):
    for prefix, label in (("dollar_volume", "成交额中位数"), ("realized_volatility", "实现波动率"),
        ("relative_strength", "相对基准收益差"), ("atr_over_price", "ATR/价格"), ("gap_up", "上跳空"),
        ("gap_down", "下跳空"), ("drawdown_depth", "滚动回撤深度分位数"),
        ("current_rolling_drawdown", "当前滚动回撤"), ("current_drawdown_percentile", "当前回撤历史分位位置"),
        ("recovered_conditional_median", "已恢复样本条件中位天数"), ("recovery_km_median", "KM恢复中位天数"),
        ("unrecovered_count", "未恢复数量"), ("left_truncated_count", "左截断数量"), ("episode_count", "观察到的回撤事件数量")):
        if name.startswith(prefix):
            return label
    return name


def write_underlying_profile_artifacts(output_directory, results, *, audit=None):
    root = Path(output_directory)
    if root.exists() and any(root.iterdir()):
        raise ValueError("PROFILE_OUTPUT_DIRECTORY_NOT_EMPTY")
    root.mkdir(parents=True, exist_ok=True)
    payload = [r.model_dump(mode="json") for r in results]
    dictionary = {m.metric_id: {"label_zh": metric_label(m.metric_id), "formula": m.formula,
                  "unit": m.unit, "window_sessions": m.window_sessions, "definition_ref": m.definition_ref}
                  for r in results for m in r.measurements}
    documents = {"underlying_profiles.json": payload,
        "underlying_profiles.ai.json": [underlying_profile_to_ai_view(r) for r in results],
        "underlying_profile.schema.json": UnderlyingProfile.model_json_schema(),
        "profile_input.schema.json": ProfileInput.model_json_schema(),
        "field_dictionary.json": {"metrics": dictionary, "missing": "null + reason_codes; never zero imputation",
            "source_status": "DERIVED or MISSING; execution_status is separate",
            "episode_age": "elapsed exchange-session intervals; trigger day is zero; see left/right censor flags",
            "timestamps": "source_timestamp may be unknown; session close is only a derived known-at lower bound"},
        "read_audit.json": audit or {}}
    hashes = {name: _write_atomic(root/name, json.dumps(doc, ensure_ascii=False, indent=2, allow_nan=False))
              for name, doc in documents.items()}
    hashes["underlying_profiles.zh-CN.md"] = _write_atomic(root/"underlying_profiles.zh-CN.md", underlying_profiles_to_markdown(results))
    code_root = Path(__file__).resolve().parents[3]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=code_root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=code_root, text=True).strip())
    _write_atomic(root/"artifact_manifest.json", json.dumps({"module": "underlying_profile", "version": "1.0",
        "source_commit": sha, "tracked_source_dirty": dirty, "created_at": datetime.now(timezone.utc).isoformat(),
        "symbols": [r.symbol for r in results], "result_ids": [r.result_id for r in results], "sha256": hashes}, indent=2))
    return root


def run_profile_command(args):
    symbols = list(dict.fromkeys(s.strip().upper() for s in args.symbols.split(",") if s.strip()))
    if not 1 <= len(symbols) <= 8:
        raise ValueError("PROFILE_SYMBOL_LIMIT_1_TO_8")
    reader = ProfileDataReader()
    results, failures = [], []
    for symbol in symbols:
        ctx = CallContext(symbol=symbol, requested_as_of=args.as_of, effective_daily_session=args.as_of,
                          mode="HISTORICAL", run_id=args.run_id, request_id=f"{args.run_id}:{symbol}", scope="UNDERLYING_PROFILE")
        try:
            results.append(measure_underlying_profile(reader.load(ctx, benchmark_symbol=args.benchmark)))
        except (ValueError, RuntimeError) as exc:
            failures.append({"symbol": symbol, "stage": "PROFILE_READ_OR_MEASURE", "reason": str(exc)})
    verification = reader.verify_unchanged()
    root = write_underlying_profile_artifacts(args.output_directory, results,
        audit={"reads": reader.audit, "source_unchanged": verification, "failures": failures})
    print(json.dumps({"output_directory": str(root.resolve()), "profiles": len(results), "failures": failures}, indent=2))
