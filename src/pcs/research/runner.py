"""Single, guarded orchestration boundary for PCS research.

The runner owns intent validation and population routing.  Data-specific
research modules provide preflight counts; they never choose a population.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import json
import hashlib
import os
import gc
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import pandas as pd
from typing import Any, Mapping

from .research_framework import (
    ResearchMode, ResearchSpec, ResearchStatus, ResearchSpecError,
    FunnelStage, build_funnel, onboarding_report, validate_population_routing,
    load_spec, spec_hash, assert_research_output, validate_rule_set,
)
from pcs.data.access import PCSDataAccess, DataAccessError
from .underlying_state import evaluate_as_of, UnderlyingState
from pcs.trend.config import TrendIndicatorConfig
from pcs.trend.indicators import calculate_base_indicators
from pcs.trend.market_structure import _find_confirmed_swings
from pcs.trend.relative_strength import RelativeStrengthResult, _classify_state, _is_stock_specific_weakness, _safe_return
from .pit_cache_identity import build_pit_cache_identity, cache_identity_matches
from .integrity_contract import REPRODUCIBILITY_REQUIRED, validate_reproducibility_manifest


def _status_for_funnel(records: list[Any]) -> str:
    return next((r.status.value for r in records if r.output_count == 0), ResearchStatus.COMPUTABLE.value)


def _evaluate_pit_timeline(daily: pd.DataFrame, ticker: str) -> list[dict[str, Any]]:
    """Evaluate the existing PIT adapter with reusable daily-only inputs."""
    config = TrendIndicatorConfig()
    indicators = calculate_base_indicators(daily, config)
    swings = tuple(_find_confirmed_swings(daily, daily.date, config))
    closes = daily.close.astype(float).to_numpy()
    dates = pd.to_datetime(daily.date).dt.normalize().tolist()
    rs_by_date = {}
    for i, day in enumerate(dates):
        values = {}
        if i > 60:
            for window in (5, 20, 60):
                value = _safe_return(closes[i], closes[i-window])
                if value is None:
                    break
                values[f"stock_return_{window}d"] = value
                values[f"benchmark_return_{window}d"] = value
                values[f"relative_return_{window}d"] = 0.0
        if len(values) == 9:
            rs_by_date[day] = RelativeStrengthResult(True, values['stock_return_5d'], values['benchmark_return_5d'], 0.0, values['stock_return_20d'], values['benchmark_return_20d'], 0.0, values['stock_return_60d'], values['benchmark_return_60d'], 0.0, _classify_state(values, config), _is_stock_specific_weakness(values, config))
        else:
            rs_by_date[day] = RelativeStrengthResult(False, None, None, None, None, None, None, None, None, None, None, None)
    # Snapshot construction intentionally keeps the canonical PIT semantics,
    # but its intermediate DataFrames can be large for long-lived tickers.
    # Bound peak memory without changing ordering or result rows.
    dates = list(daily.date)
    def evaluate_chunk(bounds):
        start, end = bounds
        chunk = []
        for day in dates[start:start + 250]:
            chunk.append(evaluate_as_of(
                daily, ticker, day, config,
                precomputed_indicators=indicators,
                precomputed_swings=swings,
                precomputed_relative_strength=rs_by_date[pd.Timestamp(day).normalize()],
            ))
        return start, chunk

    bounds = [(start, min(start + 250, len(dates))) for start in range(0, len(dates), 250)]
    # Threads share the read-only caches and avoid eight copies of the full
    # historical DataFrame on Windows.  Sort by original chunk start to keep
    # byte/order semantics deterministic.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="pcs-msft-pit") as pool:
        completed = list(pool.map(evaluate_chunk, bounds))
    result = []
    for _, chunk in sorted(completed, key=lambda item: item[0]):
        result.extend(chunk)
    gc.collect()
    return result


class ResearchRunner:
    """Run only a validated spec and write only to an isolated research path."""

    def __init__(self, spec: ResearchSpec, *, output_dir: str | Path = "research_outputs"):
        self.spec = validate_rule_set(validate_population_routing(spec))
        self.output_dir = Path(output_dir) / self.spec.research_id
        assert_research_output(self.output_dir)

    @classmethod
    def from_path(cls, path: str | Path, **kwargs: Any) -> "ResearchRunner":
        return cls(load_spec(path), **kwargs)

    def preflight(self, counts: Mapping[str, int] | None = None,
                  *, onboarding: Mapping[str, str] | None = None,
                  reasons: Mapping[str, str] | None = None,
                  remediations: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Create a machine-readable preflight; absent counts remain zero.

        Adapters should supply counts from PCSDataAccess and deterministic
        engines.  No fallback to a frozen ledger is permitted.
        """
        counts = dict(counts or {})
        if self.spec.research_mode != ResearchMode.NEW_ENTRY:
            counts = {k: v for k, v in counts.items() if k not in {
                FunnelStage.PRECURSOR_EPISODES.value, FunnelStage.SIGNAL_DATES.value}}
        funnel = build_funnel(counts, reasons=reasons, remediations=remediations)
        result = {
            "module": "pcs.research.runner", "version": "1.0", "research_id": self.spec.research_id,
            "ticker": self.spec.ticker, "research_mode": self.spec.research_mode.value,
            "status": _status_for_funnel(funnel), "scenario_hash": spec_hash(self.spec),
            "population_source": dict(self.spec.population_source),
            "rules": dict(self.spec.rules),
            "rule_set": self._rule_set(),
            "data_source": "SYNTHETIC_FIXTURE",
            "funnel": [asdict(row) for row in funnel],
            "onboarding": onboarding_report(onboarding or {}) if onboarding is not None else None,
            "final_oos_read": False, "production_changes_allowed": False,
            "reason_codes": ["RESEARCH_SPEC_VALIDATED", "POPULATION_ROUTED_EXPLICITLY",
                             "FINAL_OOS_NOT_READ", "PRODUCTION_WRITE_BLOCKED"],
        }
        return result

    def _rule_set(self) -> dict[str, Any]:
        if self.spec.research_mode.value != "CURRENT_STRATEGY_REPLAY":
            return {}
        from .research_framework import CURRENT_RULE_DEFAULTS
        out = dict(CURRENT_RULE_DEFAULTS)
        out.update(self.spec.rules)
        out["allowed_widths"] = [float(x) for x in out["allowed_widths"]]
        out["width_mode"] = str(out["width_mode"]).upper()
        return out

    def rule_set_plumbing(self, counts: Mapping[str, int] | None = None) -> dict[str, Any]:
        """Return an isolated rule-set funnel contract for adapter execution.

        This deliberately does not invent candidate counts.  A future canonical
        adapter supplies counts after invoking the shared producers and gates.
        """
        if self.spec.research_mode.value != "CURRENT_STRATEGY_REPLAY":
            raise ResearchSpecError("RULE_SET_ONLY_SUPPORTS_CURRENT_STRATEGY_REPLAY")
        counts = dict(counts or {})
        names = ("TRADING_DAYS", "FEATURE_READY_DAYS", "SETUP_ELIGIBLE_DAYS",
                 "CONTRACT_CANDIDATES", "SELECTED_ENTRIES", "LIFECYCLES_COMPLETED")
        funnel = [{"stage": name, "count": int(counts.get(name, 0))} for name in names]
        for rule in ("trend", "pullback", "support", "regime", "event", "dte",
                     "safe_strike", "liquidity", "credit_width"):
            key = f"{rule.upper()}_REJECTED"
            funnel.append({"stage": key, "count": int(counts.get(key, 0)),
                           "enabled": self._rule_set().get(f"{rule}_gate", True)})
        return {"module": "pcs.research.runner", "version": "1.0",
                "research_id": self.spec.research_id, "ticker": self.spec.ticker,
                "research_mode": self.spec.research_mode.value,
                "data_source": "PCS_CANONICAL_DATA", "rule_set": self._rule_set(),
                "funnel": funnel, "final_oos_read": False,
                "production_changes_allowed": False,
                "reason_codes": ["RULE_SET_VALIDATED", "RULE_SET_ISOLATED",
                                 "FINAL_OOS_NOT_READ", "PRODUCTION_WRITE_BLOCKED"]}

    def execute_current_strategy_replay(self, *, data_access: PCSDataAccess | None = None) -> dict[str, Any]:
        if self.spec.research_mode.value not in {"CURRENT_STRATEGY_REPLAY", "NEW_ENTRY"}:
            raise ResearchSpecError("EXECUTION_ONLY_SUPPORTS_NEW_ENTRY_OR_CURRENT_REPLAY")
        from .ticker_readiness import assert_research_ready
        from pcs.data.ticker_registry import get_ticker_state
        access = data_access or PCSDataAccess.canonical()
        registry_state = get_ticker_state(self.spec.ticker, access=access)
        if registry_state.PCS_RESEARCH_READY != "YES":
            raise ResearchSpecError(
                f"PCS_RESEARCH_NOT_READY_REGISTRY:{self.spec.ticker.upper()}:"
                f"{registry_state.PRIMARY_BLOCKER}"
            )
        assert_research_ready(self.spec.ticker, access=access)
        clean_path = self.spec.population_source.get("authoritative_clean_dataset")
        if clean_path:
            # This is an explicit, research-scoped admission path.  It validates
            # the requested clean population rather than substituting the
            # unrelated full-history preflight population.
            p = Path(clean_path)
            if not p.is_file():
                raise RuntimeError(f"CLEAN_DATASET_UNAVAILABLE:{p}")
            frame = pd.read_parquet(p)
            required = {"date", "symbol", "testable_day"}
            if not required.issubset(frame.columns) or frame.empty or not bool(frame.testable_day.all()):
                raise RuntimeError("CLEAN_DATASET_VALIDATION_FAILED")
        from .current_strategy_replay import run_current_strategy_replay
        from pcs.data.price_basis import load_corporate_actions
        return run_current_strategy_replay(self.spec, data_access=access, output_dir=self.output_dir.parent,
                                           price_basis_service=load_corporate_actions())

    execute_research_replay = execute_current_strategy_replay

    def persist(self, result: Mapping[str, Any], *, filename: str = "preflight.json") -> Path:
        """Persist an auditable research-only result envelope."""
        if result.get("data_source") != "PCS_CANONICAL_DATA":
            raise PermissionError("SYNTHETIC_FIXTURE_CANNOT_WRITE_RESEARCH_OUTPUTS")
        assert_research_output(self.output_dir / filename)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / filename
        target.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        return target

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _git_commit_sha() -> str:
        """Return the exact source revision, never a semantic placeholder."""
        try:
            return subprocess.check_output(
                ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL,
                text=True, cwd=Path.cwd(), timeout=5,
            ).strip()
        except (OSError, subprocess.SubprocessError):
            return "UNKNOWN_GIT_COMMIT"

    def write_artifact_manifest(self, files: list[str], *, data_version: str,
                                population_semantics: str,
                                artifact_version: str = "1.0",
                                reproducibility: Mapping[str, Any] | None = None) -> Path:
        """Write the one CURRENT manifest for this research id."""
        for sibling in self.output_dir.parent.glob(f"{self.spec.research_id}*"):
            sibling_manifest = sibling / "artifact_manifest.json"
            if sibling != self.output_dir and sibling_manifest.is_file():
                try:
                    if json.loads(sibling_manifest.read_text(encoding="utf-8")).get("current") is True:
                        raise RuntimeError("DUPLICATE_CURRENT_ARTIFACT")
                except json.JSONDecodeError:
                    continue
        self.output_dir.mkdir(parents=True, exist_ok=True)
        records = []
        for relative in files:
            path = self.output_dir / relative
            if not path.is_file():
                raise FileNotFoundError(f"artifact manifest file missing: {relative}")
            records.append({"path": relative.replace("\\", "/"), "sha256": self._sha256(path)})
        identity = dict(reproducibility or {})
        identity.setdefault("git_commit_sha", self._git_commit_sha())
        identity.setdefault("research_spec_hash", spec_hash(self.spec))
        identity.setdefault("runner_version", "pcs.research.runner:1.1")
        identity.setdefault("feature_calculation_version", "UNKNOWN")
        identity.setdefault("strategy_definition_hash", "UNKNOWN")
        identity.setdefault("daily_source_version", data_version)
        identity.setdefault("options_source_version", "UNKNOWN")
        identity.setdefault("daily_manifest_path", "UNKNOWN")
        identity.setdefault("options_manifest_path", "UNKNOWN")
        identity.setdefault("daily_manifest_sha", "UNKNOWN")
        identity.setdefault("options_manifest_sha", "UNKNOWN")
        identity.setdefault("corporate_action_version", "UNKNOWN")
        identity.setdefault("config_hash", "UNKNOWN")
        identity.setdefault("population_hash", "UNKNOWN")
        identity.setdefault("candidates_ledger_hash", "UNKNOWN")
        identity.setdefault("selected_trade_ledger_hash", "UNKNOWN")
        identity.setdefault("lifecycle_ledger_hash", "UNKNOWN")
        complete = all(identity.get(key) not in (None, "", "UNKNOWN") for key in REPRODUCIBILITY_REQUIRED)
        manifest = {
            "research_id": self.spec.research_id,
            "status": "CURRENT" if complete else "LEGACY_REFERENCE_INCOMPLETE",
            "artifact_version": artifact_version, "population_semantics": population_semantics,
            "data_source": "PCS_CANONICAL_DATA", "ticker": self.spec.ticker,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "code_version": "pcs.research.runner:1.1",
            "git_commit_sha": self._git_commit_sha(),
            "data_version": data_version, "spec_hash": spec_hash(self.spec), "files": records,
            "current": complete,
            "reproducibility_complete": complete,
            **identity,
        }
        target = self.output_dir / "artifact_manifest.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(temp, target)
        return target

    def read_current_artifact(self) -> dict[str, Any]:
        """Read only a hash-validated CURRENT canonical artifact set."""
        path = self.output_dir / "artifact_manifest.json"
        if not path.is_file():
            raise RuntimeError("STALE_ARTIFACT")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if ((manifest.get("current") is not True and manifest.get("status") != "LEGACY_REFERENCE_INCOMPLETE")
                or manifest.get("data_source") != "PCS_CANONICAL_DATA"
                or manifest.get("research_id") != self.spec.research_id
                or manifest.get("spec_hash") != spec_hash(self.spec)
                or manifest.get("code_version") != "pcs.research.runner:1.1"):
            raise RuntimeError("STALE_ARTIFACT")
        try:
            current_data_version = PCSDataAccess().resolve_source("daily", self.spec.ticker).source_version
        except Exception as exc:
            raise RuntimeError("STALE_ARTIFACT") from exc
        if manifest.get("data_version") != current_data_version:
            raise RuntimeError("STALE_ARTIFACT")
        recorded_git = manifest.get("git_commit_sha")
        if recorded_git and recorded_git != self._git_commit_sha():
            raise RuntimeError("STALE_ARTIFACT")
        if manifest.get("reproducibility_complete") is True:
            try:
                validate_reproducibility_manifest(manifest)
                current_options = PCSDataAccess().resolve_source("options", self.spec.ticker)
            except Exception as exc:
                raise RuntimeError("STALE_ARTIFACT") from exc
            if manifest.get("options_source_version") != current_options.source_version:
                raise RuntimeError("STALE_ARTIFACT")
        for record in manifest.get("files", []):
            file_path = self.output_dir / record["path"]
            if not file_path.is_file() or self._sha256(file_path) != record.get("sha256"):
                raise RuntimeError("STALE_ARTIFACT")
        if (manifest.get("status") == "LEGACY_REFERENCE_INCOMPLETE"
                or manifest.get("reproducibility_complete") is not True):
            raise RuntimeError("LEGACY_REFERENCE_INCOMPLETE")
        return manifest

    def dry_run(self, **kwargs: Any) -> dict[str, Any]:
        if self.spec.research_mode.value == "CURRENT_STRATEGY_REPLAY":
            result = self.rule_set_plumbing(kwargs.pop("counts", None))
            result["execution"] = "DRY_RUN_ONLY"
            return result
        result = self.preflight(**kwargs)
        result["execution"] = "DRY_RUN_ONLY"
        return result

    def real_preflight(self, *, data_access: PCSDataAccess | None = None,
                       start_date: Any = None, end_date: Any = None) -> dict[str, Any]:
        """Read the ticker calendar through PCSDataAccess and report readiness.

        This is intentionally descriptive: it does not select contracts,
        replay lifecycle, tune thresholds, or read any candidate ledger.
        """
        access = data_access or PCSDataAccess()
        try:
            daily = access.read_prices(self.spec.ticker, start_date, end_date)
            daily_status = "PASS" if len(daily) else "MISSING"
        except (FileNotFoundError, DataAccessError, ValueError) as exc:
            return {"module": "pcs.research.runner", "version": "1.0", "ticker": self.spec.ticker,
                    "research_mode": self.spec.research_mode.value, "status": ResearchStatus.DAILY_DATA_MISSING.value,
                    "data_source": "PCS_CANONICAL_DATA", "exact_reason": str(exc),
                    "final_oos_read": False, "production_changes_allowed": False}
        options_status = "PASS"
        try:
            option_source = access.resolve_source("options", self.spec.ticker)
        except Exception as exc:
            options_status, option_source = "MISSING", {"exact_reason": str(exc)}
        # Build the existing PIT state adapter's timeline.  Do not replace it
        # with a local indicator calculation; dependency failures must remain
        # explicit rather than becoming UNKNOWN precursor results.
        timeline_path = self.output_dir / "pit_state_timeline.parquet"
        daily_source = access.resolve_source("daily", self.spec.ticker, start_date, end_date)
        cache_meta = build_pit_cache_identity(
            symbol=self.spec.ticker,
            daily_data_identity=daily_source.source_version,
            date_range={"start": str(daily.date.min().date()), "end": str(daily.date.max().date())},
            feature_config=asdict(TrendIndicatorConfig()), research_config=self.spec.rules,
        )
        cache_created_at = datetime.now(timezone.utc).isoformat()
        cache_action = "MISS_REBUILT"
        if timeline_path.exists():
            cached = pd.read_parquet(timeline_path)
            cache_valid = cache_identity_matches(cached, cache_meta)
            cached_ready = sum(
                bool(row.get("available_data")) and row.get("final_underlying_state") != UnderlyingState.UNKNOWN.value
                for row in cached.to_dict("records")
            )
            if cache_valid and len(cached) == len(daily) and set(cached.symbol.astype(str).str.upper()) == {self.spec.ticker} and cached_ready > 0:
                states = cached.to_dict("records")
                cache_action = "REUSED_COMPATIBLE"
            else:
                identity_compatible = cache_valid
                states = _evaluate_pit_timeline(daily, self.spec.ticker)
                cache_valid = False
                cache_action = ("IDENTITY_COMPATIBLE_BUT_NOT_READY_REBUILT" if identity_compatible else "STALE_OR_MISSING_IDENTITY_REBUILT")
        else:
            states = _evaluate_pit_timeline(daily, self.spec.ticker)
            cache_valid = False
        if not timeline_path.exists() or not cache_valid:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            timeline_frame = pd.DataFrame(states)
            timeline_frame["symbol"] = cache_meta["symbol"]
            timeline_frame["ticker"] = cache_meta["symbol"]
            for key, value in cache_meta.items():
                if key not in {"symbol", "date_range_start", "date_range_end"}:
                    timeline_frame[key] = value
            timeline_frame["date_range_start"] = cache_meta["date_range_start"]
            timeline_frame["date_range_end"] = cache_meta["date_range_end"]
            timeline_frame["created_at"] = cache_created_at
            for column in timeline_frame.columns:
                if timeline_frame[column].map(lambda value: isinstance(value, (list, dict, tuple))).any():
                    timeline_frame[column] = timeline_frame[column].map(
                        lambda value: json.dumps(value, default=str) if isinstance(value, (list, dict, tuple)) else value
                    )
            timeline_frame.to_parquet(timeline_path, index=False)
        state_ready = sum(
            bool(row.get("available_data")) and row.get("final_underlying_state") != UnderlyingState.UNKNOWN.value
            for row in states
        )
        breakdown_runs = 0
        in_breakdown_run = False
        for row in states:
            is_breakdown = row.get("final_underlying_state") == UnderlyingState.BREAKDOWN.value
            if is_breakdown and not in_breakdown_run:
                breakdown_runs += 1
            in_breakdown_run = is_breakdown
        warmup = min(len(daily), 200)
        feature_ready = state_ready
        state_timeline_status = "PASS" if state_ready else "MISSING"
        counts = {FunnelStage.ALL_TRADING_DAYS.value: len(daily),
                  FunnelStage.FEATURE_READY_DAYS.value: feature_ready,
                  FunnelStage.PRECURSOR_EPISODES.value: breakdown_runs,
                  FunnelStage.SIGNAL_DATES.value: 0,
                  FunnelStage.EVENT_ELIGIBLE_DATES.value: 0,
                  FunnelStage.CONTRACT_AVAILABLE_DATES.value: 0,
                  FunnelStage.LIQUIDITY_ELIGIBLE_DATES.value: 0,
                  FunnelStage.SIMULATED_ENTRIES.value: 0}
        result = self.preflight(counts, onboarding={
            "DATA_DISCOVERY": "PASS", "DAILY_VALIDATION": daily_status,
            "OPTIONS_VALIDATION": options_status, "PIT_FEATURE_BUILD": "PASS" if feature_ready else "MISSING",
            "STATE_TIMELINE_BUILD": state_timeline_status, "CONTRACT_SELECTION_SMOKE_TEST": "NOT_RUN",
            "LIFECYCLE_SMOKE_TEST": "NOT_RUN", "RESEARCH_READY": "NOT_READY"},
            reasons={FunnelStage.PRECURSOR_EPISODES.value: "descriptive BREAKDOWN episode discovery from PIT state timeline",
                     FunnelStage.SIGNAL_DATES.value: "signal definition requires frozen research contract",
                     FunnelStage.FEATURE_READY_DAYS.value: "canonical PIT state adapter returned no usable states"},
            remediations={FunnelStage.PRECURSOR_EPISODES.value: "none; freeze recovery predicate before signal discovery",
                          FunnelStage.SIGNAL_DATES.value: "freeze signal_definition before hypothesis execution",
                          FunnelStage.FEATURE_READY_DAYS.value: "install/validate the declared TA-Lib dependency and rerun PIT feature build"})
        result.update({"data_source": "PCS_CANONICAL_DATA", "daily_source": "PCSDataAccess",
                       "daily_rows": len(daily), "daily_first_date": str(daily.date.min().date()),
                       "daily_last_date": str(daily.date.max().date()),
                       "options_source": option_source.to_dict() if hasattr(option_source, "to_dict") else option_source})
        result["state_timeline_rows"] = len(states)
        result["state_ready_rows"] = state_ready
        result["pit_cache_action"] = cache_action
        result["breakdown_run_count"] = breakdown_runs
        result["recovery_signal_status"] = "NOT_DEFINED"
        if not state_ready:
            result["exact_reason"] = "PIT state adapter produced no usable states; inspect onboarding PIT_FEATURE_BUILD"
            result["reason_codes"].append("PIT_STATE_TIMELINE_UNAVAILABLE")
        result["signal_availability"] = "DESCRIPTIVE_ONLY"
        result["signal_execution"] = "NOT_RUN"
        result["status"] = ResearchStatus.SPEC_INCOMPLETE.value
        result["funnel_status"] = next((r["status"] for r in result["funnel"] if r["output_count"] == 0), ResearchStatus.COMPUTABLE.value)
        result["reason_codes"].extend(["SIGNAL_CONTRACT_NOT_FROZEN", "DESCRIPTIVE_PREFLIGHT_ONLY"])
        self.persist(result)
        return result

    def calendar_preflight(self, *, data_access: PCSDataAccess | None = None) -> dict[str, Any]:
        """Fast canonical-calendar admission check without state construction."""
        access = data_access or PCSDataAccess()
        daily = access.read_prices(self.spec.ticker)
        options = access.resolve_source("options", self.spec.ticker)
        result = {
            "module": "pcs.research.runner", "version": "1.1", "research_id": self.spec.research_id,
            "ticker": self.spec.ticker, "research_mode": self.spec.research_mode.value,
            "status": ResearchStatus.COMPUTABLE.value, "data_source": "PCS_CANONICAL_DATA",
            "daily_source": "PCSDataAccess", "daily_rows": len(daily),
            "daily_first_date": str(daily.date.min().date()), "daily_last_date": str(daily.date.max().date()),
            "options_source": options.to_dict(), "final_oos_read": False,
            "production_changes_allowed": False, "signal_execution": "NOT_RUN",
            "reason_codes": ["RESEARCH_SPEC_VALIDATED", "CANONICAL_CALENDAR_RESOLVED", "FINAL_OOS_NOT_READ"],
        }
        self.persist(result, filename="calendar_preflight.json")
        return result


def run(path: str | Path, *, counts: Mapping[str, int] | None = None,
        onboarding: Mapping[str, str] | None = None) -> dict[str, Any]:
    return ResearchRunner.from_path(path).dry_run(counts=counts, onboarding=onboarding)
