import pandas as pd
import pytest

from pcs.data.storage_schema import canonicalize_option_frame


def _rows():
    return pd.DataFrame([
        {"Unnamed: 0": 10, "symbol": "qqq", "trade_date": "2023-01-03", "expiration_date": "2023-01-20", "strike": 300, "call_put": "C", "last": 1.0, "bid": .9, "ask": 1.1},
        {"Unnamed: 0": 11, "symbol": "qqq", "trade_date": "2023-01-03", "expiration_date": "2023-01-20", "strike": 300, "call_put": "C", "last": 1.0, "bid": .9, "ask": 1.1},
    ])


def test_exact_same_key_payload_keeps_first_source_row():
    result = canonicalize_option_frame(_rows())
    assert len(result) == 1
    assert "Unnamed: 0" not in result
    assert result.iloc[0].symbol == "QQQ"


def test_conflicting_same_key_payload_fails_closed():
    conflicting = _rows().copy()
    conflicting.loc[1, "bid"] = .8
    with pytest.raises(ValueError, match="conflicting option payloads"):
        canonicalize_option_frame(conflicting)


def test_canonicalization_is_idempotent():
    once = canonicalize_option_frame(_rows())
    twice = canonicalize_option_frame(once)
    assert twice.equals(once)


def test_conflict_is_not_silently_reduced_to_one_row():
    conflicting = _rows().copy()
    conflicting.loc[1, "last"] = 1.01
    with pytest.raises(ValueError):
        canonicalize_option_frame(conflicting)
