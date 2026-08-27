"""Descriptive AMD BREAKDOWN_RUN / OBSERVATION_WINDOW audit.

Reads only the canonical PIT state timeline. It never reads P&L, contract
outcomes, VALIDATION, or FINAL OOS artifacts and never executes a signal.
"""
from __future__ import annotations
import json
import os
import shutil
import tempfile
import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pcs.research.runner import ResearchRunner

OFFSETS = (1, 3, 5, 10)
RECOVERY_STATES = {"stabilizing": "STABILIZING", "pullback_in_uptrend": "PULLBACK_IN_UPTREND", "uptrend": "UPTREND"}


def _runs(timeline: pd.DataFrame) -> list[tuple[int, int]]:
    """Maximal contiguous BREAKDOWN runs in trading-row order."""
    mask = timeline.final_underlying_state.eq("BREAKDOWN").tolist()
    runs, start = [], None
    for index, is_breakdown in enumerate(mask + [False]):
        if is_breakdown and start is None:
            start = index
        elif not is_breakdown and start is not None:
            runs.append((start, index - 1))
            start = None
    return runs


def _first_state(rows: pd.DataFrame, state: str):
    found = rows[rows.final_underlying_state.eq(state)]
    return None if found.empty else pd.Timestamp(found.iloc[0].date).date().isoformat()


def _support_status(timeline: pd.DataFrame) -> str:
    required = {"support_level", "support_first_usable_date", "recovery_reclaim_result"}
    if not required.issubset(timeline.columns):
        return "PIT_FEATURE_MISSING"
    return "PIT_FEATURE_MISSING"


def build_report(timeline: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    timeline = timeline.sort_values("date").reset_index(drop=True).copy()
    timeline["date"] = pd.to_datetime(timeline.date).dt.normalize()
    runs = _runs(timeline)
    support_status = _support_status(timeline)
    rows = []
    for episode_number, (start, end) in enumerate(runs, 1):
        after = timeline.iloc[end + 1:]
        next_breakdown = after.index[after.final_underlying_state.eq("BREAKDOWN")]
        observation_end = int(next_breakdown[0]) if len(next_breakdown) else len(timeline)
        observation = timeline.iloc[end + 1:observation_end]
        row = {
            "episode_id": f"AMD-BREAKDOWN-RUN-{episode_number:04d}",
            "breakdown_start": timeline.iloc[start].date.date().isoformat(),
            "breakdown_end": timeline.iloc[end].date.date().isoformat(),
            "breakdown_duration": end - start + 1,
            "prior_state": None if start == 0 else timeline.iloc[start - 1].final_underlying_state,
            "prior_support_reclaimed_date": None,
            "support_reclaim_status": support_status,
            "censored": end + max(OFFSETS) >= len(timeline),
            "unknown_state_in_observation_window": bool(observation.final_underlying_state.eq("UNKNOWN").any()),
            "status": "RECOVERY_PREDICATE_NOT_DEFINED",
        }
        for offset in OFFSETS:
            index = end + offset
            row[f"state_after_{offset}d"] = timeline.iloc[index].final_underlying_state if index < len(timeline) else None
        for name, state in RECOVERY_STATES.items():
            row[f"first_{name}_date"] = _first_state(observation, state)
        rows.append(row)
    episodes = pd.DataFrame(rows)
    true_count = len(episodes)
    censored_count = int(episodes.censored.sum())
    invariant_violations = int(episodes.state_after_1d.eq("BREAKDOWN").sum())
    eligible = true_count - censored_count
    signal_counts = {}
    for name in RECOVERY_STATES:
        col = f"first_{name}_date"
        actual = int(episodes[col].notna().sum())
        missing = int((episodes[col].isna() & episodes.unknown_state_in_observation_window & ~episodes.censored).sum())
        signal_counts[name] = {
            "eligible_episode_count": eligible,
            "actual_signal_count": actual,
            "missing_feature_count": missing,
            "censored_count": censored_count,
            "no_signal_count": int(eligible - actual - missing),
        }
    transitions = {}
    for offset in OFFSETS:
        column = f"state_after_{offset}d"
        transitions[column] = {str(k): int(v) for k, v in episodes[column].value_counts(dropna=False).items()}
    summary = {
        "data_source": "PCS_CANONICAL_DATA",
        "true_breakdown_run_count": true_count,
        "population_semantics": "MAXIMAL_CONTIGUOUS_BREAKDOWN_RUNS",
        "by_year": {str(k): int(v) for k, v in episodes.breakdown_start.str[:4].value_counts().sort_index().items()},
        "diagnostic_only_years": ["2026"],
        "duration_distribution": {str(k): int(v) for k, v in episodes.breakdown_duration.value_counts().sort_index().items()},
        "transition_counts_anchored_at_run_end": transitions,
        "run_end_invariant_breakdown_after_1d_count": invariant_violations,
        "recovery_signal_counts": signal_counts,
        "support_availability_funnel": {"status": support_status, "classification": "SUPPORT_FEATURE_UNAVAILABLE",
            "eligible_episode_count": eligible,
            "actual_signal_count": 0, "missing_feature_count": true_count,
            "censored_count": censored_count, "no_signal_count": 0},
        "signal_execution": "NOT_RUN", "final_oos_read": False,
    }
    return episodes, summary


def main() -> None:
    runner = ResearchRunner.from_path(ROOT / "config/research/templates/new_entry.yaml")
    runner.real_preflight()
    timeline = pd.read_parquet(runner.output_dir / "pit_state_timeline.parquet")
    episodes, summary = build_report(timeline)
    parent = runner.output_dir
    temp_root = Path(tempfile.mkdtemp(prefix="state_transition_report.", dir=parent))
    output = parent / "state_transition_report"
    try:
        episodes.to_csv(temp_root / "breakdown_runs.csv", index=False)
        (temp_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if len(pd.read_csv(temp_root / "breakdown_runs.csv")) != len(episodes):
            raise RuntimeError("ARTIFACT_VALIDATION_FAILED")
        backup = parent / ".state_transition_report.previous"
        if backup.exists():
            shutil.rmtree(backup)
        if output.exists():
            os.replace(output, backup)
        os.replace(temp_root, output)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        raise
    data_version = str(timeline.data_version.iloc[0]) if "data_version" in timeline.columns else "UNKNOWN"
    runner.write_artifact_manifest([
        "preflight.json", "pit_state_timeline.parquet", "state_transition_report/breakdown_runs.csv",
        "state_transition_report/summary.json"], data_version=data_version,
        population_semantics="MAXIMAL_CONTIGUOUS_BREAKDOWN_RUNS")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
