from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticCrashScenario:
    name: str
    symbol: str
    price_shock_pct: float
    days: int
    iv_shock_pct: float = 0
    bid_ask_widen_pct: float = 0
    slippage_bps: float = 0


DEFAULT_SYNTHETIC_SCENARIOS = [
    SyntheticCrashScenario("QQQ -10% in 5 days", "QQQ", -10, 5, iv_shock_pct=40, bid_ask_widen_pct=50, slippage_bps=25),
    SyntheticCrashScenario("QQQ -20% in 10 days", "QQQ", -20, 10, iv_shock_pct=80, bid_ask_widen_pct=100, slippage_bps=50),
    SyntheticCrashScenario("SOXX -25%", "SOXX", -25, 10, iv_shock_pct=90, bid_ask_widen_pct=120, slippage_bps=60),
    SyntheticCrashScenario("NVDA -30%", "NVDA", -30, 10, iv_shock_pct=100, bid_ask_widen_pct=150, slippage_bps=75),
]


class StressLab:
    def run_synthetic(self, portfolio: list[dict], scenario: SyntheticCrashScenario) -> dict:
        shocked = []
        for position in portfolio:
            if position.get("ticker") != scenario.symbol:
                continue
            planned = float(position.get("planned_risk", 0))
            theoretical = float(position.get("theoretical_max_loss", 0))
            shocked.append({
                "ticker": position["ticker"],
                "scenario": scenario.name,
                "estimated_planned_risk_pressure": planned * (1 + abs(scenario.price_shock_pct) / 100),
                "theoretical_max_loss": theoretical,
                "roll_watch": True,
                "slippage_bps": scenario.slippage_bps,
            })
        return {"scenario": scenario.name, "positions": shocked}
