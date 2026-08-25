import pandas as pd

from pcs.entry.contract_v2 import later_expirations, nearby_strikes


def chain():
    return pd.DataFrame({"expiration": ["2026-09-18"] * 6 + ["2026-10-16"],
                         "call_put": ["p"] * 7,
                         "strike": [90, 95, 100, 105, 110, 110, 120]})


def test_nearby_is_two_each_side_distinct_and_excludes_short():
    assert nearby_strikes(chain(), "2026-09-18", "p", 100) == 4
    assert nearby_strikes(chain(), "2026-09-18", "p", 90) == 2


def test_later_expirations_are_distinct_strictly_later_and_type_filtered():
    x = pd.concat([chain(), pd.DataFrame({"expiration": ["2026-10-16"], "call_put": ["c"], "strike": [100]})])
    assert later_expirations(x, "2026-09-18", "p") == 1
