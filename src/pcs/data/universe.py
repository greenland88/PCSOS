from __future__ import annotations
from pathlib import Path
import yaml

DEFAULT_UNIVERSE_PATH = Path("config/market_universe.yaml")

def _symbols(value, group):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"universe group {group!r} must be a non-empty string list")
    result = []
    for symbol in value:
        symbol = symbol.strip().upper()
        if symbol not in result: result.append(symbol)
    return result

def load_market_universe(groups=None, path=DEFAULT_UNIVERSE_PATH):
    groups = ["benchmarks", "pcs_universe"] if groups is None else list(groups)
    if groups == ["default"]: groups = ["benchmarks", "pcs_universe"]
    try: payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except Exception as exc: raise ValueError(f"cannot load market universe: {exc}") from exc
    if not isinstance(payload, dict): raise ValueError("market universe YAML must contain a mapping")
    result = []
    for group in groups:
        if group not in payload: raise ValueError(f"unknown universe group: {group}")
        for symbol in _symbols(payload[group], group):
            if symbol not in result: result.append(symbol)
    return result

def merge_symbols(universe=None, explicit_symbols=None, portfolio_symbols=None):
    result = []
    for symbol in list(universe or []) + list(portfolio_symbols or []) + list(explicit_symbols or []):
        if not isinstance(symbol, str) or not symbol.strip(): raise ValueError("symbols must be non-empty strings")
        symbol = symbol.strip().upper()
        if symbol not in result: result.append(symbol)
    return result
