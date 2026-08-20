import pandas as pd
import pytest

from pcs.research.phase0_replay import (build_coverage, map_target_to_listed_strike,
    normalize_candidate_universe, resolve_canonical_source, validate_lifecycle, validate_point_in_time)
from pcs.research.credit_stop import load_quotes_canonical, load_quotes_canonical_index


def test_strike_mapping_never_invents_strike():
    assert map_target_to_listed_strike(97, [90, 95, 100]) == 95
    assert map_target_to_listed_strike(89, [90, 95]) is None


def test_point_in_time_rejects_future_sources():
    f = pd.DataFrame({"decision_timestamp": ["2024-01-01"], "chain_timestamp": ["2024-01-02"]})
    assert "FUTURE_CHAIN_DATA" in validate_point_in_time(f)


def test_coverage_preserves_rejected_rows():
    f = pd.DataFrame({"chain_available": [True, False], "complete_universe": [True, False],
                      "lifecycle_complete": [True, False], "support_available": [True, False],
                      "trend_gate_available": [True, False], "event_data_valid": [True, False],
                      "fully_replayable": [True, False], "missing_data_reasons": [[], ["NO_CHAIN"]]})
    c = build_coverage(f, "MU", "declared")
    assert c.total_candidate_dates == 2 and c.fully_replayable_pct == 50.0
    assert "NO_CHAIN" in c.missing_data_reasons


def test_universe_normalization_keeps_rejected_rows_and_nulls():
    out = normalize_candidate_universe(pd.DataFrame({"ticker": ["MU", "MU"], "candidate_status": ["ACCEPT", "REJECT"]}))
    assert len(out) == 2 and out.loc[1, "chain_available"] is pd.NA


def test_lifecycle_requires_explicit_missing_quote_state():
    f = pd.DataFrame({"mark_date": ["2024-01-02"], "expiration": ["2024-01-05"],
                      "short_strike": [95], "long_strike": [90], "quote_available": [pd.NA]})
    assert "UNMARKED_MISSING_QUOTE" in validate_lifecycle(f)


def test_canonical_source_is_ticker_specific_and_manifest_backed():
    tsla = resolve_canonical_source("TSLA")
    mu = resolve_canonical_source("MU")
    assert "symbol=TSLA" in tsla["glob"] and "symbol=MU" in mu["glob"]
    assert tsla["imported_row_count"] > 12_000_000 and mu["imported_row_count"] > 3_000_000


def test_canonical_query_cannot_substitute_qqq():
    tsla, meta = load_quotes_canonical("TSLA", "2020-01-02", "2020-01-02")
    assert meta["symbol"] == "TSLA"
    assert tsla.empty or tsla["Trade Date"].max() <= pd.Timestamp("2020-01-02")


def test_bounded_index_matches_canonical_loader():
    baseline, _ = load_quotes_canonical("MU", "2020-01-02", "2020-01-03")
    index, meta = load_quotes_canonical_index("MU", "2020-01-02", "2020-01-03")
    optimized = pd.concat(index.values(), ignore_index=True).sort_values(
        ["Trade Date", "Expiry Date", "Strike", "Call/Put"]
    ).reset_index(drop=True)
    expected = baseline.sort_values(
        ["Trade Date", "Expiry Date", "Strike", "Call/Put"]
    ).reset_index(drop=True)
    pd.testing.assert_frame_equal(expected, optimized, check_dtype=False)
    assert meta["scan_count"] == 1
