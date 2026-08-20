from pcs.research.stability import _changes, _durations, _flip_flops, _reversals

def test_state_metrics():
    values=["A","A","B","A","A"]
    assert _changes(values)==[2,3]
    assert _flip_flops(["PASS","WATCH","PASS"],5)==[2]
    assert _reversals(["PASS","REJECT","PASS"],3)==[1]
    assert _durations(["A","A","B"]) == {"A":2.0,"B":1.0}

def test_metrics_do_not_read_future_rows():
    values=["A","B","A","C"]
    assert _flip_flops(values[:2],5)==[]
