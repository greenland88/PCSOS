import threading
from pcs.data.clickhouse import ClickHouseConfig, ClickHouseError, PCSClickHouseClient


class Response:
    def __init__(self, status, body): self.status_code, self._body, self.headers = status, body, {"X-ClickHouse-Query-Id": "qid"}
    @property
    def ok(self): return self.status_code < 400
    def iter_content(self, _size): yield self._body


class Session:
    def __init__(self, responses): self.responses, self.calls = list(responses), 0
    def mount(self, *_): pass
    def post(self, *args, **kwargs): self.calls += 1; return self.responses.pop(0)


def cfg(**kw): return ClickHouseConfig(pool_size=2, max_concurrency=1, max_attempts=kw.pop("max_attempts", 3), backoff_base=0, **kw)


def test_500_retries_and_reuses_session(tmp_path):
    s = Session([Response(500, b"Code: 241\nDB::Exception: memory limit"), Response(200, b"PAR1data")])
    c = PCSClickHouseClient("http://x", "u", "p", config=cfg(), session=s)
    d = c.query("SELECT 1", output=tmp_path / "x.parquet")
    assert d.http_status == 200 and s.calls == 2 and (tmp_path / "x.parquet").read_bytes() == b"PAR1data"


def test_repeated_500_fails_closed_with_bounded_body():
    s = Session([Response(500, b"Code: 241\nDB::Exception: " + b"x" * 1000)] * 3)
    c = PCSClickHouseClient("http://x", "u", "p", config=cfg(max_attempts=3), session=s)
    try: c.query("SELECT 1")
    except ClickHouseError as e:
        assert e.diagnostics.failure_class == "HTTP_5XX_TRANSIENT"
        assert e.diagnostics.clickhouse_code == "241"
        assert len(e.diagnostics.response_body) <= 64 * 1024
    else: assert False


def test_concurrency_gate():
    class Slow(Session):
        def __init__(self): super().__init__([])
        def post(self, *a, **k):
            import time; time.sleep(.02); return Response(200, b"ok")
    c = PCSClickHouseClient("http://x", "u", "p", config=cfg(), session=Slow())
    active = []
    def run(): active.append(c.query("SELECT 1").concurrency)
    ts = [threading.Thread(target=run) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert max(active) == 1
