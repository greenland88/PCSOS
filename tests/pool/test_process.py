import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import time

import pytest

from pcs.pool.models import EligibilityStatus, PoolRunSnapshot, PoolScanResult, TickerScanResult
from pcs.pool.process import ReadOnlyScanRequest, run_read_only_scan


def _result_then_hang(request, sender):
    rows = tuple(TickerScanResult(symbol, "fixture", request.as_of, EligibilityStatus.DATA_BLOCKED,
                                  reason_codes=("WORKER_TIMEOUT",)) for symbol in request.symbols)
    result = PoolScanResult(PoolRunSnapshot("fixture", request.as_of, request.mode, None, "fixture"),
                           rows, {"missing_ticker_decisions": 0, "run_status": "PARTIAL_TIMEOUT"})
    sender.send(("result", result))
    time.sleep(60)


def _exit_without_result(request, sender):
    sender.close()


def _never_return(request, sender):
    time.sleep(60)


def test_completed_timeout_result_does_not_leave_child_alive():
    before = {child.pid for child in multiprocessing.active_children()}
    result = run_read_only_scan(ReadOnlyScanRequest(symbols=("BBB", "AAA")),
                                timeout_seconds=15, _worker=_result_then_hang)
    assert [row.symbol for row in result.ticker_results] == ["BBB", "AAA"]
    assert result.snapshot.run_id == "fixture"
    assert result.ticker_results[0].reason_codes == ("WORKER_TIMEOUT",)
    assert {child.pid for child in multiprocessing.active_children()} == before


@pytest.mark.parametrize("worker,timeout,reason", [
    (_never_return, .2, "POOL_SCAN_TIMEOUT"),
    (_exit_without_result, 15, "POOL_SCAN_PROCESS_FAILED"),
])
def test_process_failure_returns_one_ordered_failure_per_symbol(worker, timeout, reason):
    result = run_read_only_scan(ReadOnlyScanRequest(symbols=("BBB", "AAA")),
                                timeout_seconds=timeout, _worker=worker)
    assert [row.symbol for row in result.ticker_results] == ["BBB", "AAA"]
    assert all(row.reason_codes == (reason,) and row.spread_count == 0 for row in result.ticker_results)
    assert result.summary["missing_ticker_decisions"] == 0


def test_cli_process_deadline_and_startup_observability(tmp_path):
    root = Path(__file__).parents[2]
    completed = subprocess.run([
        sys.executable, "-m", "pcs.cli", "pool-scan", "--mode", "EOD",
        "--symbol", "BBB", "--symbol", "AAA", "--as-of", "2026-09-03T21:00:00Z",
        "--manifest-path", str(tmp_path / "manifest.csv"),
        "--parquet-root", str(tmp_path / "parquet"), "--scan-timeout-seconds", ".001",
    ], cwd=root, capture_output=True, text=True, timeout=15)
    assert completed.returncode == 2, completed.stderr
    started = json.loads(completed.stderr.splitlines()[0])
    assert started["status"] == "POOL_SCAN_STARTED"
    payload = json.loads(completed.stdout)
    assert [row["symbol"] for row in payload["ticker_results"]] == ["BBB", "AAA"]
    assert all(row["reason_codes"] == ["POOL_SCAN_TIMEOUT"] for row in payload["ticker_results"])
    assert not (tmp_path / "manifest.csv").exists()
    assert not list(tmp_path.rglob("*.parquet"))


def test_startup_is_flushed_before_scan_setup(monkeypatch, capsys):
    import pcs.cli as cli
    import pcs.pool.process as process

    def check_started(*args, **kwargs):
        assert json.loads(capsys.readouterr().err)["status"] == "POOL_SCAN_STARTED"
        raise RuntimeError("setup reached")

    monkeypatch.setattr(process, "run_read_only_scan", check_started)
    monkeypatch.setattr(sys, "argv", ["pcs", "pool-scan", "--mode", "EOD", "--symbol", "AAA"])
    with pytest.raises(RuntimeError, match="setup reached"):
        cli.main()


@pytest.mark.parametrize("args", [
    ["--stage-timeout-seconds", "nan"], ["--scan-timeout-seconds", "inf"],
    ["--max-workers", "0"], ["--data-mode", "PREPARE_THEN_SCAN"],
])
def test_cli_rejects_invalid_requests_before_start(monkeypatch, capsys, args):
    import pcs.cli as cli
    monkeypatch.setattr(sys, "argv", ["pcs", "pool-scan", "--mode", "EOD", *args])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "POOL_SCAN_STARTED" not in capsys.readouterr().err
