"""Build the resumable queue for invalid replay artifact rebuilds."""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "research_outputs/global_cardinality_audit_20260825.csv"
OUT = ROOT / "research_outputs/system_integrity/invalid_artifact_rebuild_queue.csv"

PRIORITY = {"NVDA": "P0", "AMD": "P0", "META": "P0", "COST": "P1", "MSFT": "P1", "AMZN": "P1", "TSLA": "P2"}
NAMES = {
    "amd_current_strategy_replay_train_plumbing": "AMD current strategy",
    "amd_full_clean_qqq_controlled_reset": "AMD Controlled Reset",
    "amd_full_clean_h006_recovery_stabilization": "AMD Recovery Stabilization",
    "amd_full_clean_h016_sma50_reclaim": "AMD SMA50 Reclaim",
    "amzn_current_strategy_replay_active_options_v2_20260824": "AMZN current strategy",
    "msft_current_strategy_replay_active_options_v2_20260824": "MSFT current strategy",
    "tsla_current_strategy_replay_train_plumbing": "TSLA current strategy",
    "meta_global_quality_replay": "META global quality",
}


def name(path: str, ticker: str) -> str:
    key = Path(path).name
    if key in NAMES:
        return NAMES[key]
    if "general_pcs" in path:
        return f"{ticker} general PCS replay"
    if "h010" in path.lower():
        return "NVDA Trend Continuation"
    if "h027" in path.lower():
        return "NVDA Constructive Recovery"
    if "cost_frozen_sma50" in path:
        return "COST SMA50 Reclaim"
    if "cost_pcs_discovery" in path:
        return "COST broad replay"
    if "vda_" in path.lower() or "nvda_" in path.lower():
        return "NVDA replay"
    return f"{ticker} replay"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open(newline="", encoding="utf-8") as src:
        rows = list(csv.DictReader(src))
    invalid = [r for r in rows if r.get("status") == "INVALID_REPLAY_ARTIFACT"]
    out = []
    for r in invalid:
        ticker = r["ticker"]
        artifact = r["artifact"]
        adaptive = "adaptive" in artifact.lower()
        out.append({
            "ticker": ticker,
            "strategy_name": name(artifact, ticker),
            "legacy_artifact_path": artifact,
            "legacy_status": "INVALID_REPLAY_ARTIFACT",
            "raw_signal_days": r.get("raw_signals", ""),
            "reconstructed_independent_episodes": r.get("independent_episodes", ""),
            "legacy_selected_trades": r.get("selected_trades", ""),
            "legacy_completed_lifecycles": r.get("lifecycles", ""),
            "max_trades_per_episode": r.get("max_trades_per_episode", ""),
            "violation_type": r.get("reasons", ""),
            "canonical_route": "PER_TICKER_CANONICAL",
            "readiness_status": "CHECK_REQUIRED",
            "rebuild_priority": PRIORITY.get(ticker, "P2"),
            "rebuild_status": "QUEUED_ADAPTIVE_BLOCKED" if adaptive else "QUEUED",
            "new_artifact_path": "",
        })
    fields = list(out[0]) if out else ["ticker", "strategy_name"]
    with OUT.open("w", newline="", encoding="utf-8") as dst:
        writer = csv.DictWriter(dst, fieldnames=fields)
        writer.writeheader(); writer.writerows(sorted(out, key=lambda x: (x["rebuild_priority"], x["ticker"], x["strategy_name"])))
    print(f"queued={len(out)} output={OUT}")


if __name__ == "__main__":
    main()
