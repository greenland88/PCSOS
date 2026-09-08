"""Independent public support-zone call.

Canonical example (run from H:/workspace/PCSOS):
  python H:/workspace/PCSOS-selection-v2-step-03/examples/support_zones.py --symbol NVDA --as-of 2026-09-04
Add --fixture for a provider-free TEST sequence.
"""
import argparse
import pandas as pd

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.selection_models import SupportFeatureBar, SupportFeatureView, SupportZoneInput
from pcs.trend.support_zones import evaluate_support_zones


def fixture_input():
    dates = [str(d.date()) for d in pd.bdate_range("2025-01-06", periods=9)]
    facts = [
        {"low": 101, "high": 102, "close": 101},
        {"low": 99.5, "high": 101, "close": 100},
        {"low": 99.5, "high": 102, "close": 101.4},
        {"low": 101, "high": 103, "close": 101.5},
        {"low": 99.8, "high": 101, "close": 100},
        {"low": 99.5, "high": 102, "close": 101.4},
    ]
    bars = [SupportFeatureBar(session=s, open=101, sma20=100, sma50=100.2,
        atr14=2, **row) for s, row in zip(dates, facts)]
    view = SupportFeatureView(symbol="TEST", bars=bars, confirmed_swings=[],
        expected_sessions=dates, analysis_start=dates[0], indicator_seed_start="TEST:seed",
        indicator_identity="TEST:precomputed:sma20:sma50:atr14:pivot3x3",
        source=SourceReference(source_id="TEST:daily", source_kind="TEST", record_identity="fixture-v1"),
        price_basis="TEST_ADJUSTED", corporate_action_version="TEST", input_kind="TEST")
    ctx = CallContext(symbol="TEST", requested_as_of=dates[5], effective_daily_session=dates[5],
        mode="HISTORICAL", run_id="fixture", request_id="fixture", scope="SUPPORT_ZONES")
    return SupportZoneInput(call_context=ctx, feature_view=view)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NVDA")
    parser.add_argument("--as-of", default="2026-09-04")
    parser.add_argument("--fixture", action="store_true")
    args = parser.parse_args()
    if args.fixture:
        result = evaluate_support_zones(fixture_input())
    else:
        from pcs.pool.support_zones import SupportZoneDataReader
        reader = SupportZoneDataReader()
        ctx = CallContext(symbol=args.symbol, requested_as_of=args.as_of,
            effective_daily_session=args.as_of, mode="HISTORICAL", run_id="independent-support",
            request_id="independent-support", scope="SUPPORT_ZONES")
        result = evaluate_support_zones(reader.load(ctx))
        reader.verify_unchanged()
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
