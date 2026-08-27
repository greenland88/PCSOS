import pandas as pd

from pcs.research.entry_candidate_universe import (
    build_historical_setup_context,
    build_historical_setup_context_table,
)


def _summary(ctx):
    snapshot = ctx.get("snapshot")
    return {
        "available": ctx.get("available"),
        "trend_state": ctx.get("trend_state"),
        "pullback_state": ctx.get("pullback_state"),
        "support_state": ctx.get("support_state"),
        "predictability_state": ctx.get("predictability_state"),
        "trend_gate": getattr(ctx.get("trend_gate_result"), "trend_gate_result", None),
        "pullback_gate": getattr(ctx.get("pullback_gate_result"), "pullback_gate_result", None),
        "underlying": getattr(getattr(snapshot, "pullback", None), "current_close", None),
        "atr": getattr(getattr(snapshot, "support", None), "current_atr", None),
        "support_confluence": getattr(getattr(snapshot, "support", None), "support_confluence_state", None),
    }


def test_cached_setup_context_is_equivalent_on_representative_dates():
    from pcs.data.access import PCSDataAccess
    access = PCSDataAccess()
    daily = access.read_prices("NVDA", "2020-01-01", "2025-12-31")
    bench = access.read_prices("QQQ", "2020-01-01", "2025-12-31")
    dates = pd.to_datetime(["2021-02-08", "2022-02-08", "2023-06-01", "2024-06-07", "2025-02-10"])
    table = build_historical_setup_context_table(daily, bench, dates, "NVDA", "QQQ")
    for day in dates:
        old = build_historical_setup_context(daily, bench, day, "NVDA", "QQQ")
        new = table[day.normalize()]
        left, right = _summary(old), _summary(new)
        for key in ("underlying", "atr"):
            assert abs(float(left[key]) - float(right[key])) < 1e-10
        assert {k: v for k, v in left.items() if k not in {"underlying", "atr"}} == {k: v for k, v in right.items() if k not in {"underlying", "atr"}}
