"""CLI adapters; calculation always delegates to pool.runner via process.py."""
import argparse
import json
from pathlib import Path
from hashlib import sha256

from .observation_models import StockObservationInput,ObservationQuery
from .observation import read_stock_observation


def run_observation_command(args):
    if not args.observation_spec or args.data_mode!='READ_ONLY' or args.auto_prepare_data:
        raise ValueError('OBSERVATION_READ_ONLY_SPEC_REQUIRED')
    spec=StockObservationInput.model_validate_json(Path(args.observation_spec).read_text(encoding='utf-8'))
    if spec.selection_profile.profile_id!=args.selection_profile:
        raise ValueError('OBSERVATION_PROFILE_MISMATCH')
    if args.resume_run_id:
        spec=spec.model_copy(update={'run_id':args.resume_run_id,'resume_run_id':args.resume_run_id})
    from .process import ReadOnlyScanRequest,run_read_only_scan
    result=run_read_only_scan(ReadOnlyScanRequest(symbols=tuple(spec.symbols),as_of=spec.context.requested_as_of,
        mode='EOD',observation_spec=spec.model_dump(mode='json')),timeout_seconds=spec.budgets.total_seconds+30)
    print(json.dumps(dict(run_id=result.run_id,status=result.status,coverage=result.coverage,
        output_directory=result.output_directory,summary=result.summary,selection_v2=result.selection_v2),ensure_ascii=False))
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='Query or render an existing observation; never scans')
    parser.add_argument('--run-directory',required=True)
    parser.add_argument('--symbol')
    parser.add_argument('--evidence-id')
    parser.add_argument('--render-directory')
    args=parser.parse_args(argv)
    if args.render_directory:
        from pcs.selection.models import StockShortlist
        from pcs.selection.storage import shortlist_csv,shortlist_markdown
        from .observation_storage import read_json,write_json
        root=Path(args.run_directory);manifest=read_json(root/'observation_manifest.json')
        raw=(root/'stock_shortlist.json').read_bytes()
        if sha256(raw).hexdigest()!=manifest['sha256']['stock_shortlist.json']:
            raise ValueError('OBSERVATION_SHORTLIST_HASH_MISMATCH')
        result=StockShortlist.model_validate_json(raw)
        out=Path(args.render_directory);out.mkdir(parents=True,exist_ok=False)
        write_json(out/'stock_shortlist.json',result.model_dump(mode='json'))
        write_json(out/'stock_shortlist.ai.json',result.model_dump(mode='json'))
        from pcs.pool.artifacts import _write_atomic
        _write_atomic(out/'stock_shortlist.csv',shortlist_csv(result))
        _write_atomic(out/'stock_shortlist.zh-CN.md',shortlist_markdown(result))
        print(json.dumps(dict(status='RENDERED',source=str(root),output=str(out),canonical_reads=0,detector_calls=0)))
    elif args.symbol:
        result=read_stock_observation(ObservationQuery(run_directory=args.run_directory,symbol=args.symbol,evidence_id=args.evidence_id))
        print(result.model_dump_json(indent=2))
    else:parser.error('--symbol or --render-directory is required')


if __name__=='__main__':main()
