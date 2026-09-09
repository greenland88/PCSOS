"""Run a declared single stock/batch with the canonical pool runner."""
import argparse
from pathlib import Path
from pcs.pool.observation import run_stock_observation,resume_stock_observation,read_stock_observation
from pcs.pool.observation_models import StockObservationInput,ObservationResumeInput,ObservationQuery


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec')
    parser.add_argument('--resume-run-id')
    parser.add_argument('--query-symbol')
    args=parser.parse_args()
    spec=StockObservationInput.model_validate_json(Path(args.spec).read_text(encoding='utf-8'))
    result=resume_stock_observation(ObservationResumeInput(spec=spec,run_id=args.resume_run_id)) if args.resume_run_id else run_stock_observation(spec)
    print(result.model_dump_json(indent=2))
    if args.query_symbol:
        query=read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol=args.query_symbol))
        print(query.model_dump_json(indent=2))


if __name__=='__main__':main()
