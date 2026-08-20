import argparse
import json
from pathlib import Path

from pcs.collectors.option_chain_snapshot import OptionChainSnapshotCollector
from pcs.data.storage import ParquetStore
from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.providers.hood_trader_provider import HoodTraderProvider, JsonHoodClient
from pcs.providers.mock_provider import MockProvider
from pcs.research.youtube_subtitles import DEFAULT_TRANSCRIPT_DIR, download_youtube_subtitles
from pcs.simulation.paper_trading import run_daily_paper_trading
from pcs.stress_lab.scenarios import DEFAULT_SYNTHETIC_SCENARIOS, StressLab


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


def main():
    parser = argparse.ArgumentParser(prog="pcs-lite")
    sub = parser.add_subparsers(required=True)

    collect = sub.add_parser("collect-options", help="write read-only option-chain snapshots to Parquet")
    collect.add_argument("--hood-json", help="local exported Hood payload JSON; omit to use MockProvider")
    collect.add_argument("--data-root", default="data")
    collect.add_argument("symbols", nargs="+")
    collect.set_defaults(func=collect_options)

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

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
