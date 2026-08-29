"""Codex hood_trader to the existing PCS read-only production boundary."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hood_snapshot import generate_hoodtrader_snapshot
from .hood_trader_provider import HoodTraderProvider, JsonHoodClient
from .covered_call_live import BrokerCoveredCallLiveAdapter
from pcs.covered_call_production import decide_nvda_call_today


def decide_nvda_call_from_hood_trader(hood_trader: Any, *, as_of: str | None = None,
                                      output_path: str | Path = "data/live/hoodtrader_snapshot.json",
                                      event_risk_provider: Any = None) -> dict[str, Any]:
    """Collect a fresh connector snapshot and execute PCS decision logic only."""
    observed = as_of or datetime.now(timezone.utc).isoformat()
    try:
        snapshot = generate_hoodtrader_snapshot(
            hood_trader, ["NVDA"], observed, output_path=output_path,
            source="ROBINHOOD_HOOD_TRADER", event_risk_provider=event_risk_provider)
        provider = BrokerCoveredCallLiveAdapter(HoodTraderProvider(JsonHoodClient(snapshot)))
        return decide_nvda_call_today(provider, as_of=observed)
    except Exception:
        # Connector failures are deliberately converted to the production
        # fail-closed vocabulary; no research snapshot is consulted.
        return {"module": "pcs.codex_hood_bridge", "version": "1.0",
                "symbol": "NVDA", "as_of": observed, "action": "WAIT",
                "reason_codes": ["LIVE_DATA_UNAVAILABLE"],
                "data_source": "ROBINHOOD_HOOD_TRADER"}
