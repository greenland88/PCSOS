"""Independent breakout/retest API over one hash-verified saved input."""
from __future__ import annotations
import argparse
from pcs.pool.opportunities import read_opportunity_bundle
from pcs.trend.breakout_retest import detect_breakout_retest
from pcs.trend.selection_models import BreakoutRetestInput, OpportunityInput


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory",required=True)
    parser.add_argument("--symbol",required=True)
    parser.add_argument("--as-of",required=True)
    args=parser.parse_args()
    _,docs=read_opportunity_bundle(args.input_directory)
    raw=next(x for x in docs["prepared_opportunity_inputs.json"]
             if x["call_context"]["symbol"]==args.symbol.upper())
    source=OpportunityInput.model_validate(raw)
    if args.as_of>source.call_context.effective_daily_session:
        raise ValueError("SAVED_INPUT_END_EXCEEDED")
    context=source.call_context.model_copy(update={"requested_as_of":args.as_of,
        "effective_daily_session":args.as_of})
    result=detect_breakout_retest(BreakoutRetestInput(call_context=context,
        feature_view=source.feature_view,effective_policy=source.breakout_policy,
        opportunity_policy=source.effective_policy,calendar=source.calendar))
    print(result.model_dump_json(indent=2))


if __name__=="__main__":
    main()
