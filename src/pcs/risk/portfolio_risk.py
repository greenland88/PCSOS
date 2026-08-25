from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    planned_loss: float
    theoretical_max_loss: float
    bucket_planned_loss: dict[str, float] = field(default_factory=dict)
    ticker_planned_loss: dict[str, float] = field(default_factory=dict)
    simultaneous_stop_loss_exposure: float = 0.0
    account_pct_simultaneous_planned_loss: float = 0.0
    concentration_exposure: dict[str, float] = field(default_factory=dict)
    account_capital: float = 0.0


class PortfolioRiskAggregator:
    """Single production aggregation point for PCS risk state."""

    def snapshot(self, positions, pending_planned_loss: float = 0.0, account_capital: float = 0.0) -> PortfolioRiskSnapshot:
        planned = float(pending_planned_loss)
        theoretical = 0.0
        buckets: dict[str, float] = {}
        tickers: dict[str, float] = {}
        for p in positions:
            loss = float(getattr(p, "planned_loss", getattr(p, "planned_risk", 0.0)))
            planned += loss
            theoretical += float(getattr(p, "theoretical_max_loss", 0.0))
            ticker = str(getattr(p, "ticker", "UNKNOWN"))
            tickers[ticker] = tickers.get(ticker, 0.0) + loss
            bucket = str(getattr(p, "correlation_bucket", "other"))
            buckets[bucket] = buckets.get(bucket, 0.0) + loss
        account_pct = planned / account_capital * 100 if account_capital else 0.0
        return PortfolioRiskSnapshot(planned, theoretical, buckets, tickers, planned, account_pct, dict(tickers), account_capital)

    def from_portfolio(self, portfolio: dict, account_capital: float | None = None) -> PortfolioRiskSnapshot:
        capital = float(account_capital or portfolio.get("account_capital", 0.0))
        planned = float(portfolio.get("planned_loss", portfolio.get("planned_risk", 0.0)))
        theoretical = float(portfolio.get("theoretical_max_loss", 0.0))
        buckets = dict(portfolio.get("bucket_risk", {}))
        tickers = dict(portfolio.get("ticker_risk", {}))
        pct = planned / capital * 100 if capital else 0.0
        return PortfolioRiskSnapshot(planned, theoretical, buckets, tickers, planned, pct, dict(tickers), capital)


def summarize_portfolio(positions, *, max_planned_risk: float | None = None, account_capital: float | None = None):
    s = PortfolioRiskAggregator().snapshot(positions, account_capital=float(account_capital or 0.0))
    return {"planned_risk": s.planned_loss, "planned_loss": s.planned_loss,
            "theoretical_max_loss": s.theoretical_max_loss,
            "capacity_used_pct": (s.planned_loss / max_planned_risk * 100) if max_planned_risk else None,
            "account_pct_simultaneous_planned_loss": s.account_pct_simultaneous_planned_loss,
            "account_capital": s.account_capital,
            "bucket_risk": s.bucket_planned_loss,
            "simultaneous_stop_loss_exposure": s.simultaneous_stop_loss_exposure}
