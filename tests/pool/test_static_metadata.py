from pcs.pool.models import EligibilityStatus
from pcs.pool.runner import _evaluate_symbol


def test_static_metadata_is_applied_before_daily_stage():
    row = _evaluate_symbol("AAA", run_id="r", asof="2025-01-01", access=None,
                           benchmark=None, benchmark_symbol="QQQ", options_reader=None,
                           option_rules=None, static_metadata_reader=lambda _: {"optionable": False})
    assert row.eligibility_status == EligibilityStatus.HARD_EXCLUDED
    assert row.reason_codes == ("OPTIONS_NOT_LISTED",)
