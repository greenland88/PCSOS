"""Versioned adaptive configs used by normal research replays.

`adaptive_profiles` remains the explicit recalibration tool.  This module is
the read-only boundary used by replay code; it never derives parameters from
the replay input.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import hashlib
import json
import yaml

from .adaptive_profiles import ResolvedStrategyConfig, TickerCharacteristics

DEFAULT_ROOT = Path("config/strategies/adaptive")


def load_frozen_strategy_config(ticker: str, *, config_root: str | Path = DEFAULT_ROOT) -> ResolvedStrategyConfig:
    root = Path(config_root)
    requested = str(ticker).lower()
    path = root / f"{requested}.yaml"
    if not path.is_file() and root.is_dir():
        matches = [candidate for candidate in root.iterdir()
                   if candidate.is_file() and candidate.suffix.lower() == ".yaml"
                   and candidate.stem.lower() == requested]
        if len(matches) == 1:
            path = matches[0]
    if not path.is_file():
        raise FileNotFoundError(f"FROZEN_ADAPTIVE_CONFIG_NOT_FOUND:{str(ticker).upper()}")
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("status") != "FROZEN":
        raise ValueError(f"ADAPTIVE_CONFIG_NOT_FROZEN:{path}")
    if str(raw.get("ticker", "")).upper() != str(ticker).upper():
        raise ValueError(f"ADAPTIVE_CONFIG_TICKER_MISMATCH:{path}")
    payload = dict(raw)
    payload.pop("status", None)
    payload.pop("config_id", None)
    stored_hash = raw.get("config_sha256")
    payload.pop("config_sha256", None)
    payload["characteristics"] = TickerCharacteristics(**payload["characteristics"])
    config = ResolvedStrategyConfig(**payload)
    if not stored_hash or str(stored_hash) != config_sha256(config):
        raise ValueError(f"FROZEN_CONFIG_INTEGRITY_FAILURE:{path}")
    return config


def frozen_config_dict(config: ResolvedStrategyConfig) -> dict[str, Any]:
    return asdict(config)


def config_sha256(config: ResolvedStrategyConfig) -> str:
    data = json.dumps(frozen_config_dict(config), sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(data).hexdigest()


__all__ = ["load_frozen_strategy_config", "frozen_config_dict", "config_sha256"]
