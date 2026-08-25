import pandas as pd
import pytest

from pcs.research.scheduled_event_calendar import load_calendar, tag_entry_dates, normalize_source_events, validate_events, ingest_offline_raw


def _trades():
    return pd.DataFrame({"entry_date":["2025-01-02"],"expiration":["2025-02-01"],"x":[1]})


def test_tagging_is_entry_date_based_and_does_not_mutate():
    t=_trades(); original=t.copy(deep=True)
    c=pd.DataFrame({"event_date":["2025-01-06"],"event_type":["FOMC"],"source":["fed"],"source_version":["v1"]})
    out=tag_entry_dates(t,c)
    assert bool(out.loc[0,"FOMC_inside_5d"])
    assert out.loc[0,"FOMC_inside_DTE"]
    pd.testing.assert_frame_equal(t,original)


def test_invalid_event_type_rejected(tmp_path):
    p=tmp_path/"events.csv"
    pd.DataFrame({"event_date":["2025-01-01"],"event_type":["UNKNOWN"],"source":["x"],"source_version":["1"]}).to_csv(p,index=False)
    with pytest.raises(ValueError): load_calendar(p)


def test_empty_calendar_is_explicitly_supported(tmp_path):
    d=load_calendar(tmp_path/"missing.csv")
    assert d.empty


def test_versioned_normalization_and_duplicate_audit():
    e=normalize_source_events(pd.DataFrame({"event_type":["FOMC","FOMC"],"event_date":["2025-01-29","2025-01-29"],"symbol":[None,None]}),"fed","v1","fed://2025")
    a=validate_events(e)
    assert len(e)==2
    assert (a.duplicate_event_check=="FAIL").all()


def test_earnings_symbol_validation():
    e=normalize_source_events(pd.DataFrame({"event_type":["EARNINGS"],"event_date":["2025-01-01"],"symbol":["QQQ"]}),"x","v1","x")
    assert validate_events(e).iloc[0].validation_status=="FAIL"


def test_offline_csv_ingestion_requires_provenance(tmp_path):
    raw=tmp_path/"raw"; (raw/"fomc").mkdir(parents=True)
    pd.DataFrame({"event_date":["2025-01-29"],"event_type":["FOMC"],"source_url":["fed://2025"],"source_name":["Fed"],"source_version":["2025"],"provenance_status":["VERIFIED"]}).to_csv(raw/"fomc"/"f.csv",index=False)
    result=ingest_offline_raw(raw,tmp_path/"out")
    assert result["rows"]==1
    assert (tmp_path/"out"/"scheduled_event_calendar_v1.csv").exists()
