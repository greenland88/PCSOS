"""Bounded 11A saved anchor / one frozen full-universe observation attempt."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from pcs.pool.observation_models import StockObservationInput,ObservationQuery,ObservationBudgets
from pcs.pool.observation import run_stock_observation,read_stock_observation
from pcs.selection.models import BatchContext
from pcs.validation import ValidationRun,RunStatus


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-directory',required=True)
    parser.add_argument('--saved-anchor',action='store_true')
    parser.add_argument('--full-universe',action='store_true')
    parser.add_argument('--resume-run-id')
    args=parser.parse_args()
    if args.saved_anchor==args.full_universe:parser.error('choose one acceptance scope')
    code=Path(__file__).resolve().parents[1]
    if subprocess.check_output(['git','status','--porcelain'],cwd=code,text=True).strip():
        raise ValueError('ACCEPTANCE_REQUIRES_CLEAN_COMMITTED_SOURCE')
    from pcs.pool.observation_components import dependencies
    dep=tuple(code/p for p in dependencies())+tuple((code/'src/pcs/selection').glob('*.py'))+(Path(__file__),)
    guard=ValidationRun(code,dep)
    root=Path(args.output_directory);root.mkdir(parents=True,exist_ok=True)
    context=BatchContext(requested_as_of='2026-09-04',requested_session='2026-09-04',effective_daily_session='2026-09-04')
    if args.saved_anchor:
        from pcs.selection.adapters import SelectionInputManifest
        source=code/'examples/selection_step08_inputs.json'
        saved=SelectionInputManifest.model_validate_json(source.read_text(encoding='utf-8'))
        spec=StockObservationInput(symbols=saved.requested_symbols,context=saved.context,
            output_directory=str(root),run_id=args.resume_run_id or 'saved-eight',resume_run_id=args.resume_run_id,
            saved_selection_manifest=str(source))
    else:
        directory=Path('H:/workspace/PCSOS/data/artifacts/global_pcs_candidates')
        source=directory/'global_pcs_candidates_c2ede1f139e783e3-fd5a6552aff369df.json'
        manifest=json.loads(source.with_name(source.stem+'_manifest.json').read_text(encoding='utf-8'))
        parquet=source.with_suffix('.parquet')
        assert sha256(parquet.read_bytes()).hexdigest()==manifest['artifact_hash']
        universe=json.loads(source.read_text(encoding='utf-8'))
        symbols=universe['included_symbols']
        assert len(symbols)==2953 and len(set(symbols))==2953
        frozen=json.loads(Path('H:/workspace/PCSOS-pool-scan-control/docs/operations/pool-scan-baseline-20260904/contract.json').read_text())
        # Frozen membership is checked against the source manifest, not the old strategy's 38 candidates.
        assert manifest['inventory_fingerprint']==frozen['universe']['fingerprint']
        spec=StockObservationInput(symbols=symbols,universe_id=universe['universe_id'],universe_source=str(source),
            universe_sha256=sha256(source.read_bytes()).hexdigest(),universe_members_field='included_symbols',context=context,
            output_directory=str(root),run_id=args.resume_run_id or 'historical-20260904',resume_run_id=args.resume_run_id,
            budgets=ObservationBudgets(total_seconds=21600))
    (root/'actual_spec.json').write_text(spec.model_dump_json(indent=2),encoding='utf-8')
    try:
        if args.saved_anchor:
            result=run_stock_observation(spec)
        else:
            from pcs.pool.process import ReadOnlyScanRequest,run_read_only_scan
            result=run_read_only_scan(ReadOnlyScanRequest(symbols=tuple(spec.symbols),mode='EOD',
                observation_spec=spec.model_dump(mode='json')),timeout_seconds=spec.budgets.total_seconds+30)
        report=dict(status=result.status,coverage=result.coverage,source_commit=result.source_commit,
            run=result.model_dump(mode='json'),scope='SAVED_EIGHT' if args.saved_anchor else 'FROZEN_2953_READ_ONLY',
            canonical_read=not args.saved_anchor,model_called=False,provider_called=False,options_called=False)
        if args.saved_anchor:
            from pcs.selection.storage import read_selection_bundle
            previous=Path('H:/workspace/PCSOS/selection_v2_outputs/step_08_r1_r3_20260909')
            old,packets,_=read_selection_bundle(previous)
            new=json.loads((Path(result.output_directory)/'stock_shortlist.json').read_text(encoding='utf-8'))
            assert new['shortlist_id']==old.shortlist_id
            old_by_symbol={p.symbol:p for p in packets}
            for symbol in spec.symbols:
                query=read_stock_observation(ObservationQuery(run_directory=result.output_directory,symbol=symbol))
                assert query.packet.packet_id==old_by_symbol[symbol].packet_id
            report['saved_shortlist_id_unchanged']=True
            report['eight_packet_ids_unchanged']=True
        report['validation_status']=guard.finish(root/'validation_run.json').value
        assert report['validation_status']=='VALID'
    except Exception as exc:
        report=dict(status='BLOCKED',stage='OBSERVATION_ACCEPTANCE',reason_codes=[type(exc).__name__,str(exc)],
            source_commit=guard.start_head,canonical_read_attempted=not args.saved_anchor)
        guard.finish(root/'validation_run.json',status=RunStatus.BLOCKED)
        (root/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    (root/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'actual_command.json').write_text(json.dumps(dict(argv=sys.argv,cwd=str(Path.cwd()),python=sys.executable),indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','coverage','source_commit','scope')},ensure_ascii=False))


if __name__=='__main__':main()
