from pcs.research.run_pcs_backtest import _write


def test_empty_rerun_removes_stale_output(tmp_path):
    target = tmp_path / "backtest_trades.csv"
    _write(target, [{"run_id": "old", "pnl": 1}])
    assert target.exists()
    _write(target, [])
    assert not target.exists()
