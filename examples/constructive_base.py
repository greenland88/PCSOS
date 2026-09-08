"""Independent platform API over one hash-verified saved prepared input."""
import argparse
from pcs.pool.opportunities import read_opportunity_bundle
from pcs.trend.constructive_base import detect_constructive_base
from pcs.trend.selection_models import ConstructiveBaseInput, OpportunityInput


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-directory', required=True)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    _, docs = read_opportunity_bundle(args.input_directory)
    saved = next((x for x in docs['prepared_opportunity_inputs.json']
                  if x['call_context']['symbol'] == args.symbol.upper()), None)
    if saved is None:
        raise ValueError('BASE_SAVED_SYMBOL_MISSING')
    source = OpportunityInput.model_validate(saved)
    if args.as_of > source.call_context.effective_daily_session:
        raise ValueError('BASE_SAVED_INPUT_END_EXCEEDED')
    context = source.call_context.model_copy(update={'requested_as_of':args.as_of,
        'effective_daily_session':args.as_of,'mode':'HISTORICAL'})
    result = detect_constructive_base(ConstructiveBaseInput(call_context=context,feature_view=source.feature_view,
        effective_policy=source.base_policy,opportunity_policy=source.effective_policy,
        structure_evidence=source.base_structure_evidence,calendar=source.calendar))
    if args.summary:
        print(f'{result.symbol} / {result.as_of}：{result.explanation}')
        print(f'候选{len(result.formation_candidates)}，形成{len(result.base_events)}，'
              f'实时触及{sum(e.retest_session is not None for e in result.base_events)}，'
              f'确认{sum(e.confirmation_session is not None for e in result.base_events)}；{result.result_id}')
    else:
        print(result.model_dump_json(indent=2))


if __name__ == '__main__':
    main()
