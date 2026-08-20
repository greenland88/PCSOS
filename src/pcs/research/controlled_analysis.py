"""Research-only matched analysis for persisted PCS trade records."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from statistics import median


GATES = ("PASS", "WATCH", "REJECT")


def dte_bucket(dte):
    dte = float(dte)
    if 20 <= dte <= 29:
        return "20-29"
    if 30 <= dte <= 39:
        return "30-39"
    if 40 <= dte <= 45:
        return "40-45"
    return "outside"


def atr_bucket(value):
    value = float(value)
    if value <= 1.75:
        return "<=1.75"
    if value <= 2.25:
        return "1.76-2.25"
    return ">2.25"


def credit_bucket(value):
    value = float(value)
    if value <= 0.20:
        return "15-20%"
    if value <= 0.30:
        return "21-30%"
    return ">30%"


def _is_before(trade, event):
    value = trade.get("events", {}).get(event)
    stop = trade.get("events", {}).get("stop")
    return value is not None and (stop is None or value <= stop)


def _stop_before(trade):
    stop = trade.get("events", {}).get("stop")
    profit = trade.get("events", {}).get("profit50")
    return stop is not None and (profit is None or stop < profit)


def enrich_trade(trade):
    """Return a new analysis row without mutating the persisted record."""
    row = dict(trade)
    row["entry_date"] = row.get("date")
    row["gate"] = row.get("current_state", row.get("trend_gate"))
    expiration = row.get("expiration")
    entry_date = row.get("date")
    if isinstance(expiration, str):
        expiration = datetime.fromisoformat(expiration)
    if isinstance(entry_date, str):
        entry_date = datetime.fromisoformat(entry_date)
    row["dte"] = (expiration - entry_date).days if expiration is not None and entry_date is not None else None
    row["atr_buffer"] = row.get("short_buffer_atr")
    row["credit_width"] = row.get("credit_width_ratio")
    row["entry_credit"] = row.get("initial_credit")
    row["profit50_before_stop"] = _is_before(row, "profit50")
    row["profit70_before_stop"] = _is_before(row, "profit70")
    row["stop_before_profit50"] = _stop_before(row)
    row["stop_loss_amount"] = row.get("realized_pnl") if row.get("exit_reason") == "STOP" else None
    row["dte_bucket"] = dte_bucket(row["dte"]) if row["dte"] is not None else "unknown"
    row["atr_bucket"] = atr_bucket(row["atr_buffer"]) if row["atr_buffer"] is not None else "unknown"
    row["credit_bucket"] = credit_bucket(row["credit_width"]) if row["credit_width"] is not None else "unknown"
    return row


def _pf(rows):
    gains = sum(float(r["realized_pnl"]) for r in rows if r.get("realized_pnl") is not None and r["realized_pnl"] > 0)
    losses = -sum(float(r["realized_pnl"]) for r in rows if r.get("realized_pnl") is not None and r["realized_pnl"] < 0)
    return gains / losses if losses else None


def _confidence(n):
    return "SMALL_SAMPLE" if n < 10 else "LIMITED" if n < 20 else "USABLE"


def group_metrics(rows):
    rows = list(rows)
    n = len(rows)
    pnl = [r["realized_pnl"] for r in rows if r.get("realized_pnl") is not None]
    p50 = [r for r in rows if r["profit50_before_stop"]]
    p70 = [r for r in rows if r["profit70_before_stop"]]
    stops = [r for r in rows if r["stop_before_profit50"]]
    return {
        "sample_count": n,
        "confidence": _confidence(n),
        "profit50_before_stop_rate": len(p50) / n if n else None,
        "profit70_before_stop_rate": len(p70) / n if n else None,
        "stop_before_profit50_rate": len(stops) / n if n else None,
        "average_pnl": sum(pnl) / len(pnl) if pnl else None,
        "median_pnl": median(pnl) if pnl else None,
        "profit_factor": _pf(rows) if rows else None,
        "median_days_to_profit50": median([r["days_held"] for r in p50]) if p50 else None,
        "median_days_to_stop": median([r["days_held"] for r in stops]) if stops else None,
        "average_stop_loss": (sum(r["realized_pnl"] for r in stops) / len(stops)) if stops else None,
        "worst_trade": min(pnl) if pnl else None,
    }


def controlled_groups(trades):
    rows = [enrich_trade(t) for t in trades]
    groups = defaultdict(list)
    for row in rows:
        groups[(row["dte_bucket"], row["atr_bucket"], row["credit_bucket"], row["gate"])].append(row)
    return rows, groups


def matched_pairwise(groups):
    out = []
    for key in sorted({k[:3] for k in groups}):
        present = {k[3] for k in groups if k[:3] == key}
        for left, right in (("PASS", "WATCH"), ("PASS", "REJECT"), ("WATCH", "REJECT")):
            if left not in present or right not in present:
                continue
            a, b = group_metrics(groups[(*key, left)]), group_metrics(groups[(*key, right)])
            out.append({"dte_bucket": key[0], "atr_bucket": key[1], "credit_bucket": key[2], "left": left, "right": right,
                        "left_count": a["sample_count"], "right_count": b["sample_count"],
                        "stop_rate_difference": a["stop_before_profit50_rate"] - b["stop_before_profit50_rate"],
                        "average_pnl_difference": a["average_pnl"] - b["average_pnl"],
                        "profit_factor_difference": (a["profit_factor"] - b["profit_factor"]) if a["profit_factor"] is not None and b["profit_factor"] is not None else None})
    return out


def matched_aggregate(groups):
    matched = {g: [] for g in GATES}
    for key in {k[:3] for k in groups}:
        present = {k[3] for k in groups if k[:3] == key}
        if "PASS" in present and ("WATCH" in present or "REJECT" in present):
            for gate in present:
                matched[gate].extend(groups[(*key, gate)])
    return {gate: group_metrics(rows) | {"gate": gate} for gate, rows in matched.items() if rows}
