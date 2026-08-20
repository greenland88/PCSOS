"""Standardized, non-strategy ticker onboarding for historical option data.

The pipeline is intentionally blocked unless the historical TXT/ZIP source has
an exact-key overlap with ClickHouse.  All canonical writes go through
``PCSDataAccess``.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from .access import PCSDataAccess, DataQualityError
from .import_option_archives import OPTION_COLUMNS
from .storage_schema import OPTION_FIELDS

KEY = ["symbol", "trade_date", "expiration_date", "call_put", "strike"]
COMPARE = ["last", "bid", "ask", "bid_iv", "ask_iv", "open_interest", "volume"]
ARCHIVE_RE = re.compile(r"(?P<year>\d{4})_q(?P<quarter>[1-4])_option_chain_.*\.zip$", re.I)


@dataclass(frozen=True)
class OverlapResult:
    status: str
    overlap_rows: int
    mismatched_rows: int
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class OnboardingResult:
    symbol: str
    status: str
    periods: int
    rows_written: int
    provenance_records: int
    reason_codes: list[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self):
        return asdict(self)


def replay_onboarded_partition(access: PCSDataAccess, dataset: str, symbol: str, year: int, quarter: int, expected_rows: int) -> bool:
    """Replay the canonical partition through PCSDataAccess and verify identity."""
    frame = access.read_partition(dataset, symbol, f"year={year}/quarter={quarter}", f"{symbol}_{year}_q{quarter}.parquet")
    return len(frame) == expected_rows and not frame.duplicated(KEY).any()


@dataclass(frozen=True)
class ConflictPolicyResult:
    frame: pd.DataFrame
    exact_duplicates_removed: int
    conflicts_resolved: int
    conflicts_blocked: int


def apply_conflict_policy(txt: pd.DataFrame, clickhouse: pd.DataFrame) -> ConflictPolicyResult:
    """Apply ``VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW`` deterministically."""
    frame = txt[OPTION_FIELDS].copy()
    for data in (frame, clickhouse):
        if data is None or data.empty:
            continue
        data["symbol"] = data["symbol"].astype(str).str.upper()
        for col in ("trade_date", "expiration_date"):
            data[col] = pd.to_datetime(data[col], errors="coerce").dt.date
        data["call_put"] = data["call_put"].astype(str).str.lower()
        data["strike"] = pd.to_numeric(data["strike"], errors="coerce")
    exact = frame.drop_duplicates(subset=OPTION_FIELDS, keep="last")
    removed = len(frame) - len(exact)
    groups = exact.groupby(KEY, sort=False, dropna=False)
    if all(len(group) == 1 for _, group in groups):
        return ConflictPolicyResult(exact.reset_index(drop=True), removed, 0, 0)
    selected = []
    resolved = blocked = 0
    for _, group in groups:
        if len(group) == 1:
            selected.append(group.iloc[0]); continue
        selected.append(group.iloc[0]); resolved += 1
    return ConflictPolicyResult(pd.DataFrame(selected, columns=OPTION_FIELDS).reset_index(drop=True), removed, resolved, 0)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_rows(raw: bytes, symbol: str) -> pd.DataFrame:
    # Archive members are historical TXT rows without a reliable header.
    frame = pd.read_csv(io.BytesIO(raw), header=None, names=OPTION_COLUMNS, encoding="utf-8", on_bad_lines="error")
    if len(frame) and str(frame.iloc[0, 0]).strip().lower() in {"trade date", "tradedate"}:
        frame = frame.iloc[1:].reset_index(drop=True)
    frame = frame.rename(columns={
        "Trade Date": "trade_date", "Expiry Date": "expiration_date", "Strike": "strike",
        "Call/Put": "call_put", "Last Trade Price": "last", "Bid Price": "bid", "Ask Price": "ask",
        "Bid Implied Volatility": "bid_iv", "Ask Implied Volatility": "ask_iv", "Open Interest": "open_interest",
        "Volume": "volume", "Delta": "delta", "Gamma": "gamma", "Vega": "vega", "Theta": "theta", "Rho": "rho",
    })
    frame["symbol"] = str(symbol).strip().upper()
    for col in ("trade_date", "expiration_date"):
        frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
    for col in OPTION_FIELDS:
        if col not in frame:
            frame[col] = None
    for col in OPTION_FIELDS:
        if col not in KEY + ["symbol"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame[OPTION_FIELDS]


class HistoricalTxtZipAdapter:
    """Read one ticker/quarter from the approved historical TXT/ZIP root."""

    def __init__(self, root: str | Path = r"K:\BaiduNetdiskDownload\USDailyOptions"):
        self.root = Path(root)

    def _archive(self, year: int, quarter: int) -> Path:
        matches = [p for p in self.root.glob(f"{year}_q{quarter}_option_chain_*.zip") if ARCHIVE_RE.match(p.name)]
        if not matches:
            raise FileNotFoundError(f"historical archive unavailable for {year} Q{quarter}")
        return sorted(matches)[0]

    def read_period(self, symbol: str, year: int, quarter: int) -> tuple[pd.DataFrame, dict]:
        symbol = str(symbol).strip().upper()
        archive = self._archive(year, quarter)
        member = f"{symbol}_{year}_q{quarter}_option_chain.txt"
        with zipfile.ZipFile(archive) as zf:
            if member not in zf.namelist():
                raise FileNotFoundError(f"historical member unavailable: {member}")
            raw = zf.read(member)
        frame = _normalize_rows(raw, symbol)
        return frame, {"source": "historical_txt_zip", "source_path": str(archive), "source_member": member,
                       "source_sha256": _sha256_bytes(raw), "archive_sha256": _sha256_bytes(archive.read_bytes()),
                       "year": year, "quarter": quarter, "rows": len(frame)}


def validate_txt_clickhouse_overlap(txt: pd.DataFrame, clickhouse: pd.DataFrame) -> OverlapResult:
    """Mandatory exact-contract overlap check; quote differences are audited."""
    if clickhouse is None or clickhouse.empty:
        return OverlapResult("BLOCKED", 0, 0, ["CLICKHOUSE_OVERLAP_MISSING"])
    left = txt.copy(); right = clickhouse.copy()
    left = left[OPTION_FIELDS]; right = right[OPTION_FIELDS]
    for frame in (left, right):
        frame["symbol"] = frame["symbol"].astype(str).str.upper()
        for col in ("trade_date", "expiration_date"):
            frame[col] = pd.to_datetime(frame[col], errors="coerce").dt.date
        frame["call_put"] = frame["call_put"].astype(str).str.lower()
        frame["strike"] = pd.to_numeric(frame["strike"], errors="coerce")
    joined = left.merge(right, on=KEY, how="inner", suffixes=("_txt", "_ch"))
    if joined.empty:
        return OverlapResult("BLOCKED", 0, 0, ["CLICKHOUSE_OVERLAP_EMPTY"])
    mismatch = pd.Series(False, index=joined.index)
    for col in COMPARE:
        mismatch |= ~(joined[f"{col}_txt"].fillna(float("nan")).eq(joined[f"{col}_ch"].fillna(float("nan"))) | (joined[f"{col}_txt"].isna() & joined[f"{col}_ch"].isna()))
    count = int(mismatch.sum())
    return OverlapResult("READY", len(joined), count, [] if count == 0 else ["CLICKHOUSE_QUOTE_DIFFERENCE_AUDIT"])


def onboard_ticker(symbol: str, periods: list[tuple[int, int]], clickhouse_loader: Callable[[str, int, int], pd.DataFrame], *, adapter: HistoricalTxtZipAdapter, access: PCSDataAccess, dataset: str = "options_v2") -> OnboardingResult:
    """Onboard a new ticker, ending only in READY or BLOCKED."""
    symbol = str(symbol).strip().upper()
    frames = []
    metadata = []
    for year, quarter in periods:
        try:
            frame, meta = adapter.read_period(symbol, year, quarter)
            clickhouse = clickhouse_loader(symbol, year, quarter)
            policy = apply_conflict_policy(frame, clickhouse)
            frame = policy.frame
            meta.update({"exact_duplicates_removed": policy.exact_duplicates_removed, "conflicts_resolved": policy.conflicts_resolved})
            overlap = validate_txt_clickhouse_overlap(frame, clickhouse)
        except (FileNotFoundError, DataQualityError, ValueError) as exc:
            return OnboardingResult(symbol, "BLOCKED", len(frames), 0, 0, ["HISTORICAL_SOURCE_INVALID"], str(exc))
        if overlap.status != "READY":
            return OnboardingResult(symbol, "BLOCKED", len(frames), 0, 0, overlap.reason_codes, "TXT/ClickHouse overlap validation failed")
        frames.append(frame); metadata.append(meta)
    written = records = 0
    for frame, meta in zip(frames, metadata):
        year = meta["year"]; quarter = meta["quarter"]
        source_version = f"historical_txt:{meta['source_member']}:sha256:{meta['source_sha256']}"
        access.write_partition(frame, dataset, symbol, f"year={year}/quarter={quarter}", source_version=source_version,
                               filename=f"{symbol}_{year}_q{quarter}.parquet")
        access.record_provenance({**meta, "dataset": dataset, "symbol": symbol, "source_table": "historical_txt",
                                  "source_version": source_version, "status": "READY", "written_rows": len(frame),
                                  "synced_at": datetime.now(timezone.utc).isoformat()})
        written += len(frame); records += 1
    if not all(replay_onboarded_partition(access, dataset, symbol, meta["year"], meta["quarter"], len(frame)) for frame, meta in zip(frames, metadata)):
        return OnboardingResult(symbol, "BLOCKED", len(frames), written, records, ["CANONICAL_REPLAY_MISMATCH"], "post-append replay validation failed")
    return OnboardingResult(symbol, "READY", len(frames), written, records, [], "append and canonical replay verified")


__all__ = ["HistoricalTxtZipAdapter", "ConflictPolicyResult", "OverlapResult", "OnboardingResult", "apply_conflict_policy", "validate_txt_clickhouse_overlap", "replay_onboarded_partition", "onboard_ticker"]
