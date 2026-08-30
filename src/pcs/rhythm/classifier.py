from .models import AxisState, RhythmAxisState
def classify_axes(row, *, thresholds=None):
    t=thresholds or {"slope":.02,"acceleration":.01,"breadth_broad":.6,"breadth_narrow":.4}
    s=row.get("normalized_slope_20d"); a=row.get("acceleration_short");
    if s is None or a is None: trend=AxisState.UNKNOWN.value
    elif s>t["slope"] and a>t["acceleration"]: trend=AxisState.ACCELERATING_UP.value
    elif s>t["slope"]: trend=AxisState.DECELERATING_UP.value
    elif s< -t["slope"] and a< -t["acceleration"]: trend=AxisState.ACCELERATING_DOWN.value
    elif s< -t["slope"]: trend=AxisState.DECELERATING_DOWN.value
    else: trend=AxisState.RANGE.value
    breadth=row.get("breadth_50"); participation=AxisState.UNKNOWN.value if breadth is None else (AxisState.BROAD.value if breadth>=t["breadth_broad"] else AxisState.NARROW.value if breadth<=t["breadth_narrow"] else AxisState.BROADENING.value)
    ratio=row.get("rv5_rv20"); volatility=AxisState.UNKNOWN.value if ratio is None else AxisState.EXPANDING.value if ratio>1.2 else AxisState.COMPRESSING.value if ratio<.8 else AxisState.NORMAL.value
    rel=row.get("market_relative_20d"); relative=AxisState.UNKNOWN.value if rel is None else AxisState.LEADING.value if rel>.02 else AxisState.LAGGING.value if rel<-.02 else AxisState.SYNCHRONOUS.value
    return {k:RhythmAxisState(k,v,dict(row),()) for k,v in {"trend":trend,"participation":participation,"volatility":volatility,"relative_strength":relative}.items()}
