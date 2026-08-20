from dataclasses import dataclass, field


@dataclass
class ReplayResult:
    entries: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


class HistoricalReplayEngine:
    """Basic strategy replay skeleton; live decision logic remains separate."""

    def __init__(self, slippage_bps: float = 5):
        self.slippage_bps = slippage_bps

    def replay(self, feature_rows: list[dict]) -> ReplayResult:
        result = ReplayResult()
        for row in feature_rows:
            valid = (
                row.get("market_regime") == "GREEN"
                and 30 <= row.get("DTE", row.get("dte", 0)) <= 40
                and row.get("liquidity_score", 0) >= 50
                and row.get("buffer_ratio", 0) >= 1
            )
            target = result.entries if valid else result.skipped
            target.append(dict(row, slippage_bps=self.slippage_bps))
        return result
