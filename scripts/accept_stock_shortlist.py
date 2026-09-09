"""One bounded Step 8 acceptance over the declared eight saved stocks."""
import argparse
from contextlib import ExitStack,redirect_stdout
from datetime import datetime,timezone
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from pcs.cli import main as cli_main
from pcs.selection import rank_stock_opportunities,build_decision_evidence_packet,resolve_evidence
from pcs.selection.adapters import load_selection_input
from pcs.selection.models import DecisionPacketInput,EvidenceQuery,ReviewSubmission
from pcs.selection.storage import read_selection_bundle,read_ai_reviews,shortlist_csv,shortlist_markdown
from pcs.validation import ValidationRun


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output_directory')
    parser.add_argument('--input-manifest',default='examples/selection_step08_inputs.json')
    parser.add_argument('--compare-directory',help='Hash-verified historical output; compare raw JSON without reusing old semantics')
    args=parser.parse_args()
    code=Path(__file__).resolve().parents[1]
    root=Path(args.output_directory)
    dependencies=tuple((code/'src/pcs/selection').glob('*.py'))+(Path(__file__),Path(args.input_manifest),
        code/'src/pcs/cli.py',code/'src/pcs/pool/ai_evidence.py',code/'examples/stock_shortlist.py')
    guard=ValidationRun(code,dependencies)
    if subprocess.check_output(['git','status','--porcelain'],cwd=code,text=True).strip():
        raise ValueError('ACCEPTANCE_REQUIRES_COMMITTED_CLEAN_SOURCE')
    if root.exists():
        raise ValueError('ACCEPTANCE_OUTPUT_ALREADY_EXISTS')
    previous=None
    previous_hashes={}
    if args.compare_directory:
        previous_root=Path(args.compare_directory)
        manifest_path=previous_root/'artifact_manifest.json'
        previous_manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        previous_hashes={**previous_manifest['sha256'],'artifact_manifest.json':sha256(manifest_path.read_bytes()).hexdigest()}
        for name,checksum in previous_hashes.items():
            path=(previous_root/name).resolve()
            assert path.is_relative_to(previous_root.resolve())
            assert sha256(path.read_bytes()).hexdigest()==checksum
        previous=json.loads((previous_root/'stock_shortlist.json').read_text(encoding='utf-8'))
    targets=['pcs.pool.opportunities.evaluate_entry_opportunity','pcs.trend.opportunity_engine.evaluate_entry_opportunity',
        'pcs.trend.constructive_base.detect_constructive_base','pcs.trend.support_zones.evaluate_support_zones',
        'pcs.pool.opportunities.calculate_base_indicators','pcs.trend.indicators.calculate_base_indicators',
        'pcs.pool.opportunities.ProfileDataReader','pcs.cli.pool_scan']
    with ExitStack() as preload:
        for target in targets:
            preload.enter_context(patch(target,side_effect=AssertionError('FORBIDDEN_STEP08_CALL:'+target)))
        loaded=load_selection_input(args.input_manifest)
    inp=loaded.ranking_input
    assert set(inp.requested_symbols)=={'NVDA','PLTR','MSFT','HOOD','UBER','MDLZ','AAL','AAOI'}
    assert len(inp.opportunities)==28 and len({r.symbol for r in inp.opportunities})==7
    old_ids=[r.result_id for r in inp.opportunities if r.family!='CONSTRUCTIVE_BASE']
    assert len(old_ids)==21
    before_inputs=sha256(inp.model_dump_json().encode()).hexdigest()
    commands=[]
    def invoke(arguments):
        output=io.StringIO()
        with patch.object(sys,'argv',['pcs',*arguments]),redirect_stdout(output):
            cli_main()
        commands.append(dict(argv=['python','-m','pcs.cli',*arguments],execution='ACTUAL_IN_PROCESS_CLI_MAIN',
            stdout=output.getvalue(),status='PASS'))
    # Deliberately fail if a detector, indicator or canonical preparation is accidentally reached.
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target,side_effect=AssertionError('FORBIDDEN_STEP08_CALL:'+target)))
        # A single verified load is reused across these calls; no provider/source preparation is hidden in examples.
        stack.enter_context(patch('pcs.selection.cli.load_selection_input',return_value=loaded))
        invoke(['stock-shortlist','--input-manifest',args.input_manifest,'--as-of','2026-09-04',
            '--output-directory',str(root),'--summary'])
        shortlist,packets,docs=read_selection_bundle(root)
        assert shortlist.shortlist_id==rank_stock_opportunities(inp).shortlist_id
        by_symbol={p.symbol:p for p in packets}
        assert {r.symbol for r in shortlist.rows}==set(inp.requested_symbols)
        assert [b.result_id for b in shortlist.input_bindings if b.family!='CONSTRUCTIVE_BASE']==sorted(old_ids)
        assert all(r.base_result.calculation_version=='constructive-base-v2' for r in inp.opportunities if r.family=='CONSTRUCTIVE_BASE')
        spec=importlib.util.spec_from_file_location('step08_example',code/'examples/stock_shortlist.py')
        example=importlib.util.module_from_spec(spec);spec.loader.exec_module(example)
        single_dir=root.with_name(root.name+'_single_nvda')
        argv=['--input-manifest',args.input_manifest,'--symbol','NVDA','--output-directory',str(single_dir)]
        with redirect_stdout(io.StringIO()):
            single,packet=example.main(argv,loaded=loaded)
        row=next(r for r in shortlist.rows if r.symbol=='NVDA')
        assert single.rows[0].row_id==row.row_id and single.rows[0].sort_keys==row.sort_keys
        assert packet==by_symbol['NVDA']
        commands.append(dict(argv=['python','examples/stock_shortlist.py',*argv],execution='ACTUAL_EXAMPLE_MAIN_WITH_VERIFIED_TYPED_INPUT_CACHE',status='PASS'))
        aal=by_symbol['AAL']
        aal_dir=root.with_name(root.name+'_aal_packet')
        invoke(['pool-evidence','--selection-directory',str(root),'--symbol','AAL','--discussion-packet','--output-directory',str(aal_dir)])
        reference=next(r for r in aal.opposing_evidence if resolve_evidence(EvidenceQuery(packet=aal,evidence_id=r.evidence_id)).record.kind=='CONDITION')
        invoke(['pool-evidence','--selection-directory',str(root),'--symbol','AAL','--evidence-id',reference.evidence_id])
        resolved_counts={}
        ranking_reference_counts={}
        for row in shortlist.rows:
            refs=sorted({ref for key in row.sort_keys for ref in key.source_refs})
            packet=by_symbol[row.symbol]
            if refs:
                query=resolve_evidence(EvidenceQuery(packet=packet,evidence_ids=refs))
                assert query.status=='RESOLVED',(row.symbol,query.unresolved_refs,query.reason_codes)
            ranking_reference_counts[row.symbol]=len(refs)
            for component in packet.component_refs:
                assert resolve_evidence(EvidenceQuery(packet=packet,evidence_id=component['result_id'])).status=='RESOLVED'
        for packet in packets:
            if packet.detail_index:
                query=resolve_evidence(EvidenceQuery(packet=packet,evidence_ids=[r.evidence_id for r in packet.detail_index]))
                assert query.status=='RESOLVED' and len(query.records)==len(packet.detail_index)
                resolved_counts[packet.symbol]=len(query.records)
            else:
                resolved_counts[packet.symbol]=0
        render=root.with_name(root.name+'_render')
        invoke(['stock-shortlist','--selection-directory',str(root),'--render-only','--output-directory',str(render)])
        for name in ('stock_shortlist.json','stock_shortlist.ai.json','stock_shortlist.csv','stock_shortlist.zh-CN.md'):
            assert (root/name).read_bytes()==(render/name).read_bytes(),name
        assert json.loads((root/'stock_shortlist.ai.json').read_text(encoding='utf-8'))==shortlist.model_dump(mode='json')
        assert (root/'stock_shortlist.csv').read_text(encoding='utf-8').splitlines()==shortlist_csv(shortlist).splitlines()
        assert (root/'stock_shortlist.zh-CN.md').read_text(encoding='utf-8')==shortlist_markdown(shortlist)
        review_root=root.with_name(root.name+'_TEST_reviews')
        review_file=root.with_name(root.name+'_TEST_review_input.json')
        submission=ReviewSubmission(packet_id=aal.packet_id,packet_content_identity=aal.content_identity,
            generated_at=datetime.now(timezone.utc).isoformat(),origin='TEST',model='TEST:no-model-call',prompt_version='TEST:step08-v1',
            recommendation='程序当前不适用；可继续研究已保存的反对依据。不能据此交易。',scope='STOCK_RESEARCH',
            opposing_refs=[reference.evidence_id],disagreements_with_program=['TEST：保留进一步研究意见，与当前程序不适用并存。'],
            unresolved_questions=['公司质量和期权报价均未评估。'],change_conditions=['新的、有来源的股票证据需要新packet。'])
        review_file.write_text(submission.model_dump_json(indent=2),encoding='utf-8')
        arguments=['pool-evidence','--selection-directory',str(root),'--symbol','AAL','--import-review',str(review_file),'--review-directory',str(review_root)]
        invoke(arguments)
        first=(review_root/'review_manifest.json').read_bytes()
        invoke(arguments)
        assert (review_root/'review_manifest.json').read_bytes()==first
        reviews=read_ai_reviews(review_root)
        assert len(reviews)==1 and reviews[0].origin=='TEST' and not reviews[0].model_called_by_pcs
        invoke(['pool-evidence','--selection-directory',str(root),'--symbol','AAL','--review-id',reviews[0].review_id,'--review-directory',str(review_root)])
        after,after_packets,_=read_selection_bundle(root)
        assert after==shortlist and after_packets==packets
    assert sha256(inp.model_dump_json().encode()).hexdigest()==before_inputs
    source_checks=[]
    for entry in loaded.input_manifest['sources']:
        source_root=Path(entry['path'])
        name='artifact_manifest.json' if (source_root/'artifact_manifest.json').exists() else 'manifest.json'
        assert json.loads((source_root/name).read_text(encoding='utf-8'))==entry['manifest']
        hashes=entry['manifest'].get('sha256',entry['manifest'].get('artifact_hashes'))
        for filename,checksum in hashes.items():
            assert sha256((source_root/filename).read_bytes()).hexdigest()==checksum
        source_checks.append(dict(bundle_id=entry['bundle_id'],files_checked=len(hashes),manifest_sha256=entry['manifest_sha256'],status='UNCHANGED'))
    report=dict(status='PASS',source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=code,text=True).strip(),
        as_of=shortlist.as_of,mode=shortlist.context.mode,shortlist_id=shortlist.shortlist_id,source_checks=source_checks,
        package_read_count=loaded.read_audit['package_read_count'],old_21_child_ids_unchanged=sorted(old_ids),
        resolved_evidence_counts=resolved_counts,ranking_reference_counts=ranking_reference_counts,aal_query=reference.model_dump(mode='json'),
        model_called=False,provider_read=False,canonical_read=False,detector_called=False,scope='EIGHT_SAVED_STOCKS_ONLY',
        rows=[r.model_dump(mode='json') for r in shortlist.rows],test_review_id=reviews[0].review_id,
        validation='Pure TEST tests are separate; this run only assembled saved derived results and resolved references.')
    if previous:
        for name,checksum in previous_hashes.items():
            assert sha256((previous_root/name).read_bytes()).hexdigest()==checksum
        old_rows={r['symbol']:r for r in previous['rows']}
        report['revision_comparison']=dict(previous_directory=str(previous_root),previous_artifacts_unchanged=True,
            previous_calculation_version=previous['calculation_version'],calculation_version=shortlist.calculation_version,
            previous_shortlist_id=previous['shortlist_id'],shortlist_id=shortlist.shortlist_id,
            rows=[dict(symbol=r.symbol,old_group=old_rows[r.symbol]['group'],group=r.group,
                old_row_id=old_rows[r.symbol]['row_id'],row_id=r.row_id,
                old_packet_id=old_rows[r.symbol]['packet_id'],packet_id=r.packet_id,
                sort_values_changed={k.field:dict(old=next(x['value'] for x in old_rows[r.symbol]['sort_keys'] if x['field']==k.field),new=k.value)
                    for k in r.sort_keys if next(x['value'] for x in old_rows[r.symbol]['sort_keys'] if x['field']==k.field)!=k.value})
                for r in shortlist.rows])
    (root/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (root/'actual_commands.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
    for path in (root,single_dir,aal_dir,render,review_root,review_file):
        guard.add_output(path)
    assert guard.finish(root/'validation_run.json').value=='VALID'
    hashes={name:sha256((root/name).read_bytes()).hexdigest() for name in ('acceptance.json','actual_commands.json','validation_run.json')}
    (root/'acceptance_manifest.json').write_text(json.dumps(hashes,indent=2),encoding='utf-8')
    print(json.dumps(dict(status='PASS',shortlist_id=shortlist.shortlist_id,source_checks=source_checks,
        rows=[dict(symbol=r.symbol,rank=r.rank,group=r.group,eligible=r.current_eligible) for r in shortlist.rows],
        resolved_evidence_counts=resolved_counts,command_count=len(commands),model_called=False),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
