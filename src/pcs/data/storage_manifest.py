from pathlib import Path
import csv
from datetime import datetime, timezone

FIELDS=["dataset","symbol","source_file","source_size","source_modified_time","row_count","min_date","max_date","year","quarter","parquet_path","schema_version","import_timestamp","status"]


def append_manifest(path, record):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); exists=path.exists()
    with path.open("a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS); 
        if not exists:w.writeheader()
        w.writerow({k:record.get(k) for k in FIELDS})


def now_utc(): return datetime.now(timezone.utc).isoformat()
