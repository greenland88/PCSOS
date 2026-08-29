"""Read-only runtime-to-snapshot bridge for HoodTrader-compatible clients."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import os
import uuid
from typing import Any, Iterable

from .hood_trader_provider import HoodTraderSnapshot


def _ids(chain: Iterable[Any]) -> list[str]:
    result = []
    for item in chain:
        if isinstance(item, dict):
            value = item.get("option_id", item.get("id", item.get("symbol")))
        else:
            value = getattr(item, "option_id", getattr(item, "id", getattr(item, "symbol", None)))
        if value is not None:
            result.append(str(value))
    return result


def generate_hoodtrader_snapshot(client: Any, symbols: Iterable[str], as_of: str,
                                 *, output_path: str | Path | None = None,
                                 max_age_seconds: int = 300,
                                 source: str = "ROBINHOOD_HOOD_TRADER",
                                 event_risk_provider: Any = None) -> HoodTraderSnapshot:
    """Collect facts only; selection, sizing, gates and decisions remain downstream."""
    symbols = [str(s).upper() for s in symbols]
    captured = datetime.now(timezone.utc).isoformat()
    timestamps: dict[str, str] = {}

    def collect(name, fn, *args):
        value = fn(*args)
        if value is None:
            raise RuntimeError(f"SNAPSHOT_COMPONENT_UNAVAILABLE:{name}")
        timestamps[name] = captured
        return value

    accounts = collect("accounts", client.get_accounts)
    positions = collect("positions", client.get_positions)
    portfolio = collect("portfolio", client.get_portfolio)
    equity_quotes = {s: collect(f"equity_quote:{s}", client.get_equity_quote, s) for s in symbols}
    chains = {s: collect(f"option_chain:{s}", client.get_option_chain, s) for s in symbols}
    quote_map = {}
    for symbol, chain in chains.items():
        quotes = collect(f"option_quotes:{symbol}", client.get_option_quotes, _ids(chain))
        quote_map[symbol] = quotes
    event_risk = {}
    event_getter = getattr(client, "get_event_risk", None) or event_risk_provider
    if event_getter:
        for symbol in symbols:
            value = event_getter(symbol)
            if value is not None:
                event_risk[symbol] = value
                timestamps[f"event_risk:{symbol}"] = captured

    snapshot = HoodTraderSnapshot(as_of=str(as_of), accounts=accounts,
        equity_quotes=equity_quotes,
        equity_positions=[p for p in positions if str(p.get("asset_type", "EQUITY")).upper() in {"EQUITY", "STOCK"}] if positions and isinstance(positions[0], dict) else positions,
        option_positions=[p for p in positions if str(p.get("asset_type", "")).upper() in {"OPTION", "CALL", "PUT"}] if positions and isinstance(positions[0], dict) else [],
        option_chains=chains, option_quotes=quote_map, portfolio=portfolio, event_risk=event_risk)
    if output_path is not None:
        write_hoodtrader_snapshot(snapshot, output_path, source=source,
                                  captured_at=captured, timestamps=timestamps,
                                  max_age_seconds=max_age_seconds)
    return snapshot


def write_hoodtrader_snapshot(snapshot: HoodTraderSnapshot, output_path: str | Path,
                              *, source: str, captured_at: str,
                              timestamps: dict[str, str], max_age_seconds: int = 300) -> Path:
    """Atomically persist a validated snapshot; failures leave no partial target."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot.to_dict()
    payload.update({"source": source, "schema_version": "1.0",
                    "snapshot_status": "VALID",
                    "freshness": {"captured_at": captured_at, "max_age_seconds": max_age_seconds,
                                   "component_timestamps": timestamps}})
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        with temp.open("r+b") as handle:
            os.fsync(handle.fileno())
        temp.replace(path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return path
