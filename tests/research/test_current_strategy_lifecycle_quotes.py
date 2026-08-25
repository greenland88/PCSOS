import pandas as pd
import pytest

from pcs.research.current_strategy_replay import build_lifecycle_quote_rows
from pcs.research.stage4a_lifecycle import LifecycleAdapterError


def candidate():
    return {"ticker": "AMD", "candidate_id": "c1", "date": "2021-01-14",
            "expiration": "2021-02-19", "short_strike": 77.5, "long_strike": 72.5}


def chain(*rows):
    return pd.DataFrame(rows, columns=["symbol", "trade_date", "expiration_date", "strike", "call_put", "bid", "ask"])


def test_call_and_put_same_strike_selects_put_only():
    q = chain(
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "c", 14.75, 14.90],
        ["AMD", "2021-01-14", "2021-02-19", 72.5, "c", 19.05, 19.20],
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "p", 1.13, 1.14],
        ["AMD", "2021-01-14", "2021-02-19", 72.5, "p", .47, .48],
    )
    rows = build_lifecycle_quote_rows(q, candidate())
    assert len(rows) == 1
    assert rows[0]["short_bid"] == 1.13
    assert rows[0]["long_bid"] == .47


def test_wrong_option_type_fails_closed():
    q = chain(
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "c", 14.75, 14.90],
        ["AMD", "2021-01-14", "2021-02-19", 72.5, "c", 19.05, 19.20],
    )
    with pytest.raises(LifecycleAdapterError, match="MISSING"):
        build_lifecycle_quote_rows(q, candidate())


def test_duplicate_put_leg_fails_closed():
    q = chain(
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "p", 1.13, 1.14],
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "p", 1.12, 1.15],
        ["AMD", "2021-01-14", "2021-02-19", 72.5, "p", .47, .48],
    )
    with pytest.raises(LifecycleAdapterError, match="DUPLICATE"):
        build_lifecycle_quote_rows(q, candidate())


def test_missing_put_leg_fails_closed():
    q = chain(["AMD", "2021-01-14", "2021-02-19", 77.5, "p", 1.13, 1.14])
    with pytest.raises(LifecycleAdapterError, match="MISSING"):
        build_lifecycle_quote_rows(q, candidate())


def test_put_spread_debit_is_not_negative_from_call_mismatch():
    q = chain(
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "c", 14.75, 14.90],
        ["AMD", "2021-01-14", "2021-02-19", 72.5, "c", 19.05, 19.20],
        ["AMD", "2021-01-14", "2021-02-19", 77.5, "p", 1.13, 1.14],
        ["AMD", "2021-01-14", "2021-02-19", 72.5, "p", .47, .48],
    )
    row = build_lifecycle_quote_rows(q, candidate())[0]
    assert row["short_ask"] - row["long_bid"] >= 0
