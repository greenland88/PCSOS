"""Runner-owned immutable objects and compact, token-checked checkpoints."""
import json
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pcs.pool.artifacts import _write_atomic
from pcs.selection.identity import digest
from .observation_models import ComponentRef,ObservationSymbol
from .observation_components import now


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path,value):
    return _write_atomic(Path(path),json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')))


def checked_path(root,file):
    root=Path(root).resolve();path=(root/file).resolve()
    if not path.is_relative_to(root):
        raise ValueError('OBSERVATION_PATH_OUTSIDE_RUN')
    return path


def read_ref(root,ref):
    if not isinstance(ref,ComponentRef):
        ref=ComponentRef.model_validate(ref)
    raw=checked_path(root,ref.file).read_bytes()
    if sha256(raw).hexdigest()!=ref.sha256:
        raise ValueError('OBSERVATION_OBJECT_HASH_MISMATCH')
    obj=json.loads(raw)
    if (obj['symbol'],obj['kind'],obj['dependency_id'])!=(ref.symbol,ref.kind,ref.dependency_id):
        raise ValueError('OBSERVATION_OBJECT_IDENTITY_MISMATCH')
    value=obj['value']
    if isinstance(value,dict):
        if value.get('symbol',ref.symbol)!=ref.symbol:
            raise ValueError('OBSERVATION_OBJECT_SYMBOL_MISMATCH')
        if ref.result_id and ref.result_id!=value.get('result_id',value.get('packet_id',value.get('row_id'))):
            raise ValueError('OBSERVATION_OBJECT_RESULT_ID_MISMATCH')
    return obj['value']


def interrupted_run(spec,reason,detail):
    """Called by the supervisor only after the child has been reaped."""
    from collections import Counter
    from .observation_models import StockObservationRun
    root=Path(spec.output_directory)/spec.run_id
    saved=read_json(root/'checkpoint.json') if (root/'checkpoint.json').exists() else {}
    token=saved.get('attempt_id','NOT_STARTED')
    counts=Counter()
    for symbol in spec.symbols:
        ref=saved.get('symbols',{}).get(symbol)
        state=ObservationSymbol.model_validate(read_ref(root,ref)) if ref else None
        counts[state.execution if state and state.served_attempt==token else 'UNPROCESSED']+=1
    dependency=read_json(root/'dependency_manifest.json') if (root/'dependency_manifest.json').exists() else {}
    result=StockObservationRun(run_id=spec.run_id,attempt_id=token,status='PARTIAL',coverage='PARTIAL',
        context=spec.context,source_commit=dependency.get('source_commit','UNCONFIRMED_CHILD'),
        spec_id=saved.get('spec_id','NOT_STARTED'),profile_id=spec.selection_profile.profile_id,
        universe_id=spec.universe_id,requested_symbols=spec.symbols,output_directory=str(root),checkpoint=str(root/'checkpoint.json'),
        summary=dict(requested_count=len(spec.symbols),execution_completed_count=counts['COMPLETED'],
            data_blocked_count=counts['DATA_BLOCKED'],failed_count=counts['FAILED'],
            timed_out_count=counts['TIMED_OUT'],unprocessed_count=counts['UNPROCESSED']),
        selection_v2=dict(status='RECOVER_FROM_COMMITTED_COMPONENTS'),reason_codes=[reason,detail])
    write_json(root/'interrupted_run.json',result.model_dump(mode='json'))
    write_json(Path(spec.output_directory)/'LATEST_ATTEMPT.json',dict(run_id=spec.run_id,directory=str(root),
        status='PARTIAL',coverage='PARTIAL',requested_as_of=spec.context.requested_as_of))
    return result


class ObservationStore:
    def __init__(self,root,spec_id,run_id,*,resume=False):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        (self.root/'objects').mkdir(exist_ok=True)
        self.path=self.root/'checkpoint.json'
        if self.path.exists():
            if not resume:
                raise ValueError('OBSERVATION_RUN_EXISTS_USE_RESUME')
            self.checkpoint=read_json(self.path)
            if self.checkpoint['spec_id']!=spec_id or self.checkpoint['run_id']!=run_id:
                raise ValueError('OBSERVATION_RESUME_IDENTITY_MISMATCH')
        else:
            if resume:
                raise ValueError('OBSERVATION_RESUME_ANCHOR_MISSING')
            self.checkpoint=dict(version='1.0',scope='STOCK_OBSERVATION',spec_id=spec_id,run_id=run_id,symbols={})
        self.token=uuid4().hex
        self.checkpoint['attempt_id']=self.token
        self.checkpoint['status']='IN_PROGRESS'
        self.flush()

    def flush(self):
        write_json(self.path,self.checkpoint)

    def state(self,symbol):
        ref=self.checkpoint['symbols'].get(symbol)
        if not ref:
            return ObservationSymbol(symbol=symbol)
        return ObservationSymbol.model_validate(read_ref(self.root,ref))

    def put(self,symbol,kind,dependency_id,value,revision=1):
        if hasattr(value,'model_dump'):
            value=value.model_dump(mode='json')
        obj=dict(symbol=symbol,kind=kind,dependency_id=dependency_id,value=value)
        raw=json.dumps(obj,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
        checksum=sha256(raw).hexdigest()
        file=f'objects/{checksum}.json'
        path=self.root/file
        if not path.exists():
            _write_atomic(path,raw.decode('utf-8'))
        elif path.read_bytes()!=raw:
            raise ValueError('OBSERVATION_IMMUTABLE_OBJECT_CONFLICT')
        ref=ComponentRef(file=file,sha256=checksum,kind=kind,symbol=symbol,dependency_id=dependency_id,
            result_id=value.get('result_id',value.get('packet_id',value.get('row_id'))),revision=revision,computed_at=now())
        read_ref(self.root,ref)
        return ref

    def save_state(self,state):
        ref=self.put(state.symbol,'SYMBOL_STATE',self.checkpoint['spec_id'],state)
        self.checkpoint['symbols'][state.symbol]=ref.model_dump(mode='json')
        self.flush()

    def commit(self,state,kind,dependency_id,value,*,token,expected_revision,deadline,after_object=None):
        actual=self.state(state.symbol)
        revision=actual.components[kind].revision if kind in actual.components else 0
        if token!=self.token or read_json(self.path)['attempt_id']!=token or revision!=expected_revision or perf_counter()>deadline:
            raise ValueError('OBSERVATION_STALE_ATTEMPT_OR_REVISION')
        ref=self.put(state.symbol,kind,dependency_id,value,revision+1)
        if after_object:
            after_object(ref)
        if perf_counter()>deadline:
            raise ValueError('OBSERVATION_COMMIT_DEADLINE_EXCEEDED')
        updated=state.model_copy(update={'components':{**state.components,kind:ref},'served_at':now(),
            'recomputed':state.recomputed+1})
        self.save_state(updated)
        return updated
