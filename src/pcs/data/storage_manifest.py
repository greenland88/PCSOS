from pathlib import Path
import csv
import os
import uuid
from datetime import datetime, timezone

FIELDS=["dataset","symbol","source_file","source_size","source_modified_time","row_count","min_date","max_date","year","quarter","parquet_path","schema_version","import_timestamp","status"]


def append_manifest(path, record):
    # Keep the legacy helper safe for parallel importers too.  A direct append
    # can interleave header/rows or leave a truncated manifest on interruption.
    from .access import PCSDataAccess
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with PCSDataAccess._file_lock(path):
        rows = []
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        rows.append({k: record.get(k) for k in FIELDS})
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with tmp.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)


def now_utc(): return datetime.now(timezone.utc).isoformat()
