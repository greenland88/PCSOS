"""Bounded, read-only SOXL source probe; no ingestion or strategy execution."""
from __future__ import annotations
import json, os, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from pcs.data.clickhouse import PCSClickHouseClient, ClickHouseConfig, ClickHouseError

def run() -> dict:
    run_id = uuid.uuid4().hex; job_id = uuid.uuid4().hex
    client = PCSClickHouseClient(os.getenv("CLICKHOUSE_URL", "http://db.base32.cn:8123/"), os.getenv("CLICKHOUSE_USER", "hisdata230"), os.getenv("CLICKHOUSE_PASSWORD", ""), config=ClickHouseConfig.from_env())
    table = "firstrate.options_kline_1d"; steps=[]; first_failure=None
    def step(name, sql):
        nonlocal first_failure
        started = datetime.now(timezone.utc).isoformat(); t=time.perf_counter()
        row={"step":name,"started_at":started,"retry_count":0}
        try:
            d=client.query(sql, ticker="SOXL", operation="probe_"+name.lower())
            row.update({"ended_at":datetime.now(timezone.utc).isoformat(),"elapsed_seconds":round(time.perf_counter()-t,3),"status":"PASS","attempts":d.attempt,"attempt_timestamps":d.attempt_timestamps,"provider_result":{"http_status":d.http_status}})
            return d
        except ClickHouseError as exc:
            d=exc.diagnostics; row.update({"ended_at":datetime.now(timezone.utc).isoformat(),"elapsed_seconds":round(time.perf_counter()-t,3),"status":"FAIL","attempts":d.attempt,"attempt_timestamps":d.attempt_timestamps,"timeout":d.timeout_code == "PROVIDER_PROBE_TIMEOUT","error_type":type(exc).__name__,"error":str(exc),"provider_result":{"failure_class":d.failure_class,"http_status":d.http_status}})
            if first_failure is None: first_failure=name
            return None
        finally: steps.append(row)
    if not os.getenv("CLICKHOUSE_PASSWORD"):
        first_failure="AUTH"; steps.append({"step":"AUTH","status":"FAIL","error_type":"ConfigurationError","error":"CLICKHOUSE_CREDENTIALS_MISSING"})
    else:
        step("CONNECT", "SELECT 1 FORMAT TabSeparated")
        step("SELECT_1", "SELECT 1 FORMAT TabSeparated")
        step("AUTH", "SELECT currentUser() FORMAT TabSeparated")
        step("TABLE_DISCOVERY", "SELECT name FROM system.tables WHERE database='firstrate' AND name='options_kline_1d' FORMAT TabSeparated")
        symbol_diag = step("SYMBOL_EXISTENCE", f"SELECT count() FROM {table} WHERE Symbol='SOXL' FORMAT TabSeparated")
        coverage_diag = step("COVERAGE", f"SELECT min(TradeDate), max(TradeDate), count(), uniqExact(TradeDate) FROM {table} WHERE Symbol='SOXL' FORMAT TabSeparated")
    def fields(diag):
        if not diag: return []
        return diag.response_body.strip().split("\t")
    sf, cf = fields(symbol_diag), fields(coverage_diag)
    result={"run_id":run_id,"job_id":job_id,"dataset":"options_v2","symbol":"SOXL","requested_date_range":{"start":"2018-01-01","end":"2026-08-18"},"providers_checked":[{"provider":"clickhouse_options","table":table,"adapter":"pcs.data.clickhouse.PCSClickHouseClient"}],"steps":steps,"first_failed_layer":first_failure,"final_classification":"PROVIDER_PROBE_TIMEOUT" if any(x.get("timeout") for x in steps) else "SOURCE_TRULY_UNAVAILABLE" if first_failure else "SOURCE_DISCOVERED","soxl_exists":bool(sf and int(sf[0])>0),"source_coverage":{"min_date":cf[0],"max_date":cf[1],"row_count":int(cf[2]),"distinct_trade_dates":int(cf[3])} if len(cf)>=4 else None,"ingestion_status":"NOT_STARTED","validation_status":"NOT_STARTED","promotion_status":"NOT_STARTED"}
    out=Path("research_outputs/data_discovery")/run_id; out.mkdir(parents=True,exist_ok=True); (out/"source_discovery.json").write_text(json.dumps(result,indent=2,default=str),encoding="utf-8"); print(json.dumps(result,indent=2,default=str)); return result
if __name__ == "__main__": run()
