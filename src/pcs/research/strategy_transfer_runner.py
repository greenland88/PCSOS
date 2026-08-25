"""Generic research-only exact strategy transfer runner."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import json, hashlib
import pandas as pd
from pcs.data.access import PCSDataAccess, DataAccessError, DataQualityError
from pcs.strategies.research_templates.catalog import get_strategy

@dataclass(frozen=True)
class TransferRequest:
    strategy_id: str
    ticker: str
    train_start: str
    train_end: str

def _transfer_identity(access: PCSDataAccess, spec: Any, ticker: str,
                       train_start: Any, train_end: Any) -> dict[str, Any]:
    """Return the immutable input identity for a descriptive transfer."""
    sources = {}
    for dependency in spec.data_dependencies:
        source = access.resolve_source(dependency, ticker, train_start, train_end)
        sources[dependency] = source.source_version
    code_path = Path(__file__).resolve()
    spec_payload = spec.to_dict()
    payload = {
        "strategy_id": spec.strategy_id,
        "strategy_spec": spec_payload,
        "ticker": ticker,
        "data_dependencies": sorted(spec.data_dependencies),
        "source_identities": sources,
        "train_start": str(pd.Timestamp(train_start).normalize().date()),
        "train_end": str(pd.Timestamp(train_end).normalize().date()),
        "runner_code_sha256": hashlib.sha256(code_path.read_bytes()).hexdigest(),
        "pit_feature_version": "strategy_transfer_runner.features.v1",
    }
    payload["identity_sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return payload

def _features(d: pd.DataFrame) -> pd.DataFrame:
    x = d.sort_values("date").copy(); c = pd.to_numeric(x["close"])
    x["sma200"] = c.rolling(200, min_periods=200).mean(); x["sma50"] = c.rolling(50, min_periods=50).mean()
    x["ret5"] = c.pct_change(5); x["ret10"] = c.pct_change(10); x["ret20"] = c.pct_change(20)
    x["drawdown60"] = c / c.rolling(60, min_periods=60).max() - 1
    if "volume" in x: x["volume_relative_to_20d_mean"] = x.volume / x.volume.rolling(20, min_periods=20).mean()
    else: x["volume_relative_to_20d_mean"] = pd.NA
    atr = (x.high - x.low).rolling(14, min_periods=14).mean()
    x["close_sma50_atr"] = (c - x.sma50) / atr
    x["prior_close_sma50_atr"] = x.close_sma50_atr.shift(1)
    return x

def _validate_transfer_daily(access: PCSDataAccess, daily: pd.DataFrame, ticker: str,
                             train_start: Any, train_end: Any) -> None:
    """Validate warmup and execution windows separately.

    Warmup rows are required for PIT indicators and are therefore expected to
    precede ``train_start``. Passing the full frame to the requested-window
    coverage validator incorrectly rejected valid warmup data.
    """
    access.validate_schema(daily, "daily")
    dates = pd.to_datetime(daily["date"]).dt.normalize()
    execution = daily.loc[dates.between(pd.Timestamp(train_start).normalize(), pd.Timestamp(train_end).normalize())]
    access.validate_coverage(execution, ticker, train_start, train_end, "date")
    if execution.empty:
        raise DataQualityError("daily execution window empty")

def run_transfer(request: TransferRequest, *, data_access: PCSDataAccess | None = None, output_dir: str | Path | None = None) -> dict[str, Any]:
    spec = get_strategy(request.strategy_id); ticker = request.ticker.upper(); access = data_access or PCSDataAccess()
    try:
        transfer_identity = _transfer_identity(access, spec, ticker, request.train_start, request.train_end)
        requested_start = pd.Timestamp(request.train_start)
        warmup_start = requested_start - pd.Timedelta(days=320)
        daily = access.read_prices(ticker, warmup_start, request.train_end)
        _validate_transfer_daily(access, daily, ticker, request.train_start, request.train_end)
        if daily.empty or daily.date.duplicated().any(): raise DataQualityError("daily duplicate keys or empty coverage")
        dependencies = set(spec.data_dependencies)
        if "options" in dependencies:
            options = access.read_quotes(ticker, request.train_start, request.train_end)
            access.validate_schema(options, "options")
    except (OSError, ValueError, DataAccessError, DataQualityError) as exc:
        return {"module":"pcs.research.strategy_transfer_runner","status":"DATA_BLOCKED","reason_codes":["CANONICAL_DATA_INVALID"],"ticker":ticker,"strategy_id":request.strategy_id,"error":str(exc)}
    frame = _features(daily); frame = frame[frame.date >= requested_start].copy(); dates=[]; evaluations=[]
    for _, row in frame.iterrows():
        ev = spec.evaluate(ticker, row.date, row.to_dict()); evaluations.append(asdict(ev))
        if ev.status == "QUALIFY": dates.append(pd.Timestamp(row.date))
    qualifying = pd.Series(sorted(set(dates)))
    episodes=[]
    if len(qualifying):
        sessions = pd.DatetimeIndex(frame.date).normalize()
        positions = {d: i for i, d in enumerate(sessions)}
        breaks = qualifying.map(lambda d: positions.get(pd.Timestamp(d).normalize(), -999)).diff().fillna(999).ne(1).cumsum()
        episodes = [{"episode_id":int(i),"qualifying_dates":[d.date().isoformat() for d in g]} for i,g in qualifying.groupby(breaks)]
    result = {"module":"pcs.research.strategy_transfer_runner","version":"v1","status":"COMPLETED_DESCRIPTIVE","strategy_id":request.strategy_id,"ticker":ticker,"train_start":request.train_start,"train_end":request.train_end,"data_dependencies":sorted(spec.data_dependencies),"transfer_identity":transfer_identity,"qualifying_dates":[d.date().isoformat() for d in qualifying],"independent_episodes":episodes,"executable_episodes":[],"trades":[],"performance":{},"reason_codes":["EXACT_TRANSFER","PIT_FEATURES","CANONICAL_DATA_VALIDATED","DECLARED_DATA_DEPENDENCIES","COMPOSITE_TRANSFER_IDENTITY","CONTRACT_LIFECYCLE_DELEGATED"]}
    if output_dir:
        out=Path(output_dir); out.mkdir(parents=True, exist_ok=True); (out/"transfer_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result

__all__ = ["TransferRequest", "run_transfer"]
