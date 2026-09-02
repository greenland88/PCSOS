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
    """Compatibility wrapper for the single market-data import entrypoint."""
    from pcs.data.access import PCSDataAccess
    from pcs.data.control_plane import MarketDataControlPlane
    access = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)
    control = MarketDataControlPlane(access=access)
    requirements = {"start": "2018-01-01", "end": pd.Timestamp.now().date().isoformat(),
                    "datasets": {"options": {"required": True}}, "consumer": "CLI_ONBOARDING"}
    result = control.ensure_market_data(requirements, symbol=args.symbol)
    print(json.dumps(result.to_dict() if hasattr(result, "to_dict") else result, sort_keys=True, default=str))


def readiness(args):
    from pcs.data.access import PCSDataAccess
    from pcs.research.ticker_readiness import preflight_ticker
    result = preflight_ticker(args.symbol, access=PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root))
    print(json.dumps(result.to_dict(), sort_keys=True, default=str))

def pcs_status(args):
    from pcs.pcs_status import evaluate_pcs_status
    from pcs.data.access import PCSDataAccess
    portfolio = json.loads(Path(args.portfolio_json).read_text(encoding="utf-8")) if args.portfolio_json else None
    result = evaluate_pcs_status(args.symbol, args.as_of, mode=args.mode, portfolio_context=portfolio,
        rules=load_rules(args.rules), data_access=PCSDataAccess(
            manifest_path=args.manifest_path, parquet_root=args.parquet_root))
    print(result.model_dump_json(indent=2 if args.json else None))

def canonical_rollback(args):
    from pcs.data.access import PCSDataAccess
    result = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root).rollback_generation(
        args.dataset, args.symbol, f"year={args.year}/quarter={args.quarter}")
    print(json.dumps(result, sort_keys=True))

def canonical_repair(args):
    from pcs.data.access import PCSDataAccess
    from pcs.data.canonical_recovery import CanonicalRecoveryService
    access=PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)
    service=CanonicalRecoveryService(access)
    if args.action == "apply":
        if not args.plan_id:
            print(json.dumps({"status":"BLOCKED","reason_codes":["REPAIR_PLAN_ID_REQUIRED"]})); return
        print(json.dumps(service.apply(args.plan_id), sort_keys=True, default=str, indent=2 if args.json else None)); return
    plan=service.plan(dataset=args.dataset,symbol=args.symbol,year=args.year,quarter=args.quarter)
    plan_path=Path(service.plan_root) / f"{plan.repair_plan_id}.json"
    result=plan.to_dict(); result["plan_path"]=str(plan_path)
    print(json.dumps(result, sort_keys=True, default=str, indent=2 if args.json else None))

def doctor(args):
    from pcs.recovery import SystemHealthController
    from pcs.data.access import PCSDataAccess
    result=SystemHealthController(PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)).ensure_capability(
        "LIVE_PCS_DECISION" if args.mode == "live" else "EOD_PCS_DECISION", args.symbol, args.as_of or "2099-12-31")
    print(json.dumps(result.to_dict(), sort_keys=True, default=str, indent=2 if args.json else None))


def covered_call_status(args):
    """One-ticker covered-call admission/decision status.

    This command deliberately stops at the ticker's own profile/readiness
    gate.  It never scans a sibling ticker or substitutes another ticker's
    research artifacts.
    """
    from pcs.data.access import PCSDataAccess
    from pcs.research.covered_call_decision import (evaluate_covered_call, evaluate_nvdl_research,
        evaluate_covered_call_research_only, build_pit_entry_features)
    from pcs.research.covered_call_profiles import resolve_covered_call_profile
    from pcs.research.ticker_readiness import preflight_ticker

    symbol = str(args.symbol).strip().upper()
    if args.research_only and symbol != "NVDL":
        access = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)
        result = evaluate_covered_call_research_only(
            symbol, args.as_of, data_access=access,
            market=args.market_context or {},
            event_context=args.event_context or {"earnings_status": "UNKNOWN"},
            shares_owned=args.shares_owned if args.shares_owned is not None else None,
            active_calls=args.active_calls or 0)
        print(json.dumps(result, sort_keys=True, default=str))
        return
    if symbol == "NVDL" and args.research_only:
        access = PCSDataAccess(manifest_path=args.manifest_path, parquet_root=args.parquet_root)
        stock = build_pit_entry_features(access.read_prices(symbol, end_date=args.as_of), as_of_date=args.as_of)
        from pcs.research.covered_call_research import read_pit_call_chain
        result = evaluate_nvdl_research(as_of_date=args.as_of, stock=stock,
            quotes=read_pit_call_chain(symbol, args.as_of, data_access=access),
            shares_owned=args.shares_owned, active_calls=args.active_calls)
        print(json.dumps(result, sort_keys=True, default=str))
        return
    # Production requests have one canonical orchestration path.  The former
    # implementation performed an independent preflight, swallowed refresh
    # exceptions, then invoked the evaluator again, allowing readiness and
    # strategy data to drift apart.
    from pcs.covered_call_executor import execute_covered_call_request
    result = execute_covered_call_request(
        symbol, "live", as_of=args.as_of, research_only=False,
        overrides={"shares_owned": args.shares_owned, "active_calls": args.active_calls}
        if args.shares_owned is not None else None,
        adapters={"data_access": PCSDataAccess(
            manifest_path=args.manifest_path, parquet_root=args.parquet_root)})
    print(json.dumps(result, sort_keys=True, default=str))
    return


def covered_call_status_executor(args):
    from pcs.covered_call_executor import execute_covered_call_request
    result = execute_covered_call_request(
        args.symbol, args.mode, as_of=args.as_of, research_only=args.research_only,
        overrides={"shares_owned": args.shares_owned, "active_calls": args.active_calls}
        if args.shares_owned is not None else None)
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

    status_cmd = sub.add_parser("pcs-status", help="read-only deterministic PCS decision for one ticker")
    status_cmd.add_argument("symbol")
    status_cmd.add_argument("--mode", choices=["eod", "live"], default="eod")
    status_cmd.add_argument("--as-of", required=True)
    status_cmd.add_argument("--rules", default="config/pcs_rules.yaml")
    status_cmd.add_argument("--parquet-root", default="data/parquet")
    status_cmd.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    status_cmd.add_argument("--portfolio-json")
    status_cmd.add_argument("--json", action="store_true")
    status_cmd.set_defaults(func=pcs_status)

    admin = sub.add_parser("admin", help="administrator diagnostics and recovery tools")
    admin_sub = admin.add_subparsers(required=True)

    rollback = admin_sub.add_parser("canonical-rollback", help="switch a logical partition to its previous immutable generation")
    rollback.add_argument("--dataset", required=True); rollback.add_argument("--symbol", required=True)
    rollback.add_argument("--year", type=int, required=True); rollback.add_argument("--quarter", type=int, required=True)
    rollback.add_argument("--parquet-root", default="data/parquet"); rollback.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    rollback.set_defaults(func=canonical_rollback)

    repair = admin_sub.add_parser("canonical-repair", help="inspect or safely migrate a canonical partition")
    repair.add_argument("action", choices=["plan", "apply"]); repair.add_argument("--dataset", required=True); repair.add_argument("--symbol", required=True)
    repair.add_argument("--year", type=int, required=True); repair.add_argument("--quarter", type=int, required=True); repair.add_argument("--apply", action="store_true"); repair.add_argument("--plan-id")
    repair.add_argument("--json", action="store_true"); repair.add_argument("--parquet-root", default="data/parquet"); repair.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    repair.set_defaults(func=canonical_repair)

    generations = admin_sub.add_parser("canonical-generations", help="list immutable generations without garbage collection")
    generations.add_argument("action", choices=["list"]); generations.add_argument("--dataset", required=True); generations.add_argument("--symbol", required=True)
    generations.add_argument("--year", type=int, required=True); generations.add_argument("--quarter", type=int, required=True)
    generations.add_argument("--parquet-root", default="data/parquet"); generations.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    generations.set_defaults(func=lambda a: print(json.dumps(__import__("pcs.data.canonical_generations", fromlist=["list_generations"]).list_generations(dataset=a.dataset,symbol=a.symbol,year=a.year,quarter=a.quarter,data_access=__import__("pcs.data.access", fromlist=["PCSDataAccess"]).PCSDataAccess(manifest_path=a.manifest_path,parquet_root=a.parquet_root)), default=str, indent=2)))

    doc = admin_sub.add_parser("doctor", help="inspect and safely prepare system capabilities")
    doc.add_argument("symbol", nargs="?"); doc.add_argument("--mode", choices=["eod","live"], default="eod"); doc.add_argument("--as-of"); doc.add_argument("--json", action="store_true")
    doc.add_argument("--parquet-root", default="data/parquet"); doc.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv"); doc.set_defaults(func=doctor)

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
    cc.add_argument("--as-of")
    cc.add_argument("--mode", choices=["eod", "live"], default="eod")
    cc.add_argument("--shares-owned", type=int, required=False)
    cc.add_argument("--active-calls", type=int, default=0)
    cc.add_argument("--event-context", type=json.loads, default=None)
    cc.add_argument("--market-context", type=json.loads, default=None)
    cc.add_argument("--research-only", action="store_true", help="NVDL state-aware research decision; never production authorization")
    cc.add_argument("--parquet-root", default="data/parquet")
    cc.add_argument("--manifest-path", default="data/manifests/storage_manifest.csv")
    cc.set_defaults(func=covered_call_status_executor)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
