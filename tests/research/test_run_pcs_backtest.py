from pcs.research.run_pcs_backtest import parse_args


def test_cli_args():
    args = parse_args(["--symbol", "QQQ", "--benchmark", "RSP", "--start-date", "2026-06-01", "--end-date", "2026-06-30", "--run-label", "smoke"])
    assert (args.symbol, args.benchmark, args.run_label) == ("QQQ", "RSP", "smoke")
