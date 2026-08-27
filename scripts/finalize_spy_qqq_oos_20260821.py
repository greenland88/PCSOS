"""Run the frozen SPY/QQQ FINAL OOS evaluation once and persist eligibility."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
EXPECTED_CONFIG = "PCS-SPY-QQQ-OOS-FROZEN-20260821-V1"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stats(frame: pd.DataFrame, include_yearly: bool = True) -> dict:
    x = frame.dropna(subset=["pnl"]).sort_values("decision_date").copy()
    pnl = x["pnl"].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    curve = pnl.cumsum()
    drawdown = curve - curve.cummax()
    gross_loss = abs(float(losses.sum()))
    result = {
        "trade_count": int(len(x)),
        "expectancy": float(pnl.mean()) if len(pnl) else None,
        "profit_factor": float(wins.sum() / gross_loss) if gross_loss else None,
        "win_rate": float((pnl > 0).mean()) if len(pnl) else None,
        "stop_rate": float(x["stop"].mean()) if len(x) else None,
        "total_pnl": float(pnl.sum()) if len(pnl) else None,
        "average_winner": float(wins.mean()) if len(wins) else None,
        "average_loser": float(losses.mean()) if len(losses) else None,
        "worst_trade": float(pnl.min()) if len(pnl) else None,
        "max_drawdown": float(drawdown.min()) if len(pnl) else None,
        "tail_loss_count": int((pnl <= -200).sum()),
    }
    if include_yearly:
        result["yearly_breakdown"] = {str(int(year)): stats(group, include_yearly=False) for year, group in x.assign(year=pd.to_datetime(x.decision_date).dt.year).groupby("year")}
    return result


def classify(train: dict, validation: dict, final: dict) -> str:
    if any(s.get("trade_count", 0) < 30 for s in (train, validation, final)):
        return "INSUFFICIENT_DATA"
    if all((s["expectancy"] or 0) > 0 and (s["profit_factor"] or 0) >= 1.25 for s in (train, validation, final)):
        return "ROBUST_POSITIVE"
    if (validation["expectancy"] or 0) < 0 or (final["expectancy"] or 0) < 0:
        return "NEGATIVE"
    return "CONDITIONAL_POSITIVE"


def main() -> None:
    config = json.loads((ROOT / "immutable_oos_config.json").read_text())
    split_manifest = json.loads((ROOT / "split_manifest.json").read_text())
    if config["config_id"] != EXPECTED_CONFIG or config["final_oos_run"] or config["parameter_search"]:
        raise RuntimeError("Frozen OOS contract is invalid or already consumed")
    attestation_path = ROOT / "frozen_dependency_equivalence_attestation.json"
    if not attestation_path.exists():
        raise RuntimeError("Missing frozen dependency equivalence attestation")
    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    if attestation.get("status") != "PASS" or attestation.get("sealed_chain") != "PASS" or not attestation.get("zero_current_source_access"):
        raise RuntimeError("Frozen dependency attestation is not valid")
    if any(x.get("status") != "MATCH" for x in attestation.get("direct_dependencies", [])):
        raise RuntimeError("Direct runtime dependency changed")

    report = {"module": "spy_qqq_final_oos_eligibility", "version": "20260821.v1", "final_oos_used_for_tuning": False, "production_rules_changed": False, "tickers": {}}
    for ticker in ("SPY", "QQQ"):
        contracts = pd.read_parquet(ROOT / f"{ticker}_entry_contract_v2.parquet")
        lifecycle = pd.read_parquet(ROOT / f"{ticker}_lifecycle_marks.parquet")
        outcomes = pd.read_parquet(ROOT / f"{ticker}_train_validation_outcomes.parquet")
        if len(contracts) != int(contracts.candidate_id.nunique()) or not bool(contracts.lifecycle_complete.all()) or not bool(contracts.pit_status.eq("PIT_SAFE").all()):
            raise RuntimeError(f"{ticker}: frozen artifact integrity check failed")
        splits = {}
        for split in split_manifest["splits"][ticker]:
            mask = pd.to_datetime(outcomes.decision_date).between(split["start"], split["end"])
            subset = outcomes.loc[mask]
            if len(subset) != split["candidate_count"]:
                raise RuntimeError(f"{ticker}: split count mismatch for {split['name']}")
            splits[split["name"]] = stats(subset)
            splits[split["name"]]["candidate_count"] = int(len(subset))
            splits[split["name"]]["date_range"] = {"start": split["start"], "end": split["end"]}
        classification = classify(splits["TRAIN"], splits["VALIDATION"], splits["FINAL_OOS"])
        report["tickers"][ticker] = {"candidate_count": int(len(contracts)), "entry_contract": "PASS", "lifecycle": "PASS", "pit": "PASS", "validation": splits["VALIDATION"], "splits": splits, "final_classification": classification, "baseline_eligibility": "YES" if classification == "ROBUST_POSITIVE" else "NO" if classification == "NEGATIVE" else "INSUFFICIENT", "pcs_supported_ticker": classification == "ROBUST_POSITIVE", "production_ready": False, "ready_for_production_consideration": classification in {"ROBUST_POSITIVE", "CONDITIONAL_POSITIVE"}, "lifecycle_rows": int(len(lifecycle))}
    report["index_baseline_system_verdict"] = "MIXED" if {x["final_classification"] for x in report["tickers"].values()} == {"ROBUST_POSITIVE", "NEGATIVE"} else "SUPPORTED" if all(x["final_classification"] == "ROBUST_POSITIVE" for x in report["tickers"].values()) else "NOT_SUPPORTED"
    report["generic_blocker"] = "NONE"
    (ROOT / "final_oos_eligibility.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
