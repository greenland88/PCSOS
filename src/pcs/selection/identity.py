"""Canonical semantic hashing; transport metadata never changes an assessment."""
import hashlib
import json

TRANSPORT = {'run_id','request_id','received_at','source_bundle','manifest_sha256','source_commit',
    'canonical_paths','manifest_path'}


def plain(value):
    return value.model_dump(mode='json') if hasattr(value,'model_dump') else value


def semantic(value):
    value = plain(value)
    if isinstance(value,dict):
        return {k:sorted(v.values()) if k=='file_sha256' and isinstance(v,dict) else semantic(v)
            for k,v in value.items() if k not in TRANSPORT}
    if isinstance(value,(list,tuple)):
        return [semantic(v) for v in value]
    return value


def digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(plain(value),sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def semantic_id(value):
    return digest(semantic(value))
