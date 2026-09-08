"""Independent detector from a verified saved input; no vendor or scanner call."""
from __future__ import annotations

import argparse
import json
from pcs.pool.opportunities import read_opportunity_bundle
from pcs.trend.selection_models import OpportunityInput, ShallowPullbackInput
from pcs.trend.setup_detectors import detect_shallow_pullback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-directory", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    _, documents = read_opportunity_bundle(args.input_directory)
    saved = next(x for x in documents["prepared_opportunity_inputs.json"]
                 if x["call_context"]["symbol"] == args.symbol)
    source = OpportunityInput.model_validate(saved)
    if args.as_of > source.call_context.effective_daily_session:
        raise ValueError("SAVED_INPUT_END_EXCEEDED")
    context = source.call_context.model_copy(update={"requested_as_of": args.as_of,
        "effective_daily_session": args.as_of})
    result = detect_shallow_pullback(ShallowPullbackInput(call_context=context,
        feature_view=source.feature_view, support_facts=source.support_facts,
        effective_policy=source.shallow_policy, shared_policy=source.effective_policy))
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
