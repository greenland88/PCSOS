from __future__ import annotations

from datetime import datetime, timezone
import os
import uuid
from pathlib import Path

import duckdb
import pandas as pd


class ParquetStore:
    def __init__(self, root: str | Path = "data"):
        self.root = Path(root)

    def write_snapshot(self, dataset: str, rows: list[dict], as_of: datetime | None = None, name: str = "snapshot") -> Path:
        if not rows:
            raise ValueError("cannot write empty snapshot")
        as_of = as_of or datetime.now(timezone.utc)
        folder = self.root / "raw" / dataset / as_of.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}_{as_of.strftime('%H%M%S')}.parquet"
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            pd.DataFrame(rows).to_parquet(tmp, index=False)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path

    def write_processed(self, dataset: str, rows: list[dict], name: str) -> Path:
        if not rows:
            raise ValueError("cannot write empty processed dataset")
        folder = self.root / "processed" / dataset
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{name}.parquet"
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            pd.DataFrame(rows).to_parquet(tmp, index=False)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path


class DuckDBQuery:
    def __init__(self, database: str | Path = ":memory:"):
        self.conn = duckdb.connect(str(database))

    def query_parquet(self, parquet_glob: str, sql_tail: str = "LIMIT 1000") -> pd.DataFrame:
        query = f"SELECT * FROM read_parquet('{parquet_glob}') {sql_tail}"
        return self.conn.execute(query).fetch_df()
