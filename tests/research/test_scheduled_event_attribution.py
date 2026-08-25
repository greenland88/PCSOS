import pandas as pd
from pcs.research.scheduled_event_attribution import _tag

def test_event_tagging_keeps_occurrence_and_entry_flags_separate():
    trades=pd.DataFrame({"date":pd.to_datetime(["2025-01-02"]),"expiration":pd.to_datetime(["2025-02-01"]),"symbol":["NVDA"]})
    cal=pd.DataFrame({"event_date":pd.to_datetime(["2025-01-06"]),"event_type":["FOMC"],"symbol":[pd.NA]})
    out=_tag(trades,cal)
    assert bool(out.loc[0,"FOMC_inside_5d"])
    assert "ANY_SCHEDULED_EVENT_inside_10d" in out
