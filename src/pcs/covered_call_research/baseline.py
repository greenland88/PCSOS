"""Fixed, non-optimized PLTR covered-call baseline."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json
import pandas as pd

from pcs.data.access import PCSDataAccess


@dataclass
class BaselineConfig:
    entry_dte_min: int = 30
    entry_dte_max: int = 60
    delta_min: float = 0.10
    delta_max: float = 0.30
    max_short_calls: int = 3
    manage_dte: int = 7
    roll_target_dte_min: int = 30
    roll_target_dte_max: int = 120
    commission_per_contract: float = 0.65
    slippage_per_contract: float = 0.05
    prevalidate_paths: bool = True
    excluded_quote_keys: tuple[tuple[str, str, float], ...] = ()
    entry_timing: str = "FIRST_MONTHLY"
    profit_take_fraction: float | None = None
    strike_rule: str = "HIGHEST_ELIGIBLE"
    strike_percent_above_spot: float = 0.10
    strike_atr_multiplier: float = 1.0
    roll_trigger: str = "DTE_OR_ITM"
    roll_delta_threshold: float = 0.30
    roll_price_near_fraction: float = 0.98
    roll_extrinsic_fraction: float = 0.10


def _valid_call_rows(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame[frame.call_put.astype(str).str.lower().isin({"c", "call"})].copy()
    x["trade_date"] = pd.to_datetime(x.trade_date).dt.normalize()
    x["expiration_date"] = pd.to_datetime(x.expiration_date).dt.normalize()
    x["dte"] = (x.expiration_date - x.trade_date).dt.days
    x["delta_abs"] = x.delta.abs()
    return x[(x.bid.notna()) & (x.ask.notna()) & (x.bid >= 0) & (x.ask >= x.bid) &
             (x.expiration_date > x.trade_date) & (x.strike > 0)]


def _select_entry(calls: pd.DataFrame, day: pd.Timestamp, cfg: BaselineConfig,
                  underlying_price: float | None = None,
                  underlying_atr: float | None = None,
                  prior_resistance: float | None = None) -> pd.Series | None:
    x = calls[(calls.trade_date == day) & calls.dte.between(cfg.entry_dte_min, cfg.entry_dte_max) &
              calls.delta_abs.between(cfg.delta_min, cfg.delta_max)]
    if x.empty: return None
    rule = cfg.strike_rule.upper()
    if rule == "PERCENT_ABOVE_SPOT":
        if underlying_price is None:
            raise ValueError("underlying_price is required for PERCENT_ABOVE_SPOT")
        x = x[x.strike >= float(underlying_price) * (1.0 + cfg.strike_percent_above_spot)]
        if x.empty: return None
        return x.sort_values(["strike", "expiration_date", "delta_abs"], ascending=[True, True, True], kind="mergesort").iloc[0]
    if rule == "ATR":
        if underlying_price is None or underlying_atr is None:
            raise ValueError("underlying_price and underlying_atr are required for ATR")
        x = x[x.strike >= float(underlying_price) + float(cfg.strike_atr_multiplier) * float(underlying_atr)]
        if x.empty: return None
        return x.sort_values(["strike", "expiration_date", "delta_abs"], ascending=[True, True, True], kind="mergesort").iloc[0]
    if rule == "PRIOR_HIGH_RESISTANCE":
        if prior_resistance is None:
            raise ValueError("prior_resistance is required for PRIOR_HIGH_RESISTANCE")
        x = x[x.strike >= float(prior_resistance)]
        if x.empty: return None
        return x.sort_values(["strike", "expiration_date", "delta_abs"], ascending=[True, True, True], kind="mergesort").iloc[0]
    ascending = rule == "LOWEST_ELIGIBLE"
    return x.sort_values(["strike", "expiration_date", "delta_abs"], ascending=[ascending, True, True], kind="mergesort").iloc[0]


def _complete_contract_path(calls: pd.DataFrame, row: pd.Series, calendar: list[pd.Timestamp]) -> bool:
    """Require exact executable asks for every day through the listed expiry."""
    path = calls[(calls.expiration_date == row.expiration_date) & (calls.strike == row.strike)]
    required = [d for d in calendar if row.trade_date <= d <= row.expiration_date]
    if not required or row.expiration_date > calendar[-1]: return False
    available = path[path.trade_date.isin(required)]
    return len(available) == len(required) and available.trade_date.nunique() == len(required) and available.ask.notna().all()


def run_baseline(symbol: str = "PLTR", *, start: str | None = None, end: str | None = None,
                 access: PCSDataAccess | None = None, config: BaselineConfig | None = None,
                 output_dir: str | Path | None = None,
                 candidate_population: pd.DataFrame | None = None,
                 population_mode: str = "EXACT_CONTRACTS") -> dict[str, Any]:
    cfg = config or BaselineConfig(); access = access or PCSDataAccess.canonical(); symbol = symbol.upper()
    daily = access.read_daily(symbol, start, end).copy(); daily.date = pd.to_datetime(daily.date).dt.normalize()
    qspec = access.resolve_source("options", symbol)
    qstart = max(pd.Timestamp(start) if start else pd.Timestamp(qspec.first_date), pd.Timestamp(qspec.first_date))
    qend = min(pd.Timestamp(end) if end else pd.Timestamp(qspec.last_date), pd.Timestamp(qspec.last_date))
    quotes = _valid_call_rows(access.read_quotes(symbol, qstart, qend))
    quotes = quotes[(quotes.trade_date >= qstart) & (quotes.trade_date <= qend)]
    frozen_count = None
    allowed_entries = None
    if candidate_population is not None:
        required = {"entry_date", "expiration", "strike"}
        if not required.issubset(candidate_population.columns):
            raise ValueError("candidate_population must contain entry_date, expiration, strike")
        pop = candidate_population.copy()
        pop["entry_date"] = pd.to_datetime(pop["entry_date"]).dt.normalize()
        pop["expiration"] = pd.to_datetime(pop["expiration"]).dt.normalize()
        pop["strike"] = pop["strike"].astype(float)
        frozen_count = len(pop)
        # Freeze the entry population only.  Keep the complete exact-contract
        # path after entry so HOLD/CLOSE/ROLL/EXPIRY semantics remain valid.
        if population_mode.upper() == "ENTRY_DATES":
            allowed_entries = set(pop.entry_date)
        elif population_mode.upper() == "EXACT_CONTRACTS":
            allowed_entries = set(zip(pop.entry_date, pop.expiration, pop.strike))
        else:
            raise ValueError("population_mode must be EXACT_CONTRACTS or ENTRY_DATES")
    if cfg.excluded_quote_keys:
        excluded = {(pd.Timestamp(d).normalize(), pd.Timestamp(e).normalize(), float(s)) for d, e, s in cfg.excluded_quote_keys}
        quotes = quotes[~quotes.apply(lambda r: (r.trade_date, r.expiration_date, float(r.strike)) in excluded, axis=1)].copy()
    by_day = {d: g for d, g in quotes.groupby("trade_date", sort=False)}
    event_path = Path("research_outputs/pltr_covered_call_research_v1/earnings_events.csv")
    if not event_path.exists():
        event_path = Path("data/raw/events/official_event_dates_2010-01-01_to_2026-07-31.csv")
    earnings_dates = set()
    if event_path.exists():
        events = pd.read_csv(event_path)
        if {"symbol", "event_date"}.issubset(events.columns):
            earnings_dates = set(pd.to_datetime(events.loc[events.symbol.astype(str).str.upper().eq(symbol), "event_date"]).dt.normalize())
    daily = daily.sort_values("date").copy()
    prev_close = daily.close.shift(1)
    daily["true_range"] = pd.concat([daily.high - daily.low, (daily.high - prev_close).abs(), (daily.low - prev_close).abs()], axis=1).max(axis=1)
    daily["atr14"] = daily.true_range.shift(1).rolling(14, min_periods=14).mean()
    daily["prior_high20"] = daily.high.shift(1).rolling(20, min_periods=20).max()
    price = daily.set_index("date").close.to_dict(); atr = daily.set_index("date").atr14.to_dict(); resistance = daily.set_index("date").prior_high20.to_dict(); days = sorted(d for d in price if qstart <= d <= qend)
    lots: list[dict[str, Any]] = []; actions: list[dict[str, Any]] = []; blockers: list[str] = []
    active: list[dict[str, Any]] = []; prior_month = None
    for day in days:
        # One fixed entry opportunity per month, capped by the user's three-call capacity.
        timing_ok = True
        if cfg.entry_timing == "UP_DAY":
            prior_days = [x for x in days if x < day]
            timing_ok = bool(prior_days) and float(price[day]) > float(price[prior_days[-1]])
        if cfg.entry_timing == "POST_EARNINGS":
            recent = [d for d in earnings_dates if d <= day]
            timing_ok = bool(recent) and (day - max(recent)).days <= 5
        if cfg.entry_timing == "IV_RISING":
            today = by_day.get(day, quotes.iloc[0:0])
            iv_cols = [c for c in ("bid_iv", "ask_iv") if c in today.columns]
            today_iv = float(today[iv_cols].stack().median()) if iv_cols and not today.empty else float("nan")
            prior = [d for d in days if d < day]
            prev = by_day.get(prior[-1], quotes.iloc[0:0]) if prior else quotes.iloc[0:0]
            prev_iv = float(prev[iv_cols].stack().median()) if iv_cols and not prev.empty else float("nan")
            timing_ok = pd.notna(today_iv) and pd.notna(prev_iv) and today_iv > prev_iv
        if cfg.entry_timing == "RESISTANCE_NEAR":
            prior = resistance.get(day)
            timing_ok = prior is not None and pd.notna(prior) and abs(float(price[day]) / float(prior) - 1.0) <= 0.02
        if day.to_period("M") != prior_month and timing_ok and len(active) < cfg.max_short_calls:
            entry_quotes = by_day.get(day, quotes.iloc[0:0])
            if allowed_entries is not None:
                if population_mode.upper() == "ENTRY_DATES":
                    entry_quotes = entry_quotes[entry_quotes.trade_date.isin(allowed_entries)]
                else:
                    entry_quotes = entry_quotes[entry_quotes.apply(lambda r: (r.trade_date, r.expiration_date, float(r.strike)) in allowed_entries, axis=1)]
            row = _select_entry(entry_quotes, day, cfg, float(price[day]), atr.get(day), resistance.get(day))
            if row is not None and cfg.prevalidate_paths and not _complete_contract_path(quotes, row, days):
                # Do not open a position whose future exact quotes cannot be
                # observed.  No adjacent-day or synthetic quote is allowed.
                row = None
            if row is not None:
                proceeds = float(row.bid) * 100 - cfg.commission_per_contract - cfg.slippage_per_contract
                lot = {"lot_id": f"{symbol}|{day.date()}|{row.expiration_date.date()}|{float(row.strike):.8f}|C",
                       "entry_date": day, "expiration": row.expiration_date, "strike": float(row.strike),
                       "entry_proceeds": proceeds, "entry_bid": float(row.bid), "premium_received": proceeds, "buyback_cost": 0.0, "roll_credit": 0.0,
                       "roll_debit": 0.0, "itm_days": 0, "assignment_risk_events": 0,
                       "closed_date": None, "holding_days": None, "max_underlying_price": float(price[day]),
                       "capped_upside_opportunity_cost_proxy": 0.0}
                active.append(lot); lots.append(lot); actions.append({"date": day, "action": "HOLD", "event": "OPEN_CALL", "lot_id": lot["lot_id"], "execution": "BID", "execution_required": True, "quote_available": True, "cashflow": proceeds})
            prior_month = day.to_period("M")
        for lot in list(active):
            lot["max_underlying_price"] = max(float(lot["max_underlying_price"]), float(price[day]))
            lot["capped_upside_opportunity_cost_proxy"] = max(
                float(lot["capped_upside_opportunity_cost_proxy"]),
                max(0.0, float(price[day]) - float(lot["strike"])) * 100.0,
            )
            if float(price[day]) >= lot["strike"]: lot["itm_days"] += 1
            dte = (lot["expiration"] - day).days
            current = by_day.get(day, quotes.iloc[0:0]); old = current[(current.expiration_date == lot["expiration"]) & (current.strike == lot["strike"])]
            profit_take_due = False
            if cfg.profit_take_fraction is not None and not old.empty and pd.notna(old.iloc[0].ask):
                profit_take_due = float(old.iloc[0].ask) <= float(lot["entry_bid"]) * (1.0 - float(cfg.profit_take_fraction))
            trigger = cfg.roll_trigger.upper()
            if trigger not in {"DTE_ONLY", "DTE_OR_ITM", "DELTA", "PRICE_NEAR_OR_ABOVE_STRIKE", "EXTRINSIC_VALUE"}:
                raise ValueError("unsupported roll_trigger")
            execution_due = dte <= cfg.manage_dte or profit_take_due
            if trigger == "DTE_OR_ITM":
                execution_due = execution_due or float(price[day]) >= lot["strike"]
            if trigger in {"DELTA", "EXTRINSIC_VALUE"} and not old.empty:
                current_delta = old.iloc[0].get("delta")
                if trigger == "DELTA":
                    execution_due = execution_due or (pd.notna(current_delta) and abs(float(current_delta)) >= cfg.roll_delta_threshold)
                else:
                    ask = old.iloc[0].get("ask")
                    intrinsic = max(float(price[day]) - lot["strike"], 0.0)
                    execution_due = execution_due or (pd.notna(ask) and float(ask) - intrinsic <= max(float(ask), 0.0) * cfg.roll_extrinsic_fraction)
            if trigger == "PRICE_NEAR_OR_ABOVE_STRIKE":
                execution_due = execution_due or float(price[day]) >= lot["strike"] * cfg.roll_price_near_fraction
            if not execution_due:
                # Daily observation is not an execution requirement.  Keep
                # the missing quote visible for reconciliation, but do not
                # invalidate the lifecycle merely because a HOLD mark is absent.
                actions.append({"date": day, "action": "HOLD", "lot_id": lot["lot_id"],
                                "quote_available": bool(not old.empty and pd.notna(old.iloc[0].bid) and pd.notna(old.iloc[0].ask)),
                                "execution_required": False})
                continue
            if execution_due:
                if old.empty or pd.isna(old.iloc[0].ask):
                    if dte <= 0 and float(price[day]) >= lot["strike"]: lot["assignment_risk_events"] += 1
                    actions.append({"date": day, "action": "BUY_TO_CLOSE" if dte <= 0 else "ROLL_OR_CLOSE",
                                    "lot_id": lot["lot_id"], "execution_required": True,
                                    "quote_available": False, "required_side": "ASK",
                                    "gap_stage": "EXPIRY" if dte <= 0 else "CLOSE_OR_ROLL"})
                    blockers.append(f"MISSING_EXACT_CONTRACT_ASK:{day.date()}:{lot['lot_id']}"); continue
                old = old.iloc[0]
                candidates = current[current.dte.between(cfg.roll_target_dte_min, cfg.roll_target_dte_max) & (current.strike > max(lot["strike"], float(price[day])))].sort_values(["strike", "expiration_date"], ascending=[False, True])
                if dte > 0 and not candidates.empty and not profit_take_due:
                    new = candidates.iloc[0]; buyback = float(old.ask) * 100 + cfg.commission_per_contract + cfg.slippage_per_contract; sale = float(new.bid) * 100 - cfg.commission_per_contract - cfg.slippage_per_contract; net = sale - buyback
                    old_lot_id = lot["lot_id"]; lot["buyback_cost"] += buyback; lot["premium_received"] += sale; lot["roll_credit"] += max(net, 0); lot["roll_debit"] += max(-net, 0); lot["expiration"], lot["strike"] = new.expiration_date, float(new.strike); lot["lot_id"] = f"{symbol}|{lot['entry_date'].date()}|{new.expiration_date.date()}|{float(new.strike):.8f}|C"; actions.append({"date":day,"action":"ROLL","lot_id":old_lot_id,"old_contract":f"{symbol}|{old.expiration_date.date()}|{float(old.strike):.8f}|C","new_contract":f"{symbol}|{new.expiration_date.date()}|{float(new.strike):.8f}|C","buyback_ask":buyback,"sale_bid":sale,"net_roll":net,"cashflow":net,"execution_required":True,"quote_available":True}); continue
                buyback = float(old.ask) * 100 + cfg.commission_per_contract + cfg.slippage_per_contract; lot["buyback_cost"] += buyback; lot["closed_date"] = day; lot["holding_days"] = (day - lot["entry_date"]).days; actions.append({"date":day,"action":"BUY_TO_CLOSE","lot_id":lot["lot_id"],"cost":buyback,"execution":"ASK","execution_required":True,"quote_available":True,"cashflow":-buyback}); active.remove(lot)
        prior_month = day.to_period("M")
    # Open lots are deliberately retained as censored-at-horizon positions;
    # annual shards and the final data horizon never force-close them.
    action_by_entry = {}
    for action in actions:
        lid = str(action.get("lot_id", "")); parts = lid.split("|")
        if len(parts) >= 2: action_by_entry.setdefault(parts[1], []).append(action)
    for lot in lots:
        events = action_by_entry.get(str(pd.Timestamp(lot["entry_date"]).date()), [])
        exec_missing = [x for x in events if x.get("execution_required") and not x.get("quote_available", True)]
        hold_missing = [x for x in events if x.get("action") == "HOLD" and x.get("execution_required") is False and not x.get("quote_available", True)]
        lot["execution_completeness"] = "INCOMPLETE" if exec_missing else "COMPLETE"
        lot["management_observability"] = "PARTIAL" if hold_missing else "COMPLETE"
        lot["mark_to_market_completeness"] = "PARTIAL" if hold_missing else "COMPLETE"
        lot["final_usability"] = "EXIT_NOT_EXECUTABLE" if exec_missing else ("REALIZED_PNL_EXECUTABLE_MARKS_PARTIAL" if hold_missing else "FULLY_EXECUTABLE")
    result = {"module":"pcs.covered_call_research.baseline","version":"1.1","symbol":symbol,"status":"BLOCKED" if blockers else "COMPLETED","config":asdict(cfg),"data_source":"PCS_CANONICAL_DATA","final_oos_read":False,"actions":actions,"lots":lots,"blockers":sorted(set(blockers))}
    execution_legs = sum(1 if a.get("action") in {"BUY_TO_CLOSE", "HOLD"} and a.get("execution_required") else 2 if a.get("action") == "ROLL" else 0 for a in actions)
    result["metrics"] = {"eligible_dates":len(days),"total_candidates":frozen_count if frozen_count is not None else len(lots),"calls_opened":len(lots),"completed_lifecycles":sum(x["closed_date"] is not None for x in lots),"open_at_horizon":sum(x["closed_date"] is None for x in lots),"premium_received":sum(x["premium_received"] for x in lots),"buyback_cost":sum(x["buyback_cost"] for x in lots),"roll_credit":sum(x["roll_credit"] for x in lots),"roll_debit":sum(x["roll_debit"] for x in lots),"fees":execution_legs * cfg.commission_per_contract,"slippage":execution_legs * cfg.slippage_per_contract,"execution_legs":execution_legs,"call_overlay_pnl":sum(x["premium_received"]-x["buyback_cost"] for x in lots),"capped_upside_opportunity_cost_proxy":sum(x["capped_upside_opportunity_cost_proxy"] for x in lots),"itm_days":sum(x["itm_days"] for x in lots),"assignment_risk_events":sum(x["assignment_risk_events"] for x in lots),"average_holding_days":(sum(x["holding_days"] for x in lots if x["holding_days"] is not None)/sum(x["holding_days"] is not None for x in lots)) if any(x["holding_days"] is not None for x in lots) else None}
    for lot in lots:
        lot["buy_and_hold_pnl_proxy"] = (float(price[days[-1]]) - float(price[lot["entry_date"]])) * 100.0
    yearly = []
    lots_frame = pd.DataFrame(lots)
    if not lots_frame.empty:
        for year, group in lots_frame.groupby(pd.to_datetime(lots_frame["entry_date"]).dt.year):
            yearly.append({"year": int(year), "calls_opened": int(len(group)), "completed_lifecycles": int(group["closed_date"].notna().sum()), "premium_received": float(group["premium_received"].sum()), "buyback_cost": float(group["buyback_cost"].sum()), "roll_credit": float(group["roll_credit"].sum()), "roll_debit": float(group["roll_debit"].sum()), "call_overlay_pnl": float((group["premium_received"] - group["buyback_cost"]).sum()), "buy_and_hold_pnl_proxy": float(group["buy_and_hold_pnl_proxy"].sum()), "capped_upside_opportunity_cost_proxy": float(group["capped_upside_opportunity_cost_proxy"].sum())})
    result["metrics"]["buy_and_hold_pnl_proxy"] = float(sum(x["buy_and_hold_pnl_proxy"] for x in lots))
    result["metrics"]["realized_pnl"] = float(result["metrics"]["call_overlay_pnl"])
    result["metrics"]["expiration_assignment_settlement"] = 0.0
    result["metrics"]["accounting_residual"] = float(
        result["metrics"]["premium_received"] - result["metrics"]["buyback_cost"]
        - result["metrics"]["realized_pnl"]
        - result["metrics"]["expiration_assignment_settlement"]
    )
    result["metrics"]["yearly_results"] = yearly
    result["metrics"]["source_gaps"] = int(sum(x["final_usability"] == "SOURCE_GAP_UNRESOLVED" for x in lots))
    result["metrics"]["non_executable_closes_rolls"] = int(sum(x["final_usability"] in {"EXIT_NOT_EXECUTABLE", "ROLL_NOT_EXECUTABLE"} for x in lots))
    result["metrics"]["assignment_exposures"] = int(sum(x["assignment_risk_events"] > 0 for x in lots))
    cash = pd.Series([float(a.get("cashflow", 0.0)) for a in actions if a.get("execution_required")], dtype="float64")
    if not cash.empty:
        curve = cash.cumsum(); result["metrics"]["max_drawdown"] = float((curve - curve.cummax()).min())
    else:
        result["metrics"]["max_drawdown"] = 0.0
    if output_dir:
        out=Path(output_dir); out.mkdir(parents=True,exist_ok=True); (out/"baseline.json").write_text(json.dumps(result,indent=2,default=str),encoding="utf-8")
    return result
