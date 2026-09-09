"""Explicit observation CLI, separate from formal pool scan defaults."""
import json
from pathlib import Path
from .adapters import load_selection_input
from .models import DecisionPacketInput, EvidenceQuery, ReviewSubmission
from .ranking import rank_stock_opportunities
from .packets import build_decision_evidence_packet, resolve_evidence
from .storage import (write_selection_bundle,read_selection_bundle,link_packets,
    import_ai_review,read_ai_reviews,write_packet)


def summary(shortlist):
    return dict(shortlist_id=shortlist.shortlist_id,as_of=shortlist.as_of,mode=shortlist.context.mode,
        status=shortlist.status,coverage_complete=shortlist.coverage_complete,
        rows=[dict(symbol=r.symbol,rank=r.rank,group=r.group,eligible=r.current_eligible,
            representative_result_id=r.representative_result_id,reason_codes=r.reason_codes) for r in shortlist.rows])


def run_shortlist_command(args):
    if args.render_only:
        if not args.selection_directory or args.input_manifest:
            raise SystemExit('RENDER_REQUIRES_SELECTION_DIRECTORY_ONLY')
        shortlist,packets,docs=read_selection_bundle(args.selection_directory)
        if args.as_of and args.as_of!=shortlist.as_of:
            raise SystemExit('RENDER_AS_OF_MISMATCH')
        write_selection_bundle(args.output_directory,shortlist,packets,
            input_manifest=docs['input_manifest.json'],read_audit=dict(operation='RENDER_ONLY',source_package_reads_this_call=0,
                canonical_read=False,provider_read=False,detector_called=False,reused_source_audit=docs['read_audit.json']))
    else:
        if not args.input_manifest or args.selection_directory:
            raise SystemExit('SHORTLIST_REQUIRES_INPUT_MANIFEST_ONLY')
        loaded=load_selection_input(args.input_manifest)
        inp=loaded.ranking_input
        if args.as_of:
            if inp.context.mode!='HISTORICAL':
                raise SystemExit('CURRENT_EOD_TIME_MUST_BE_EXPLICIT_IN_MANIFEST')
            inp=inp.model_copy(update={'context':type(inp.context).model_validate({**inp.context.model_dump(mode='json'),
                'requested_as_of':args.as_of,'requested_session':args.as_of,'effective_daily_session':args.as_of})})
        if args.previous_directory:
            previous,_,_=read_selection_bundle(args.previous_directory)
            inp=inp.model_copy(update={'previous':previous})
        shortlist=rank_stock_opportunities(inp)
        packets=[build_decision_evidence_packet(DecisionPacketInput(symbol=s,selection_input=inp,
            extra_records=loaded.extra_records.get(s,[]))) for s in inp.requested_symbols]
        shortlist=link_packets(shortlist,packets)
        write_selection_bundle(args.output_directory,shortlist,packets,
            input_manifest=loaded.input_manifest,read_audit=loaded.read_audit)
    print(json.dumps(summary(shortlist),ensure_ascii=False,indent=2))


def run_evidence_command(args):
    if args.run_directory or args.upgrade or args.explain_selection:
        raise SystemExit('SELECTION_AND_LEGACY_EVIDENCE_MODES_ARE_EXCLUSIVE')
    _,packets,_=read_selection_bundle(args.selection_directory)
    found=[p for p in packets if (not args.symbol or p.symbol==args.symbol.upper()) and
        (not args.packet_id or p.packet_id==args.packet_id)]
    if len(found)!=1:
        raise SystemExit('EXACT_SYMBOL_OR_PACKET_REQUIRED')
    packet=found[0]
    if sum(bool(v) for v in (args.evidence_id,args.import_review,args.review_id))>1:
        raise SystemExit('EVIDENCE_QUERY_MODES_ARE_EXCLUSIVE')
    if args.import_review:
        if not args.review_directory:
            raise SystemExit('EXPLICIT_REVIEW_DIRECTORY_REQUIRED')
        submission=ReviewSubmission.model_validate_json(Path(args.import_review).read_text(encoding='utf-8'))
        result=import_ai_review(args.review_directory,packet,submission)
        print(result.model_dump_json(indent=2))
    elif args.review_id:
        if not args.review_directory:
            raise SystemExit('EXPLICIT_REVIEW_DIRECTORY_REQUIRED')
        reviews=[r for r in read_ai_reviews(args.review_directory) if r.review_id==args.review_id and r.packet_id==packet.packet_id]
        print(json.dumps(dict(status='FOUND' if reviews else 'NOT_FOUND',reviews=[r.model_dump(mode='json') for r in reviews]),ensure_ascii=False,indent=2))
    elif args.evidence_id:
        result=resolve_evidence(EvidenceQuery(packet=packet,evidence_id=args.evidence_id))
        print(result.model_dump_json(indent=2))
    else:
        if args.output_directory:
            write_packet(args.output_directory,packet)
        print(json.dumps(dict(symbol=packet.symbol,packet_id=packet.packet_id,status=packet.status,as_of=packet.as_of,
            current_gaps=packet.current_gaps,evidence_count=len(packet.detail_index),
            program_assessments=[{k:a[k] for k in ('family','state','eligible_at_requested_time') if k in a} for a in packet.program_assessments],
            ai_review_status=packet.ai_review_status,user_decision_status=packet.user_decision_status),ensure_ascii=False,indent=2))
