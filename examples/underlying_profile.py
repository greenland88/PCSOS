"""Independent public API caller. Run from the canonical project's working directory.

python H:/workspace/PCSOS-selection-v2-step-02/examples/underlying_profile.py --symbol NVDA --as-of 2026-09-04
Use --fixture for an explicit TEST source, with no filesystem data or network reads.
"""
import argparse

from pcs.analysis_contracts import CallContext, SourceReference
from pcs.trend.selection_models import DailyBar, DailyFeatureView, ProfileInput, ProfilePolicy
from pcs.trend.underlying_profile import measure_underlying_profile, profile_sessions


def fixture_input(*, missing=False):
    policy = ProfilePolicy()
    sessions = profile_sessions("2026-09-04", policy)
    prices = [100.]*(len(sessions)-4)+[95., 90., 96., 100.]
    bars = [DailyBar(session=s, open=p, high=p+1, low=p-1, close=p,
                     volume=None if missing else 1000.) for s, p in zip(sessions, prices)]
    view = DailyFeatureView(symbol="TEST", bars=bars, source=SourceReference(
        source_id="TEST:recover-in-three-sessions", source_kind="TEST"), input_kind="TEST",
        price_basis="TEST_ADJUSTED", corporate_action_version="TEST", currency="USD")
    return ProfileInput(call_context=CallContext(symbol="TEST", requested_as_of="2026-09-04",
        effective_daily_session="2026-09-04", mode="HISTORICAL", run_id="fixture", request_id="fixture",
        scope="UNDERLYING_PROFILE"), feature_view=view, benchmark=None if missing else view)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NVDA")
    parser.add_argument("--as-of", default="2026-09-04")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--missing", action="store_true")
    args = parser.parse_args()
    if args.fixture:
        result = measure_underlying_profile(fixture_input(missing=args.missing))
    else:
        from pcs.pool.underlying_profiles import ProfileDataReader
        reader = ProfileDataReader()
        ctx = CallContext(symbol=args.symbol, requested_as_of=args.as_of, effective_daily_session=args.as_of,
            mode="HISTORICAL", run_id="independent-profile", request_id="independent-profile", scope="UNDERLYING_PROFILE")
        result = measure_underlying_profile(reader.load(ctx))
        reader.verify_unchanged()
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
