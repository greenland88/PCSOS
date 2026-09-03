import threading

from pcs.pool.runtime import PoolRuntime


def test_resolve_options_single_flight_for_session():
    calls = []
    entered = threading.Event()
    release = threading.Event()

    def resolver(symbol, as_of, *, data_access=None, manifest_snapshot=None):
        calls.append((symbol, as_of))
        entered.set()
        release.wait(timeout=2)
        return {"symbol": symbol, "as_of": as_of}

    runtime = PoolRuntime(options_handle_resolver=resolver)
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        runtime.resolve_options(" nvda ", "2026-09-01"))) for _ in range(2)]
    threads[0].start()
    assert entered.wait(timeout=2)
    threads[1].start()
    release.set()
    for thread in threads: thread.join()
    assert calls == [("NVDA", "2026-09-01")]
    assert results == [{"symbol": "NVDA", "as_of": "2026-09-01"}] * 2
