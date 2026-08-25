"""Descriptive AMD recovery discovery from the canonical clean dataset only."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "research_outputs" / "amd_canonical_test_dataset" / "amd_master_daily_research.parquet"
OUT = ROOT / "research_outputs" / "amd_clean_recovery_research_v1"


def episodes(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame[(frame.boundary == "TRAIN") & frame.testable_day].sort_values("date").reset_index(drop=True)
    b = x.breakdown_result.eq("PASS")
    starts = np.where(b & ~b.shift(1, fill_value=False))[0]
    ends = np.where(b & ~b.shift(-1, fill_value=False))[0]
    rows = []
    for n, (s, e) in enumerate(zip(starts, ends), 1):
        rows.append({"episode_id": f"AMD-CLEAN-BREAKDOWN-{n:03d}", "start_index": int(s), "end_index": int(e),
                     "breakdown_start": x.date.iloc[s], "breakdown_end": x.date.iloc[e],
                     "calendar_year": int(x.date.iloc[s].year), "breakdown_days": int(e - s + 1)})
    return pd.DataFrame(rows), x


def transition_rows(x: pd.DataFrame, eps: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ep in eps.to_dict("records"):
        r = {k: ep[k] for k in ["episode_id", "breakdown_start", "breakdown_end", "calendar_year", "breakdown_days"]}
        e = int(ep["end_index"])
        for h in (5, 10, 20, 30):
            j = e + h
            if j >= len(x):
                r[f"h{h}_status"] = "CENSORED"
                continue
            z = x.iloc[j]
            r[f"h{h}_status"] = "OBSERVED"
            for c in ["date", "final_underlying_state", "pullback_raw_state", "close", "sma20", "sma50", "sma200",
                      "sma20_slope", "sma50_slope", "sma200_slope", "ret5", "ret10", "ret20", "ret60", "drawdown60",
                      "atr14", "predictability_score"]:
                r[f"h{h}_{c}"] = z[c]
            r[f"h{h}_above_sma20"] = bool(pd.notna(z.sma20) and z.close > z.sma20)
            r[f"h{h}_above_sma50"] = bool(pd.notna(z.sma50) and z.close > z.sma50)
            r[f"h{h}_above_sma200"] = bool(pd.notna(z.sma200) and z.close > z.sma200)
        rows.append(r)
    return pd.DataFrame(rows)


def first_recovery_date(x: pd.DataFrame, ep: dict, kind: str) -> pd.Timestamp | None:
    for j in range(int(ep["end_index"]) + 1, min(int(ep["end_index"]) + 31, len(x))):
        z = x.iloc[j]
        if kind == "state":
            ok = z.final_underlying_state in {"PULLBACK_IN_UPTREND", "UPTREND", "STABILIZING"}
        else:
            ok = bool(pd.notna(z.sma20) and pd.notna(z.sma50) and z.close > z.sma20 and z.close > z.sma50)
        if ok:
            return pd.Timestamp(z.date)
    return None


def build() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    master = pd.read_parquet(IN)
    eps, train_clean = episodes(master)
    full_train = master[master.boundary == "TRAIN"].sort_values("date").reset_index(drop=True)
    full_b = full_train.breakdown_result.eq("PASS")
    full_starts = np.where(full_b & ~full_b.shift(1, fill_value=False))[0]
    full_ends = np.where(full_b & ~full_b.shift(-1, fill_value=False))[0]
    full_episode_count = len(full_starts)
    excluded_episode_count = sum(not bool(full_train.iloc[s:e + 1].testable_day.any()) for s, e in zip(full_starts, full_ends))
    trans = transition_rows(train_clean, eps)
    eps.to_csv(OUT / "clean_breakdown_episodes.csv", index=False)
    trans.to_parquet(OUT / "clean_breakdown_transitions.parquet", index=False)
    concept_rows = []
    for ep in eps.to_dict("records"):
        concept_rows.append({"episode_id": ep["episode_id"], "calendar_year": ep["calendar_year"],
                             "state_recovery_date": first_recovery_date(train_clean, ep, "state"),
                             "ma_repair_date": first_recovery_date(train_clean, ep, "ma")})
    dates = pd.DataFrame(concept_rows)
    dates.to_csv(OUT / "concept_signal_dates.csv", index=False)
    state_dates = sorted(pd.to_datetime(dates.state_recovery_date.dropna()).dt.strftime("%Y-%m-%d").unique().tolist())
    ma_dates = sorted(pd.to_datetime(dates.ma_repair_date.dropna()).dt.strftime("%Y-%m-%d").unique().tolist())
    definitions = {
        "module": "pcs.research.amd_clean_recovery_diagnostic", "version": "1.0", "data_source": "PCS_CANONICAL_DATA",
        "input": str(IN.relative_to(ROOT)), "date_boundary": {"start": "2020-01-02", "end": "2025-12-31", "2026_used_for_tuning": False},
        "clean_population": {"train_days": int(len(train_clean)), "clean_testable_days": int(train_clean.testable_day.sum()),
                              "breakdown_days": int(train_clean.breakdown_result.eq("PASS").sum()), "breakdown_episodes": int(len(eps)),
                              "episodes_by_year": eps.calendar_year.value_counts().sort_index().astype(int).to_dict()},
        "excluded": {"breakdown_days_not_testable": int(((master.boundary == "TRAIN") & (master.breakdown_result == "PASS") & ~master.testable_day).sum()),
                     "full_train_breakdown_episodes_before_clean_filter": int(full_episode_count),
                     "breakdown_episodes_excluded_for_no_testable_breakdown_day": int(excluded_episode_count),
                     "breakdown_episodes_excluded": "Episodes are built only from clean testable days; no excluded episode is silently mapped."},
        "descriptive_concepts": [
            {"concept_id": "AMD_STATE_RECOVERY_AFTER_BREAKDOWN_V1", "name": "State Recovery After Breakdown",
             "definition": "For each independent clean breakdown episode, use the first subsequent date through 30 trading days on which the existing AMD final_underlying_state is PULLBACK_IN_UPTREND, UPTREND, or STABILIZING.",
             "why": "Represents a state-machine transition away from active breakdown without importing QQQ labels or numeric thresholds.", "signal_dates": state_dates},
            {"concept_id": "AMD_PROGRESSIVE_PRICE_REPAIR_AFTER_BREAKDOWN_V1", "name": "Progressive Price Repair After Breakdown",
             "definition": "For each independent clean breakdown episode, use the first subsequent date through 30 trading days on which close is above both existing PIT SMA20 and PIT SMA50.",
             "why": "Represents broader price repair observed after the descriptive state transition; it is a research concept, not a production filter.", "signal_dates": ma_dates},
        ],
        "controls": {"strategy_definitions_changed_before_freeze": False, "production_rules_changed": False, "final_oos_touched": False},
    }
    (OUT / "frozen_concept_definitions.json").write_text(json.dumps(definitions, indent=2, default=str), encoding="utf-8")
    # ResearchSpec files freeze dates/definitions before canonical replay.
    base = {"ticker": "AMD", "research_mode": "NEW_ENTRY", "population_source": {"type": "ticker_daily_calendar", "clean_dataset": str(IN.relative_to(ROOT)), "frozen": False, "point_in_time": True}, "entry_date_rule": {"rule": "frozen_concept_signal_date", "entry_date_need_not_remain_breakdown": True}, "date_range": {"start": "2020-01-02", "end": "2025-12-31", "split": "TRAIN_ONLY"}, "split_policy": {"name": "TRAIN_ONLY", "train_end": "2025-12-31"}, "contract_selection_policy": {"mode": "AUTHORITATIVE_CURRENT_STRATEGY_SELECTOR", "width_priority": [5, 10, 2], "as_of_only": True}, "lifecycle_policy": {"source": "canonical_lifecycle_adapter", "no_future_selection": True}, "frozen_parameters": {}, "allowed_parameters": {"research_only": True}, "final_oos_access": False, "production_changes_allowed": False}
    for concept_id, dates_key in [("AMD_STATE_RECOVERY_AFTER_BREAKDOWN_V1", state_dates), ("AMD_PROGRESSIVE_PRICE_REPAIR_AFTER_BREAKDOWN_V1", ma_dates)]:
        spec = dict(base); spec.update({"research_id": concept_id.lower(), "hypothesis": concept_id, "signal_definition": {"concept_id": concept_id, "creates_new_entry_dates": True, "execution_dates": dates_key, "track_a_execution_only": True}})
        (OUT / f"{concept_id}.yaml").write_text("\n".join(f"{k}: {json.dumps(v, default=str)}" for k, v in spec.items()) + "\n", encoding="utf-8")
    return definitions


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, default=str))
