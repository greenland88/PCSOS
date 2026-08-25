from datetime import date
import pytest
from pcs.data.executable_boundary import DEFAULT_EXECUTABLE_START_DATE, resolve_executable_start_date

def test_system_default_applies_to_jpm_and_meta():
    routes = {"executable_boundary": {"default_start_date": "2018-01-01", "ticker_overrides": {}}}
    assert DEFAULT_EXECUTABLE_START_DATE == date(2018, 1, 1)
    assert resolve_executable_start_date("JPM", routes) == date(2018, 1, 1)
    assert resolve_executable_start_date("META", routes) == date(2018, 1, 1)

def test_override_can_only_move_later():
    routes = {"executable_boundary": {"default_start_date": "2018-01-01", "ticker_overrides": {"META": "2020-01-01"}}}
    assert resolve_executable_start_date("META", routes) == date(2020, 1, 1)
    with pytest.raises(ValueError, match="CANNOT_MOVE_EARLIER"):
        resolve_executable_start_date("META", {"executable_boundary": {"default_start_date": "2018-01-01", "ticker_overrides": {"META": "2017-01-01"}}})
