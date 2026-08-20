from pcs.research.popular100_preflight import POPULAR
def test_popular_list_is_fixed_and_unique():
    assert len(POPULAR)==100
    assert len(set(POPULAR))==100
