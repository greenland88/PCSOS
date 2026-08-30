"""Research-only, ticker-independent Covered Call A2 timing primitives.

This module deliberately contains no production profile and no order path.
It provides PIT-safe timing evidence and frozen contract-neighborhood schemas
for the A2 runner.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import StrEnum
from typing import Any, Mapping
from collections import defaultdict
from pathlib import Path
import csv
import json


class TimingFamily(StrEnum):
    ALWAYS_SELL = "ALWAYS_SELL"
    RALLY = "RALLY"
    STRONG_RALLY = "STRONG_RALLY"
    OVEREXTENSION = "OVEREXTENSION"
    RECENT_HIGH = "RECENT_HIGH"
    RESISTANCE = "RESISTANCE"
    HIGH_IV = "HIGH_IV"
    RALLY_HIGH_IV = "RALLY_HIGH_IV"
    RALLY_RESISTANCE = "RALLY_RESISTANCE"
    TREND_OVEREXTENSION = "TREND_OVEREXTENSION"
    TREND_RECENT_HIGH = "TREND_RECENT_HIGH"
    SIDEWAYS = "SIDEWAYS"
    MOMENTUM_SLOWDOWN = "MOMENTUM_SLOWDOWN"


class WaitState(StrEnum):
    BREAKOUT = "BREAKOUT"
    STRONG_UPTREND_ACCELERATION = "STRONG_UPTREND_ACCELERATION"
    POST_CORRECTION_RECOVERY = "POST_CORRECTION_RECOVERY"
    LOW_IV = "LOW_IV"
    EARNINGS_RISK = "EARNINGS_RISK"
    UNPREDICTABLE = "UNPREDICTABLE"


@dataclass(frozen=True)
class TimingEvidence:
    qualified: bool
    reason_codes: tuple[str, ...]
    feature_values: dict[str, Any]
    thresholds_used: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FrozenContractNeighborhood:
    dte_min: int
    dte_max: int
    deltas: tuple[float, ...]
    otm_values: tuple[float, ...]
    atr_values: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.dte_min > self.dte_max or not self.deltas or not self.otm_values or not self.atr_values:
            raise ValueError("FROZEN_STAGE_B_NEIGHBORHOOD_INVALID")
        if any(x <= 0 for x in self.deltas + self.otm_values + self.atr_values):
            raise ValueError("FROZEN_STAGE_B_NEIGHBORHOOD_VALUES_INVALID")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(row: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    value = row.get(key, default)
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _known_num(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def evaluate_timing_family(family: str | TimingFamily, features: Mapping[str, Any]) -> TimingEvidence:
    """Evaluate one timing family using only already-computed PIT features."""
    name = TimingFamily(str(family).upper())
    sma20, sma50 = _known_num(features, "close_vs_sma20"), _known_num(features, "close_vs_sma50")
    r5, r20 = _known_num(features, "return_5d"), _known_num(features, "return_20d")
    extension, recent_distance = _known_num(features, "distance_to_sma20_atr"), _known_num(features, "distance_from_20d_high")
    resistance_distance, iv_rank = (_known_num(features, "distance_to_resistance_pct"),
                                    _known_num(features, "iv_rank"))
    trend = sma20 is not None and sma50 is not None and sma20 >= 0 and sma50 >= 0
    rally = r5 is not None and r5 > 0
    strong = rally and r20 is not None and r20 > 0 and extension is not None and extension >= 1.0
    over = extension is not None and extension >= 1.0
    recent = recent_distance is not None and recent_distance >= -0.02
    resistance = resistance_distance is not None and resistance_distance <= 0.02
    high_iv = iv_rank is not None and iv_rank >= 0.60
    sideways = r20 is not None and extension is not None and abs(r20) <= 0.05 and abs(extension) < 1.0
    slowdown = r5 is not None and r20 is not None and r5 < r20 / 4.0
    predicates = {
        TimingFamily.ALWAYS_SELL: True,
        TimingFamily.RALLY: rally,
        TimingFamily.STRONG_RALLY: strong,
        TimingFamily.OVEREXTENSION: over,
        TimingFamily.RECENT_HIGH: recent,
        TimingFamily.RESISTANCE: resistance,
        TimingFamily.HIGH_IV: high_iv,
        TimingFamily.RALLY_HIGH_IV: rally and high_iv,
        TimingFamily.RALLY_RESISTANCE: rally and resistance,
        TimingFamily.TREND_OVEREXTENSION: trend and over,
        TimingFamily.TREND_RECENT_HIGH: trend and recent,
        TimingFamily.SIDEWAYS: sideways,
        TimingFamily.MOMENTUM_SLOWDOWN: slowdown,
    }
    selected = bool(predicates[name])
    return TimingEvidence(
        qualified=selected,
        reason_codes=("TIMING_QUALIFIED" if selected else "TIMING_NOT_QUALIFIED", "PIT_FEATURES_ONLY"),
        feature_values={k: features.get(k) for k in (
            "return_5d", "return_20d", "close_vs_sma20", "close_vs_sma50",
            "distance_to_sma20_atr", "distance_from_20d_high",
            "distance_to_resistance_pct", "iv_rank")},
        thresholds_used={"rally_return_5d": 0.0, "overextension_atr": 1.0,
                         "recent_high_distance": -0.02, "resistance_distance_pct": 0.02,
                         "high_iv_rank": 0.60, "sideways_abs_return_20d": 0.05},
    )


def evaluate_wait_states(features: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return explicit WAIT evidence; UNKNOWN inputs never imply PASS."""
    checks = {
        WaitState.BREAKOUT: bool(features.get("breakout")),
        WaitState.STRONG_UPTREND_ACCELERATION: bool(features.get("strong_uptrend_acceleration")),
        WaitState.POST_CORRECTION_RECOVERY: bool(features.get("post_correction_recovery")),
        WaitState.LOW_IV: (_known_num(features, "iv_rank") is not None and
                           _known_num(features, "iv_rank") < 0.25),
        WaitState.EARNINGS_RISK: bool(features.get("earnings_risk")),
        WaitState.UNPREDICTABLE: bool(features.get("unpredictable")),
    }
    return [{"wait_reason": state.value, "qualified": value,
             "underlying_state": features.get("underlying_state", "UNKNOWN"),
             "relevant_features": dict(features)}
            for state, value in checks.items() if value]


def build_pit_timing_rows(daily: Any, market: Any) -> list[dict[str, Any]]:
    """Join canonical daily features and market context for A2 execution."""
    import pandas as pd
    from .covered_call import build_sell_timing_features
    stock = build_sell_timing_features(daily)
    left = stock.copy(); right = market.copy()
    left["date"] = pd.to_datetime(left["date"]).dt.normalize()
    right["date"] = pd.to_datetime(right["date"]).dt.normalize()
    joined = left.merge(right, on="date", how="left", suffixes=("", "_market"))
    return joined.where(pd.notna(joined), None).to_dict("records")


def select_frozen_neighborhood_contract(contracts: list[Mapping[str, Any]], *, spot: float,
                                       neighborhood: FrozenContractNeighborhood,
                                       target_otm: float | None = None,
                                       target_atr: float | None = None,
                                       target_delta: float | None = None,
                                       target_dte: float | None = None,
                                       dte_tolerance: float | None = None,
                                       enforce_delta: bool = True) -> dict[str, Any] | None:
    """Select one exact contract without leaving a frozen Stage B neighborhood."""
    candidates = []
    for raw in contracts:
        try:
            dte = float(raw["dte"]); delta = abs(float(raw["delta"])); strike = float(raw["strike"])
            bid = float(raw["bid"]); ask = float(raw["ask"])
            otm = (strike / float(spot)) - 1.0
            atr_distance = float(raw["actual_atr_distance"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (neighborhood.dte_min <= dte <= neighborhood.dte_max): continue
        if target_dte is not None and dte_tolerance is not None and abs(dte - target_dte) > dte_tolerance: continue
        if enforce_delta and min(abs(delta - x) for x in neighborhood.deltas) > 0.025: continue
        if not (0.0 < delta < 1.0): continue
        if min(abs(otm - x) for x in neighborhood.otm_values) > 0.0125: continue
        if min(abs(atr_distance - x) for x in neighborhood.atr_values) > 0.5: continue
        if bid <= 0 or ask < bid: continue
        if bid >= float(spot): continue
        if otm >= 0.075 and delta >= 0.95: continue
        if raw.get("price_basis", "MARKET_RAW") != "MARKET_RAW": continue
        distance = ((abs(delta - target_delta) if target_delta is not None else 0.0) +
                    (abs(otm - target_otm) if target_otm is not None else 0.0) +
                    (abs(atr_distance - target_atr) if target_atr is not None else 0.0) +
                    (abs(dte - target_dte) if target_dte is not None else 0.0))
        candidates.append((distance, raw, dte, delta, otm, atr_distance, bid, ask))
    if not candidates:
        return None
    _, raw, dte, delta, otm, atr_distance, bid, ask = min(candidates, key=lambda x: x[0])
    return {"selection_mode": "FROZEN_STAGE_B_NEIGHBORHOOD", "target_value": target_delta or target_otm or target_atr,
            "actual_value": delta, "spot": float(spot), "strike": float(raw["strike"]),
            "dte": int(dte), "delta": delta, "actual_otm": otm,
            "actual_atr_distance": atr_distance, "bid": bid, "ask": ask,
            "premium": bid * 100.0, "price_basis": "MARKET_RAW",
            "contract_identity": raw.get("contract_identity", {k: raw.get(k) for k in ("symbol", "quote_date", "expiration", "strike")})}


def run_covered_call_timing_from_pit_calendar(
    symbol: str, daily: Any, market: Any, *,
    research_window: Mapping[str, Any],
    neighborhood: FrozenContractNeighborhood,
    timing_families: list[str | TimingFamily],
    wait_states: list[str | WaitState],
    lifecycle_policy: Mapping[str, Any],
    chain_loader: Any,
    run_lifecycle: Any,
) -> dict[str, Any]:
    """Build A2 signals from a PIT calendar and exact per-day chains.

    ``chain_loader`` receives ``(symbol, date)`` and must read only the
    canonical chain available on that date. It returns normalized mappings
    consumed by :func:`select_frozen_neighborhood_contract`.
    """
    rows = build_pit_timing_rows(daily, market)

    def selector(row: Mapping[str, Any], frozen: FrozenContractNeighborhood) -> dict[str, Any] | None:
        spot = row.get("close")
        atr = row.get("atr")
        if spot is None or atr is None:
            return None
        contracts = chain_loader(str(symbol).upper(), row.get("date")) or []
        normalized = []
        for contract in contracts:
            if hasattr(contract, "strike"):
                normalized.append({
                    "symbol": getattr(contract, "symbol", symbol),
                    "quote_date": getattr(contract, "quote_date", row.get("date")),
                    "expiration": getattr(contract, "expiration", None),
                    "strike": contract.strike, "bid": contract.bid, "ask": contract.ask,
                    "delta": contract.delta, "dte": contract.dte,
                    "actual_atr_distance": (float(contract.strike) - float(spot)) / float(atr),
                    "contract_identity": {
                        "symbol": getattr(contract, "symbol", symbol),
                        "quote_date": getattr(contract, "quote_date", row.get("date")),
                        "expiration": getattr(contract, "expiration", None),
                        "strike": contract.strike,
                    },
                })
            else:
                item = dict(contract)
                if "actual_atr_distance" not in item:
                    try:
                        item["actual_atr_distance"] = (float(item["strike"]) - float(spot)) / float(atr)
                    except (KeyError, TypeError, ValueError, ZeroDivisionError):
                        continue
                normalized.append(item)
        return select_frozen_neighborhood_contract(normalized, spot=float(spot), neighborhood=frozen)

    result = run_covered_call_timing_research(
        symbol, research_window, neighborhood, timing_families, wait_states,
        lifecycle_policy, daily_rows=rows, select_contract=selector,
        run_lifecycle=run_lifecycle)
    result["chain_access"] = "CANONICAL_PIT_PER_DAY"
    result["reason_codes"].append("EXACT_CHAIN_LOADER_INJECTED")
    return result


def run_covered_call_timing_research(
    symbol: str,
    research_window: Mapping[str, Any],
    frozen_contract_neighborhood: FrozenContractNeighborhood,
    timing_families: list[str | TimingFamily],
    wait_states: list[str | WaitState],
    lifecycle_policy: Mapping[str, Any],
    *,
    daily_rows: list[Mapping[str, Any]],
    select_contract: Any,
    run_lifecycle: Any,
) -> dict[str, Any]:
    """Execute timing research around caller-supplied frozen primitives.

    ``select_contract`` receives ``(row, neighborhood)`` and must return an
    auditable contract mapping or ``None``. ``run_lifecycle`` receives the
    selected entry rows and frozen policy and returns lifecycle rows. Keeping
    those two deterministic engines injected makes this runner reusable and
    prevents it from inventing contract or lifecycle rules.
    """
    if not daily_rows or not timing_families or not wait_states:
        raise ValueError("A2_INPUT_CONTRACT_INCOMPLETE")
    families = [TimingFamily(str(x).upper()) for x in timing_families]
    waits = [WaitState(str(x).upper()) for x in wait_states]
    by_family: dict[str, dict[str, Any]] = {}
    for family in families:
        signals: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        qualified_days = 0
        for raw in daily_rows:
            evidence = evaluate_timing_family(family, raw)
            overlays = [x for x in evaluate_wait_states(raw) if x["wait_reason"] in {w.value for w in waits}]
            if overlays:
                rejected.append({"date": raw.get("date"), "timing_family": family.value,
                                 "wait_reason": overlays[0]["wait_reason"],
                                 "underlying_state": raw.get("underlying_state", "UNKNOWN"),
                                 "relevant_features": evidence.feature_values})
                continue
            if evidence.qualified:
                qualified_days += 1
                contract = select_contract(raw, frozen_contract_neighborhood)
                if contract is not None:
                    signals.append({"date": raw.get("date"), "timing_family": family.value,
                                    "timing_evidence": evidence.to_dict(), "contract": dict(contract)})
        # A zero-signal family is a true zero-trade result.  Besides being
        # cheaper, skipping the injected lifecycle engine keeps the research
        # funnel auditable: no contract or lifecycle work occurred.
        lifecycle = list(run_lifecycle(signals, lifecycle_policy) or []) if signals else []
        metrics = summarize_timing_lifecycles(lifecycle)
        by_family[family.value] = {"qualifying_days": qualified_days,
                                   "contract_available_days": len(signals),
                                   "opened_calls": len(lifecycle), "signals": signals,
                                   "wait_rows": rejected, "lifecycle": lifecycle,
                                   "metrics": metrics}
    return {"module": "pcs.research.covered_call_timing", "version": "1.0",
            "symbol": str(symbol).upper(), "status": "COMPLETED_RESEARCH_ONLY",
            "research_only": True, "data_source": "PCS_CANONICAL_DATA",
            "research_window": dict(research_window),
            "frozen_stage_b_spec": frozen_contract_neighborhood.to_dict(),
            "lifecycle_policy": dict(lifecycle_policy), "timing_families": by_family,
            "final_oos_read": False, "production_changes_allowed": False,
            "reason_codes": ["PIT_SAFE_FEATURES", "FROZEN_CONTRACT_NEIGHBORHOOD",
                             "SERIAL_ONE_CALL_CAPACITY_DELEGATED", "RESEARCH_ONLY"]}


def run_covered_call_timing_neighborhood(
    symbol: str, research_window: Mapping[str, Any],
    frozen_contract_neighborhood: FrozenContractNeighborhood,
    timing_families: list[str | TimingFamily], wait_states: list[str | WaitState],
    lifecycle_policy: Mapping[str, Any], *, daily_rows: list[Mapping[str, Any]],
    select_contract: Any, run_lifecycle: Any, checkpoint_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the same A2 engine independently for every frozen OTM/ATR cell."""
    combined: dict[str, dict[str, Any]] = {}
    cell_results = []
    for otm in frozen_contract_neighborhood.otm_values:
        for atr in frozen_contract_neighborhood.atr_values:
            cell_id = f"OTM_{otm:g}_ATR_{atr:g}"
            cell_neighborhood = FrozenContractNeighborhood(
                frozen_contract_neighborhood.dte_min, frozen_contract_neighborhood.dte_max,
                frozen_contract_neighborhood.deltas, (otm,), (atr,))
            result = run_covered_call_timing_research(
                symbol, research_window, cell_neighborhood, timing_families, wait_states,
                {**lifecycle_policy, "contract_cell": cell_id}, daily_rows=daily_rows,
                select_contract=select_contract, run_lifecycle=run_lifecycle)
            cell_results.append({"cell_id": cell_id, "otm": otm, "atr": atr})
            for family, payload in result["timing_families"].items():
                target = combined.setdefault(family, {"qualifying_days": 0,
                    "contract_available_days": 0, "opened_calls": 0, "signals": [],
                    "wait_rows": [], "lifecycle": [], "neighborhood_cells": []})
                target["qualifying_days"] += payload["qualifying_days"]
                target["contract_available_days"] += payload.get("contract_available_days", 0)
                target["signals"].extend({**x, "contract_cell": cell_id} for x in payload["signals"])
                target["wait_rows"].extend(payload["wait_rows"])
                target["lifecycle"].extend({**x, "contract_cell": cell_id} for x in payload["lifecycle"])
                target["neighborhood_cells"].append({"cell_id": cell_id,
                    "otm": otm, "atr": atr, "opened_calls": payload["opened_calls"]})
                target["opened_calls"] = len(target["lifecycle"])
                target["metrics"] = summarize_timing_lifecycles(target["lifecycle"])
            if checkpoint_dir is not None:
                checkpoint = Path(checkpoint_dir)
                checkpoint.mkdir(parents=True, exist_ok=True)
                (checkpoint / f"{cell_id}.json").write_text(
                    json.dumps({"cell_id": cell_id, "otm": otm, "atr": atr,
                                "result": result}, indent=2, default=str), encoding="utf-8")
    return {"module": "pcs.research.covered_call_timing", "version": "1.0",
            "symbol": str(symbol).upper(), "status": "COMPLETED_RESEARCH_ONLY",
            "research_only": True, "data_source": "PCS_CANONICAL_DATA",
            "research_window": dict(research_window),
            "frozen_stage_b_spec": frozen_contract_neighborhood.to_dict(),
            "lifecycle_policy": dict(lifecycle_policy), "timing_families": combined,
            "neighborhood_cells": cell_results, "final_oos_read": False,
            "production_changes_allowed": False,
            "reason_codes": ["PIT_SAFE_FEATURES", "INDEPENDENT_FROZEN_CELLS",
                             "SERIAL_ONE_CALL_CAPACITY_DELEGATED", "RESEARCH_ONLY"]}


def summarize_timing_lifecycles(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize lifecycle rows without mixing stock P&L into call P&L."""
    pnl = [float(r.get("net_call_only_pnl", r.get("option_only_pnl", r.get("pnl", 0))) or 0) for r in rows]
    positive = [x for x in pnl if x > 0]
    negative = [x for x in pnl if x < 0]
    years: dict[str, float] = defaultdict(float)
    for row, value in zip(rows, pnl):
        day = str(row.get("entry_date", row.get("date", "")))[:4]
        if day: years[day] += value
    curve = 0.0; peak = 0.0; drawdown = 0.0
    for value in pnl:
        curve += value; peak = max(peak, curve); drawdown = max(drawdown, peak - curve)
    n = len(rows)
    breaches = sum(bool(r.get("strike_breach", r.get("short_strike_breach", False))) for r in rows)
    forced = sum(bool(r.get("forced_btc", False)) for r in rows)
    itm = sum(bool(r.get("expiration_itm", r.get("assigned", False))) for r in rows)
    holding = [float(r[x]) for r in rows for x in ("holding_days", "average_holding_days") if r.get(x) is not None]
    total_pnl = sum(pnl)
    top_year = max(years.values()) if years else None
    top_contribution = (top_year / total_pnl * 100.0) if total_pnl and top_year is not None else None
    return {"opened_calls": n, "gross_premium": sum(float(r.get("gross_premium", r.get("call_premium", 0)) or 0) for r in rows),
            "BTC_cost": sum(float(r.get("BTC_cost", r.get("btc_cost", 0)) or 0) for r in rows),
            "net_call_only_pnl": sum(pnl), "pnl_per_call": sum(pnl) / n if n else None,
            "profit_factor": sum(positive) / abs(sum(negative)) if negative else None,
            "win_rate": len(positive) / n if n else None, "positive_years": sum(v > 0 for v in years.values()),
            "negative_years": sum(v < 0 for v in years.values()),
            "positive_year_rate": (sum(v > 0 for v in years.values()) / len(years)) if years else None,
            "top_year_pnl_contribution_pct": top_contribution,
            "year_concentration_risk": bool(top_contribution is not None and top_contribution > 50.0),
            "worst_year": min(years.items(), key=lambda x: x[1]) if years else None,
            "best_year": max(years.items(), key=lambda x: x[1]) if years else None,
            "max_drawdown": drawdown, "max_single_loss": min(pnl) if pnl else None,
            "strike_breach_count": breaches, "strike_breach_rate": breaches / n if n else None,
            "forced_BTC_count": forced, "forced_BTC_rate": forced / n if n else None,
            "expiration_ITM_count": itm, "expiration_ITM_rate": itm / n if n else None,
            "average_holding_days": sum(holding) / len(holding) if holding else None,
            "yearly_net_call_only_pnl": dict(years)}


def compare_to_always_sell(families: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return explicit deltas against the mandatory ALWAYS_SELL control."""
    base = families.get(TimingFamily.ALWAYS_SELL.value, {}).get("metrics", {})
    out: dict[str, dict[str, Any]] = {}
    for name, value in families.items():
        m = value.get("metrics", {})
        def delta(key: str) -> Any:
            if m.get(key) is None or base.get(key) is None: return None
            return m[key] - base[key]
        out[name] = {"incremental_pnl_vs_always": delta("net_call_only_pnl"),
                     "pnl_per_call_change": delta("pnl_per_call"),
                     "profit_factor_change": delta("profit_factor"),
                     "trade_reduction_pct": ((base.get("opened_calls", 0) - m.get("opened_calls", 0)) /
                                              base["opened_calls"] if base.get("opened_calls") else None),
                     "max_drawdown_change": delta("max_drawdown"),
                     "strike_breach_change": delta("strike_breach_rate"),
                     "forced_BTC_change": delta("forced_BTC_count"),
                     "expiration_ITM_change": delta("expiration_ITM_count")}
    return out


def build_neighborhood_matrix(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten family/cell lifecycle results for adjacent-cell robustness checks."""
    rows: list[dict[str, Any]] = []
    for family, payload in result.get("timing_families", {}).items():
        for lifecycle in payload.get("lifecycle", []):
            contract = lifecycle.get("contract", {}) if isinstance(lifecycle, Mapping) else {}
            rows.append({"symbol": result.get("symbol"), "timing_family": family,
                         "contract_cell": lifecycle.get("contract_cell"),
                         "dte": contract.get("dte"), "delta": contract.get("delta"),
                         "actual_otm": contract.get("actual_otm"),
                         "actual_atr_distance": contract.get("actual_atr_distance"),
                         "net_call_only_pnl": lifecycle.get("net_call_only_pnl", lifecycle.get("pnl")),
                         "entry_date": lifecycle.get("entry_date", lifecycle.get("date"))})
    return rows


def persist_a2_artifacts(result: Mapping[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Persist the required A2 artifact set in an isolated research directory."""
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    metadata = {"research_only": True, "price_basis": "MARKET_RAW",
                "data_source": result.get("data_source", "PCS_CANONICAL_DATA"),
                "research_window": result.get("research_window", {}),
                "frozen_stage_b_spec": result.get("frozen_stage_b_spec", {}),
                "runner_version": result.get("version", "1.0")}
    paths: dict[str, str] = {}
    spec = {**metadata, "module": result.get("module"), "symbol": result.get("symbol"),
            "final_oos_read": False, "production_changes_allowed": False}
    paths["covered_call_a2_spec.json"] = str(out / "covered_call_a2_spec.json")
    (out / "covered_call_a2_spec.json").write_text(json.dumps(spec, indent=2, default=str), encoding="utf-8")
    family_rows = []
    wait_rows = []
    yearly_rows = []
    comparisons = compare_to_always_sell(result.get("timing_families", {}))
    for family, payload in result.get("timing_families", {}).items():
        family_rows.append({"timing_family": family, **payload.get("metrics", {}),
                            **comparisons.get(family, {}),
                            "robustness_class": classify_timing_robustness(payload)})
        wait_rows.extend({"timing_family": family, **row} for row in payload.get("wait_rows", []))
        for year, pnl in payload.get("metrics", {}).get("yearly_net_call_only_pnl", {}).items():
            yearly_rows.append({"timing_family": family, "year": year, "net_call_only_pnl": pnl})
    matrix = build_neighborhood_matrix(result)
    files = {"covered_call_a2_timing_summary.csv": family_rows,
             "covered_call_a2_neighborhood_matrix.csv": matrix,
             "covered_call_a2_yearly.csv": yearly_rows,
             "covered_call_a2_wait_analysis.csv": wait_rows,
             "covered_call_a2_representative_lifecycles.csv": matrix}
    for name, rows in files.items():
        path = out / name
        keys = sorted({key for row in rows for key in row})
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=keys or ["timing_family"])
            writer.writeheader(); writer.writerows(rows)
        paths[name] = str(path)
    report = ["# Covered Call A2 Timing Research", "", "Research-only: true", "Price basis: MARKET_RAW", "",
              f"Symbol: {result.get('symbol')}", "", "## Families", ""]
    report.extend(f"- {row.get('timing_family')}: {row.get('net_call_only_pnl')} net call-only P&L" for row in family_rows)
    paths["covered_call_a2_report.md"] = str(out / "covered_call_a2_report.md")
    (out / "covered_call_a2_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return paths


def classify_timing_robustness(payload: Mapping[str, Any]) -> str:
    """Apply conservative, transparent robustness gates to one family.

    This is deliberately not a performance optimizer: a family must have
    multiple contract cells and multiple positive years before it can be
    called robust. Missing cell/year evidence remains conditional.
    """
    lifecycle = list(payload.get("lifecycle", []))
    metrics = payload.get("metrics", {})
    explicit_cells = payload.get("neighborhood_cells")
    if explicit_cells is None:
        return "NO_EDGE"
    cells = {str(r.get("cell_id", r.get("contract_cell", "")))
             for r in explicit_cells if isinstance(r, Mapping)}
    years = metrics.get("yearly_net_call_only_pnl", {})
    if not lifecycle:
        return "NO_EDGE"
    if len(cells - {""}) < 2 or len(years) < 2:
        return "CONDITIONAL"
    if sum(float(x) > 0 for x in years.values()) < 2:
        return "NO_EDGE"
    if metrics.get("positive_years", 0) / max(len(years), 1) < .5:
        return "OVERFIT"
    return "ROBUST"
