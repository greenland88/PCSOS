import pytest

from pcs.entry.contract_v2 import normalize_price_confirmation
from pcs.scoring.trend_score import score_trend


@pytest.mark.parametrize("native, expected", [(0, 0), (1, 25), (2, 50), (3, 75), (4, 100)])
def test_price_confirmation_v2_normalization(native, expected):
    assert normalize_price_confirmation(native) == expected


def test_existing_70_30_trend_score_is_preserved():
    class Candidate:
        trend_score = 80
        price_confirmation = 80
    assert score_trend(Candidate()) == 80
