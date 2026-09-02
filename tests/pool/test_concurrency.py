import time

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
    results = run_symbol_workers(["SLOW"], lambda _: time.sleep(.2), timeout_seconds=.01)
    assert results[0].reason_codes == ("WORKER_TIMEOUT",)
