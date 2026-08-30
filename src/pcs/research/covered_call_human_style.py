"""Human-style opportunistic covered-call TRAIN research.

This is a research-only NEW_ENTRY engine.  It starts from the complete ticker
calendar, keeps WAIT as the default action, selects exact canonical contracts,
and applies an episode-level defense budget.  It never reads FINAL OOS or
writes production configuration.
"""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping
import hashlib
import json

import pandas as pd

from pcs.data.access import PCSDataAccess


CALCULATION_VERSION = "human-style-opportunistic-covered-call-v3"


@dataclass(frozen=True)
class HumanStyleConfig:
    config_id: str
    family: str
    dte_min: int
    dte_max: int
    delta_min: float
    delta_max: float
    profit_take: float
    minimum_moneyness: float
    minimum_premium_yield: float
    cooldown_days: int
    max_rolls: int
    defense_budget_ratio: float
    minimum_roll_strike_increase: float = 0.05
    iv_rank_min: float = 0.65
    rally_5d_min: float = 0.06
    resistance_tolerance: float = 0.03

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HumanStyleConfig":
        values = {}
        for name, field in cls.__dataclass_fields__.items():
            if name in value:
                values[name] = value[name]
            elif field.default is not MISSING:
                values[name] = field.default
        result = cls(**values)
        result.validate()
        return result

    def validate(self) -> None:
        if not (14 <= self.dte_min <= self.dte_max <= 45):
            raise ValueError("DTE_OUTSIDE_HUMAN_STYLE_V3_RANGE")
        if not (0.08 <= self.delta_min <= self.delta_max <= 0.20):
            raise ValueError("DELTA_OUTSIDE_HUMAN_STYLE_V3_RANGE")
        if not (0 <= self.max_rolls <= 10):
            raise ValueError("ROLL_COUNT_OUTSIDE_HUMAN_STYLE_V3_RANGE")


def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
                                     separators=(",", ":")).encode()).hexdigest()


def economic_hash(result: Mapping[str, Any]) -> str:
    """Hash realized economics, excluding configuration labels and actions."""
    m = result.get("metrics", {})
    episodes = [{k: e.get(k) for k in (
        "opened_date", "closed_date", "expiration", "strike", "entry_spot",
        "premium_received", "buyback_cost", "roll_debit", "net_overlay",
        "close_reason")} for e in result.get("episodes", [])]
    payload = {"episodes": episodes, "metrics": {k: m.get(k) for k in (
        "premium_received", "buyback_roll_cost", "roll_debit", "net_option_overlay",
        "combined_wealth", "capped_upside", "max_drawdown", "management_count",
        "assignment_exposure_days", "assignment_exposure_episodes")}}
    return _json_hash(payload)


def deduplicate_economic_candidates(results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set(); out: list[dict[str, Any]] = []
    for result in results:
        item = dict(result)
        item["economic_hash"] = economic_hash(item)
        if item["economic_hash"] not in seen:
            seen.add(item["economic_hash"]); out.append(item)
    return out


def audit_mechanical_configurations(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Audit whether the old matrix explicitly tested each requested behavior."""
    rows = list(rows)
    ids = lambda predicate: sorted(str(r["config_id"]) for r in rows if predicate(r))
    resistance = ids(lambda r: r.get("entry_timing") == "RESISTANCE_NEAR")
    low_delta = ids(lambda r: float(r.get("target_delta", 9)) <= .15)
    short_dte = ids(lambda r: int(r.get("dte_min", 999)) <= 30 and int(r.get("dte_max", 999)) <= 45)
    fast_profit = ids(lambda r: float(r.get("profit_target", 9)) in {.50, .65})
    checks = [
        (1, "BLOCK_STRONG_RALLY_OR_BREAKOUT", False, []),
        (2, "SELL_ONLY_AFTER_SHORT_TERM_SURGE", False, []),
        (3, "SELL_ONLY_WHEN_IV_RELATIVELY_HIGH", False, []),
        (4, "SELL_NEAR_PIT_RESISTANCE_OR_PRIOR_HIGH", bool(resistance), resistance),
        (5, "DO_NOT_SELL_BEFORE_EARNINGS", False, []),
        (6, "LOW_DELTA_FAR_OTM", bool(low_delta), low_delta),
        (7, "SHORT_TO_MEDIUM_DTE", bool(short_dte), short_dte),
        (8, "FAST_PROFIT_TAKE_50_OR_65", bool(fast_profit), fast_profit),
        (9, "COOLDOWN_AFTER_CLOSE", False, []),
        (10, "DO_NOT_MAINTAIN_CONTINUOUS_COVERAGE", False, []),
        (11, "ROLL_COUNT_CAP", False, []),
        (12, "ROLL_DEBIT_CAP", False, []),
        (13, "ROLL_MUST_MATERIALLY_RAISE_STRIKE", False, []),
        (14, "BUY_BACK_AND_WAIT_WHEN_NO_GOOD_ROLL", False, []),
        (15, "WAIT_WHEN_PREMIUM_INSUFFICIENT", False, []),
        (16, "PAUSE_AFTER_STRONG_TREND_RESUMES", False, []),
    ]
    behavior_rows = [{"number": n, "behavior": name, "tested": "YES" if yes else "NO",
                      "config_ids": config_ids} for n, name, yes, config_ids in checks]
    complete = all(x[2] for x in checks)
    return {
        "module": "pcs.research.covered_call_human_style.mechanical_audit",
        "version": "3.0", "audited_config_count": len(rows),
        "prior_classification": "COVERED_CALL_TRAIN_CANDIDATE_MATRIX",
        "reclassification": "MECHANICAL_COVERED_CALL_ENGINE_BASELINE",
        "behaviors": behavior_rows, "all_behaviors_fully_tested": complete,
        "prior_candidate_status": "REMAINS_CURRENT" if complete else "SUPERSEDED_BEFORE_HOLDOUT",
        "holdout_opened": False, "validation_opened": False, "final_oos_read": False,
        "reason_codes": ["MECHANICAL_MATRIX_AUDITED", "HOLDOUT_NOT_OPENED",
                         "VALIDATION_NOT_RUN"] + ([] if complete else ["HUMAN_STYLE_BEHAVIORS_INCOMPLETE"]),
    }


def _load_earnings(path: str | Path, symbol: str, *, data_access: PCSDataAccess | None = None,
                   start=None, end=None) -> tuple[set[pd.Timestamp], str]:
    if symbol.upper() in {"QQQ", "SPY", "IWM", "DIA"}:
        return set(), "NOT_APPLICABLE_ETF"
    if str(path) == "PCSDataAccess:events":
        if data_access is None:
            raise ValueError("EVENT_DATA_ACCESS_REQUIRED")
        frame = data_access.read("events", symbol)
        event_dates = pd.to_datetime(frame["event_date"])
        if start is not None:
            frame = frame[event_dates >= pd.Timestamp(start)]
            event_dates = pd.to_datetime(frame["event_date"])
        if end is not None:
            frame = frame[event_dates <= pd.Timestamp(end)]
    else:
        frame = pd.read_csv(path)
    symbol_col = frame.get("symbol", pd.Series(index=frame.index, dtype="object"))
    event_col = frame.get("event_type", pd.Series(index=frame.index, dtype="object"))
    selected = frame[symbol_col.astype(str).str.upper().eq(symbol.upper()) &
                     event_col.astype(str).str.upper().eq("EARNINGS")]
    dates = set(pd.to_datetime(selected.event_date).dt.normalize()) if not selected.empty else set()
    pit_field = next((c for c in ("event_asof", "known_at", "event_date_known_at_entry")
                      if c in selected.columns), None)
    evidence = "PIT_TIMESTAMP_PRESENT" if pit_field and selected[pit_field].notna().all() else "PIT_TIMESTAMP_MISSING"
    return dates, evidence


def _features(daily: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    x = daily.copy().sort_values("date")
    x["date"] = pd.to_datetime(x.date).dt.normalize()
    prior_close = x.close.shift(1)
    tr = pd.concat([(x.high - x.low), (x.high - prior_close).abs(),
                    (x.low - prior_close).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.shift(1).rolling(14, min_periods=14).mean()
    x["sma20"] = x.close.shift(1).rolling(20, min_periods=20).mean()
    x["sma50"] = x.close.shift(1).rolling(50, min_periods=50).mean()
    x["prior_high60"] = x.high.shift(1).rolling(60, min_periods=40).max()
    x["return_3d"] = x.close / x.close.shift(3) - 1.0
    x["return_5d"] = x.close / x.close.shift(5) - 1.0
    x["return_10d"] = x.close / x.close.shift(10) - 1.0
    x["prior_return_5d"] = x.close.shift(1) / x.close.shift(6) - 1.0
    x["close_location"] = (x.close - x.low) / (x.high - x.low).replace(0, pd.NA)
    calls = quotes[quotes.call_put.astype(str).str.lower().isin({"c", "call"})].copy()
    calls["trade_date"] = pd.to_datetime(calls.trade_date).dt.normalize()
    iv_cols = [c for c in ("bid_iv", "ask_iv") if c in calls.columns]
    if iv_cols:
        calls["snapshot_iv"] = calls[iv_cols].mean(axis=1)
        iv = calls.groupby("trade_date", sort=True).snapshot_iv.median().rename("median_call_iv")
        x = x.merge(iv, left_on="date", right_index=True, how="left")
    else:
        x["median_call_iv"] = pd.NA
    x["iv_change_5d"] = x.median_call_iv - x.median_call_iv.shift(5)
    x["iv_rank_126"] = x.median_call_iv.rolling(126, min_periods=40).apply(
        lambda values: float(pd.Series(values).rank(pct=True).iloc[-1]), raw=False)
    x["breakout"] = (x.close > x.prior_high60 * 1.01) & (x.return_5d > .06)
    x["trend_acceleration"] = (x.return_3d > .06) | ((x.return_5d > .10) & (x.close > x.sma20))
    # The surge must already be observable before the sale date.  A quiet or
    # mildly positive session after that surge is eligible; the acceleration
    # gate still blocks selling into an active breakout.
    x["rally"] = (x.prior_return_5d >= .06) & ((x.close / x.close.shift(1) - 1.0) <= .02)
    x["resistance_near"] = (x.close >= x.prior_high60 * .97) & (x.close <= x.prior_high60 * 1.005)
    x["feature_ready"] = x[["atr14", "sma20", "sma50", "prior_high60", "return_5d",
                              "median_call_iv", "iv_rank_126"]].notna().all(axis=1)
    x["volume_ratio20"] = x.volume / x.volume.shift(1).rolling(20, min_periods=20).median()
    x["sma20_slope10"] = x.sma20 / x.sma20.shift(10) - 1
    x["sma50_slope20"] = x.sma50 / x.sma50.shift(20) - 1
    explosive = (x.close > x.prior_high60) & (x.return_5d > .06) & (x.volume_ratio20 >= 1.5) & (x.sma20_slope10 > 0) & (x.sma50_slope20 > 0)
    recovery = (x.close > x.sma20) & (x.close > x.sma50) & (x.return_5d > 0) & (x.return_5d.shift(1) <= 0)
    stall = (x.prior_return_5d >= .06) & (x.return_3d <= .04) & (x.close <= x.prior_high60 * 1.005) & (x.iv_rank_126 >= .65)
    edge = (x.close >= x.prior_high60 * .97) & (x.close <= x.prior_high60 * 1.005) & (x.volume_ratio20 < 1.5) & (x.return_5d < .10)
    continuation = (x.close > x.sma20) & (x.close > x.sma50) & (x.sma20_slope10 > 0) & (x.sma50_slope20 >= 0)
    x["state"] = "DOWNTREND_OR_PANIC"
    x.loc[continuation, "state"] = "TREND_CONTINUATION"
    x.loc[recovery, "state"] = "EARLY_RECOVERY"
    x.loc[edge, "state"] = "RANGE_UPPER_EDGE"
    x.loc[stall, "state"] = "POST_RALLY_STALL"
    x.loc[explosive, "state"] = "EXPLOSIVE_BREAKOUT"
    return x


def _valid_calls(quotes: pd.DataFrame) -> pd.DataFrame:
    x = quotes[quotes.call_put.astype(str).str.lower().isin({"c", "call"})].copy()
    x["trade_date"] = pd.to_datetime(x.trade_date).dt.normalize()
    x["expiration_date"] = pd.to_datetime(x.expiration_date).dt.normalize()
    x["dte"] = (x.expiration_date - x.trade_date).dt.days
    x["delta_abs"] = pd.to_numeric(x.delta, errors="coerce").abs()
    mid = (x.bid + x.ask) / 2.0
    x["spread_pct"] = (x.ask - x.bid) / mid.where(mid > 0)
    return x[(x.bid > 0) & (x.ask >= x.bid) & x.open_interest.notna() & x.volume.notna()]


def _signal(row: Mapping[str, Any], cfg: HumanStyleConfig, earnings: set[pd.Timestamp],
            event_window_days: int) -> tuple[bool, list[str]]:
    reasons = []
    day = pd.Timestamp(row["date"]).normalize()
    if not bool(row.get("feature_ready")):
        return False, ["FEATURE_NOT_READY"]
    if bool(row.get("breakout")): reasons.append("STRONG_BREAKOUT")
    if bool(row.get("trend_acceleration")): reasons.append("TREND_ACCELERATION")
    if any(0 <= (event - day).days <= event_window_days for event in earnings):
        reasons.append("EARNINGS_RISK_WINDOW")
    high_iv = float(row.get("iv_rank_126", -1)) >= cfg.iv_rank_min
    iv_rising = float(row.get("iv_change_5d", -1)) > 0
    rally = float(row.get("prior_return_5d", row.get("return_5d", -1))) >= cfg.rally_5d_min and bool(row.get("rally"))
    resistance = bool(row.get("resistance_near"))
    family = cfg.family.upper()
    state = str(row.get("state", "DOWNTREND_OR_PANIC"))
    if state in {"EXPLOSIVE_BREAKOUT", "EARLY_RECOVERY", "DOWNTREND_OR_PANIC"}:
        reasons.append(f"STATE_WAIT_{state}")
    momentum_stall = resistance and float(row.get("return_3d", 0)) <= .04
    rich_premium = high_iv or iv_rising
    family_ok = {
        "BALANCED": (rally and high_iv) or (rally and resistance) or (high_iv and resistance),
        "ACTIVE": ((rally and high_iv) or (rally and resistance) or (high_iv and resistance)) and state != "EXPLOSIVE_BREAKOUT",
        "RALLY_IV": rally and high_iv,
        "RESISTANCE_STALL": momentum_stall,
        "RICH_PREMIUM": rich_premium,
    }.get(family, False)
    if not family_ok:
        reasons.append(f"NO_{family}_SIGNAL")
    return not reasons, reasons


def _select_entry(day_quotes: pd.DataFrame, row: Mapping[str, Any], cfg: HumanStyleConfig,
                  train_end: pd.Timestamp) -> pd.Series | None:
    spot = float(row["close"])
    x = day_quotes[day_quotes.dte.between(cfg.dte_min, cfg.dte_max) &
                   day_quotes.delta_abs.between(cfg.delta_min, cfg.delta_max) &
                   (day_quotes.open_interest >= 100) & (day_quotes.volume >= 1) &
                   (day_quotes.spread_pct <= .20) &
                   (day_quotes.expiration_date <= train_end)].copy()
    min_strike = spot * (1.0 + cfg.minimum_moneyness)
    if cfg.family.upper() == "RESISTANCE_SELLER":
        min_strike = max(min_strike, float(row["prior_high60"]) * 1.01)
    x = x[(x.strike >= min_strike) & ((x.bid / spot) >= cfg.minimum_premium_yield)]
    if x.empty: return None
    target_delta = (cfg.delta_min + cfg.delta_max) / 2.0
    target_dte = (cfg.dte_min + cfg.dte_max) / 2.0
    x["delta_gap"] = (x.delta_abs - target_delta).abs()
    x["dte_gap"] = (x.dte - target_dte).abs()
    return x.sort_values(["delta_gap", "dte_gap", "spread_pct", "strike"],
                         ascending=[True, True, True, False], kind="mergesort").iloc[0]


def _select_roll(day_quotes: pd.DataFrame, lot: Mapping[str, Any], spot: float,
                 cfg: HumanStyleConfig, train_end: pd.Timestamp) -> pd.Series | None:
    x = day_quotes[(day_quotes.expiration_date > lot["expiration"]) &
                   (day_quotes.dte >= 14) &
                   (day_quotes.strike > float(lot["strike"])) &
                   (day_quotes.open_interest >= 100) & (day_quotes.volume >= 1) &
                   (day_quotes.spread_pct <= .20)].copy()
    if x.empty: return None
    # Stock-retention defense may not be financed by a debit roll.  A roll is
    # eligible only when the new bid covers the old ask plus both-leg costs.
    x["net_credit"] = (x.bid - float(lot["current_ask"])) * 100 - 2 * 0.70
    x = x[x.net_credit >= 0]
    if x.empty:
        return None
    return x.sort_values(["net_credit", "strike", "dte"], ascending=[False, False, True],
                         kind="mergesort").iloc[0]


def _run_config(features: pd.DataFrame, quotes: pd.DataFrame, earnings: set[pd.Timestamp],
                cfg: HumanStyleConfig, *, event_window_days: int,
                commission: float, slippage: float, train_end: pd.Timestamp) -> dict[str, Any]:
    by_day = {d: g for d, g in quotes.groupby("trade_date", sort=False)}
    actions: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    cooldown_until_index = -1
    wait_days = coverage_days = assignment_days = management_count = 0
    blockers: list[str] = []
    cashflows: list[tuple[pd.Timestamp, float]] = []
    trigger_dates: list[str] = []
    eligible_dates: list[str] = []
    rows = list(features.sort_values("date").to_dict("records"))

    def close_lot(day: pd.Timestamp, ask: float, spot: float, reason: str, index: int) -> None:
        nonlocal active, cooldown_until_index, management_count
        assert active is not None
        cost = ask * 100
        fees = commission + slippage
        active["buyback_cost"] += cost
        active["transaction_cost"] += fees
        active["closed_date"] = str(day.date())
        active["holding_days"] = (day - active["opened_ts"]).days
        active["close_reason"] = reason
        active["net_overlay"] = active["premium_received"] - active["buyback_cost"] - active["transaction_cost"]
        active["remaining_premium_budget"] = max(active["maximum_allowed_defense_spend"] - active["defense_spend"], 0.0)
        cashflows.append((day, -cost - fees)); management_count += 1
        actions.append({"date": str(day.date()), "action": "CLOSE", "reason_code": reason,
                        "episode_id": active["episode_id"], "cashflow": -cost - fees})
        cooldown_until_index = index + cfg.cooldown_days
        active = None

    for index, row in enumerate(rows):
        day = pd.Timestamp(row["date"]).normalize(); spot = float(row["close"])
        day_quotes = by_day.get(day, quotes.iloc[0:0])
        active_at_close = active is not None
        if active is not None:
            current = day_quotes[(day_quotes.expiration_date == active["expiration"]) &
                                 (day_quotes.strike == active["strike"])]
            active["max_spot"] = max(active["max_spot"], spot)
            if spot >= active["strike"]:
                assignment_days += 1; active["assignment_exposure_days"] += 1
            if current.empty:
                blockers.append(f"MISSING_EXACT_CONTRACT_QUOTE:{day.date()}:{active['episode_id']}")
                actions.append({"date": str(day.date()), "action": "HOLD", "reason_code": "EXACT_QUOTE_UNAVAILABLE"})
            else:
                quote = current.sort_values("trade_date", kind="mergesort").iloc[0]
                active["current_ask"] = float(quote.ask)
                dte = int((active["expiration"] - day).days)
                profit_ready = float(quote.ask) <= active["current_sale_bid"] * (1.0 - cfg.profit_take)
                breakout_close = bool(row.get("breakout")) or bool(row.get("trend_acceleration"))
                risk_due = dte <= 14 and spot >= active["strike"] * .95
                if profit_ready:
                    close_lot(day, float(quote.ask), spot, "PROFIT_TARGET", index)
                elif breakout_close and spot >= active["strike"] * .95:
                    # A defensive trend resumption gets first access to the
                    # legal-roll path; buy back only when no qualifying roll
                    # is available.
                    can_roll = active["roll_count"] < cfg.max_rolls and cfg.max_rolls > 0
                    new = _select_roll(day_quotes, active, spot, cfg, train_end) if can_roll else None
                    remaining = active["maximum_allowed_defense_spend"] - active["defense_spend"]
                    debit = ((float(quote.ask) - float(new.bid)) * 100 + 2 * (commission + slippage)
                             if new is not None else float("inf"))
                    if new is not None and max(debit, 0.0) <= remaining:
                        buyback = float(quote.ask) * 100; proceeds = float(new.bid) * 100
                        roll_credit = proceeds - buyback - 2 * (commission + slippage)
                        active.setdefault("roll_details", []).append({"date": str(day.date()), "old_expiration": str(active["expiration"].date()), "old_strike": float(active["strike"]), "old_ask": float(quote.ask), "new_expiration": str(pd.Timestamp(new.expiration_date).date()), "new_strike": float(new.strike), "new_bid": float(new.bid), "dte_after_roll": int(new.dte), "net_credit": float(roll_credit)})
                        active["buyback_cost"] += buyback; active["premium_received"] += proceeds; active["transaction_cost"] += 2 * (commission + slippage); active["roll_count"] += 1; active["strike"] = float(new.strike); active["expiration"] = pd.Timestamp(new.expiration_date).normalize(); active["current_sale_bid"] = float(new.bid); active["maximum_allowed_defense_spend"] = active["premium_received"] * cfg.defense_budget_ratio
                        cashflows.append((day, proceeds - buyback - 2 * (commission + slippage)))
                        actions.append({"date": str(day.date()), "action": "ROLL", "episode_id": active["episode_id"], "new_strike": float(new.strike), "cashflow": proceeds - buyback - 2 * (commission + slippage)})
                        management_count += 1
                    else:
                        close_lot(day, float(quote.ask), spot, "STRONG_TREND_RESUMED", index)
                elif day >= active["expiration"] and spot < active["strike"]:
                    active["closed_date"] = str(day.date()); active["holding_days"] = (day - active["opened_ts"]).days
                    active["close_reason"] = "EXPIRE_WORTHLESS"
                    active["net_overlay"] = active["premium_received"] - active["buyback_cost"] - active["transaction_cost"]
                    active["remaining_premium_budget"] = max(active["maximum_allowed_defense_spend"] - active["defense_spend"], 0.0)
                    actions.append({"date": str(day.date()), "action": "CLOSE", "reason_code": "EXPIRE_WORTHLESS",
                                    "episode_id": active["episode_id"], "cashflow": 0.0})
                    management_count += 1; cooldown_until_index = index + cfg.cooldown_days; active = None
                elif risk_due:
                    can_roll = active["roll_count"] < cfg.max_rolls and cfg.max_rolls > 0
                    new = _select_roll(day_quotes, active, spot, cfg, train_end) if can_roll else None
                    remaining = active["maximum_allowed_defense_spend"] - active["defense_spend"]
                    debit = ((float(quote.ask) - float(new.bid)) * 100 + 2 * (commission + slippage)
                             if new is not None else float("inf"))
                    if new is not None and max(debit, 0.0) <= remaining:
                        buyback = float(quote.ask) * 100; proceeds = float(new.bid) * 100
                        roll_credit = proceeds - buyback - 2 * (commission + slippage)
                        active.setdefault("roll_details", []).append({
                            "date": str(day.date()),
                            "old_expiration": str(active["expiration"].date()),
                            "old_strike": float(active["strike"]),
                            "old_ask": float(quote.ask),
                            "new_expiration": str(pd.Timestamp(new.expiration_date).date()),
                            "new_strike": float(new.strike),
                            "new_bid": float(new.bid),
                            "dte_after_roll": int(new.dte),
                            "net_credit": float(roll_credit),
                        })
                        active["buyback_cost"] += buyback; active["premium_received"] += proceeds
                        active["transaction_cost"] += 2 * (commission + slippage)
                        active["roll_debit"] += max(buyback - proceeds, 0.0)
                        active["defense_spend"] += max(debit, 0.0)
                        active["roll_count"] += 1; active["strike"] = float(new.strike)
                        active["expiration"] = pd.Timestamp(new.expiration_date).normalize()
                        active["current_sale_bid"] = float(new.bid)
                        active["maximum_allowed_defense_spend"] = active["premium_received"] * cfg.defense_budget_ratio
                        cashflows.append((day, proceeds - buyback - 2 * (commission + slippage)))
                        actions.append({"date": str(day.date()), "action": "ROLL", "episode_id": active["episode_id"],
                                        "new_strike": float(new.strike), "cashflow": proceeds - buyback - 2 * (commission + slippage)})
                        management_count += 1
                    else:
                        # A forced buyback may exceed the budget; the budget
                        # caps further defense/roll spending, not the amount
                        # needed to close the risk and return to WAIT.
                        active["defense_spend"] += float(quote.ask) * 100 + commission + slippage
                        close_lot(day, float(quote.ask), spot,
                                  "NO_LEGAL_ROLL_BUYBACK_WAIT" if can_roll else "NO_ROLL_CONTROL_BUYBACK_WAIT", index)
                else:
                    actions.append({"date": str(day.date()), "action": "HOLD",
                                    "reason_code": "ACTIVE_CALL", "episode_id": active["episode_id"]})
        if active is not None:
            coverage_days += 1
            continue
        if index <= cooldown_until_index:
            wait_days += 1; actions.append({"date": str(day.date()), "action": "WAIT", "reason_code": "COOLDOWN"}); continue
        signal, reasons = _signal(row, cfg, earnings, event_window_days)
        if signal:
            trigger_dates.append(str(day.date()))
        if not signal:
            wait_days += 1; actions.append({"date": str(day.date()), "action": "WAIT", "reason_codes": reasons}); continue
        selected = _select_entry(day_quotes, row, cfg, train_end)
        if selected is None:
            wait_days += 1; actions.append({"date": str(day.date()), "action": "WAIT", "reason_code": "NO_LIQUID_PREMIUM_QUALIFIED_CONTRACT"}); continue
        eligible_dates.append(str(day.date()))
        premium = float(selected.bid) * 100
        costs = commission + slippage
        episode_id = f"{cfg.config_id}|{day.date()}|{pd.Timestamp(selected.expiration_date).date()}|{float(selected.strike):.8f}|C"
        active = {"episode_id": episode_id, "family": cfg.family, "opened_date": str(day.date()),
                  "opened_ts": day, "expiration": pd.Timestamp(selected.expiration_date).normalize(),
                  "strike": float(selected.strike), "entry_spot": spot, "current_sale_bid": float(selected.bid),
                  "premium_received": premium, "buyback_cost": 0.0, "roll_debit": 0.0,
                  "transaction_cost": costs, "defense_spend": 0.0,
                  "maximum_allowed_defense_spend": premium * cfg.defense_budget_ratio,
                  "remaining_premium_budget": premium * cfg.defense_budget_ratio,
                  "roll_count": 0, "roll_details": [], "assignment_exposure_days": 0, "max_spot": spot,
                  "closed_date": None, "holding_days": None, "close_reason": None, "net_overlay": None}
        episodes.append(active); cashflows.append((day, premium - costs))
        actions.append({"date": str(day.date()), "action": "OPEN", "episode_id": episode_id,
                        "premium_received": premium, "cashflow": premium - costs})
        coverage_days += 1

    if active is not None:
        blockers.append("OPEN_EPISODE_AT_TRAIN_HORIZON")
    completed = [x for x in episodes if x["closed_date"] is not None]
    for episode in completed:
        episode["capped_upside"] = max(episode["max_spot"] - episode["strike"], 0.0) * 100
        episode["covered_notional"] = episode["entry_spot"] * 100
        episode["net_return_on_covered_notional"] = episode["net_overlay"] / episode["covered_notional"] if episode["covered_notional"] else None
        episode["gross_premium_yield"] = episode["premium_received"] / episode["covered_notional"] if episode["covered_notional"] else None
        episode["entry_year"] = pd.Timestamp(episode["opened_date"]).year
        episode["exit_year"] = pd.Timestamp(episode["closed_date"]).year
        episode.pop("opened_ts", None); episode["expiration"] = str(episode["expiration"].date())
    premium = sum(x["premium_received"] for x in completed)
    buyback = sum(x["buyback_cost"] for x in completed)
    costs = sum(x["transaction_cost"] for x in completed)
    overlay = premium - buyback - costs
    daily_cash = pd.Series({day: sum(v for d, v in cashflows if d == day) for day in sorted({d for d, _ in cashflows})}, dtype=float)
    curve = daily_cash.cumsum() if not daily_cash.empty else pd.Series(dtype=float)
    drawdown = float((curve - curve.cummax()).min()) if not curve.empty else 0.0
    yearly = []
    for year in range(run_start.year if 'run_start' in locals() else pd.Timestamp(rows[0]["date"]).year,
                      (run_end.year if 'run_end' in locals() else pd.Timestamp(rows[-1]["date"]).year) + 1):
        group = [x for x in completed if x["entry_year"] == year]
        exits = [x for x in completed if x["exit_year"] == year]
        yearly.append({"year": year, "canonical_sessions": sum(pd.Timestamp(r["date"]).year == year for r in rows),
                       "feature_ready_sessions": sum(bool(r.get("feature_ready")) and pd.Timestamp(r["date"]).year == year for r in rows),
                       "opens": len(group), "completed_trades_by_entry_year": len(group),
                       "completed_trades_by_exit_year": len(exits), "cross_year_trades": sum(x["entry_year"] != x["exit_year"] for x in group),
                       "premium_received": sum(x["premium_received"] for x in group),
                       "normal_profit_buyback": sum(x["buyback_cost"] for x in group if x["close_reason"] == "PROFIT_TARGET"),
                       "defensive_buyback": sum(x["buyback_cost"] for x in group if x["close_reason"] != "PROFIT_TARGET"),
                       "fees_slippage": sum(x["transaction_cost"] for x in group),
                       "buyback_roll_cost": sum(x["buyback_cost"] + x["transaction_cost"] for x in group),
                       "net_overlay": sum(x["net_overlay"] for x in group),
                       "mean_net_return": (sum(x["net_return_on_covered_notional"] for x in group) / len(group) if group else None),
                       "average_holding_days": (sum(x["holding_days"] for x in group) / len(group) if group else None),
                       "accounting_residual": 0.0})
    first_close, last_close = float(features.iloc[0].close), float(features.iloc[-1].close)
    buy_hold_start = first_close * 100; buy_hold_end = last_close * 100
    metrics = {"open_count": len(episodes), "completed_episodes": len(completed),
               "wait_ratio": wait_days / len(rows) if rows else None,
               "coverage_time_ratio": coverage_days / len(rows) if rows else None,
               "premium_received": premium, "buyback_roll_cost": buyback + costs,
               "roll_debit": sum(x["roll_debit"] for x in completed), "net_option_overlay": overlay,
               "combined_wealth": buy_hold_end + overlay, "buy_and_hold_wealth": buy_hold_end,
               "buy_and_hold_pnl": buy_hold_end - buy_hold_start,
               "capped_upside": sum(x["capped_upside"] for x in completed),
               "assignment_exposure_days": assignment_days,
               "assignment_exposure_episodes": sum(x["assignment_exposure_days"] > 0 for x in completed),
               "average_holding_days": (sum(x["holding_days"] for x in completed) / len(completed) if completed else None),
               "yearly_results": yearly, "positive_year_count": sum(x["net_overlay"] > 0 for x in yearly),
               "max_drawdown": drawdown, "management_count": management_count,
               "top_episode_contribution": (max((x["net_overlay"] for x in completed), default=0) / overlay
                                            if overlay > 0 else None)}
    reason_dates: dict[str, set[str]] = {}
    for action in actions:
        for reason in action.get("reason_codes", [action.get("reason_code")]):
            if reason:
                reason_dates.setdefault(str(reason), set()).add(str(action["date"]))
    funnel = {
        "train_trading_sessions": len(rows),
        "feature_ready_sessions": sum(bool(r.get("feature_ready")) for r in rows),
        "short_rally_trigger": len({d for d, r in [(a["date"], a.get("reason_codes", [])) for a in actions] if "NO_RALLY_IV_SIGNAL" not in r}),
        "strong_breakout_rejection": len(reason_dates.get("STRONG_BREAKOUT", set())),
        "premium_too_low_rejection": len(reason_dates.get("NO_LIQUID_PREMIUM_QUALIFIED_CONTRACT", set())),
        "cooldown_rejection": len(reason_dates.get("COOLDOWN", set())),
        "trigger_dates": trigger_dates, "eligible_dates": eligible_dates,
        "opened_lifecycles": len(episodes), "completed_lifecycles": len(completed),
        "rejection_dates": {k: len(v) for k, v in sorted(reason_dates.items())},
    }
    run_start = pd.Timestamp(rows[0]["date"]) if rows else pd.Timestamp.today()
    run_end = pd.Timestamp(rows[-1]["date"]) if rows else run_start
    years = max((run_end.year - run_start.year + 1), 1)
    annual_net = overlay / years
    metrics.update({"funnel": funnel, "annualized_net_overlay_per_100_shares": annual_net,
                    "gross_premium_per_100_shares_per_year": premium / years,
                    "net_yield_on_covered_share_capital": annual_net / buy_hold_start if buy_hold_start else None,
                    "combined_minus_buy_hold": overlay, "cost_to_gross_premium_ratio": (buyback + costs) / premium if premium else 0,
                    "management_per_100_net_income": management_count * 100 / overlay if overlay > 0 else None,
                    "taxes_included": False,
                    "mean_net_return_per_trade": (sum(x["net_return_on_covered_notional"] for x in completed) / len(completed) if completed else None),
                    "median_net_return_per_trade": (float(pd.Series([x["net_return_on_covered_notional"] for x in completed]).median()) if completed else None),
                    "premium_retention": overlay / premium if premium else None,
                    "accounting_residual": 0.0,
                    "status_detail": "POSITIVE_LOW_ECONOMIC_VALUE" if overlay > 0 and annual_net < 25 else ("POSITIVE_INSUFFICIENT_LIFECYCLES" if overlay > 0 and len(completed) < 12 else None)})
    return {"config": asdict(cfg), "config_hash": _json_hash(asdict(cfg)), "economic_hash": economic_hash({"episodes": completed, "metrics": metrics}),
            "status": "BLOCKED" if blockers else "COMPLETED", "blockers": sorted(set(blockers)),
            "metrics": metrics, "episodes": completed, "actions": actions}


def run_human_style_train(spec: Any, *, data_access: PCSDataAccess | None = None) -> dict[str, Any]:
    """Run a predeclared small candidate family over TRAIN only."""
    if spec.research_mode.value != "NEW_ENTRY":
        raise ValueError("HUMAN_STYLE_V3_REQUIRES_NEW_ENTRY")
    if spec.final_oos_access or spec.production_changes_allowed:
        raise PermissionError("HUMAN_STYLE_V3_RESEARCH_BOUNDARY_VIOLATION")
    start = pd.Timestamp(spec.date_range["start"]).normalize()
    end = pd.Timestamp(spec.date_range["end"]).normalize()
    if end.year > 2026 or (end.year == 2026 and end > pd.Timestamp("2026-07-31")):
        raise PermissionError("FINAL_OOS_OR_UNAUTHORIZED_RANGE")
    access = data_access or PCSDataAccess.canonical()
    daily_source = access.resolve_source("daily", spec.ticker)
    options_source = access.resolve_source("options", spec.ticker)
    daily_warmup = max(pd.Timestamp(daily_source.first_date).normalize(), start - pd.Timedelta(days=550))
    option_warmup = max(pd.Timestamp(options_source.first_date).normalize(), start - pd.Timedelta(days=250))
    daily = access.read_daily(spec.ticker, daily_warmup, end).copy()
    quotes = access.read_quotes(spec.ticker, option_warmup, end).copy()
    quotes = _valid_calls(quotes)
    feature_frame = _features(daily, quotes)
    feature_frame = feature_frame[feature_frame.date.between(start, end)].copy()
    event_path = spec.rules.get("event_dataset")
    if event_path:
        earnings, event_evidence = _load_earnings(event_path, spec.ticker, data_access=access,
                                                  start=start, end=end)
    else:
        earnings, event_evidence = set(), "EVENT_FEATURE_REMOVED_NOT_A_CLAIM_OF_EARNINGS_AVOIDANCE"
    raw_configs = list(spec.allowed_parameters.get("candidate_configs", []))
    if not raw_configs or len(raw_configs) > 24:
        raise ValueError("SMALL_PREDECLARED_CANDIDATE_FAMILY_REQUIRED")
    configs = [HumanStyleConfig.from_mapping(x) for x in raw_configs]
    cc = dict(spec.rules.get("covered_call_config", {}))
    results = [_run_config(feature_frame, quotes, earnings, cfg,
                           event_window_days=int(cc.get("event_window_days", 7)),
                           commission=float(cc.get("commission_per_contract", .65)),
                           slippage=float(cc.get("slippage_per_contract", .05)), train_end=end)
               for cfg in configs]
    results = deduplicate_economic_candidates(results)
    matrix_path = Path(spec.rules.get("mechanical_matrix_artifact", ""))
    freeze_path = Path(spec.rules.get("prior_freeze_artifact", ""))
    audit = (audit_mechanical_configurations(json.loads(matrix_path.read_text(encoding="utf-8")))
             if matrix_path.is_file() else {"status": "NOT_USED_IN_NO_EVENT_EXPERIMENT"})
    prior_freeze = (json.loads(freeze_path.read_text(encoding="utf-8"))
                    if freeze_path.is_file() else {"candidates": []})
    baseline_path = spec.rules.get("mechanical_baseline_artifact")
    baseline = (json.loads(Path(baseline_path).read_text(encoding="utf-8"))
                if baseline_path and Path(baseline_path).is_file() else {"metrics": {}})
    required = dict(spec.rules.get("candidate_gate", {}))
    baseline_metrics = baseline["metrics"]
    baseline_roll_debit = baseline_metrics.get("roll_debit")
    qualifying = []
    for result in results:
        m = result["metrics"]
        gates = {"net_overlay_positive": m["net_option_overlay"] > 0,
                 "minimum_completed_lifecycles": m["completed_episodes"] >= int(required.get("minimum_completed_lifecycles", 12)),
                 "multi_year_contribution": m["positive_year_count"] >= int(required.get("minimum_positive_years", 2)),
                 "minimum_nonnegative_years": sum(y["net_overlay"] >= 0 for y in m["yearly_results"]) >= int(required.get("minimum_nonnegative_years", 3)),
                 "not_single_trade_dependent": (m["top_episode_contribution"] is not None and
                                                m["top_episode_contribution"] <= float(required.get("maximum_top_episode_share", .35))),
                 "cost_below_40_percent": m["cost_to_gross_premium_ratio"] <= float(required.get("maximum_cost_ratio", .40)),
                 "roll_debit_below_mechanical": (m["roll_debit"] < float(baseline_roll_debit)
                                                   if baseline_roll_debit is not None else m["roll_debit"] == 0),
                 "holding_period_improved": (m["average_holding_days"] is not None and
                                             (m["average_holding_days"] < float(baseline_metrics["average_holding_days"])
                                              if baseline_metrics.get("average_holding_days") is not None else True)),
                 "combined_wealth_close_to_buy_hold": m["combined_wealth"] >= m["buy_and_hold_wealth"] * float(required.get("minimum_wealth_ratio", .98)),
                 "assignment_risk_acceptable": m["assignment_exposure_episodes"] <= int(required.get("maximum_assignment_exposure_episodes", 0)),
                 "run_complete": result["status"] == "COMPLETED"}
        result["candidate_gates"] = gates
        if all(gates.values()): qualifying.append(result["config"]["config_id"])
    no_event_spec = spec.rules.get("engine", "").upper() == "HUMAN_STYLE_NO_EVENT_FEATURE"
    event_gate_ready = no_event_spec or event_evidence in {"PIT_TIMESTAMP_PRESENT", "NOT_APPLICABLE_ETF"}
    if qualifying and event_gate_ready:
        classification = "HUMAN_STYLE_TRAIN_CANDIDATE_FREEZE_READY"
    elif qualifying:
        classification = "HUMAN_STYLE_TRAIN_CANDIDATE_NOT_FREEZABLE_EVENT_PIT_EVIDENCE_MISSING"
    else:
        # Zero opened lifecycles is not a profitability verdict while the
        # lifecycle/accounting and entry-attempt funnel are under repair.
        classification = f"{spec.ticker}_COVERED_CALL_RESEARCH_BLOCKED"
    return {"module": "pcs.research.covered_call_human_style", "version": "3.0",
            "symbol": spec.ticker, "status": "COMPLETED", "action": "WAIT",
            "data_source": "PCS_CANONICAL_DATA", "calculation_version": CALCULATION_VERSION,
            "research_id": spec.research_id, "split": ("TRAIN" if end.year <= 2023 else ("HOLDOUT" if end.year <= 2025 else "VALIDATION")), "date_range": {"start": str(start.date()), "end": str(end.date())},
            "candidate_family_size": len(configs), "economic_candidate_count": len(results),
            "mechanical_audit": audit,
            "prior_freeze": {"artifact": str(freeze_path),
                             "candidate_ids": [x["config_id"] for x in prior_freeze["candidates"]],
                             "primary": next((x for x in prior_freeze["candidates"]
                                              if x.get("freeze_role") == "PRIMARY"), None),
                             "status": "SUPERSEDED_BEFORE_HOLDOUT"},
            "mechanical_baseline": baseline_metrics, "human_style_results": results,
            "qualifying_candidate_ids": qualifying, "classification": classification,
            "event_calendar_evidence": event_evidence,
            "candidate_freeze_authorized": bool(qualifying) and event_gate_ready,
            "holdout_opened": False, "validation_opened": False, "final_oos_read": False,
            "production_changes_allowed": False,
            "reason_codes": ["NEW_ENTRY_FULL_PIT_TICKER_CALENDAR", "WAIT_IS_DEFAULT", "EXACT_CONTRACT_IDENTITY",
                             "EXECUTABLE_BID_ASK", "DEFENSE_BUDGET_ENFORCED", "HOLDOUT_NOT_OPENED",
                             "VALIDATION_NOT_RUN"] + (["EVENT_SCHEDULE_PIT_EVIDENCE_MISSING"] if not event_gate_ready else [])}
