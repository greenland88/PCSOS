import time
import threading
import pytest

from pcs.pool.concurrency import run_symbol_workers


def test_workers_are_ordered_and_isolated():
    def worker(symbol):
        if symbol == "BAD":
            raise RuntimeError("bad ticker")
        return symbol.lower()

    results = run_symbol_workers(["AAA", "BAD", "CCC"], worker, max_workers=2)
    assert [item.symbol for item in results] == ["AAA", "BAD", "CCC"]
    assert results[0].value == "aaa"
    assert results[1].reason_codes == ("WORKER_FAILED", "RuntimeError")


def test_worker_timeout_is_explicit():
    release = threading.Event()
    started = time.perf_counter()
    try:
        results = run_symbol_workers(["SLOW", "QUEUED"], lambda _: release.wait(2),
                                     max_workers=1, timeout_seconds=.02)
        assert time.perf_counter() - started < .5
        assert results[0].reason_codes == ("WORKER_TIMEOUT",)
        assert results[1].reason_codes == ("STAGE_DEADLINE_NOT_STARTED",)
    finally:
        release.set()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout_cannot_start_work(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        run_symbol_workers(["AAA"], lambda _: pytest.fail("worker started"), timeout_seconds=timeout)
