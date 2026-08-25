"""Read-only aggregate audit of active canonical option routes."""
from __future__ import annotations
import json
from pathlib import Path
import duckdb
import pandas as pd
from pcs.data.access import PCSDataAccess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "canonical_options_quote_quality"

def audit_symbol(access: PCSDataAccess, symbol: str) -> dict:
    try:
        spec = access.resolve_source("options", symbol)
    except Exception as exc:
        return {"symbol": symbol, "status": "ROUTE_BLOCKED", "route_error": f"{type(exc).__name__}:{exc}",
                "legacy_fallback_used": False}
    files = spec.path.split(";") if ";" in spec.path else [spec.path]
    rel = " UNION ALL ".join(["SELECT * FROM read_parquet(?)"] * len(files))
    con = duckdb.connect()
    try:
        sql = f"""
        WITH r AS ({rel}), x AS (
          SELECT *, date_diff('day', trade_date, expiration_date) AS dte
          FROM r WHERE upper(symbol)=?
        )
        SELECT
          count(*) AS total_rows,
          count(*) FILTER (WHERE bid IS NULL OR ask IS NULL) AS null_quote_rows,
          count(*) FILTER (WHERE (bid IS NOT NULL AND NOT isfinite(bid)) OR (ask IS NOT NULL AND NOT isfinite(ask))) AS nonfinite_rows,
          count(*) FILTER (WHERE bid IS NOT NULL AND ask IS NOT NULL AND isfinite(bid) AND isfinite(ask) AND ask < bid) AS ask_lt_bid_rows,
          count(*) FILTER (WHERE bid IS NOT NULL AND isfinite(bid) AND bid < 0) AS negative_bid_rows,
          count(*) FILTER (WHERE ask IS NOT NULL AND isfinite(ask) AND ask < 0) AS negative_ask_rows,
          count(*) FILTER (WHERE dte BETWEEN 30 AND 45 AND NOT (bid IS NOT NULL AND ask IS NOT NULL AND isfinite(bid) AND isfinite(ask) AND bid >= 0 AND ask >= bid)) AS executable_window_invalid_rows,
          count(*) FILTER (WHERE expiration_date IS NULL OR trade_date IS NULL OR expiration_date <= trade_date OR strike IS NULL OR NOT isfinite(strike) OR strike <= 0 OR call_put NOT IN ('p','c')) AS identity_or_expiry_invalid_rows,
          count(*) FILTER (WHERE dte BETWEEN 30 AND 45 AND bid IS NOT NULL AND ask IS NOT NULL AND isfinite(bid) AND isfinite(ask) AND bid >= 0 AND ask >= bid) AS remaining_clean_rows
        FROM x
        """
        row = con.execute(sql, list(files) + [symbol.upper()]).fetchone()
        cols = [d[0] for d in con.description]
    finally:
        con.close()
    out = dict(zip(cols, row)); out.update({"status": "COMPLETED", "symbol": symbol, "dataset": spec.dataset,
        "route_manifest": str(access.resolve_source("options", symbol).source_version),
        "first_date": spec.first_date, "last_date": spec.last_date,
        "legacy_fallback_used": False})
    return out

def main():
    access = PCSDataAccess.canonical()
    symbols = sorted(access.source_routes.get("options", {}).get("by_symbol", {}).keys())
    rows = [audit_symbol(access, s) for s in symbols]
    OUT.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows).sort_values("symbol")
    frame.to_csv(OUT / "active_ticker_quote_quality.csv", index=False)
    (OUT / "active_ticker_quote_quality.json").write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print(frame.to_string(index=False))

if __name__ == "__main__":
    main()
