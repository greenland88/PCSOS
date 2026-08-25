"""Research-only daily warning analysis; no roll or rule decisions."""

from __future__ import annotations

from copy import deepcopy

import pandas as pd


WARNING_TYPES = ("spread_deterioration", "relative_weakness", "semiconductor_weakness", "market_weakness", "relative_plus_semiconductor")


def eventual_outcome(trade):
    events = trade.get("events", {})
    if events.get("stop") is not None and (events.get("profit50") is None or events["stop"] < events["profit50"]): return "STOP"
    if events.get("profit50") is not None and (events.get("stop") is None or events["profit50"] <= events["stop"]): return "PROFIT50"
    return "NEITHER"


def warning_flags(day):
    spread = day.get("spread_multiple")
    relative = day.get("relative_weakness", False)
    semi = day.get("semiconductor_weakness", False)
    market = day.get("market_weakness", False)
    return {
        "spread_deterioration": spread is not None and spread >= 1.5,
        "relative_weakness": relative,
        "semiconductor_weakness": semi,
        "market_weakness": market,
        "relative_plus_semiconductor": relative and semi,
        "spread_plus_relative": spread is not None and spread >= 1.5 and relative,
    }


def first_warnings(lifecycle):
    first = {}
    for day in lifecycle:
        for name, active in warning_flags(day).items():
            if active and name not in first:
                first[name] = dict(day, warning_type=name)
    return first


def truncate_lifecycle(lifecycle, trade):
    """Keep only observable lifecycle days through the first terminal event."""
    events = trade.get("events", {})
    exits = [pd.Timestamp(v) for v in (events.get("profit50"), events.get("stop")) if v is not None]
    if not exits:
        return list(lifecycle)[:20]
    cutoff = min(exits)
    return [day for day in lifecycle if pd.Timestamp(day["date"]) <= cutoff][:20]


def warning_lead_days(warning_date, stop_date):
    if warning_date is None or stop_date is None: return None
    return int((pd.Timestamp(stop_date) - pd.Timestamp(warning_date)).days)


def recovery_after_warning(lifecycle, warning_date, initial_credit):
    after = [d for d in lifecycle if pd.Timestamp(d["date"]) >= pd.Timestamp(warning_date)]
    if not after: return {"max_spread_multiple_after_warning": None, "recovery_days": None}
    maximum = max(d.get("spread_multiple", float("nan")) for d in after)
    recovered = next((d for d in after if d.get("spread_multiple") is not None and d["spread_multiple"] <= 0.5), None)
    return {"max_spread_multiple_after_warning": maximum, "recovery_days": (pd.Timestamp(recovered["date"]) - pd.Timestamp(warning_date)).days if recovered else None}


def summarize_warnings(records):
    out = []
    for warning_type in sorted({r["warning_type"] for r in records}):
        rows = [r for r in records if r["warning_type"] == warning_type]
        counts = {outcome: sum(r["eventual_outcome"] == outcome for r in rows) for outcome in ("STOP", "PROFIT50", "NEITHER")}
        stops = [r for r in rows if r["eventual_outcome"] == "STOP"]
        leads = [r["lead_days"] for r in stops if r.get("lead_days") is not None]
        additional = [r["additional_loss_after_warning"] for r in stops if r.get("additional_loss_after_warning") is not None]
        out.append({"warning_type": warning_type, "warning_count": len(rows), **{f"{k.lower()}_count": v for k, v in counts.items()},
                    "stop_rate_after_warning": counts["STOP"] / len(rows) if rows else None,
                    "profit50_rate_after_warning": counts["PROFIT50"] / len(rows) if rows else None,
                    "median_lead_days": float(pd.Series(leads).median()) if leads else None,
                    "mean_lead_days": float(pd.Series(leads).mean()) if leads else None,
                    "additional_loss_mean": float(pd.Series(additional).mean()) if additional else None,
                    "false_warning_rate": counts["PROFIT50"] / len(rows) if rows else None})
    return out


def classify_warning(summary_row):
    n = summary_row["warning_count"]
    if n < 10: return "NO_SIGNAL"
    if summary_row.get("median_lead_days") is not None and summary_row["median_lead_days"] <= 1: return "TOO_LATE"
    if summary_row.get("stop_rate_after_warning", 0) >= 0.7 and summary_row.get("false_warning_rate", 1) <= 0.3: return "USEFUL"
    if summary_row.get("stop_rate_after_warning", 0) >= 0.5: return "PROMISING"
    return "TOO_NOISY"


def enrich_trade_record(trade, lifecycle):
    row = deepcopy(trade)
    row["eventual_outcome"] = eventual_outcome(row)
    row["warnings"] = first_warnings(lifecycle)
    return row
