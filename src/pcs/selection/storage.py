"""Atomic portable observation bundles and a separate append-only opinion journal."""
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
from uuid import uuid4

from pcs.pool.artifacts import _write_atomic
from .identity import digest, semantic_id, semantic
from .models import (StockShortlist, DecisionEvidencePacket, RankingInput, DecisionPacketInput,
    AIReview, AIReviewInput, ReviewSubmission)
from .packets import packet_identity, packet_identity_valid, record_ai_review

LABELS = {'READY_FOR_OPTIONS_REVIEW':'已确认，可进一步查期权','WATCH_SETUP':'有形态，等待确认或入场窗口',
    'NOT_CURRENTLY_APPLICABLE':'位置不适用或机会结束','NO_SETUP':'当前未形成','INSUFFICIENT_EVIDENCE':'关键证据不足'}


def shortlist_identity(shortlist):
    value = shortlist.model_dump(mode='json')
    value['shortlist_id']=''
    for row in value['rows']:
        row['packet_id']=None  # Evidence attachments are validated links, not ranking inputs.
    value['input_bindings']=[dict(result_id=b.result_id,feature_identity=b.feature_identity,source_identity=b.source_identity,
        price_basis=b.price_basis,indicator_identity=b.indicator_identity,calendar=b.calendar) for b in shortlist.input_bindings]
    return semantic_id(value)


def link_packets(shortlist, packets):
    by_symbol = {p.symbol:p for p in packets}
    if set(by_symbol)!=set(shortlist.requested_symbols):
        raise ValueError('PACKET_SCOPE_MISMATCH')
    for row in shortlist.rows:
        packet=by_symbol[row.symbol]
        actual={c['result_id'] for c in packet.component_refs if c.get('family')}
        expected={f.result_id for f in row.family_assessments if f.result_id}
        if actual!=expected or semantic(packet.context)!=semantic(shortlist.context):
            raise ValueError('PACKET_ROW_INPUT_MISMATCH')
        if packet.shortlist_id and packet.shortlist_id!=shortlist.shortlist_id:
            raise ValueError('PACKET_RANK_CONTEXT_MISMATCH')
    rows = [r.model_copy(update={'packet_id':by_symbol[r.symbol].packet_id}) for r in shortlist.rows]
    result=shortlist.model_copy(update={'rows':rows})
    return result.model_copy(update={'shortlist_id':shortlist_identity(result)})


def csv_rows(rows):
    stream=io.StringIO(newline='')
    if not rows:
        return ''
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list)) else
            '' if v is None else str(v).lower() if isinstance(v,bool) else v for k,v in row.items()})
    return stream.getvalue()


def shortlist_csv(shortlist):
    return csv_rows([r.model_dump(mode='json') for r in shortlist.rows])


def shortlist_markdown(shortlist):
    lines=[f'# 股票观察名单 · {shortlist.as_of}', '',
        f'模式：{shortlist.context.mode}；范围：{", ".join(shortlist.requested_symbols)}。分区为观察展示顺序。',
        '股票结果不代表期权已验证或用户交易授权。公司、期权和AI状态独立保存。', '',
        '| 序号 | 股票 | 分组 | 当前资格 | 主机会结果ID | 证据包 |', '|---|---|---|---|---|---|']
    for r in shortlist.rows:
        lines.append(f'| {r.rank} | {r.symbol} | {LABELS[r.group]} | {r.current_eligible} | {r.representative_result_id} | {r.packet_id} |')
    for r in shortlist.rows:
        lines += ['',f'## {r.symbol}', '', '主要原因：'+', '.join(r.reason_codes), '',
            '| 通道 | 原状态 | 当前资格 | 执行状态 | 当前适用性 |', '|---|---|---|---|---|']
        for a in r.family_assessments:
            lines.append(f'| {a.family} | {a.state} | {a.eligible_at_requested_time} | {a.execution} | {a.applicability} |')
        lines += ['', '| 排序字段 | 实际值 | 单位 | 方向 | 缺失原因 | 来源引用 |', '|---|---|---|---|---|---|']
        for k in r.sort_keys:
            value='未知(null)' if k.value is None else k.value
            lines.append(f'| {k.field} | {value} | {k.unit or "—"} | {k.direction} | {", ".join(k.reason_codes) or "—"} | {", ".join(k.source_refs) or "—"} |')
        for k in r.sort_keys:
            if k.details:
                lines.append('')
                lines.append(f'{k.field} 明细：'+json.dumps(k.details,ensure_ascii=False,sort_keys=True))
        old=r.old_new_comparison.get('legacy')
        lines += ['', '下一观察：'+'；'.join(r.next_observation_conditions),
            f'当前缺口{len(r.current_gaps)}项；历史覆盖缺口{len(r.coverage_gaps)}项。完整引用见packet。',
            '新旧比较：'+r.old_new_comparison['status']+
                (f'；旧评估时点{old["as_of"]}，原动作{old["action"]}，保存timing资格{old["timing_eligible"]}。' if old else '；旧记录未提供。'),
            '公司质量／行业地位：'+('尚未评估' if r.company_context.status=='NOT_EVALUATED' else '资料已提供，见独立证据'),
            f'期权数据：{r.options_context.data_status}；正式评估：{r.options_context.formal_evaluation_status}。',
            'AI意见：NOT_REVIEWED；用户决定：NOT_RECORDED。', '']
    return '\n'.join(lines)


def packet_markdown(packet):
    return '\n'.join([f'# {packet.symbol} 讨论证据包', '',f'时点：{packet.as_of}；状态：{packet.status}；packet：{packet.packet_id}',
        '公司质量／行业地位：'+packet.company_context.status,
        '期权数据／discovery／正式评估：'+json.dumps(packet.options_context.model_dump(mode='json'),ensure_ascii=False),
        'AI意见：NOT_REVIEWED；用户决定：NOT_RECORDED。',
        '程序意见：'+json.dumps(packet.program_assessments,ensure_ascii=False),
        '当前缺口：'+json.dumps(packet.current_gaps,ensure_ascii=False),
        '取消及下一观察条件：'+json.dumps(packet.invalidation_conditions+packet.next_observation_conditions,ensure_ascii=False),
        '可查询证据ID：'+json.dumps([r.evidence_id for r in packet.detail_index],ensure_ascii=False)])


def write_selection_bundle(output_directory, shortlist, packets, *, input_manifest=None, read_audit=None):
    root=Path(output_directory)
    if root.exists():
        raise ValueError('SELECTION_OUTPUT_ALREADY_EXISTS')
    if shortlist_identity(shortlist)!=shortlist.shortlist_id:
        raise ValueError('SHORTLIST_CONTENT_IDENTITY_MISMATCH')
    if any(not packet_identity_valid(p) for p in packets):
        raise ValueError('PACKET_CONTENT_IDENTITY_MISMATCH')
    stage=root.with_name(root.name+'.staging-'+uuid4().hex)
    stage.mkdir(parents=True)
    packet_index={p.symbol:dict(packet_id=p.packet_id,content_identity=p.content_identity,file=f'packets/{p.symbol}.json') for p in packets}
    comparison=[dict(symbol=r.symbol,**r.old_new_comparison) for r in shortlist.rows]
    documents={'stock_shortlist.json':shortlist.model_dump(mode='json'),
        'stock_shortlist.ai.json':shortlist.model_dump(mode='json'),'selection_diff.json':comparison,
        'shortlist_changes.json':dict(status=shortlist.changes_status,changes=shortlist.changes),
        'packet_index.json':packet_index,'evidence_index.json':[dict(symbol=p.symbol,packet_id=p.packet_id,**r.model_dump(mode='json')) for p in packets for r in p.detail_index],
        'ai_reviews.json':[],'ai_review_index.json':{},'input_manifest.json':input_manifest or {},'read_audit.json':read_audit or {},
        'stock_shortlist.schema.json':StockShortlist.model_json_schema(),'decision_packet.schema.json':DecisionEvidencePacket.model_json_schema(),
        'ranking_input.schema.json':RankingInput.model_json_schema(),'decision_packet_input.schema.json':DecisionPacketInput.model_json_schema(),
        'ai_review.schema.json':AIReview.model_json_schema(),
        'field_dictionary.json':dict(group=LABELS,sort_keys='Lexicographic actual values; unknown=null, ordered last. No weighted score.',
            support='Bound zone live tests only; retrospective platform tests are separate formation facts.',
            confirmation='Saved CONFIRMATION predicates only; absent denominator=null.',
            remaining_entry_sessions='Exchange sessions from max(requested_session,entry_start) through entry_end inclusive; waiting separately.',
            old_new='Explicit same-time legacy timing boolean; WAIT never implies false.',
            packet='Independent portable normalized evidence attachments; optional rank context explicit.',
            review='Separate append-only imported opinion journal; no model calls or trading authorization.',
            company='Missing company quality/industry position is NOT_EVALUATED.',options='25–45 DTE is a user preference, not a production rule.')}
    for p in packets:
        documents[packet_index[p.symbol]['file']]=p.model_dump(mode='json')
    hashes={}
    for name,doc in documents.items():
        (stage/name).parent.mkdir(parents=True,exist_ok=True)
        hashes[name]=_write_atomic(stage/name,json.dumps(doc,ensure_ascii=False,indent=2,allow_nan=False))
    texts={'stock_shortlist.csv':shortlist_csv(shortlist),'stock_shortlist.zh-CN.md':shortlist_markdown(shortlist),
        'selection_diff.csv':csv_rows(comparison),'shortlist_changes.csv':csv_rows(shortlist.changes)}
    texts.update({f'packets/{p.symbol}.zh-CN.md':packet_markdown(p) for p in packets})
    for name,value in texts.items():
        hashes[name]=_write_atomic(stage/name,value)
    code=Path(__file__).resolve().parents[3]
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=code,text=True).strip()
    dirty=bool(subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=code,text=True).strip())
    _write_atomic(stage/'artifact_manifest.json',json.dumps(dict(module='stock_observation',version='1.0',status='COMPLETE',
        source_commit=head,tracked_source_dirty=dirty,shortlist_id=shortlist.shortlist_id,sha256=hashes),indent=2))
    os.rename(stage,root)  # Publish only after the complete manifest exists; never overwrite a bundle.
    return root


def read_selection_bundle(directory):
    root=Path(directory)
    manifest=json.loads((root/'artifact_manifest.json').read_text(encoding='utf-8'))
    if manifest.get('module')!='stock_observation' or manifest.get('status')!='COMPLETE':
        raise ValueError('SELECTION_BUNDLE_INCOMPLETE')
    docs={}
    for name,expected in manifest['sha256'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('SELECTION_PATH_INVALID')
        raw=path.read_bytes()
        if sha256(raw).hexdigest()!=expected:
            raise ValueError('SELECTION_ARTIFACT_HASH_MISMATCH:'+name)
        if name.endswith('.json') and (name in {'stock_shortlist.json','packet_index.json','input_manifest.json','read_audit.json'} or name.startswith('packets/')):
            docs[name]=json.loads(raw)
    shortlist=StockShortlist.model_validate(docs['stock_shortlist.json'])
    if shortlist_identity(shortlist)!=shortlist.shortlist_id or manifest['shortlist_id']!=shortlist.shortlist_id:
        raise ValueError('SHORTLIST_CONTENT_IDENTITY_MISMATCH')
    packets=[DecisionEvidencePacket.model_validate(docs[p['file']]) for _,p in sorted(docs['packet_index.json'].items())]
    if any(not packet_identity_valid(p) for p in packets):
        raise ValueError('PACKET_CONTENT_IDENTITY_MISMATCH')
    if link_packets(shortlist,packets)!=shortlist:
        raise ValueError('PACKET_INDEX_ROW_LINK_MISMATCH')
    return shortlist,packets,docs


def read_ai_reviews(directory):
    root=Path(directory)
    if not (root/'review_manifest.json').exists():
        return []
    manifest=json.loads((root/'review_manifest.json').read_text(encoding='utf-8'))
    result=[]
    for key,entry in sorted(manifest['reviews'].items()):
        path=(root/entry['file']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('REVIEW_PATH_INVALID')
        raw=path.read_bytes()
        if sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('REVIEW_FILE_HASH_MISMATCH')
        review=AIReview.model_validate_json(raw)
        if review.review_id!=key:
            raise ValueError('REVIEW_INDEX_ID_MISMATCH')
        payload=review.model_dump(mode='json',include=set(ReviewSubmission.model_fields)-{'review_id'})
        if digest(payload)!=review.content_sha256:
            raise ValueError('REVIEW_CONTENT_IDENTITY_MISMATCH')
        result.append(review)
    return result


def write_packet(directory, packet):
    root=Path(directory)
    if root.exists():
        raise ValueError('PACKET_OUTPUT_ALREADY_EXISTS')
    if not packet_identity_valid(packet):
        raise ValueError('PACKET_CONTENT_IDENTITY_MISMATCH')
    stage=root.with_name(root.name+'.staging-'+uuid4().hex)
    stage.mkdir(parents=True)
    checksum=_write_atomic(stage/'packet.json',packet.model_dump_json(indent=2))
    _write_atomic(stage/'manifest.json',json.dumps(dict(module='decision_evidence_packet',status='COMPLETE',
        packet_id=packet.packet_id,sha256={'packet.json':checksum})))
    os.rename(stage,root)
    return root


def read_packet(directory):
    root=Path(directory)
    manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    raw=(root/'packet.json').read_bytes()
    if manifest.get('status')!='COMPLETE' or sha256(raw).hexdigest()!=manifest['sha256']['packet.json']:
        raise ValueError('PACKET_FILE_HASH_MISMATCH')
    packet=DecisionEvidencePacket.model_validate_json(raw)
    if not packet_identity_valid(packet) or manifest['packet_id']!=packet.packet_id:
        raise ValueError('PACKET_CONTENT_IDENTITY_MISMATCH')
    return packet


def import_ai_review(directory, packet, submission: ReviewSubmission):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True)
    lock=root/'import.lock'
    with lock.open('x',encoding='utf-8') as f:
        f.write('exclusive opinion import')
    try:
        old=read_ai_reviews(root)
        review=record_ai_review(AIReviewInput(packet=packet,submission=submission,existing_reviews=old))
        if any(r.review_id==review.review_id for r in old):
            return review
        manifest_path=root/'review_manifest.json'
        manifest=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'version':'1.0','reviews':{}}
        name=review.content_sha256.replace(':','_')+'.json'
        checksum=_write_atomic(root/name,review.model_dump_json(indent=2))
        manifest['reviews'][review.review_id]=dict(file=name,sha256=checksum,packet_id=review.packet_id,origin=review.origin)
        _write_atomic(manifest_path,json.dumps(manifest,indent=2))
        return review
    finally:
        lock.unlink()
