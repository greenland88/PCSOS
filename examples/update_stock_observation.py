"""Create a CURRENT_EOD request spec; does not read prices or start a scan."""
import argparse
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
from pcs.analysis_contracts import CallContext
from pcs.selection.models import BatchContext
from pcs.trend.opportunity_state import _resolve_requested_session
from pcs.pool.observation_models import StockObservationInput


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec')
    parser.add_argument('--output',required=True)
    parser.add_argument('--previous-run',required=True)
    parser.add_argument('--requested-as-of',default=None)
    args=parser.parse_args()
    old=StockObservationInput.model_validate_json(Path(args.spec).read_text(encoding='utf-8'))
    requested=args.requested_as_of or datetime.now(timezone.utc).isoformat()
    run_id=uuid4().hex
    context=CallContext(symbol=old.symbols[0],requested_as_of=requested,mode='CURRENT_EOD',run_id=run_id,request_id=run_id)
    session,_=_resolve_requested_session(context,old.context.calendar)
    values=old.model_dump(mode='json')
    values.update(context=BatchContext(requested_as_of=requested,requested_session=session,
        effective_daily_session=session,mode='CURRENT_EOD',calendar=old.context.calendar,run_id=run_id,request_id=run_id).model_dump(mode='json'),
        run_id=run_id,resume_run_id=None,previous_run=args.previous_run,saved_selection_manifest=None)
    spec=StockObservationInput.model_validate(values)
    Path(args.output).write_text(spec.model_dump_json(indent=2),encoding='utf-8')
    print('CURRENT_EOD spec written: '+args.output+'; effective session '+session+'; no scan executed')


if __name__=='__main__':main()
