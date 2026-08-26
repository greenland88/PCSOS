import argparse
import json
import os
import tempfile
from pathlib import Path
import pandas as pd

from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.providers.hood_trader_provider import HoodTraderProvider, JsonHoodClient
from pcs.providers.mock_provider import MockProvider
from pcs.research.youtube_subtitles import DEFAULT_TRANSCRIPT_DIR, download_youtube_subtitles
from pcs.simulation.paper_trading import run_daily_paper_trading
from pcs.stress_lab.scenarios import DEFAULT_SYNTHETIC_SCENARIOS, StressLab
from pcs.data.onboarding_engine import OnboardingEngine


def _json_provider(path: str | None):
    if not path:
        return MockProvider()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return HoodTraderProvider(JsonHoodClient(payload))


def collect_options(args):
    provider = _json_provider(args.hood_json)
    store = ParquetStore(args.data_root)
    collector = OptionChainSnapshotCollector(provider, store)
    paths = [collector.collect_symbol(symbol) for symbol in args.symbols]
    for path in paths:
        print(path)


def analyze_mock(args):
    rules = load_rules(args.rules)
    provider = MockProvider()
    engine = DecisionEngine(rules)
    market = provider.get_market_state()
    portfolio = provider.get_portfolio() | {"bucket_risk": {"nasdaq_mega": 2200, "semiconductor": 900}}
    for candidate in provider.get_candidates():
        decision = engine.evaluate_candidate(candidate, market, portfolio)
        print(decision.model_dump_json())
    for position in provider.get_positions():
        decision = engine.evaluate_position(position, market)
        print(decision.model_dump_json())


def simulate_daily(args):
    rules = load_rules(args.rules)
    provider = _json_provider(args.hood_json)
    result = run_daily_paper_trading(
        provider,
        rules,
        as_of=args.as_of,
        output_dir=args.output_dir,
        sqlite_path=args.sqlite_path,
    )
    print(result.model_dump_json())


def stress(args):
    lab = StressLab()
    portfolio = json.loads(Path(args.portfolio_json).read_text(encoding="utf-8"))
    for scenario in DEFAULT_SYNTHETIC_SCENARIOS:
        print(json.dumps(lab.run_synthetic(portfolio, scenario), indent=2))


def download_subtitles(args):
    files = download_youtube_subtitles(
        args.url,
        output_dir=Path(args.output_dir),
        languages=args.languages,
    )
    if not files:
        print("No subtitles found for the requested language(s).")
        return
    for path in files:
        print(path)


def update_data(args):
    symbols = [s.upper() for s in args.symbols] if args.symbols else sorted({p.stem.upper() for p in Path(args.daily_root).glob("*.csv")})
    for symbol in symbols:
        daily_path = Path(args.daily_root) / f"{symbol}.csv"
        options_path = Path(args.options_root) / f"{symbol}.parquet"
        daily = load_source(daily_path, symbol) if daily_path.exists() else None
        options = load_source(options_path, symbol, options=True) if options_path.exists() else None
        print(json.dumps(update_ticker(symbol, daily_frame=daily, options_frame=options, parquet_root=args.parquet_root, manifest_path=args.manifest_path, options_manifest_path=args.options_manifest_path, source_version=args.source_version), sort_keys=True))


def onboard(args):
    """Run onboarding only through the unified market-data control plane."""
    from pcs.data.access import PCSDataAccess
    from pcs.data.onboarding import HistoricalTxtZipAdapter, run_system_onboarding
    from pcs.data.control_plane import MarketDataControlPlane
    periods = [(int(item.split("-", 1)[0]), int(item.split("-", 1)[1])) for item in args.period] if args.period else None
    def clickhouse_loader(symbol, year, quarter):
        from pcs.data.clickhouse import PCSClickHouseClient
        url = os.getenv("CLICKHOUSE_URL", "http://db.base32.cn:8123/")
        user = os.getenv("CLICKHOUSE_USER", "hisdata230")
        password = os.getenv("CLICKHOUSE_PASSWORD")
        if not password:
            raise RuntimeError("NON_RECOVERABLE_EXTERNAL:CLICKHOUSE_PASSWORD_NOT_CONFIGURED")
        table = os.getenv("PCS_CLICKHOUSE_TABLE", "firstrate.options_kline_1d")
        select = "Symbol AS symbol, TradeDate AS trade_date, ExpiryDate AS expiration_date, Strike AS strike, CallPut AS call_put, LastTradePrice AS last, BidPrice AS bid, AskPrice AS ask, BidImpliedVolatilities AS bid_iv, AskImpliedVolatilities AS ask_iv, OpenInterest AS open_interest, Volume AS volume, Delta AS delta, Gamma AS gamma, Vega AS vega, Theta AS theta, Rho AS rho"
        escaped = str(symbol).replace("'", "''")
        sql = (f"SELECT {select} FROM {table} WHERE Symbol = '{escaped}' "
               f"AND toYear(TradeDate) = {int(year)} AND toQuarter(TradeDate) = {int(quarter)} "
               "FORMAT Parquet")
        with tempfile.TemporaryDirectory(prefix="pcs_onboard_ch_") as temp:
            output = Path(temp) / "quotes.parquet"
            PCSClickHouseClient(url, user, password).query(sql, ticker=symbol, partition=f"{year}Q{quarter}", output=output)
            return pd.read_parquet(output)

    access = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)
    control = MarketDataControlPlane(access=access)
    requirements = {"start": "2018-01-01", "end": pd.Timestamp.now().date().isoformat(),
                    "datasets": {"options": {"required": True}}, "consumer": "CLI_ONBOARDING"}
    result = control.ensure_market_data(
        requirements,
        importer=lambda plan: run_system_onboarding(
            args.symbol, periods=periods, clickhouse_loader=clickhouse_loader,
            adapter=HistoricalTxtZipAdapter(args.source_root), access=access,
            workers=args.workers, state_root=args.state_root, routes_path=args.routes_path,
        ),
        symbol=args.symbol,
    )
    print(json.dumps(result.to_dict() if hasattr(result, "to_dict") else result, sort_keys=True, default=str))


def readiness(args):
    from pcs.data.access import PCSDataAccess
    from pcs.research.ticker_readiness import preflight_ticker
    result = preflight_ticker(args.symbol, access=PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root))
    print(json.dumps(result.to_dict(), sort_keys=True, default=str))


def covered_call_status(args):
    """One-ticker covered-call admission/decision status.

    This command deliberately stops at the ticker's own profile/readiness
    gate.  It never scans a sibling ticker or substitutes another ticker's
    research artifacts.
    """
    from pcs.data.access import PCSDataAccess
    from pcs.research.covered_call_decision import evaluate_covered_call
    from pcs.research.covered_call_profiles import resolve_covered_call_profile
    from pcs.research.ticker_readiness import preflight_ticker

    symbol = str(args.symbol).strip().upper()
    profile = resolve_covered_call_profile(symbol)
    if profile.status.value != "VALIDATED":
        result = evaluate_covered_call(
            symbol=symbol, as_of_date=args.as_of,
            shares_owned=args.shares_owned, active_calls=args.active_calls,
            event_context=args.event_context,
            market_context=args.market_context,
            profile=profile,
        )
        print(json.dumps(result, sort_keys=True, default=str))
        return
    access = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)
    readiness_result = preflight_ticker(symbol, access=access)
    if readiness_result.PCS_RESEARCH_READY != "YES":
        result = evaluate_covered_call(
            symbol=symbol, as_of_date=args.as_of,
            shares_owned=args.shares_owned, active_calls=args.active_calls,
            event_context=args.event_context, market_context=args.market_context,
            profile=profile,
        )
        result["reason_codes"] = list(result.get("reason_codes", [])) + ["TICKER_READINESS_NOT_PASSED"]
        result["readiness"] = readiness_result.to_dict()
        print(json.dumps(result, sort_keys=True, default=str))
        return
    result = evaluate_covered_call(
        symbol=symbol, as_of_date=args.as_of,
        shares_owned=args.shares_owned, active_calls=args.active_calls,
        event_context=args.event_context, market_context=args.market_context,
        data_access=access, profile=profile,
    )
    print(json.dumps(result, sort_keys=True, default=str))


def onboarding_status(args):
    print(json.dumps(OnboardingEngine(args.symbol, args.state_root).progress(), sort_keys=True, default=str))


def market_data_status(args):
    from pcs.data.control_plane import ImportCoordinator, MarketDataControlPlane, default_import_handlers, get_market_data_status
    requirements = {"symbol": args.symbol, "required_start": args.start, "required_end": args.end,
                    "datasets": tuple(args.dataset or ("daily", "options"))}
    if args.execute:
        result = ImportCoordinator(MarketDataControlPlane(), handlers=default_import_handlers()).run(requirements)
        print(json.dumps(result, sort_keys=True, default=str))
    else:
        print(json.dumps(get_market_data_status(requirements).to_dict(), sort_keys=True, default=str))


def main():
    parser = argparse.ArgumentParser(prog="pcs-lite")
    sub = parser.add_subparsers(required=True)

    analyze = sub.add_parser("analyze-mock", help="run local PCS rule engine on mock data")
    analyze.add_argument("--rules", default="config/pcs_rules.yaml")
    analyze.set_defaults(func=analyze_mock)

    simulate = sub.add_parser(
        "simulate-daily",
        help="run deterministic PCS paper trading and persist a daily snapshot",
    )
    simulate.add_argument("--rules", default="config/pcs_rules.yaml")
    simulate.add_argument("--hood-json", help="local exported Hood payload JSON; omit to use MockProvider")
    simulate.add_argument("--as-of", help="business date for the paper-trading snapshot, YYYY-MM-DD")
    simulate.add_argument("--output-dir", default="research_outputs/paper_trading")
    simulate.add_argument("--sqlite-path", default="data/pcs.db")
    simulate.set_defaults(func=simulate_daily)

    stress_cmd = sub.add_parser("stress", help="run simple synthetic stress scenarios from local JSON portfolio")
    stress_cmd.add_argument("portfolio_json")
    stress_cmd.set_defaults(func=stress)

    subtitles = sub.add_parser(
        "download-youtube-subtitles",
        help="download YouTube subtitles as SRT files and update the research transcript index",
    )
    subtitles.add_argument("url")
    subtitles.add_argument("--output-dir", default=str(DEFAULT_TRANSCRIPT_DIR))
    subtitles.add_argument("--languages", default="en,en-orig")
    subtitles.set_defaults(func=download_subtitles)

    ready_cmd = sub.add_parser("readiness", help="run the canonical ticker readiness gate")
    ready_cmd.add_argument("symbol")
    ready_cmd.add_argument("--parquet-root", default="data/parquet")
    ready_cmd.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    ready_cmd.set_defaults(func=readiness)

    md_status = sub.add_parser("market-data-status", help="inspect canonical coverage and produce an import plan")
    md_status.add_argument("symbol")
    md_status.add_argument("--start")
    md_status.add_argument("--end")
    md_status.add_argument("--dataset", action="append", choices=["daily", "options"], default=[])
    md_status.add_argument("--execute", action="store_true", help="execute registered import handlers for missing data")
    md_status.set_defaults(func=market_data_status)

    md_import = sub.add_parser("import-market-data", help="plan and execute registered market-data imports")
    md_import.add_argument("symbol")
    md_import.add_argument("--start")
    md_import.add_argument("--end")
    md_import.add_argument("--dataset", action="append", choices=["daily", "options"], default=[])
    md_import.set_defaults(func=lambda args: (setattr(args, "execute", True), market_data_status(args))[1])

    cc = sub.add_parser("covered-call-status", help="evaluate one ticker's covered-call admission/decision")
    cc.add_argument("symbol")
    cc.add_argument("--as-of", required=True)
    cc.add_argument("--shares-owned", type=int, required=True)
    cc.add_argument("--active-calls", type=int, required=True)
    cc.add_argument("--event-context", type=json.loads, required=True)
    cc.add_argument("--market-context", type=json.loads, required=True)
    cc.add_argument("--parquet-root", default="data/parquet")
    cc.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    cc.set_defaults(func=covered_call_status)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
