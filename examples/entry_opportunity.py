"""Independent public API example for Step 4."""
import argparse
import json

from pcs.analysis_contracts import CallContext
from pcs.pool.opportunities import OpportunityDataReader
from pcs.trend.opportunity_engine import evaluate_entry_opportunity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--as-of", required=True)
    args = parser.parse_args()
    symbol = args.symbol.strip().upper()
    context = CallContext(symbol=symbol, requested_as_of=args.as_of,
        effective_daily_session=args.as_of, mode="HISTORICAL",
        run_id="independent-example", request_id=f"independent-example:{symbol}",
        scope="ENTRY_OPPORTUNITY_V2_OBSERVATION")
    reader = OpportunityDataReader()
    result = evaluate_entry_opportunity(reader.load(context))
    reader.verify_unchanged()
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
