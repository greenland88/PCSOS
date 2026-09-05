import json
import multiprocessing
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from pcs.pool.models import EligibilityStatus, PoolRunSnapshot, PoolScanResult, TickerScanResult, TimingStatus
from pcs.pool.process import ReadOnlyScanRequest, _timeout_result, run_read_only_scan
from pcs.pool.artifacts import ProgressCheckpoint


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


def test_timeout_persists_completed_progress_and_marks_unfinished_rows(tmp_path):
    checkpoint = ProgressCheckpoint(tmp_path, "progress-run", metadata={
        "effective_daily_session": "2026-09-04"})
    checkpoint.save(TickerScanResult(
        "DONE", "progress-run", "2026-09-04", EligibilityStatus.PCS_ELIGIBLE,
        TimingStatus.WATCH, final_action="WATCH", reason_codes=("RULE_WATCH",)))
    spec = SimpleNamespace(universe_id="test", version="1", fingerprint="fp",
                           symbols=("DONE", "LATE"))
    result = _timeout_result(
        ReadOnlyScanRequest(symbols=spec.symbols, output_directory=str(tmp_path)),
        spec, time.perf_counter(), "POOL_SCAN_TIMEOUT", "deadline")
    assert [row.symbol for row in result.ticker_results] == ["DONE", "LATE"]
    assert result.ticker_results[0].final_action == "WATCH"
    assert result.ticker_results[1].reason_codes == ("POOL_SCAN_TIMEOUT",)
    manifest = json.loads((tmp_path / "progress-run" / "run_manifest.json").read_text())
    assert manifest["current"] is False
    assert manifest["stage_status"]["DAILY_TIMING"] == "PARTIAL"


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
