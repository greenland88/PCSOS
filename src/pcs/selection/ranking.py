"""Pure observation ranking of saved decisions. Never calls a detector."""
from datetime import date
from collections import defaultdict
import exchange_calendars as xc

from .identity import semantic, semantic_id, digest
from .models import (FAMILIES, GROUPS, RankingInput, StockShortlist, StockRow,
    FamilyAssessment, SortKey, ResultBinding)


def flatten_results(results):
    return [c for r in results for c in (flatten_results(r.family_results) if r.family_results else [r])]


def binding_for(result, supplied):
    found = [b for b in supplied if b.result_id==result.result_id]
    if found:
        signatures=[b.model_dump(mode='json',exclude={'source_bundle','manifest_sha256','content_sha256'}) for b in found]
        if len({semantic_id(b) for b in signatures}) != 1:
            raise ValueError('AMBIGUOUS_RESULT_BINDING')
        return sorted(found,key=lambda b:(b.source_bundle,b.manifest_sha256,b.content_sha256))[0]
    state = result.next_state
    source = result.provenance[0] if result.provenance else None
    return ResultBinding(result_id=result.result_id,symbol=result.symbol,family=result.family,
        source_bundle='TYPED_INPUT',manifest_sha256='',content_sha256=semantic_id(result),
        schema_version=result.version,calculation_version=result.calculation_version,
        price_basis=state.price_basis,indicator_identity=state.indicator_identity,
        corporate_action_version=state.corporate_action_version,
        calendar=result.base_result.calendar if result.base_result else 'XNYS',
        source_identity=digest([source.sha256,source.record_identity]) if source and source.validated and source.sha256 and source.record_identity else '')


def select_results(inp, symbol):
    """Explicit same-family choice; content collisions and shared identities fail locally."""
    by_family = defaultdict(dict)
    errors, selected, bindings = [], [], []
    for r in flatten_results(inp.opportunities):
        if r.symbol != symbol or r.family not in inp.enabled_families:
            continue
        old = by_family[r.family].get(r.result_id)
        if old is not None and semantic_id(old) != semantic_id(r):
            errors.append('RESULT_ID_CONTENT_CONFLICT')
        by_family[r.family][r.result_id] = r
    for family, variants in sorted(by_family.items()):
        choice = inp.selected_result_ids.get(f'{symbol}|{inp.context.requested_session}|{family}')
        if choice is not None and choice not in variants:
            errors.append('SELECTED_RESULT_NOT_FOUND')
            continue
        if len(variants)>1 and choice is None:
            errors.append('AMBIGUOUS_FAMILY_RESULT')
            continue
        r = variants[choice] if choice else next(iter(variants.values()))
        try:
            binding = binding_for(r,inp.bindings)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if binding.symbol != symbol or binding.family != family or not binding.source_identity:
            errors.append('SOURCE_IDENTITY_UNVERIFIED')
        ctx = inp.context
        if (r.requested_session != ctx.requested_session or r.request_time_semantics != ctx.mode or
            r.as_of != ctx.effective_daily_session or
            ctx.mode=='CURRENT_EOD' and r.call_context.requested_as_of != ctx.requested_as_of):
            errors.append('ASSESSMENT_TIME_MISMATCH')
        if binding.calendar != ctx.calendar:
            errors.append('CALENDAR_MISMATCH')
        selected.append(r)
        bindings.append(binding)
    for field in ['price_basis','indicator_identity','corporate_action_version','calendar','source_identity','feature_identity']:
        values = {getattr(b,field) for b in bindings if getattr(b,field) is not None}
        if len(values)>1:
            errors.append('SHARED_INPUT_IDENTITY_CONFLICT:'+field)
    claims = defaultdict(set)
    for result,binding in zip(selected,bindings):
        resolution = result.base_result.timeline[-1].structure_resolution if result.base_result and result.base_result.timeline else None
        authority = [binding.source_identity,binding.indicator_identity]
        if resolution and resolution.selected_from!='FEATURE_BAR':
            authority = resolution.selected_source_refs
        for condition in result.current_conditions:
            if ('STRUCTURE' in condition.condition_id and condition.session==inp.context.effective_daily_session and
                condition.left_value in ('bullish','neutral','deteriorating','bearish')):
                claims[digest([authority,condition.session,'structure_state'])].add(digest(condition.left_value))
    for claim in inp.shared_facts:
        if claim.symbol == symbol and claim.known_at <= inp.context.effective_daily_session:
            claims[claim.identity].add(digest(claim.value))
    if any(len(values)>1 for values in claims.values()):
        errors.append('SHARED_REQUIRED_EVIDENCE_CONFLICT')
    return sorted(selected,key=lambda r:(r.family,r.result_id)), sorted(bindings,key=lambda b:b.result_id), sorted(set(errors))


def assess_family(result, family, enabled, errors, failures, ctx):
    if result is None:
        return FamilyAssessment(family=family,result_id=None,state=None,eligible_at_requested_time=None,
            execution='NOT_EXECUTED' if enabled else 'NOT_ENABLED',applicability='INSUFFICIENT_EVIDENCE',
            current_required_complete=False,reason_codes=(['FAMILY_NOT_EXECUTED'] if enabled else ['FAMILY_NOT_ENABLED'])+
                [c for f in failures if f.family in (None,family) for c in f.reason_codes])
    r = result
    last = next((d for d in reversed(r.timeline) if d.session <= ctx.effective_daily_session),None)
    current = [g.model_dump(mode='json') for g in r.current_missing_details]
    if r.eligible_at_requested_time is None and not current:
        current = [dict(reason_codes=['CURRENT_ELIGIBILITY_UNKNOWN'],source_result_id=r.result_id,
            condition_ids=[c.condition_id for c in r.current_conditions if c.role!='DIAGNOSTIC' and c.predicate_value is None])]
    complete = r.eligible_at_requested_time is not None and not current and not errors
    confirmation = last.confirmation_date if last else None
    start, end = r.entry_permitted_from, r.entry_permitted_until
    errors = list(errors)
    expiry_reasons = []
    if r.confirmation_deadline_elapsed_at_requested_session:
        expiry_reasons.append('CONFIRMATION_DEADLINE_ELAPSED_AT_REQUEST')
    if r.entry_window_elapsed_at_requested_session:
        expiry_reasons.append('ENTRY_WINDOW_ELAPSED_AT_REQUEST')
    if confirmation and confirmation>ctx.effective_daily_session:
        errors.append('CONFIRMATION_AFTER_EVIDENCE_SESSION')
        complete=False
        confirmation=None
    if r.eligible_at_requested_time is True and r.state in ('EXPIRED','INVALIDATED','NO_SETUP'):
        errors.append('SAVED_STATE_ELIGIBILITY_CONFLICT')
        complete=False
    if errors:
        group = 'INSUFFICIENT_EVIDENCE'
    elif expiry_reasons:
        group = 'NOT_CURRENTLY_APPLICABLE'
    elif r.eligible_at_requested_time is True:
        group = 'READY_FOR_OPTIONS_REVIEW'
    elif not complete:
        group = 'INSUFFICIENT_EVIDENCE'
    elif r.state in ('WATCH','CONFIRMING') or (r.state=='ENTRY_READY' and start and ctx.requested_session < start):
        group = 'WATCH_SETUP'
    elif r.state == 'NO_SETUP':
        group = 'NO_SETUP'
    else:
        group = 'NOT_CURRENTLY_APPLICABLE'
    identity = r.opportunity_id or r.result_id
    key = [GROUPS.index(group),not complete,confirmation is None,
        -date.fromisoformat(confirmation).toordinal() if confirmation else 0,identity,r.result_id]
    return FamilyAssessment(family=family,result_id=r.result_id,opportunity_id=r.opportunity_id,state=r.state,
        data_session=r.as_of,requested_session=r.requested_session,request_time_semantics=r.request_time_semantics,
        eligible_at_requested_time=r.eligible_at_requested_time,execution='EXECUTED',applicability=group,
        current_required_complete=complete,confirmation_date=confirmation,entry_start=start,entry_end=end,
        setup_date=last.setup_date if last else None,touch_date=last.touch_date if last else None,
        confirmation_deadline=last.confirmation_deadline if last else None,
        confirmation_deadline_elapsed_at_requested_session=r.confirmation_deadline_elapsed_at_requested_session,
        entry_window_elapsed_at_requested_session=r.entry_window_elapsed_at_requested_session,
        support_zone_id=last.support_zone_id if last else None,reason_codes=list(dict.fromkeys(errors+expiry_reasons+
            ([c.condition_id for c in r.current_conditions if c.role!='DIAGNOSTIC' and c.predicate_value is not True])+[group])),
        current_gaps=current,coverage_gaps=[g.model_dump(mode='json') for g in r.coverage_missing_evidence],
        conditions=r.current_conditions,representative_key=key)


def bound_zone(inp, result, zone_id):
    if not result or not zone_id:
        return None, []
    zones = []
    if result.base_result:
        zones += [e.lower_zone for e in result.base_result.base_events]
    if result.breakout_result:
        zones += [e.zone for e in result.breakout_result.events]
    refs = [result.result_id]
    for support in inp.supports:
        primary = result.provenance[0] if result.provenance else None
        source = support.provenance[0] if support.provenance else None
        if (support.symbol==result.symbol and support.as_of==result.as_of and
            primary and source and primary.validated and source.validated and
            (primary.sha256,primary.record_identity)==(source.sha256,source.record_identity) and
            support.next_state.price_basis==result.next_state.price_basis and
            support.next_state.corporate_action_version==result.next_state.corporate_action_version):
            zones += support.current_zones+support.archived_zones
            refs.append(support.result_id)
    matching = [z for z in zones if z.zone_id==zone_id]
    if len({semantic_id(z) for z in matching}) != 1:
        return None, refs
    return matching[0], refs


def live_support(inp, result, zone_id):
    zone, refs = bound_zone(inp,result,zone_id)
    day = inp.context.effective_daily_session
    if zone is None or zone.available_at>day:
        return None, 'UNKNOWN', refs, ['BOUND_LIVE_TEST_EVIDENCE_UNAVAILABLE']
    tests = sorted([t for t in zone.tests if zone.available_at<=t.touch_session<=day and
        (zone.zone_type!='BASE_LOWER_BOUNDARY' or t.touch_session>zone.available_at)],key=lambda t:(t.touch_session,t.test_id))
    held = [t for t in tests if t.first_held_at and t.touch_session < t.first_held_at <= day]
    independent = []
    for t in held:
        if not independent or (independent[-1].departure_session and independent[-1].departure_session<t.touch_session):
            independent.append(t)
    category = ('MULTIPLE_INDEPENDENT_HELD' if len(independent)>1 else 'SINGLE_HELD' if held else
        'IN_PROGRESS' if any(t.touch_session<=day and (t.ended_at is None or t.ended_at>day) for t in tests) else
        'UNCONFIRMED_TEST' if any(t.ended_at and t.ended_at<=day for t in tests) else 'UNKNOWN')
    value = inp.policy.live_test_order.index(category) if category!='UNKNOWN' else None
    return value, category, [*refs,zone.zone_id,*[t.test_id for t in tests]], [] if value is not None else ['NO_VERIFIED_LIVE_TEST']


def confirmation_details(result, assessment, day):
    if not result:
        return None, {}, []
    candidates = [d for d in result.timeline if d.session<=day and
        (d.session==assessment.confirmation_date if assessment.confirmation_date else
         d.session==day and assessment.state=='CONFIRMING')]
    conditions = [c for d in candidates for c in d.conditions if c.role=='CONFIRMATION']
    counts = {'true':sum(c.predicate_value is True for c in conditions),
        'false':sum(c.predicate_value is False for c in conditions),
        'unknown':sum(c.predicate_value is None and c.status!='NOT_EVALUATED' for c in conditions),
        'not_executed':sum(c.status=='NOT_EVALUATED' for c in conditions),'total':len(conditions),
        'condition_ids':[c.condition_id for c in conditions], 'session':candidates[0].session if candidates else None}
    fraction = counts['true']/len(conditions) if conditions and counts['not_executed']<len(conditions) else None
    return fraction, counts, [f'{result.result_id}:{c.session}:{c.condition_id}' for c in conditions]


def window_sessions(context, start, end):
    if not start or not end or start>end:
        return None, None, ['ENTRY_WINDOW_NOT_RECORDED']
    try:
        calendar = xc.get_calendar(context.calendar)
        if not all(calendar.is_session(s) for s in (context.requested_session,start,end)):
            return None, None, ['ENTRY_WINDOW_SESSION_INVALID']
        remaining = len(calendar.sessions_in_range(max(context.requested_session,start),end)) if context.requested_session<=end else 0
        waiting = len(calendar.sessions_in_range(context.requested_session,start))-1 if context.requested_session<start else 0
    except (ValueError,KeyError):
        return None,None,['ENTRY_WINDOW_SESSION_INVALID']
    return remaining,waiting,[]


def sort_keys(inp, symbol, representative, result):
    complete = representative.current_required_complete if representative else False
    zone_id = representative.support_zone_id if representative else None
    live, category, live_refs, live_reasons = live_support(inp,result,zone_id)
    fraction, confirmation, confirm_refs = confirmation_details(result,representative,inp.context.effective_daily_session)
    remaining, waiting, window_reasons = window_sessions(inp.context,
        representative.entry_start if representative else None,representative.entry_end if representative else None)
    diagnostics = [d for d in inp.diagnostics if d.symbol==symbol and d.metric_id=='dollar_volume_median_20']
    valid = [d for d in diagnostics if d.validated and d.as_of==inp.context.effective_daily_session and
        d.currency==inp.policy.liquidity_currency and d.unit==inp.policy.liquidity_currency+'/session' and
        result and d.price_basis==result.next_state.price_basis and d.value is not None]
    valid = list({semantic_id(d):d for d in valid}.values())
    liquidity = valid[0] if len(valid)==1 else None
    refs = [result.result_id] if result else []
    values = {
        'current_required_complete':(complete,'boolean',refs,[],{}),
        'live_support_class':(live,'category_order',live_refs,live_reasons,{'category':category,'bound_zone_id':zone_id,'retrospective_excluded':True}),
        'confirmation_fraction':(fraction,'fraction',confirm_refs,[] if fraction is not None else ['CONFIRMATION_NOT_EXECUTED_OR_NOT_RECORDED'],confirmation),
        'remaining_entry_sessions':(remaining,'exchange_sessions',refs,window_reasons,{'waiting_sessions':waiting,'calendar':inp.context.calendar}),
        'dollar_volume_median_20':(liquidity.value if liquidity else None,inp.policy.liquidity_currency+'/session',
            [liquidity.result_id, f'{liquidity.result_id}:/diagnostics/{liquidity.metric_id}'] if liquidity else [],[] if liquidity else ['COMPATIBLE_LIQUIDITY_NOT_AVAILABLE'],{}),
        'symbol':(symbol,None,[],[],{}),
    }
    return [SortKey(field=f.field,value=values[f.field][0],unit=values[f.field][1],direction=f.direction,
        available=values[f.field][0] is not None,source_refs=values[f.field][2],reason_codes=values[f.field][3],details=values[f.field][4])
        for f in inp.policy.sort_fields]


def compare_legacy(inp, symbol, eligible):
    records = [r for r in inp.legacy if r.symbol==symbol]
    old = records[0] if len(records)==1 else None
    same_time = old is not None and old.as_of==inp.context.requested_as_of
    value = old.timing_eligible if same_time else None
    status = ('INCOMPLETE_EVIDENCE' if value is None or eligible is None else
        'OLD_AND_NEW' if value and eligible else 'OLD_ONLY' if value else 'NEW_ONLY' if eligible else 'NEITHER')
    return {'status':status,'old_stock_eligible':value,'new_stock_eligible':eligible,
        'legacy':old.model_dump(mode='json') if old else None,
        'reason_codes':['LEGACY_ASSESSMENT_TIME_MISMATCH'] if old and not same_time else ['LEGACY_TIMING_NOT_RECORDED'] if value is None else []}


def condition_supports(value):
    predicate = value.get('predicate_value')
    if predicate is None or value.get('role')=='DIAGNOSTIC':
        return None
    if value.get('condition_id') in {'CLOSE_BELOW_FIXED_INVALIDATION','SUPPORT_ZONE_BROKEN','STRUCTURE_BEARISH_INVALIDATION'}:
        return not predicate
    return predicate


def build_row(inp, symbol):
    results, bindings, errors = select_results(inp,symbol)
    by_family = {r.family:r for r in results}
    failures = [f for f in inp.failures if f.symbol==symbol]
    assessments = [assess_family(by_family.get(f),f,f in inp.enabled_families,errors,failures,inp.context) for f in FAMILIES]
    available=flatten_results(inp.opportunities)
    enriched=[]
    for assessment in assessments:
        candidates=sorted({r.result_id for r in available if r.symbol==symbol and r.family==assessment.family})
        update={'candidate_result_ids':candidates}
        if candidates and assessment.family in inp.enabled_families and assessment.result_id is None:
            update.update(execution='EXECUTED',reason_codes=errors+['FAMILY_RESULT_NOT_SELECTED'],
                current_gaps=[dict(candidate_result_ids=candidates,reason_codes=errors+['FAMILY_RESULT_NOT_SELECTED'])])
        enriched.append(assessment.model_copy(update=update))
    assessments=enriched
    enabled = [a for a in assessments if a.family in inp.enabled_families]
    ready = [a for a in enabled if a.applicability=='READY_FOR_OPTIONS_REVIEW']
    watch = [a for a in enabled if a.applicability=='WATCH_SETUP']
    unknown = [a for a in enabled if a.applicability=='INSUFFICIENT_EVIDENCE']
    group = ('INSUFFICIENT_EVIDENCE' if errors else 'READY_FOR_OPTIONS_REVIEW' if ready else 'WATCH_SETUP' if watch else
        'INSUFFICIENT_EVIDENCE' if unknown else 'NOT_CURRENTLY_APPLICABLE' if any(a.applicability=='NOT_CURRENTLY_APPLICABLE' for a in enabled) else 'NO_SETUP')
    pool = ready or watch or unknown or enabled
    choices = sorted([a for a in pool if a.result_id],key=lambda a:a.representative_key)
    representative = choices[0] if choices else None
    result = by_family.get(representative.family) if representative else None
    eligible = True if group=='READY_FOR_OPTIONS_REVIEW' else None if group=='INSUFFICIENT_EVIDENCE' else False
    conditions = [dict(family=r.family,result_id=r.result_id,**c.model_dump(mode='json')) for r in results for c in r.current_conditions]
    gaps = [g for a in enabled for g in a.current_gaps]+[{'family':a.family,'reason_codes':a.reason_codes}
        for a in enabled if a.execution=='NOT_EXECUTED']+[{'reason_codes':errors} for _ in [0] if errors]
    payload = dict(symbol=symbol,group=group,representative_result_id=result.result_id if result else None,
        representative_opportunity_id=result.opportunity_id if result else None,
        representative_candidates=[dict(result_id=a.result_id,opportunity_id=a.opportunity_id,key=a.representative_key,
            selected=bool(result and a.result_id==result.result_id)) for a in sorted(enabled,key=lambda a:a.result_id or '')],
        family_assessments=assessments,active_families=[a.family for a in ready],watch_families=[a.family for a in watch],
        historical_families=[a.family for a in enabled if a.state in ('EXPIRED','INVALIDATED') or a.applicability=='NOT_CURRENTLY_APPLICABLE'],
        current_eligible=eligible,current_required_complete=bool(representative and representative.current_required_complete and not errors),
        coverage_complete=not gaps and all(a.execution=='EXECUTED' and not a.coverage_gaps for a in enabled),
        sort_keys=sort_keys(inp,symbol,representative,result),supporting_evidence=[c for c in conditions if condition_supports(c) is True],
        opposing_evidence=[c for c in conditions if condition_supports(c) is False],current_gaps=gaps,
        coverage_gaps=[g for a in enabled for g in a.coverage_gaps],next_observation_conditions=[s for r in results for s in r.next_observation_conditions],
        invalidation_conditions=[c for c in conditions if c['role']=='INVALIDATION'],
        old_new_comparison=compare_legacy(inp,symbol,eligible),reason_codes=sorted(set(errors+[group]+[c for f in failures for c in f.reason_codes])))
    row = StockRow(row_id='',**payload)
    return row.model_copy(update={'row_id':semantic_id([row.model_dump(mode='json'),inp.context.model_dump(mode='json'),inp.policy.model_dump(mode='json')])}),bindings


def shortlist_changes(previous, rows, policy_hash):
    if previous is None:
        return []
    old, new = {r.symbol:r for r in previous.rows}, {r.symbol:r for r in rows}
    changes = []
    for symbol in sorted(set(old)|set(new)):
        a,b = old.get(symbol),new.get(symbol)
        reasons = []
        refs = []
        if b is None:
            reasons.append('SCOPE_REMOVED')
        elif a is None:
            reasons.append('SCOPE_ADDED')
        else:
            if any(f.execution=='NOT_EXECUTED' for f in b.family_assessments if f.family in previous.enabled_families):
                reasons.append('EVIDENCE_UNAVAILABLE')
            old_f = {f.family:f for f in a.family_assessments}
            for f in b.family_assessments:
                before = old_f[f.family]
                if f.confirmation_date and f.confirmation_date != before.confirmation_date:
                    reasons.append('NEW_CONFIRMATION');refs.append(f.result_id)
                if f.state in ('EXPIRED','INVALIDATED') and f.state!=before.state:
                    reasons.append(f.state);refs.append(f.result_id)
                for c in f.conditions:
                    if c.role=='CURRENT_ELIGIBILITY' and 'DISTANCE' in c.condition_id and c.predicate_value is False and not any(
                        x.condition_id==c.condition_id and x.predicate_value is False for x in before.conditions):
                        reasons.append('DISTANCE_NOT_APPLICABLE');refs.append(f'{f.result_id}:{c.session}:{c.condition_id}')
            signature=lambda row:[(f.family,f.result_id,f.state,f.eligible_at_requested_time,f.data_session) for f in row.family_assessments]
            if signature(a)!=signature(b):
                reasons.append('EVIDENCE_UPDATED')
            elif a.rank!=b.rank and previous.policy_sha256==policy_hash:
                reasons.append('RANK_ONLY_CHANGED')
        if previous.policy_sha256!=policy_hash:
            reasons.append('POLICY_CHANGED')
        if b and set(f.family for f in b.family_assessments if f.execution!='NOT_ENABLED')!=set(previous.enabled_families):
            reasons.append('FAMILY_SCOPE_CHANGED')
        changes.append(dict(symbol=symbol,previous_rank=a.rank if a else None,current_rank=b.rank if b else None,
            reason_codes=sorted(set(reasons)) or ['UNCHANGED'],evidence_refs=sorted(set(refs))))
    return changes


def rank_stock_opportunities(input: RankingInput) -> StockShortlist:
    inp = input
    if not inp.enabled_families:
        raise ValueError('ENABLED_FAMILY_SCOPE_REQUIRED')
    pairs = [build_row(inp,symbol) for symbol in inp.requested_symbols]
    def key(row):
        return (inp.policy.group_order.index(row.group),*[(k.value is None,
            -k.value if k.value is not None and k.direction=='DESC' and isinstance(k.value,(float,int)) else
            tuple(-ord(c) for c in k.value)+(0,) if isinstance(k.value,str) and k.direction=='DESC' else k.value)
            for k in row.sort_keys])
    counts = defaultdict(int)
    rows = []
    for rank,row in enumerate(sorted((p[0] for p in pairs),key=key),1):
        counts[row.group]+=1
        rows.append(row.model_copy(update={'rank':rank,'group_rank':counts[row.group]}))
    policy_hash = digest(inp.policy)
    timestamps=[r.as_of for r in flatten_results(inp.opportunities) if r.symbol in inp.requested_symbols and r.as_of<=inp.context.effective_daily_session]
    payload = dict(shortlist_id='',as_of=inp.context.requested_as_of,data_timestamp=max(timestamps) if timestamps else None,
        run_id=inp.context.run_id,request_id=inp.context.request_id,context=inp.context,requested_symbols=inp.requested_symbols,
        enabled_families=sorted(set(inp.enabled_families)),effective_policy=inp.policy,policy_sha256=policy_hash,rows=rows,
        input_bindings=sorted([b for _,bindings in pairs for b in bindings],key=lambda b:b.result_id),
        adapter_version=inp.adapter_version,coverage_complete=all(r.coverage_complete for r in rows),
        changes_status='COMPARED' if inp.previous else 'NOT_COMPARED',changes=shortlist_changes(inp.previous,rows,policy_hash),
        reason_codes=['HISTORICAL_OBSERVATION_ONLY' if inp.context.mode=='HISTORICAL' else 'OBSERVATION_ONLY'])
    result = StockShortlist(**payload)
    identity = result.model_dump(mode='json')
    # File checksums prove storage integrity, not market semantics.
    identity['input_bindings'] = [dict(result_id=b.result_id,feature_identity=b.feature_identity,source_identity=b.source_identity,
        price_basis=b.price_basis,indicator_identity=b.indicator_identity,calendar=b.calendar) for b in result.input_bindings]
    return result.model_copy(update={'shortlist_id':semantic_id(identity)})
