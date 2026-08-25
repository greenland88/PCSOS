"""Command-line wrapper for the existing lazy-loading PCS research backtest."""
from __future__ import annotations
import argparse, csv, json, time, os, uuid
from pathlib import Path
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.research.credit_stop import run_backtest, summarize
from pcs.research.controlled_analysis import controlled_groups, matched_aggregate
from pcs.research.backend import resolve_option_backend
from pcs.research.compatibility import enforce_reliable_range
from pcs.research.ticker_readiness import assert_research_ready


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", required=True); p.add_argument("--benchmark", required=True)
    p.add_argument("--start-date", required=True); p.add_argument("--end-date", required=True)
    p.add_argument("--output-dir", default="research_outputs"); p.add_argument("--run-label", required=True)
    p.add_argument("--backend", choices=["canonical"], default="canonical", help="canonical PCSDataAccess route only"); p.add_argument("--duckdb-path", default="data/duckdb/pcs.duckdb")
    return p.parse_args(argv)


def _write(path, rows):
    path = Path(path)
    rows = list(rows)
    if not rows:
        # A reused run label must not retain output from a prior run whose
        # corresponding result was empty.
        Path(path).unlink(missing_ok=True)
        return
    keys = sorted({k for r in rows for k in r})
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _load_pit_window(access: PCSDataAccess, symbol: str, start: pd.Timestamp,
                     end: pd.Timestamp, warmup_calendar_days: int = 320) -> pd.DataFrame:
    """Load indicator warmup without expanding the executable date window.

    Trend/ATR features are computed from the returned frame, while
    ``run_backtest`` still limits candidate evaluation to ``start..end``.
    Reading only the formal window makes the first part of a replay depend on
    the arbitrary CLI start date.
    """
    warmup_start = pd.Timestamp(start).normalize() - pd.Timedelta(days=warmup_calendar_days)
    frame = access.read_prices(symbol, warmup_start, pd.Timestamp(end).normalize())
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    return frame.sort_values("date", kind="mergesort").drop_duplicates(["date"], keep="last").reset_index(drop=True)


def main(argv=None):
    args = parse_args(argv); start, end = pd.Timestamp(args.start_date), pd.Timestamp(args.end_date); backend="canonical"; enforce_reliable_range(args.symbol,start,end)
    # Admission is ticker-based.  Passing the storage directory name (for
    # example ``options_v3``) makes the lower-level backtest gate validate the
    # wrong identity, so both the traded ticker and benchmark are admitted
    # explicitly here before any research data is loaded.
    assert_research_ready(args.symbol)
    assert_research_ready(args.benchmark)
    print(json.dumps({"backend_requested":args.backend,"backend_resolved":backend}),flush=True)
    run_dir = Path(args.output_dir) / args.run_label; run_dir.mkdir(parents=True, exist_ok=True)
    access = PCSDataAccess()
    stock = _load_pit_window(access, args.symbol, start, end)
    benchmark = _load_pit_window(access, args.benchmark, start, end)
    started = time.perf_counter()
    def progress(day, processed, usable, files, _):
        print(json.dumps({"symbol": args.symbol, "current_date": str(day.date()), "processed_candidate_days": processed, "usable_trades": usable, "current_option_file": str(sorted(files)[-1]) if files else None, "elapsed_seconds": round(time.perf_counter()-started, 2)}), flush=True)
    result = run_backtest(stock, benchmark, option_root=args.symbol.upper(), start=start, end=end, progress_callback=progress, backend=backend, duckdb_path=args.duckdb_path)
    trades = result["trades"]
    _write(run_dir / "backtest_trades.csv", [{k:v for k,v in r.items() if k != "events"} for r in trades])
    _write(run_dir / "summary.csv", [{"gate":k, **v} for k,v in summarize(trades, "current_state").items()])
    _, groups = controlled_groups(trades); _write(run_dir / "controlled_analysis.csv", [{"gate":k, **v} for k,v in matched_aggregate(groups).items()])
    if args.run_label == "qqq_full":
        periods=[("2010-2014",2010,2014),("2015-2019",2015,2019),("2020-2022",2020,2022),("2023-2026",2023,2026)]
        _write(run_dir / "period_stability.csv", [{"period":label,"sample_count":sum(first<=pd.Timestamp(r["date"]).year<=last for r in trades), **{f"{gate}_count":sum(first<=pd.Timestamp(r["date"]).year<=last and r.get("current_state")==gate for r in trades) for gate in ("PASS","WATCH","REJECT")}} for label,first,last in periods])
        years=sorted({pd.Timestamp(r["date"]).year for r in trades}); _write(run_dir / "yearly_summary.csv", [{"year":y,"sample_count":sum(pd.Timestamp(r["date"]).year==y for r in trades)} for y in years])
    metadata = run_dir / "run_metadata.json"
    metadata_tmp = run_dir / f".{metadata.name}.{uuid.uuid4().hex}.tmp"
    try:
        metadata_tmp.write_text(json.dumps({"symbol":args.symbol.upper(),"benchmark":args.benchmark.upper(),"start_date":str(start.date()),"end_date":str(end.date()),"backend":backend,"backend_requested":args.backend,"quality":result["quality"],"exclusions":result["exclusions"],"elapsed_seconds":time.perf_counter()-started},default=str,indent=2),encoding="utf-8")
        os.replace(metadata_tmp, metadata)
    finally:
        metadata_tmp.unlink(missing_ok=True)
    print(json.dumps({"run_label":args.run_label,"candidate_days":result["quality"]["candidate_days"],"usable_trades":len(trades),"output_dir":str(run_dir)}))


if __name__ == "__main__":
    main()
