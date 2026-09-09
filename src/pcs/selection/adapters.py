"""Saved-package compatibility boundary. No canonical/data-provider or detector calls."""
from pathlib import Path
from hashlib import sha256
import json
from typing import Literal
from pydantic import Field

from pcs.analysis_contracts import StrictModel
from pcs.pool.opportunities import read_opportunity_bundle
from pcs.trend.selection_models import EntryOpportunity, SupportZoneResult
from .identity import digest, semantic_id
from .models import (Family, BatchContext, RankingPolicy, RankingInput, ResultBinding,
    InputFailure, Diagnostic, LegacyAssessment, EvidenceRecord)
from .packets import evidence_record


class BundleSelection(StrictModel):
    bundle_id: str
    path: str
    kind: Literal['OPPORTUNITIES','PROFILE','EXPLANATION','SUPPORT']
    families: list[Family] = Field(default_factory=list)
    expected_source_commit: str | None = None
    optional: bool = False


class SelectionInputManifest(StrictModel):
    version: Literal['1.0'] = '1.0'
    context: BatchContext
    requested_symbols: list[str]
    enabled_families: list[Family]
    bundles: list[BundleSelection]
    selected_result_ids: dict[str,str] = Field(default_factory=dict)
    policy: RankingPolicy = Field(default_factory=RankingPolicy)


class LoadedSelection(StrictModel):
    ranking_input: RankingInput
    extra_records: dict[str,list[EvidenceRecord]]
    input_manifest: dict
    read_audit: dict


def read_hashed_documents(root):
    root = Path(root)
    manifest_path = root/('artifact_manifest.json' if (root/'artifact_manifest.json').exists() else 'manifest.json')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    hashes = manifest.get('sha256',manifest.get('artifact_hashes'))
    if not hashes:
        raise ValueError('SOURCE_MANIFEST_HASHES_REQUIRED')
    docs = {}
    for name, expected in hashes.items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('SOURCE_ARTIFACT_PATH_INVALID')
        raw = path.read_bytes()
        if sha256(raw).hexdigest()!=expected:
            raise ValueError('SOURCE_ARTIFACT_HASH_MISMATCH:'+name)
        if name in ('underlying_profiles.json','selection_explanations.json','support_zone_results.json'):
            docs[name] = json.loads(raw)
    return manifest,docs


def _children(raw):
    return [child for r in raw for child in (_children(r['family_results']) if r.get('family_results') else [r])]


def load_selection_input(input_manifest: str | Path | SelectionInputManifest) -> LoadedSelection:
    parent = Path.cwd()
    if isinstance(input_manifest,SelectionInputManifest):
        spec = input_manifest
    else:
        path = Path(input_manifest)
        parent = path.resolve().parent
        spec = SelectionInputManifest.model_validate_json(path.read_text(encoding='utf-8'))
    results,bindings,failures,diagnostics,legacy,supports = [],[],[],[],[],[]
    extra = {s:[] for s in spec.requested_symbols}
    reads, frozen = [],[]
    cache = {}
    for bundle in spec.bundles:
        root = (parent/bundle.path).resolve()
        try:
            cache_key = (root,bundle.kind=='OPPORTUNITIES')
            if cache_key not in cache:
                cache[cache_key] = read_opportunity_bundle(root) if bundle.kind=='OPPORTUNITIES' else read_hashed_documents(root)
            manifest,docs = cache[cache_key]
            if bundle.expected_source_commit and manifest.get('source_commit')!=bundle.expected_source_commit:
                raise ValueError('SOURCE_COMMIT_MISMATCH')
            if manifest.get('tracked_source_dirty') is True:
                raise ValueError('SOURCE_TRACKED_DIRTY')
        except (OSError,ValueError) as exc:
            reads.append(dict(bundle_id=bundle.bundle_id,status='UNAVAILABLE',reason=str(exc),optional=bundle.optional))
            if not bundle.optional:
                failures += [InputFailure(symbol=s,stage='SAVED_PACKAGE_READ',reason_codes=['SOURCE_PACKAGE_UNAVAILABLE'],
                    source_refs=[bundle.bundle_id]) for s in spec.requested_symbols]
            continue
        manifest_hash = digest(manifest)
        reads.append(dict(bundle_id=bundle.bundle_id,status='HASH_VERIFIED',manifest_sha256=manifest_hash))
        frozen.append(dict(**bundle.model_dump(mode='json'),manifest=manifest,manifest_sha256=manifest_hash))
        if bundle.kind=='OPPORTUNITIES':
            inputs = {r['call_context']['symbol']:r for r in docs['prepared_opportunity_inputs.json']}
            for raw in _children(docs['entry_opportunities.json']):
                symbol,family = raw['symbol'],raw['family']
                if symbol not in spec.requested_symbols or family not in bundle.families:
                    continue  # In particular: never type-load the superseded platform v1 aggregate.
                try:
                    result = EntryOpportunity.model_validate(raw)
                    view = inputs[symbol]['feature_view']
                    source = view['source']
                    if not source['validated']:
                        raise ValueError('SAVED_SOURCE_UNVERIFIED')
                    binding = ResultBinding(result_id=result.result_id,symbol=symbol,family=family,
                        source_bundle=bundle.bundle_id,manifest_sha256=manifest_hash,content_sha256=digest(raw),
                        schema_version=result.version,calculation_version=result.calculation_version,
                        feature_identity=semantic_id(view),price_basis=view['price_basis'],indicator_identity=view['indicator_identity'],
                        corporate_action_version=view['corporate_action_version'],calendar=inputs[symbol]['calendar'],
                        source_identity=digest([source['sha256'],source['record_identity']]))
                    if result.base_result and result.base_result.calendar!=binding.calendar:
                        raise ValueError('SAVED_CALENDAR_MISMATCH')
                    if any(getattr(result.next_state,field)!=getattr(binding,field)
                        for field in ('price_basis','indicator_identity','corporate_action_version')):
                        raise ValueError('RESULT_INPUT_BINDING_MISMATCH')
                    if not result.provenance or digest([result.provenance[0].sha256,result.provenance[0].record_identity])!=binding.source_identity:
                        raise ValueError('RESULT_SOURCE_BINDING_MISMATCH')
                    results.append(result);bindings.append(binding)
                except (KeyError,ValueError) as exc:
                    failures.append(InputFailure(symbol=symbol,family=family,stage='SAVED_RESULT_COMPATIBILITY',
                        reason_codes=['RESULT_SCHEMA_OR_IDENTITY_INCOMPATIBLE'],source_refs=[bundle.bundle_id,str(exc)]))
            audit = docs.get('read_audit.json',{})
            for failure in audit.get('failures',audit.get('historical_failures_not_retried',[])):
                if failure['symbol'] in spec.requested_symbols:
                    failures.append(InputFailure(symbol=failure['symbol'],stage='SAVED_FAILURE_NOT_RETRIED',
                        reason_codes=[failure.get('reason','SOURCE_RESULT_UNAVAILABLE')],source_refs=[bundle.bundle_id]))
        elif bundle.kind=='PROFILE':
            for raw in docs['underlying_profiles.json']:
                symbol = raw['symbol']
                if symbol not in spec.requested_symbols:
                    continue
                source = raw.get('provenance',[{}])[0]
                for i,metric in enumerate(raw['measurements']):
                    extra[symbol].append(evidence_record(raw['result_id'],f'/measurements/{i}',metric['metric_id'],
                        raw['as_of'],raw['version'],bundle.bundle_id,'METRIC',metric))
                    if metric['metric_id']=='dollar_volume_median_20':
                        diagnostics.append(Diagnostic(symbol=symbol,metric_id=metric['metric_id'],result_id=raw['result_id'],
                            as_of=metric.get('as_of',raw['as_of']),value=metric['value'],unit=metric['unit'],
                            currency=source.get('detail',{}).get('currency'),price_basis=source.get('detail',{}).get('price_basis'),
                            validated=bool(source.get('validated') and metric.get('status')=='COMPLETED'),
                            source_refs=metric.get('evidence_refs',[]),record=metric))
        elif bundle.kind=='EXPLANATION':
            for raw in docs['selection_explanations.json']:
                symbol = raw['symbol']
                if symbol not in spec.requested_symbols:
                    continue
                data = raw['data']; result_id=data['identity']['result_id']
                saved = data['selection_explanation']
                timing = saved.get('legacy_timing',{})
                # Missing boolean stays unknown; WAIT/OPEN/reason text never supplies it.
                eligible = timing.get('eligible_at_requested_time')
                if not isinstance(eligible,bool):
                    eligible = None
                legacy.append(LegacyAssessment(symbol=symbol,as_of=raw['as_of'],result_id=result_id,
                    timing_eligible=eligible,trend=saved.get('stock_structure',{}),timing=timing,
                    options=data.get('capabilities',{}).get('options_evaluation',{}),
                    action=data['identity'].get('source_final_action'),source_refs=[bundle.bundle_id]))
                for field in ('assessment','rule_evaluations','next_conditions','capabilities','entrypoint_disagreements'):
                    value = data.get(field,{})
                    extra[symbol].append(evidence_record(result_id,'/data/'+field,field,raw['as_of'],raw['version'],
                        bundle.bundle_id,'LEGACY_'+field.upper(),{'value':value}))
        elif bundle.kind=='SUPPORT':
            for raw in docs['support_zone_results.json']:
                if raw['symbol'] in spec.requested_symbols:
                    supports.append(SupportZoneResult.model_validate(raw))
    inp = RankingInput(context=spec.context,requested_symbols=spec.requested_symbols,enabled_families=spec.enabled_families,
        opportunities=results,bindings=bindings,selected_result_ids=spec.selected_result_ids,
        failures=failures,diagnostics=diagnostics,legacy=legacy,supports=supports,policy=spec.policy)
    return LoadedSelection(ranking_input=inp,extra_records=extra,
        input_manifest=dict(spec=spec.model_dump(mode='json'),sources=frozen,selected_bindings=[b.model_dump(mode='json') for b in bindings]),
        read_audit=dict(reads=reads,package_read_count=len(cache),failures=[f.model_dump(mode='json') for f in failures],
            canonical_read=False,provider_read=False,detector_called=False,model_called=False))
