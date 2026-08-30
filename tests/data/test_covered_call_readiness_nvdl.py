from pcs.data.covered_call_readiness import EVENT_RISK_SYMBOL


def test_nvdl_uses_explicit_nvda_event_risk_source():
    assert EVENT_RISK_SYMBOL["NVDL"] == "NVDA"
def test_index_etf_earnings_are_not_required_for_covered_call_readiness():
    from pcs.data.covered_call_readiness import resolve_ticker_data_readiness
    result = resolve_ticker_data_readiness("QQQ")
    assert result.earnings_status == "NOT_REQUIRED"
    assert result.first_blocker != "EARNINGS_MISSING"


def test_options_permission_failure_has_specific_blocker_code():
    from pcs.data.covered_call_readiness import resolve_ticker_data_readiness
    class Access:
        def read_prices(self, symbol):
            import pandas as pd
            return pd.DataFrame({"date": ["2025-01-01"], "close": [1]})
        def resolve_source(self, *args, **kwargs):
            raise PermissionError("permission denied")
    result = resolve_ticker_data_readiness("SPY", access=Access())
    assert result.first_blocker == "OPTIONS_CANONICAL_FILE_ACCESS_DENIED"
