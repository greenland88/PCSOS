import pytest
from pcs.pool.runner import run_pcs_pool


def test_runner_rejects_invalid_event_policy():
    with pytest.raises(ValueError, match="unsupported event policy"):
        run_pcs_pool(symbols=["AAA"], mode="EOD", event_policy="INVALID")


def test_runner_requires_planned_exit_buffer():
    with pytest.raises(ValueError, match="positive exit buffer"):
        run_pcs_pool(symbols=["AAA"], mode="EOD", event_policy="PLANNED_EARLY_EXIT")
