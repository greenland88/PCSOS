"""Research-only reusable PCS family definitions.

These definitions are a test battery for new tickers, not production rules or
automatic trade instructions. Numeric implementations remain tied to their
discovery ticker until direct transfer evidence is established.
"""
from __future__ import annotations
from dataclasses import asdict, dataclass
from typing import Any

@dataclass(frozen=True)
class ResearchRuleFamily:
    rule_family_id: str
    human_name: str
    description: str
    structural_logic: str
    pit_feature_requirements: tuple[str, ...]
    reference_implementation: dict[str, Any]
    discovery_ticker: str
    discovery_version: str
    evidence_status: str
    validation_status: str
    transfer_status_by_ticker: dict[str, str]

RULE_FAMILIES = {
    "PCS_TREND_CONTINUATION": ResearchRuleFamily(
        "PCS_TREND_CONTINUATION", "NVDA Trend Continuation PCS",
        "Constructive long-term trend with healthy participation and positive short-term momentum.",
        "close > SMA200 AND volume_rel20 > 1 AND return_5d > 0",
        ("close", "SMA200", "volume_rel20", "return_5d"),
        {"close_vs_sma200": ">0", "volume_rel20": ">1", "return_5d": ">0"},
        "NVDA", "V2_H010", "FROZEN_RESEARCH_CANDIDATE", "VALIDATED_ON_NVDA", {"NVDA": "VALIDATED", "QQQ": "NO_TRANSFER", "AMZN": "INSUFFICIENT_DATA", "AMD": "NO_TRANSFER", "DEFAULT": "TRANSFER_TESTING_REQUIRED"}),
    "PCS_CONSTRUCTIVE_RECOVERY": ResearchRuleFamily(
        "PCS_CONSTRUCTIVE_RECOVERY", "NVDA Constructive Recovery PCS",
        "Long-term constructive structure after medium-term weakness with a positive short-term recovery transition.",
        "close > SMA200 AND return_20d < 0 AND return_5d > 0",
        ("close", "SMA200", "return_20d", "return_5d"),
        {"close_vs_sma200": ">0", "return_20d": "<0", "return_5d": ">0"},
        "NVDA", "V2_H027", "FROZEN_RESEARCH_CANDIDATE", "INSUFFICIENT_VALIDATION_SAMPLE", {"NVDA": "INSUFFICIENT_EVIDENCE", "QQQ": "NO_TRANSFER", "AMZN": "INSUFFICIENT_DATA", "AMD": "NO_TRANSFER", "DEFAULT": "TRANSFER_TESTING_REQUIRED"}),
}

def get_rule_family(rule_family_id: str) -> ResearchRuleFamily:
    try: return RULE_FAMILIES[rule_family_id]
    except KeyError as exc: raise KeyError(f"UNKNOWN_RESEARCH_RULE_FAMILY:{rule_family_id}") from exc

def family_battery(ticker: str) -> list[dict[str, Any]]:
    """Return the frozen research battery for a new ticker."""
    return [{**asdict(f), "target_ticker": str(ticker).upper(), "transfer_status": f.transfer_status_by_ticker.get(str(ticker).upper(), f.transfer_status_by_ticker.get("DEFAULT"))} for f in RULE_FAMILIES.values()]
