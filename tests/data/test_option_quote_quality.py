import pandas as pd

from pcs.data.storage_schema import audit_option_frame


def row(**changes):
    value = {
        "symbol": "META", "trade_date": "2025-01-02", "expiration_date": "2025-02-21",
        "strike": 500.0, "call_put": "p", "bid": 1.0, "ask": 1.2,
    }
    value.update(changes)
    return value


def test_quality_boundary_quarantines_invalid_quotes_without_repairing_them():
    valid, quarantine, summary = audit_option_frame(pd.DataFrame([
        row(), row(strike=501, ask=.5), row(strike=502, bid=-1),
        row(strike=503, expiration_date="2024-12-31"), row(strike=504, call_put="x"),
    ]), source="fixture", source_member="META.txt", source_version="v1", partition="year=2025/quarter=1")
    assert len(valid) == 1
    assert len(quarantine) == 4
    assert set(quarantine.reason_code) == {"QUOTE_CROSSED", "QUOTE_BID_INVALID", "OPTION_EXPIRATION_INVALID", "OPTION_IDENTITY_INVALID"}
    assert summary["raw_rows"] == 5 and summary["executable_rows"] == 1
    assert (quarantine.source == "fixture").all()
    assert (quarantine.partition == "year=2025/quarter=1").all()


def test_duplicate_and_conflicting_identity_are_not_executable():
    valid, quarantine, summary = audit_option_frame(pd.DataFrame([
        row(), row(), row(bid=.8),
    ]))
    assert valid.empty
    assert set(quarantine.reason_code) == {"OPTION_CONFLICTING_IDENTITY"}
    assert summary["quarantined_rows"] == 3
