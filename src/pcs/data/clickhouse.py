"""Bounded, observable ClickHouse HTTP access for PCS read-only queries."""
from __future__ import annotations

from dataclasses import dataclass, field
import os, random, re, threading, time, uuid
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter


RETRYABLE_STATUS = {500, 502, 503, 504}
MAX_BODY = 64 * 1024


@dataclass(frozen=True)
class ClickHouseConfig:
    pool_size: int = 8
    max_concurrency: int = 4
    max_attempts: int = 4
    connect_timeout: float = 10.0
    read_timeout: float = 1800.0
    body_limit: int = MAX_BODY
    backoff_base: float = 1.0

    @classmethod
    def from_env(cls) -> "ClickHouseConfig":
        return cls(
            pool_size=int(os.getenv("PCS_CLICKHOUSE_POOL_SIZE", "8")),
            max_concurrency=int(os.getenv("PCS_CLICKHOUSE_MAX_CONCURRENCY", "4")),
            max_attempts=int(os.getenv("PCS_CLICKHOUSE_MAX_ATTEMPTS", "4")),
            connect_timeout=float(os.getenv("PCS_CLICKHOUSE_CONNECT_TIMEOUT", "10")),
            read_timeout=float(os.getenv("PCS_CLICKHOUSE_READ_TIMEOUT", "1800")),
        )


@dataclass
class ClickHouseDiagnostics:
    request_id: str
    operation: str
    endpoint: str
    ticker: str | None = None
    partition: str | None = None
    http_status: int | None = None
    clickhouse_code: str | None = None
    clickhouse_message: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: str = ""
    attempt: int = 0
    max_attempts: int = 0
    elapsed_seconds: float = 0.0
    concurrency: int = 0
    failure_class: str | None = None
    bytes_received: int = 0


class ClickHouseError(RuntimeError):
    def __init__(self, message: str, diagnostics: ClickHouseDiagnostics):
        super().__init__(message)
        self.diagnostics = diagnostics


class PCSClickHouseClient:
    """Thread-safe client; one instance should be created per worker process."""
    def __init__(self, url: str, user: str, password: str, *, config: ClickHouseConfig | None = None, session=None):
        self.url = url.rstrip("/") + "/"
        self.user, self.password = user, password
        self.config = config or ClickHouseConfig.from_env()
        self._gate = threading.BoundedSemaphore(max(1, self.config.max_concurrency))
        self._active = 0
        self._active_lock = threading.Lock()
        self._metrics = {"total": 0, "success": 0, "failed": 0, "retried": 0, "5xx": 0, "timeouts": 0, "retry_exhausted": 0, "latencies": []}
        self.session = session or requests.Session()
        adapter = HTTPAdapter(pool_connections=self.config.pool_size, pool_maxsize=self.config.pool_size, max_retries=0, pool_block=True)
        self.session.mount("http://", adapter); self.session.mount("https://", adapter)

    @staticmethod
    def _body(raw: bytes, limit: int) -> str:
        return raw[:limit].decode("utf-8", "replace")

    @staticmethod
    def _classify(status: int | None, exc: Exception | None = None) -> str:
        if isinstance(exc, requests.exceptions.ConnectTimeout): return "CONNECT_TIMEOUT"
        if isinstance(exc, requests.exceptions.ReadTimeout): return "READ_TIMEOUT"
        if isinstance(exc, requests.exceptions.ConnectionError): return "CONNECTION_ERROR"
        if status in RETRYABLE_STATUS: return "HTTP_5XX_TRANSIENT"
        if status and 400 <= status < 500: return "HTTP_4XX"
        return "UNKNOWN"

    @staticmethod
    def _exception_fields(body: str) -> tuple[str | None, str | None]:
        code = re.search(r"(?:Code|code)\s*:\s*(\d+)", body)
        message = re.search(r"(?:DB::Exception|Exception)\s*:\s*(.*?)(?:\n|$)", body)
        return (code.group(1) if code else None, message.group(1).strip() if message else None)

    def query(self, sql: str, *, ticker: str | None = None, partition: str | None = None, operation: str = "select", output: Path | None = None) -> ClickHouseDiagnostics:
        request_id = uuid.uuid4().hex
        d = ClickHouseDiagnostics(request_id, operation, self.url, ticker, partition, max_attempts=self.config.max_attempts)
        params = {"user": self.user, "password": self.password}
        start_all = time.perf_counter(); self._metrics["total"] += 1
        with self._gate:
            with self._active_lock: self._active += 1; d.concurrency = self._active
            try:
                for attempt in range(1, max(1, self.config.max_attempts) + 1):
                    d.attempt = attempt
                    try:
                        response = self.session.post(self.url, params=params, data=sql.encode(), headers={"X-ClickHouse-Query-Id": request_id}, timeout=(self.config.connect_timeout, self.config.read_timeout), stream=True)
                        d.http_status = response.status_code; d.response_headers = {str(k): str(v) for k, v in response.headers.items()}
                        chunks = []; total = 0
                        sink = output.open("wb") if output is not None else None
                        for chunk in response.iter_content(1024 * 1024):
                            if chunk:
                                total += len(chunk)
                                if sink is not None: sink.write(chunk)
                                remaining = self.config.body_limit - sum(map(len, chunks))
                                if remaining > 0: chunks.append(chunk[:remaining])
                        if sink is not None: sink.close()
                        body = b"".join(chunks); d.bytes_received = total; d.response_body = self._body(body, self.config.body_limit)
                        d.clickhouse_code, d.clickhouse_message = self._exception_fields(d.response_body)
                        if response.ok:
                            self._metrics["success"] += 1; return self._finish(d, start_all)
                        d.failure_class = self._classify(response.status_code); self._metrics["5xx"] += int(response.status_code in RETRYABLE_STATUS)
                        if response.status_code not in RETRYABLE_STATUS or attempt >= self.config.max_attempts: break
                    except requests.exceptions.RequestException as exc:
                        d.failure_class = self._classify(None, exc); self._metrics["timeouts"] += int(isinstance(exc, (requests.exceptions.Timeout,)))
                        if attempt >= self.config.max_attempts: break
                    self._metrics["retried"] += 1; time.sleep(self.config.backoff_base * (2 ** (attempt - 1)) + random.uniform(0, .25))
                self._metrics["failed"] += 1; self._metrics["retry_exhausted"] += 1
                raise ClickHouseError(f"ClickHouse request failed: {d.failure_class} HTTP {d.http_status}", self._finish(d, start_all))
            finally:
                with self._active_lock: self._active -= 1

    def _finish(self, d, started):
        d.elapsed_seconds = round(time.perf_counter() - started, 3); self._metrics["latencies"].append(d.elapsed_seconds); return d

    def metrics(self) -> dict[str, Any]:
        lat = sorted(self._metrics["latencies"])
        pct = lambda p: lat[min(len(lat)-1, int((len(lat)-1)*p))] if lat else 0.0
        return {**{k:v for k,v in self._metrics.items() if k != "latencies"}, "p50": pct(.5), "p95": pct(.95), "active": self._active, "error_rate": self._metrics["failed"] / max(1, self._metrics["total"]), "retry_rate": self._metrics["retried"] / max(1, self._metrics["total"])}

    def health(self) -> str:
        try:
            self.query("SELECT 1 FORMAT TabSeparated", operation="health")
            return "REACHABLE"
        except ClickHouseError:
            return "UNAVAILABLE"

    def health_check(self) -> str:
        return self.health()

    def capabilities(self) -> dict[str, Any]:
        return {"OPTION_HISTORY_QUOTES": True, "OPTION_CURRENT_QUOTES": True,
                "COVERAGE_PROBE": True}

    def probe_options_coverage(self, symbol: str, table: str = "firstrate.options_kline_1d") -> dict[str, Any]:
        escaped = str(symbol).replace("'", "''")
        sql = (f"SELECT min(TradeDate), max(TradeDate), count(), uniqExact(TradeDate) "
               f"FROM {table} WHERE Symbol = '{escaped}' FORMAT JSONEachRow")
        diag = self.query(sql, ticker=symbol, operation="probe_options_coverage")
        import json
        rows = [json.loads(line) for line in diag.response_body.splitlines() if line.strip()]
        row = rows[0] if rows else {}
        return {"source_id": "clickhouse_options", "symbol": symbol.upper(),
                "dataset": "options", "min_date": row.get("min(TradeDate)"),
                "max_date": row.get("max(TradeDate)"), "row_count": row.get("count()", 0),
                "distinct_trade_dates": row.get("uniqExact(TradeDate)", 0),
                "status": "API_COMPLETE"}

    def fetch_options_coverage(self, symbol: str, start: str, end: str,
                               table: str = "firstrate.options_kline_1d") -> dict[str, Any]:
        import json
        ticker = str(symbol).strip().upper()
        escaped = ticker.replace("'", "''")
        sql = ("SELECT min(TradeDate) AS source_min_date, max(TradeDate) AS source_max_date, "
               "count() AS physical_rows, uniqExact(tuple(TradeDate, ExpiryDate, Strike, CallPut)) AS unique_contract_keys, "
               "countIf(lower(CallPut) IN ('p','put')) AS put_rows, countIf(lower(CallPut) IN ('c','call')) AS call_rows "
               f"FROM {table} WHERE Symbol = '{escaped}' AND TradeDate BETWEEN '{str(start)[:10]}' AND '{str(end)[:10]}' FORMAT JSONEachRow")
        try:
            diag = self.query(sql, ticker=ticker, operation="fetch_options_coverage")
        except ClickHouseError as exc:
            message = str(exc).lower()
            reason = ("CLICKHOUSE_SOURCE_TABLE_UNAVAILABLE" if "unknown table" in message or "not exist" in message
                      else "CLICKHOUSE_CONNECTION_FAILED")
            return {"symbol": ticker, "requested_start": str(start)[:10], "requested_end": str(end)[:10],
                    "status": "BLOCKED", "reason_codes": [reason], "source_table": table, "detail": str(exc)}
        rows = [json.loads(line) for line in diag.response_body.splitlines() if line.strip()]
        row = rows[0] if rows else {}
        physical = int(row.get("physical_rows") or 0)
        return {"symbol": ticker, "requested_start": str(start)[:10], "requested_end": str(end)[:10],
                "source_min_date": row.get("source_min_date"), "source_max_date": row.get("source_max_date"),
                "physical_rows": physical, "unique_contract_keys": int(row.get("unique_contract_keys") or 0),
                "put_rows": int(row.get("put_rows") or 0), "call_rows": int(row.get("call_rows") or 0),
                "status": "READY" if physical else "BLOCKED",
                "reason_codes": [] if physical else ["AUTHORIZED_SOURCE_NO_ROWS"], "source_table": table}

    def _fetch_options(self, symbol: str, predicate: str, *, operation: str,
                       table: str = "firstrate.options_kline_1d"):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory(prefix="pcs_ch_options_") as directory:
            output = Path(directory) / "options.parquet"
            escaped = str(symbol).replace("'", "''")
            select = ("Symbol AS symbol, TradeDate AS trade_date, ExpiryDate AS expiration_date, "
                      "Strike AS strike, CallPut AS call_put, LastTradePrice AS last, BidPrice AS bid, "
                      "AskPrice AS ask, BidImpliedVolatilities AS bid_iv, AskImpliedVolatilities AS ask_iv, "
                      "OpenInterest AS open_interest, Volume AS volume, Delta AS delta, Gamma AS gamma, "
                      "Vega AS vega, Theta AS theta, Rho AS rho")
            sql = f"SELECT {select} FROM {table} WHERE Symbol = '{escaped}' AND {predicate} FORMAT Parquet"
            self.query(sql, ticker=symbol, operation=operation, output=output)
            import pandas as pd
            return pd.read_parquet(output)

    def fetch_options_quarter(self, symbol: str, year: int, quarter: int):
        return self._fetch_options(symbol, f"toYear(TradeDate) = {int(year)} AND toQuarter(TradeDate) = {int(quarter)}", operation="fetch_options_quarter")

    def fetch_options_range(self, symbol: str, start: str, end: str):
        return self._fetch_options(symbol, f"TradeDate BETWEEN '{start}' AND '{end}'", operation="fetch_options_range")

    def fetch_options_dates(self, symbol: str, dates: list[str]):
        if not dates: return __import__("pandas").DataFrame()
        values = ",".join("'" + str(item).replace("'", "''") + "'" for item in dates)
        return self._fetch_options(symbol, f"TradeDate IN ({values})", operation="fetch_options_dates")
