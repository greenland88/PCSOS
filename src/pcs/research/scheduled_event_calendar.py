"""Versioned scheduled-event calendar foundation (research-only).

This module deliberately consumes supplied, source-backed CSV calendars.  It
never infers dates from price moves or recurring-date heuristics.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import re
import pandas as pd

CALENDAR_VERSION = "scheduled_events_v0.1"
EVENT_TYPES = ("EARNINGS", "FOMC", "CPI", "NFP_EMPLOYMENT")
EVENT_TYPE_ALIASES = {"FOMC_POLICY_DECISION":"FOMC", "CPI_RELEASE":"CPI", "EMPLOYMENT_SITUATION":"NFP_EMPLOYMENT"}
def _known_at_entry(value) -> bool:
    return value is True or value == 1 or (isinstance(value, str) and value.strip().lower() in {"true", "1", "yes"})
RAW_EVENT_DIRS = {"FOMC":"fomc", "CPI":"cpi", "NFP_EMPLOYMENT":"employment", "EARNINGS":"earnings"}

CALENDAR_COLUMNS = [
    "symbol", "event_date", "event_type", "meeting_start_date",
    "meeting_end_date", "decision_date", "reference_period",
    "release_time", "announcement_session", "scheduled_flag", "source",
    "source_version",
]

VERSIONED_COLUMNS = ["event_id", "event_type", "symbol", "event_date",
    "event_timestamp", "session", "scheduled_vs_unscheduled",
    "reference_period", "source", "source_url_or_source_id",
    "source_version", "retrieved_at", "validation_status",
    "event_date_known_at_entry"]


def load_calendar(path: str | Path) -> pd.DataFrame:
    """Load and validate a source-backed event calendar without mutation."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=CALENDAR_COLUMNS)
    d = pd.read_csv(p)
    missing = {"event_date", "event_type", "source", "source_version"} - set(d)
    if missing:
        raise ValueError(f"calendar missing required fields: {sorted(missing)}")
    bad = set(d.event_type.dropna().unique()) - set(EVENT_TYPES)
    if bad:
        raise ValueError(f"unsupported event types: {sorted(bad)}")
    d = d.copy()
    d["event_date"] = pd.to_datetime(d["event_date"], errors="raise").dt.normalize()
    if "decision_date" in d:
        d["decision_date"] = pd.to_datetime(d["decision_date"], errors="coerce").dt.normalize()
    if "announcement_session" in d:
        allowed = {"PREMARKET", "AFTERMARKET", "DURING_MARKET", "UNKNOWN"}
        bad = set(d.announcement_session.dropna().unique()) - allowed
        if bad:
            raise ValueError(f"invalid announcement sessions: {sorted(bad)}")
    return d


def validate_events(events: pd.DataFrame) -> pd.DataFrame:
    """Return row-level validation findings; never repairs source data."""
    required = set(VERSIONED_COLUMNS) - {"event_date_known_at_entry"}
    missing = required - set(events)
    if missing:
        raise ValueError(f"events missing required fields: {sorted(missing)}")
    findings=[]
    valid_types=set(EVENT_TYPES)
    valid_sessions={"PREMARKET","AFTERMARKET","DURING_MARKET","UNKNOWN",None}
    for i,r in events.iterrows():
        issues=[]
        try: pd.Timestamp(r.event_date)
        except Exception: issues.append("INVALID_DATE")
        if r.event_type not in valid_types: issues.append("INVALID_EVENT_TYPE")
        # Earnings are ticker-scoped; do not maintain a closed list of three
        # issuers.  Known broad ETFs are not issuers, while any otherwise
        # well-formed ticker (including newly onboarded MSFT) is admissible.
        non_issuer_symbols = {"SPY", "QQQ", "SOXX"}
        if r.event_type == "EARNINGS" and (pd.isna(r.symbol) or not str(r.symbol).strip() or str(r.symbol).upper() in non_issuer_symbols):
            issues.append("INVALID_EARNINGS_SYMBOL")
        if r.get("session") not in valid_sessions and pd.notna(r.get("session")): issues.append("INVALID_SESSION")
        findings.append({"row":i,"validation_status":"PASS" if not issues else "FAIL","issues":";".join(issues)})
    out=pd.DataFrame(findings)
    if events.duplicated(["event_type","symbol","event_date"],keep=False).any():
        out.loc[:,"duplicate_event_check"]="FAIL"
    else: out.loc[:,"duplicate_event_check"]="PASS"
    return out


def normalize_source_events(events: pd.DataFrame, source: str, source_version: str,
                            source_id: str) -> pd.DataFrame:
    """Convert an explicitly source-backed table to versioned canonical rows."""
    d=events.copy()
    d["event_type"]=d["event_type"].replace(EVENT_TYPE_ALIASES)
    d["event_date"]=pd.to_datetime(d["event_date"],errors="raise").dt.normalize()
    if "symbol" not in d: d["symbol"]=pd.NA
    d["event_id"]=[f"{et}:{'' if pd.isna(sym) else sym}:{dt.date()}" for et,sym,dt in zip(d.event_type,d.symbol,d.event_date)]
    d["event_timestamp"]=d.get("event_timestamp",pd.NA)
    d["session"]=d.get("session", "UNKNOWN")
    d["scheduled_vs_unscheduled"]=d.get("scheduled_vs_unscheduled", "SCHEDULED")
    d["reference_period"]=d.get("reference_period",pd.NA)
    d["source"]=source; d["source_url_or_source_id"]=source_id; d["source_version"]=source_version
    d["retrieved_at"]=datetime.now(timezone.utc).isoformat(); d["validation_status"]="UNVALIDATED"
    d["event_date_known_at_entry"]=d.get("event_date_known_at_entry","UNKNOWN")
    return d[VERSIONED_COLUMNS]


def write_versioned_calendar(events: pd.DataFrame, output_dir: str | Path) -> dict:
    """Write immutable v1 datasets and a combined calendar."""
    out=Path(output_dir); out.mkdir(parents=True,exist_ok=True)
    findings=validate_events(events); clean=events.copy()
    clean["validation_status"]=findings.validation_status.values
    clean.to_csv(out/"scheduled_event_calendar_v1.csv",index=False)
    for et,name in [("FOMC","fomc"),("CPI","cpi"),("NFP_EMPLOYMENT","nfp"),("EARNINGS","earnings")]:
        clean[clean.event_type.eq(et)].to_csv(out/f"historical_{name}_calendar_v1.csv",index=False)
    findings.to_csv(out/"scheduled_event_validation_v1.csv",index=False)
    return {"rows":len(clean),"validation_failures":int((findings.validation_status=="FAIL").sum())}


def _read_offline_file(path: Path) -> pd.DataFrame:
    """Read a source export; HTML/PDF remain provenance artifacts.

    PDF/HTML extraction is intentionally not guessed here.  Operators can
    place a reviewed CSV/JSON export beside the original source artifact.
    """
    suffix=path.suffix.lower()
    if suffix==".csv": return pd.read_csv(path)
    if suffix==".json":
        data=json.loads(path.read_text(encoding="utf-8"))
        return pd.DataFrame(data if isinstance(data,list) else data.get("events",data))
    if suffix==".ics":
        rows=[]; current={}
        for line in path.read_text(encoding="utf-8",errors="replace").splitlines():
            if line.strip()=="BEGIN:VEVENT": current={}
            elif line.startswith("DTSTART"):
                value=line.split(":",1)[1].strip(); current["event_date"]=pd.to_datetime(re.sub(r"Z$","",value),format="%Y%m%d",errors="coerce")
            elif line.startswith("SUMMARY:"): current["summary"]=line.split(":",1)[1].strip()
            elif line.strip()=="END:VEVENT" and current: rows.append(current); current={}
        return pd.DataFrame(rows)
    raise ValueError(f"unsupported direct parser for {path.name}; convert reviewed HTML/PDF to CSV/JSON")


def ingest_offline_raw(raw_root: str | Path, output_dir: str | Path) -> dict:
    """Deterministically ingest source-backed offline event files.

    Every accepted row must carry provenance.  No network access and no date
    inference occur here.  HTML/PDF files are inventory-only until a reviewed
    tabular extraction is supplied.
    """
    root=Path(raw_root); frames=[]; inventory=[]
    sources=[(et,p) for et,dirname in RAW_EVENT_DIRS.items() for p in sorted((root/dirname).iterdir() if (root/dirname).exists() else []) if p.is_file()]
    sources += [(None,p) for p in sorted(root.iterdir() if root.exists() else []) if p.is_file() and p.suffix.lower() in {".csv",".json",".ics"}]
    for et,p in sources:
            if not p.is_file(): continue
            inventory.append({"event_type":et or "COMBINED","file":str(p),"format":p.suffix.lower().lstrip("."),"status":"INVENTORY_ONLY"})
            if p.suffix.lower() not in {".csv",".json",".ics"}: continue
            d=_read_offline_file(p)
            if d.empty: continue
            d["event_type"]=d.get("event_type",et).replace(EVENT_TYPE_ALIASES)
            d["source_name"]=d.get("source_name",p.name)
            d["source_url_or_source_id"]=d.get("source_url_or_source_id",d.get("source_url",pd.NA))
            d["source_version"]=d.get("source_version",d.get("retrieved_at",pd.NA))
            d["provenance_status"]=d.get("provenance_status","UNVERIFIED")
            required={"event_date","event_type","source_url_or_source_id","source_name","source_version","provenance_status"}
            missing=required-set(d)
            if missing: raise ValueError(f"{p} missing provenance fields: {sorted(missing)}")
            # A combined export can contain multiple issuers and sources. Do
            # not stamp the first row's provenance across the whole file.
            for (source_name, source_version, source_id), group in d.groupby(
                ["source_name", "source_version", "source_url_or_source_id"], dropna=False
            ):
                frames.append(normalize_source_events(
                    group, str(source_name), str(source_version), str(source_id)
                ))
            inventory[-1]["status"]="PARSED"
    events=pd.concat(frames,ignore_index=True) if frames else pd.DataFrame(columns=VERSIONED_COLUMNS)
    result=write_versioned_calendar(events,output_dir) if len(events) else {"rows":0,"validation_failures":0}
    Path(output_dir).mkdir(parents=True,exist_ok=True)
    pd.DataFrame(inventory).to_csv(Path(output_dir)/"offline_event_source_inventory.csv",index=False)
    result["files_seen"]=len(inventory); result["source_inventory"]=inventory
    return result


def tag_entry_dates(trades: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
    """Add pre-entry-known exposure flags; never uses realized prices."""
    required = {"entry_date", "expiration"}
    if not required <= set(trades):
        raise ValueError(f"trades missing required fields: {sorted(required-set(trades))}")
    out = trades.copy()
    out["entry_date"] = pd.to_datetime(out.entry_date).dt.normalize()
    out["expiration"] = pd.to_datetime(out.expiration).dt.normalize()
    if "symbol" in out:
        out["symbol"] = out.symbol.astype(str).str.upper()
    knowledge_column = next((c for c in ("event_date_known_at_entry", "known_at_entry") if c in calendar.columns), None)
    knowledge_proven = (knowledge_column is not None
                        and calendar[knowledge_column].map(_known_at_entry).all())
    for et in EVENT_TYPES:
        nexts=[]; dte_flags=[]
        for _, row in out.iterrows():
            event_rows = calendar[calendar.event_type.eq(et)]
            if et == "EARNINGS" and "symbol" in out and "symbol" in event_rows:
                event_rows = event_rows[(event_rows.symbol.isna()) | (event_rows.symbol.astype(str).str.upper() == row.symbol)]
            if knowledge_column is not None:
                event_rows = event_rows[event_rows[knowledge_column].map(_known_at_entry)]
            elif bool(calendar.attrs.get("historical_pit_required", False)):
                # A PIT calendar without explicit knowledge evidence cannot
                # contribute historical event features.
                event_rows = event_rows.iloc[0:0]
            dates = (pd.DatetimeIndex(event_rows.event_date)
                     if not event_rows.empty else pd.DatetimeIndex([]))
            future = dates[dates >= row.entry_date]
            nexts.append((future[0] - row.entry_date).days if len(future) else pd.NA)
            dte_flags.append(bool(((dates >= row.entry_date) & (dates <= row.expiration)).any()))
        key = "ER" if et == "EARNINGS" else ("NFP" if et == "NFP_EMPLOYMENT" else et)
        out[f"days_to_next_{key}"] = nexts
        for h in (3, 5, 10):
            out[f"{key}_inside_{h}d"] = out[f"days_to_next_{key}"].le(h-1)
        out[f"{key}_inside_DTE"] = dte_flags
    flags = [c for c in out.columns if c.endswith("_inside_5d") or c.endswith("_inside_10d") or c.endswith("_inside_DTE")]
    out["scheduled_event_count_5d"] = out[[c for c in flags if c.endswith("_inside_5d")]].sum(axis=1)
    out["scheduled_event_count_10d"] = out[[c for c in flags if c.endswith("_inside_10d")]].sum(axis=1)
    out["scheduled_event_count_DTE"] = out[[c for c in flags if c.endswith("_inside_DTE")]].sum(axis=1)
    out["event_feature_class"] = "PRE_ENTRY_KNOWN" if knowledge_proven else "PIT_KNOWLEDGE_UNPROVEN"
    return out


def availability(calendar: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for et in EVENT_TYPES:
        s=calendar[calendar.event_type.eq(et)]
        rows.append({"event_type":et,"available":bool(len(s)),"rows":len(s),"source": ";".join(sorted(s.source.dropna().unique())),"source_version":";".join(sorted(s.source_version.dropna().unique()))})
    return pd.DataFrame(rows)
