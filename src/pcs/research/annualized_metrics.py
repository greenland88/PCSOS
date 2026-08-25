"""Research-only annualized performance metrics for replay results."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd


def _number(row: Any, *names: str) -> float | None:
    for name in names:
        value = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
        if value is not None and pd.notna(value):
            return float(value)
    return None


def annualized_performance_metrics(
    frame: pd.DataFrame,
    *,
    starting_equity: float | None = None,
    test_start_date: Any = None,
    test_end_date: Any = None,
) -> dict[str, Any]:
    """Return reporting metrics without modifying or replaying trades."""
    data = frame.copy()
    complete = data[data.get("status", pd.Series("COMPLETE", index=data.index)).eq("COMPLETE")] if len(data) else data
    pnl = pd.to_numeric(complete.get("realized_pnl", pd.Series(dtype=float)), errors="coerce").dropna()
    if test_start_date is None:
        dates = pd.to_datetime(data.get("date", pd.Series(dtype="datetime64[ns]")), errors="coerce").dropna()
        test_start_date = dates.min() if len(dates) else None
    if test_end_date is None:
        dates = pd.to_datetime(data.get("exit_date", data.get("date", pd.Series(dtype="datetime64[ns]"))), errors="coerce").dropna()
        test_end_date = dates.max() if len(dates) else None
    start = pd.Timestamp(test_start_date) if test_start_date is not None else None
    end = pd.Timestamp(test_end_date) if test_end_date is not None else None
    days = (end - start).days if start is not None and end is not None else 0
    total = float(pnl.sum()) if len(pnl) else 0.0
    if starting_equity is None:
        starting_equity = _number(data.attrs, "starting_equity")
    start_eq = float(starting_equity) if starting_equity is not None else None
    end_eq = start_eq + total if start_eq is not None else None
    cagr = ((end_eq / start_eq) ** (365.25 / days) - 1) if start_eq and start_eq > 0 and end_eq and end_eq > 0 and days > 0 else None
    risk = pd.to_numeric(complete.get("planned_loss", pd.Series(dtype=float)), errors="coerce").dropna()
    collateral = pd.to_numeric(complete.get("collateral_used", complete.get("spread_width", pd.Series(dtype=float))), errors="coerce").dropna()
    if "collateral_used" not in complete and len(collateral): collateral = collateral * 100
    curve = (start_eq if start_eq is not None else 0.0) + pnl.cumsum()
    drawdown = float((curve - curve.cummax()).min()) if len(curve) else 0.0
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    avg_risk = float(risk.mean()) if len(risk) else None
    return {"total_realized_pnl": total, "starting_equity": start_eq, "ending_equity": end_eq,
            "test_start_date": start.date().isoformat() if start is not None else None,
            "test_end_date": end.date().isoformat() if end is not None else None, "test_days": days,
            "CAGR": cagr, "average_capital_at_risk": avg_risk,
            "average_collateral_used": float(collateral.mean()) if len(collateral) else None,
            "annualized_return_on_average_capital": total / float(collateral.mean()) * 365.25 / days if len(collateral) and days > 0 and collateral.mean() else None,
            "max_drawdown": drawdown, "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() else None,
            "expectancy_per_trade": float(pnl.mean()) if len(pnl) else None, "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
            "trade_count": int(len(pnl)), "average_planned_loss_exposure": avg_risk,
            "peak_planned_loss_exposure": float(risk.max()) if len(risk) else None,
            "annualized_return_on_average_planned_loss": total / avg_risk * 365.25 / days if avg_risk and days > 0 else None}
