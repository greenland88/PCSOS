"""Thin public entrypoints and single-symbol queries for the canonical pool runner."""
from pathlib import Path
from hashlib import sha256

from pcs.selection.models import DecisionEvidencePacket,EvidenceQuery
from pcs.selection.packets import resolve_evidence,packet_identity_valid
from .observation_models import (StockObservationInput,StockObservationRun,ObservationResumeInput,
    ObservationQuery,ObservationQueryResult,ObservationSymbol)
from .observation_storage import read_json,read_ref,checked_path


def run_stock_observation(input: StockObservationInput) -> StockObservationRun:
    from .runner import run_pcs_pool
    return run_pcs_pool(mode='EOD',scope='STOCK_OBSERVATION',observation_input=input)


def resume_stock_observation(input: ObservationResumeInput) -> StockObservationRun:
    spec=input.spec.model_copy(update={'run_id':input.run_id,'resume_run_id':input.run_id})
    return run_stock_observation(spec)


def read_stock_observation(input: ObservationQuery) -> ObservationQueryResult:
    root=Path(input.run_directory)
    checkpoint=read_json(root/'checkpoint.json')
    symbol=input.symbol.strip().upper()
    manifest=read_json(root/'observation_manifest.json') if (root/'observation_manifest.json').exists() else {}
    partial=checkpoint.get('attempt_id')!=manifest.get('attempt_id')
    if partial:
        spec=StockObservationInput.model_validate(read_json(root/'observation_spec.json'))
        if symbol not in spec.symbols:
            return ObservationQueryResult(status='NOT_IN_INPUT_SCOPE',symbol=symbol)
        index=checkpoint['symbols']
        if symbol not in index:
            return ObservationQueryResult(status='NOT_EXECUTED',symbol=symbol,
                state=ObservationSymbol(symbol=symbol),reason_codes=['NOT_STARTED'])
    else:
        for name in ('observation_run.json','symbol_index.json'):
            if sha256((root/name).read_bytes()).hexdigest()!=manifest['sha256'][name]:
                raise ValueError('OBSERVATION_MANIFEST_HASH_MISMATCH')
        index=read_json(root/'symbol_index.json')
    if symbol not in index:
        return ObservationQueryResult(status='NOT_IN_INPUT_SCOPE',symbol=symbol)
    state=ObservationSymbol.model_validate(read_ref(root,index[symbol]))
    if state.symbol!=symbol:
        raise ValueError('OBSERVATION_QUERY_SYMBOL_MISMATCH')
    ref=state.components.get('PACKET')
    if not ref:
        return ObservationQueryResult(status='NOT_EXECUTED',symbol=symbol,state=state,reason_codes=['PACKET_NOT_COMMITTED'])
    packet=DecisionEvidencePacket.model_validate(read_ref(root,ref))
    if packet.symbol!=symbol or not packet_identity_valid(packet):
        raise ValueError('OBSERVATION_QUERY_PACKET_IDENTITY_MISMATCH')
    return ObservationQueryResult(status='RESOLVED',symbol=symbol,state=state,packet=packet,
        reason_codes=['IN_PROGRESS_COMMITTED_EVIDENCE_ONLY'] if partial else [],
        evidence=resolve_evidence(EvidenceQuery(packet=packet,evidence_id=input.evidence_id)) if input.evidence_id else None)


__all__=['run_stock_observation','resume_stock_observation','read_stock_observation',
    'StockObservationInput','StockObservationRun','ObservationResumeInput','ObservationQuery','ObservationQueryResult']
