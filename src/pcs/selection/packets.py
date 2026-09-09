"""Portable discussion evidence and imported opinions, independent of ranking runs."""
from .identity import digest, semantic, semantic_id
from .models import (DecisionPacketInput, DecisionEvidencePacket, EvidenceRef, EvidenceRecord,
    EvidenceQuery, EvidenceQueryResult, AIReviewInput, AIReview, ReviewSubmission)
from .ranking import build_row, select_results, condition_supports


def evidence_record(result_id, pointer, entity_id, known_at, schema, bundle, kind, value):
    value = semantic(value)
    content_hash = digest(value)
    ref = EvidenceRef(evidence_id=digest([result_id,pointer,known_at,content_hash]),source_result_id=result_id,
        entity_id=entity_id,json_pointer=pointer,known_at=known_at,schema_version=schema,
        content_sha256=content_hash,source_bundle=bundle)
    return EvidenceRecord(ref=ref,kind=kind,value=value)


def packet_identity(packet):
    value = packet.model_dump(mode='json')
    for key in ('packet_id','content_identity'):
        value.pop(key)
    return semantic_id(value)


def packet_identity_valid(packet):
    identity=packet_identity(packet)
    return identity==packet.content_identity and packet.packet_id==digest([packet.calculation_version,identity])


def build_decision_evidence_packet(input: DecisionPacketInput) -> DecisionEvidencePacket:
    inp, symbol = input.selection_input, input.symbol.strip().upper()
    ctx = inp.context
    context_facts=(input.company_context.industry_position_claims+input.company_context.fundamental_evidence+
        input.market_context.facts+input.portfolio_context.facts)
    if any(f.known_at[:10]>ctx.effective_daily_session for f in context_facts):
        raise ValueError('CONTEXT_EVIDENCE_AFTER_REQUEST')
    in_scope = symbol in inp.requested_symbols
    row, _ = build_row(inp,symbol) if in_scope else (None,[])
    results, bindings, errors = select_results(inp,symbol) if in_scope else ([],[],['NOT_IN_INPUT_SCOPE'])
    binding_map = {b.result_id:b for b in bindings}
    records, components, assessments = [], [], []
    for result in results:
        binding = binding_map[result.result_id]
        components.append(dict(module=result.module,family=result.family,result_id=result.result_id,as_of=result.as_of,
            schema_version=result.version,calculation_version=result.calculation_version,source_bundle=binding.source_bundle))
        if result.as_of>ctx.effective_daily_session:
            continue  # Retain the mismatch reference, not future evidence in a historical packet.
        def add(pointer, entity, known, kind, value):
            record = evidence_record(result.result_id,pointer,entity,known,result.version,binding.source_bundle,kind,value)
            records.append(record)
            return record.ref
        assessment = next(a for a in row.family_assessments if a.family==result.family).model_dump(mode='json')
        assessment.pop('conditions')
        ref = add('/','assessment:'+result.result_id,result.as_of,'PROGRAM_ASSESSMENT',assessment)
        assessments.append(dict(**assessment,evidence_ref=ref.model_dump(mode='json')))
        for i,condition in enumerate(result.current_conditions):
            if condition.session<=ctx.effective_daily_session:
                add(f'/current_conditions/{i}',condition.condition_id,condition.session,'CONDITION',condition)
        for i, day in enumerate(result.timeline):
            if day.session>ctx.effective_daily_session:
                continue
            snapshot = day.model_dump(mode='json');snapshot.pop('conditions')
            add(f'/timeline/{i}',day.session,day.session,'DAY',snapshot)
            for j,condition in enumerate(day.conditions):
                add(f'/timeline/{i}/conditions/{j}',condition.condition_id,condition.session,'CONDITION',condition)
        for i,event in enumerate(result.episodes):
            add(f'/episodes/{i}',event.opportunity_id,result.as_of,'OPPORTUNITY_EVENT',event)
        if result.base_result:
            for i,event in enumerate(result.base_result.base_events):
                data = event.model_dump(mode='json')
                data.pop('lower_zone');data.pop('retrospective_tests')
                add(f'/base_result/base_events/{i}',event.base_id,result.as_of,'BASE_EVENT',data)
                add(f'/base_result/base_events/{i}/lower_zone',event.lower_zone.zone_id,result.as_of,'SUPPORT_ZONE',event.lower_zone)
                for j,test in enumerate(event.lower_zone.tests):
                    add(f'/base_result/base_events/{i}/lower_zone/tests/{j}',test.test_id,result.as_of,'LIVE_SUPPORT_TEST',test)
                for j,test in enumerate(event.retrospective_tests):
                    add(f'/base_result/base_events/{i}/retrospective_tests/{j}',test.test.test_id,test.known_at,'RETROSPECTIVE_FORMATION_TEST',test)
            for i,day in enumerate(result.base_result.timeline):
                if day.structure_resolution:
                    add(f'/base_result/timeline/{i}/structure_resolution',day.structure_resolution.resolution_id,day.session,'STRUCTURE_SOURCE_RESOLUTION',day.structure_resolution)
        if result.breakout_result:
            for i,event in enumerate(result.breakout_result.events):
                data = event.model_dump(mode='json');data.pop('zone')
                add(f'/breakout_result/events/{i}',event.breakout_id,result.as_of,'BREAKOUT_EVENT',data)
                add(f'/breakout_result/events/{i}/zone',event.zone.zone_id,result.as_of,'SUPPORT_ZONE',event.zone)
                for j,test in enumerate(event.zone.tests):
                    add(f'/breakout_result/events/{i}/zone/tests/{j}',test.test_id,result.as_of,'LIVE_SUPPORT_TEST',test)
    for support in inp.supports if in_scope else []:
        if support.symbol==symbol and support.as_of<=ctx.effective_daily_session:
            components.append(dict(module=support.module,result_id=support.result_id,as_of=support.as_of,schema_version=support.version))
            for field in ('current_zones','archived_zones'):
                for i,zone in enumerate(getattr(support,field)):
                    records.append(evidence_record(support.result_id,f'/{field}/{i}',zone.zone_id,support.as_of,
                        support.version,'SAVED_SUPPORT','SUPPORT_ZONE',zone))
                    for j,test in enumerate(zone.tests):
                        records.append(evidence_record(support.result_id,f'/{field}/{i}/tests/{j}',test.test_id,support.as_of,
                            support.version,'SAVED_SUPPORT','LIVE_SUPPORT_TEST',test))
    for diagnostic in inp.diagnostics if in_scope else []:
        if diagnostic.symbol==symbol and diagnostic.as_of<=ctx.effective_daily_session:
            components.append(dict(module='underlying_profile',result_id=diagnostic.result_id,as_of=diagnostic.as_of))
            records.append(evidence_record(diagnostic.result_id,'/diagnostics/'+diagnostic.metric_id,diagnostic.metric_id,
                diagnostic.as_of,'1.0','SAVED_PROFILE','METRIC',diagnostic))
    for legacy in inp.legacy if in_scope else []:
        if legacy.symbol==symbol:
            components.append(dict(module='selection_explanation',result_id=legacy.result_id,as_of=legacy.as_of))
            records.append(evidence_record(legacy.result_id,'/legacy_assessment','legacy:'+legacy.result_id,legacy.as_of,
                '1.0','SAVED_EXPLANATION','LEGACY_ASSESSMENT',legacy))
    records += [r for r in input.extra_records if r.ref.known_at[:10]<=ctx.effective_daily_session] if in_scope else []
    components=sorted({digest(c):c for c in components}.values(),key=lambda c:(c['result_id'],c.get('family','')))
    for component in components:
        # A result ID resolves to a compact component locator, never a raw source dump.
        record=evidence_record(component['result_id'],'/component',component['result_id'],component['as_of'],
            component.get('schema_version','1.0'),component.get('source_bundle','SAVED_COMPONENT'),'COMPONENT',component)
        records.append(record)
        component['evidence_ref']=record.ref.model_dump(mode='json')
    unique = {r.ref.evidence_id:r for r in records}
    if any(digest(r.value)!=r.ref.content_sha256 for r in records):
        raise ValueError('EVIDENCE_CONTENT_HASH_MISMATCH')
    records = sorted(unique.values(),key=lambda r:r.ref.evidence_id)
    current = [r for r in records if r.kind=='CONDITION' and r.ref.known_at==ctx.effective_daily_session]
    invalidation = [dict(**r.value,evidence_ref=r.ref.model_dump(mode='json')) for r in current if r.value.get('role')=='INVALIDATION']
    next_conditions = [dict(**r.value,evidence_ref=r.ref.model_dump(mode='json')) for r in current
        if r.value.get('role') in ('CONFIRMATION','CURRENT_ELIGIBILITY','DISCOVERY') and r.value.get('predicate_value') is not True]
    current_entities={r.opportunity_id for r in results if r.opportunity_id}
    current_zones={a.support_zone_id for a in row.family_assessments if a.support_zone_id} if row else set()
    for result in results:
        if result.base_result and result.base_result.timeline:
            current_entities.add(result.base_result.timeline[-1].base_id)
        if result.breakout_result and result.breakout_result.timeline:
            current_entities.add(result.breakout_result.timeline[-1].breakout_id)
    for record in records:
        if record.kind in {'BASE_EVENT','BREAKOUT_EVENT','OPPORTUNITY_EVENT'} and record.ref.entity_id in current_entities:
            values={k:v for k,v in record.value.items() if k.endswith('_deadline') or k in {
                'entry_start','entry_end','state','upside_exit_line','invalidation_line','lower','upper','zone_lower','zone_upper'}}
            next_conditions.append(dict(scope='CURRENT_REFERENCED_EVENT_SAVED_CONSTRAINTS',values=values,
                evidence_ref=record.ref.model_dump(mode='json')))
        if record.kind=='SUPPORT_ZONE' and record.ref.entity_id in current_zones:
            invalidation.append(dict(invalidation_line=record.value.get('invalidation_line'),unit='price',
                comparison_status='NOT_RECORDED_AS_A_CONDITION_IN_ZONE_RECORD',zone_state=record.value.get('state'),
                evidence_ref=record.ref.model_dump(mode='json')))
    for a in row.family_assessments if row else []:
        if a.execution=='EXECUTED':
            next_conditions.append(dict(family=a.family,source_result_id=a.result_id,confirmation_date=a.confirmation_date,
                confirmation_deadline=a.confirmation_deadline,
                entry_start=a.entry_start,entry_end=a.entry_end,threshold_status='SEE_SAVED_CONDITIONS' if a.conditions else 'NOT_RECORDED'))
    if input.shortlist:
        if input.shortlist.context.model_copy(update={'run_id':ctx.run_id,'request_id':ctx.request_id}) != ctx:
            raise ValueError('PACKET_SHORTLIST_TIME_MISMATCH')
        ranked = next((r for r in input.shortlist.rows if r.symbol==symbol),None)
        if not row or not ranked or row.row_id!=ranked.row_id:
            raise ValueError('PACKET_SHORTLIST_ROW_MISMATCH')
        assessments.append(dict(shortlist_id=input.shortlist.shortlist_id,rank=ranked.rank,group=ranked.group))
    packet = DecisionEvidencePacket(packet_id='',content_identity='',symbol=symbol,as_of=ctx.requested_as_of,
        status='NOT_IN_INPUT_SCOPE' if not in_scope else 'PARTIAL' if row.current_gaps else 'COMPLETED',
        data_timestamp=max((r.as_of for r in results if r.as_of<=ctx.effective_daily_session),default=None),run_id=ctx.run_id,request_id=ctx.request_id,context=ctx,
        evidence_scope=dict(requested_symbols=inp.requested_symbols,enabled_families=sorted(set(inp.enabled_families)),
            executed_families=[r.family for r in results],scope='DECLARED_STOCK_OBSERVATION_NOT_FULL_MARKET'),
        component_refs=components,
        program_assessments=assessments,supporting_evidence=[r.ref for r in current if condition_supports(r.value) is True],
        opposing_evidence=[r.ref for r in current if condition_supports(r.value) is False],
        current_gaps=row.current_gaps if row else [{'reason_codes':['NOT_IN_INPUT_SCOPE']}],
        coverage_gaps=row.coverage_gaps if row else [],next_observation_conditions=next_conditions,
        invalidation_conditions=invalidation,alternatives_and_disagreements=[row.old_new_comparison,
            {'family_assessments':[a.model_dump(mode='json',exclude={'conditions'}) for a in row.family_assessments]}] if row else [],
        discussion_questions=['绑定支撑的实时测试是否有充分证据？','是否只剩历史确认，当前已不适用？',
            '跳空、回撤及恢复样本有哪些缺口？','公司质量和行业地位有哪些可验证资料？','还缺哪些报价和合约核验事实？'],
        options_context=input.options_context,company_context=input.company_context,market_context=input.market_context,
        portfolio_context=input.portfolio_context,detail_index=[r.ref for r in records],evidence_records=records,
        shortlist_id=input.shortlist.shortlist_id if input.shortlist else None,
        reason_codes=errors+(['COMPANY_QUALITY_NOT_EVALUATED'] if input.company_context.status=='NOT_EVALUATED' else []))
    identity = packet_identity(packet)
    return packet.model_copy(update={'packet_id':digest([packet.calculation_version,identity]),'content_identity':identity})


def resolve_evidence(input: EvidenceQuery) -> EvidenceQueryResult:
    packet = input.packet
    if not packet_identity_valid(packet):
        return EvidenceQueryResult(status='UNRESOLVED',record=None,reason_codes=['PACKET_CONTENT_HASH_MISMATCH'])
    ids = [input.evidence_id] if input.evidence_id else input.evidence_ids
    if not ids:
        return EvidenceQueryResult(status='NOT_FOUND',record=None,reason_codes=['EVIDENCE_ID_REQUIRED'])
    lookup = {}
    for record in packet.evidence_records:
        keys=[record.ref.evidence_id,record.ref.entity_id,f'{record.ref.source_result_id}:{record.ref.json_pointer}']
        if record.kind in ('CONDITION','METRIC'):
            keys.append(f'{record.ref.source_result_id}:{record.ref.known_at}:{record.ref.entity_id}')
        for key in keys:
            lookup.setdefault(key,[]).append(record)
    index = {r.evidence_id:r for r in packet.detail_index}
    resolved,unresolved,reasons = [],[],[]
    for key in ids:
        found = lookup.get(key,[])
        if not found:
            unresolved.append(key);reasons.append('EVIDENCE_NOT_FOUND');continue
        if len({r.ref.content_sha256 for r in found})>1:
            unresolved.append(key);reasons.append('AMBIGUOUS_EVIDENCE_ID');continue
        record=sorted(found,key=lambda r:r.ref.evidence_id)[0]
        if index.get(record.ref.evidence_id)!=record.ref or digest(record.value)!=record.ref.content_sha256:
            unresolved.append(key);reasons.append('EVIDENCE_HASH_OR_INDEX_MISMATCH');continue
        resolved.append(record)
    return EvidenceQueryResult(status='RESOLVED' if not unresolved else 'NOT_FOUND' if not resolved and set(reasons)=={'EVIDENCE_NOT_FOUND'} else 'UNRESOLVED',
        record=resolved[0] if len(ids)==1 and resolved else None,records=resolved,unresolved_refs=unresolved,reason_codes=sorted(set(reasons)))


def record_ai_review(input: AIReviewInput) -> AIReview:
    packet, submission = input.packet,input.submission
    if (not packet_identity_valid(packet) or submission.packet_id!=packet.packet_id or
        submission.packet_content_identity!=packet.content_identity):
        raise ValueError('REVIEW_PACKET_IDENTITY_MISMATCH')
    for ref in submission.supporting_refs+submission.opposing_refs:
        if resolve_evidence(EvidenceQuery(packet=packet,evidence_id=ref)).status!='RESOLVED':
            raise ValueError('REVIEW_EVIDENCE_UNRESOLVED:'+ref)
    payload = submission.model_dump(mode='json');payload.pop('review_id')
    content = digest(payload)
    review_id = submission.review_id or content
    for existing in input.existing_reviews:
        if existing.review_id==review_id:
            old_payload=existing.model_dump(mode='json',include=set(ReviewSubmission.model_fields)-{'review_id'})
            if digest(old_payload)!=existing.content_sha256 or existing.content_sha256!=content:
                raise ValueError('REVIEW_ID_CONTENT_CONFLICT')
            return existing
    return AIReview(**payload,review_id=review_id,content_sha256=content)
