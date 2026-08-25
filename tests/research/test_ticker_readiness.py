import pandas as pd
import pytest

from pcs.research import ticker_readiness as gate


class MissingOptionsAccess:
    def read_prices(self, symbol, *args):
        dates = pd.date_range("2020-01-01", periods=240, freq="D")
        return pd.DataFrame({"date": dates, "open": 100.0, "high": 101.0,
                             "low": 99.0, "close": 100.0, "volume": 1000.0})

    def read(self, dataset, symbol, *args):
        raise FileNotFoundError("options route unavailable")


def test_gate_emits_exact_readiness_flags_and_all_blockers(monkeypatch):
    monkeypatch.setattr(gate, "evaluate_as_of", lambda *args, **kwargs: {"final_underlying_state": "NORMAL"})
    result = gate.preflight_ticker("TEST", access=MissingOptionsAccess())
    assert {name: getattr(result, name) for name in (
        "DATA_READY", "PIT_READY", "OPTIONS_READY", "CONTRACT_SELECTION_READY",
        "LIFECYCLE_READY", "PCS_RESEARCH_READY",
    )} == {
        "DATA_READY": "YES", "PIT_READY": "YES", "OPTIONS_READY": "NO",
        "CONTRACT_SELECTION_READY": "NO", "LIFECYCLE_READY": "NO",
        "PCS_RESEARCH_READY": "NO",
    }
    codes = {row["reason_code"] for row in result.blockers}
    assert "OPTIONS_ROUTE_OR_SOURCE_UNAVAILABLE" in codes
    assert "CONTRACT_SELECTION_BLOCKED_BY_OPTIONS" in codes


def test_admission_guard_fails_closed(monkeypatch):
    monkeypatch.setattr(gate, "evaluate_as_of", lambda *args, **kwargs: {"final_underlying_state": "NORMAL"})
    with pytest.raises(RuntimeError, match="PCS_RESEARCH_NOT_READY:TEST"):
        gate.assert_research_ready("TEST", access=MissingOptionsAccess())
