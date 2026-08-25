"""Adapter from Phase 0 lifecycle marks to the approved base replay helper."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import pandas as pd

from pcs.research.variant_b_replay import ReplayPolicy, _replay_lifecycle_batch


class LifecycleAdapterError(RuntimeError):
    pass


@dataclass
class Stage4ALifecycleReplayAdapter:
    lifecycle: pd.DataFrame
    policy: ReplayPolicy = ReplayPolicy()

    @classmethod
    def from_phase0(cls, path: str | Path = "research_outputs/phase0_20260820/lifecycle_marks.parquet", policy: ReplayPolicy | None = None):
        p = Path(path)
        if not p.exists():
            raise LifecycleAdapterError("LIFECYCLE_ARTIFACT_MISSING")
        return cls(pd.read_parquet(p), policy or ReplayPolicy())

    def __post_init__(self):
        required = {"ticker", "candidate_id", "mark_date", "expiration", "short_strike", "long_strike", "short_bid", "short_ask", "long_bid", "long_ask"}
        if not required.issubset(self.lifecycle.columns):
            raise LifecycleAdapterError("LIFECYCLE_ARTIFACT_MISSING")
        d = self.lifecycle.copy(); d["mark_date"] = pd.to_datetime(d.mark_date, errors="coerce"); d["expiration"] = pd.to_datetime(d.expiration, errors="coerce")
        if d[["mark_date", "expiration"]].isna().any().any() or (d.mark_date > d.expiration).any():
            raise LifecycleAdapterError("INVALID_LIFECYCLE_ORDER")
        if d.duplicated(["candidate_id", "mark_date"]).any():
            raise LifecycleAdapterError("LIFECYCLE_DUPLICATE_IDENTITY")
        self.lifecycle = d.sort_values(["candidate_id", "mark_date"]).copy()

    def __call__(self, candidate: dict[str, Any]) -> dict[str, Any]:
        cid = str(candidate["candidate_id"])
        rows = self.lifecycle[self.lifecycle.candidate_id.astype(str).eq(cid)].copy()
        if rows.empty:
            raise LifecycleAdapterError("CANDIDATE_LIFECYCLE_IDENTITY_MISSING")
        expected = (str(candidate["ticker"]), pd.Timestamp(candidate["expiration"]).normalize(), float(candidate["short_strike"]), float(candidate["long_strike"]))
        actual = (str(rows.iloc[0].ticker), pd.Timestamp(rows.iloc[0].expiration).normalize(), float(rows.iloc[0].short_strike), float(rows.iloc[0].long_strike))
        if actual != expected:
            raise LifecycleAdapterError("CANDIDATE_LIFECYCLE_IDENTITY_MISSING")
        short = rows[["mark_date", "short_bid", "short_ask"]].rename(columns={"mark_date":"Trade Date", "short_bid":"Bid Price", "short_ask":"Ask Price"})
        long = rows[["mark_date", "long_bid", "long_ask"]].rename(columns={"mark_date":"Trade Date", "long_bid":"Bid Price", "long_ask":"Ask Price"})
        short["Trade Date"] = pd.to_datetime(short["Trade Date"]); long["Trade Date"] = pd.to_datetime(long["Trade Date"])
        quote_index = {(expected[1], expected[2]): short, (expected[1], expected[3]): long}
        try:
            result = _replay_lifecycle_batch({"date": pd.Timestamp(candidate["date"]), "expiration": expected[1], "short_strike": expected[2], "long_strike": expected[3], "credit": float(candidate["initial_credit"])}, quote_index, self.policy)
        except Exception as exc:
            raise LifecycleAdapterError("BASE_REPLAY_FAILURE") from exc
        if result.get("status") == "COMPLETE" and result.get("exit_date") is None:
            raise LifecycleAdapterError("EXIT_RESULT_INVALID")
        result.update({"candidate_id": cid, "ticker": candidate["ticker"], "opened": True,
                       "entry_date": pd.Timestamp(candidate["date"]), "expiration_date": expected[1],
                       "initial_credit": float(candidate["initial_credit"]),
                       "holding_calendar_days": (pd.Timestamp(result["exit_date"]) - pd.Timestamp(candidate["date"])).days if result.get("exit_date") is not None else None,
                       "holding_trading_days": int(len(pd.bdate_range(pd.Timestamp(candidate["date"]), pd.Timestamp(result["exit_date"])))) if result.get("exit_date") is not None else None,
                       "stopped": bool(result.get("stop_triggered", False)), "expired": result.get("exit_reason") == "TIME_EXIT",
                       "mfe": result.get("mfe"), "mae": result.get("mae"), "lifecycle_observation_count": result.get("mark_count")})
        return result
