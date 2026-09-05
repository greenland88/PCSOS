"""Resumable operator for the existing canonical daily APIs (no strategy scan).

Run from the authorized data workspace; --output pins the request/checkpoints.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import pandas as pd
import exchange_calendars as xc
from pcs.data.access import PCSDataAccess
from pcs.data.canonical_generations import admit_migrated_daily_symbol, _migrated_daily_files, _validate_migrated_daily_file
from pcs.data.strategy_readiness import resolve_active_verified_daily_handle
from pcs.data.massive_client import load_project_environment
from pcs.pool.registry import UniverseSpec
from pcs.pool.modes import resolve_effective_market_session
from pcs.pool.runner import _daily_requirements
from pcs.data.control_plane import ensure_market_data, MarketDataRequirements


class InspectionAccess(PCSDataAccess):
    """Read-only metadata memoization; invalidates on each file stat change."""
    def _read_manifest(self, path):
        path = Path(path)
        stat = path.stat() if path.exists() else None
        key = (str(path.resolve()), stat.st_mtime_ns if stat else None, stat.st_size if stat else None)
        cache = self.__dict__.setdefault('_inspection_metadata', {})
        if key not in cache:
            cache.clear()
            cache[key] = super()._read_manifest(path)
        return cache[key].copy()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, default=str), encoding='utf-8')
    os.replace(temp, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_result(outcome):
    outcomes = outcome.get('import_outcomes', [])
    text = json.dumps(outcomes).lower()
    if 'gateway returned no daily data' in text:
        return 'SOURCE_CONFIRMED_ZERO_ROWS'
    if '401' in text or '403' in text:
        return 'SOURCE_AUTHENTICATION_FAILED'
    if 'timed out' in text or 'timeout' in text:
        return 'SOURCE_TIMEOUT'
    if 'invalid ohlc' in text or 'quality' in text:
        return 'SOURCE_QUALITY_REJECTED'
    if outcome['status'] in {'READY', 'ALREADY_COMPLETE'}:
        return 'IMPORTED' if any(o.get('status') == 'IMPORTED' for o in outcomes) else 'REUSED'
    return 'SOURCE_OR_CANONICAL_BLOCKED'


def source_attempted(record):
    for action in record['actions']:
        if action['action'] != 'DAILY_LOADER':
            continue
        outcomes = action.get('receipt', {}).get('import_outcomes', [])
        if not outcomes or any(o.get('status') != 'REUSED' for o in outcomes):
            return True
    return False


def verify(symbol, access, request):
    try:
        handle = resolve_active_verified_daily_handle(symbol, request['session'], 200, data_access=access)
        frame = access.read_verified_dataset(handle, end_date=request['session'], required_warmup_rows=200)
        dates = set(pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d'))
        missing = sorted(set(request['warmup_sessions']) - dates)
        return {'status': 'BLOCKED' if missing else 'READY', 'reason_codes': ['DAILY_SESSION_MISSING'] if missing else [],
                'identity': asdict(handle), 'pit_rows': len(frame), 'missing_sessions': missing,
                'verified_at': datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {'status': 'BLOCKED', 'reason_codes': [str(exc)], 'verified_at': datetime.now(timezone.utc).isoformat()}


def inspect(symbol, access, request):
    verified = verify(symbol, access, request)
    rows = access._read_manifest(access.manifest_path)
    rows = rows[(rows.dataset == 'daily') & (rows.symbol.astype(str).str.upper() == symbol)]
    identities = rows.to_dict('records')
    result = {'symbol': symbol, 'before': verified, 'manifest_records': identities, 'actions': [], 'physical': []}
    if verified['status'] == 'READY':
        result.update(primary='READY', final=verified)
        return result
    dates = set()
    for path in _migrated_daily_files(access, symbol):
        year = int(path.parent.name.split('=')[1])
        if not int(request['required_start'][:4]) <= year <= int(request['session'][:4]):
            continue
        try:
            frame, meta = _validate_migrated_daily_file(path, symbol, access)
            dates.update(pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d'))
            result['physical'].append(dict(meta, status='VALIDATED'))
        except Exception as exc:
            result['physical'].append({'path': str(path), 'status': 'INVALID', 'reason': str(exc)})
    result['missing_sessions'] = sorted(set(request['warmup_sessions']) - dates)
    admission = admit_migrated_daily_symbol(symbol, decision_as_of=request['session'], required_start=request['required_start'], read_only=True, data_access=access)
    result['admission_inspection'] = admission
    reasons = list(admission.get('reason_codes', []))
    has_active = rows.get('active_generation', pd.Series(dtype=str)).fillna('').astype(str).str.strip().ne('').any()
    if any('CONFLICT' in r or 'DUPLICATE' in r for r in reasons):
        primary = 'D'
    elif any(p['status'] == 'INVALID' for p in result['physical']):
        primary = 'F'
    elif admission['status'] == 'MIGRATED_CANONICAL_VALIDATED':
        primary = 'B' if has_active else 'A'
    else:
        primary = 'C' if result['missing_sessions'] else 'G'
    result.update(primary=primary, final=verified, additional_reasons=reasons)
    return result


def recover(record, access, request):
    symbol = record['symbol']
    current = verify(symbol, access, request)
    if current['status'] == 'READY':
        record['final'] = current
        return record
    if current.get('identity') and current.get('missing_sessions'):
        record['primary'] = 'B'
    if record['primary'] not in {'A', 'B', 'C'}:
        return record
    if source_attempted(record):
        return record  # Explicit bounded attempt; next retry requires review of receipt.
    if record['primary'] == 'A' and not any(a['action'] == 'ADMISSION' for a in record['actions']):
        receipt = admit_migrated_daily_symbol(symbol, decision_as_of=request['session'], required_start=request['required_start'], data_access=access)
        record['actions'].append({'action': 'ADMISSION', 'receipt': receipt})
        if receipt['status'] not in {'ADMITTED_READY', 'ADMITTED_NEEDS_INCREMENTAL', 'ALREADY_ADMITTED'}:
            record['final'] = verify(symbol, access, request)
            return record
    # Resolve each actual active partition before computing missing requests.
    # No maximum-date-only shortcut and no full-history download.
    rows = access._read_manifest(access.manifest_path)
    rows = rows[(rows.dataset == 'daily') & (rows.symbol.astype(str).str.upper() == symbol)]
    rows = rows[pd.to_datetime(rows.min_date).le(pd.Timestamp(request['session'])) &
                pd.to_datetime(rows.max_date).ge(pd.Timestamp(request['warmup_sessions'][0]))]
    dates = set()
    try:
        for row in rows.to_dict('records'):
            gid = str(row.get('active_generation', ''))
            if gid.lower() in {'nan', 'none', ''}:
                continue
            frame = access.read_pinned_generation('daily', symbol, str(row['partition_ids']), gid)
            dates.update(pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d'))
    except Exception as exc:
        record['actions'].append({'action': 'ACTIVE_READ_BLOCKED', 'reason_codes': [str(exc)]})
        return record
    missing = [s for s in request['warmup_sessions'] if s not in dates]
    groups = []
    for s in request['warmup_sessions']:
        if s in missing:
            if not groups or groups[-1][-1] != previous:
                groups.append([])
            groups[-1].append(s)
        previous = s
    for group in groups:
        req = MarketDataRequirements(symbol, group[0], group[-1], ('daily',), decision_as_of=group[-1])
        try:
            outcome = ensure_market_data(symbol, req, access=access).to_dict()
        except Exception as exc:
            outcome = {'status': 'BLOCKED', 'reason_codes': [type(exc).__name__]}
        record['actions'].append({'action': 'DAILY_LOADER', 'requirements': asdict(req), 'receipt': outcome,
                                  'source_result': source_result(outcome), 'source_id': 'private_massive_gateway',
                                  'queried_at': datetime.now(timezone.utc).isoformat(),
                                  'retry_condition': 'explicit resume after source/quality blocker changes; no retry in this run'})
        if outcome['status'] not in {'ALREADY_COMPLETE', 'READY'}:
            break
    record['final'] = verify(symbol, access, request)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--phase', choices=['inspect', 'admit', 'recover', 'verify'], default='inspect')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    root = Path(args.output)
    load_project_environment()
    request_path = root / 'request.json'
    if not request_path.exists():
        assert args.phase == 'inspect'
        now = pd.Timestamp.now(tz='UTC')
        session = str(resolve_effective_market_session(now, 'EOD', 'XNYS', now).date())
        universe = UniverseSpec.from_global_candidates()
        pointer = json.loads(Path('data/artifacts/global_pcs_candidates/active.json').read_text())
        manifest = json.loads(Path(pointer['manifest_path']).read_text())
        assert digest(pointer['snapshot_path']) == manifest['artifact_hash']
        assert len(universe.symbols) == len(set(universe.symbols))
        req = _daily_requirements('QQQ', session)
        sessions = xc.get_calendar('XNYS').sessions_in_range(req.required_start, session).strftime('%Y-%m-%d').tolist()
        request = {'started_at': now.isoformat(), 'session': session, 'symbols': list(universe.symbols),
                   'universe': asdict(universe), 'snapshot_hash': digest(pointer['snapshot_path']),
                   'required_start': req.required_start, 'warmup_sessions': sessions[-200:], 'warmup_rows': 200,
                   'code_commit': subprocess.check_output(['git', '-C', str(Path(__file__).resolve().parents[1]), 'rev-parse', 'HEAD'], text=True).strip(),
                   'config_hashes': {p: digest(p) for p in ['config/pcs_rules.yaml', 'config/data_source_routes.yaml', 'config/market_data_source_registry.yaml', 'config/data_remediation_registry.yaml']},
                   'env_path': os.environ.get('PCS_ENV_FILE'), 'env_exists': Path(os.environ['PCS_ENV_FILE']).is_file(),
                   'daily_key_loaded': bool(os.getenv('PCS_MARKET_DATA_API_KEY')), 'cwd': str(Path.cwd()),
                   'initial_manifest_hash': digest('data/manifests/storage_manifest.csv')}
        save(request_path, request)
    request = json.loads(request_path.read_text())
    for name, expected in request['config_hashes'].items():
        if digest(name) != expected:
            raise RuntimeError('FROZEN_CONFIG_CHANGED:' + name)
    code_root = Path(__file__).resolve().parents[1]
    execution = {'started_at': datetime.now(timezone.utc).isoformat(), 'phase': args.phase,
                 'source_identity': hashlib.sha256(''.join(str(p.relative_to(code_root)) + digest(p) for p in sorted((code_root / 'src/pcs').rglob('*.py'))).encode()).hexdigest(),
                 'operator_sha256': digest(__file__), 'config_hashes': request['config_hashes']}
    with (root / 'invocations.jsonl').open('a', encoding='utf-8') as log:
        log.write(json.dumps(execution) + '\n')
    access = InspectionAccess()
    symbols = list(dict.fromkeys(['QQQ', 'SPY'] + request['symbols']))
    count = 0
    for symbol in symbols:
        if (root / 'PAUSE').exists():
            break  # Only between tickers: never interrupt an active promotion.
        path = root / 'tickers' / (symbol + '.json')
        if args.phase == 'inspect':
            if path.exists():
                continue
            record = inspect(symbol, access, request)
        else:
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            if args.phase == 'recover':
                if (record['primary'] not in {'A', 'B', 'C'} and not record['before'].get('missing_sessions')) or record['final']['status'] == 'READY' or source_attempted(record):
                    continue
                try:
                    record = recover(record, access, request)
                except Exception as exc:
                    record['actions'].append({'action': 'RECOVERY_FAILED', 'reason_codes': [type(exc).__name__],
                                              'detail': str(exc)})
            if args.phase == 'admit':
                if record['primary'] != 'A' or record['actions']:
                    continue
                receipt = admit_migrated_daily_symbol(symbol, decision_as_of=request['session'], required_start=request['required_start'], data_access=access)
                record['actions'].append({'action': 'ADMISSION', 'receipt': receipt})
            record['final'] = verify(symbol, access, request)
        record['last_execution'] = execution
        save(path, record)
        count += 1
        if count % 50 == 0:
            print(json.dumps({'phase': args.phase, 'processed': count, 'symbol': symbol}), flush=True)
        if args.limit and count >= args.limit:
            break
    records = [json.loads(p.read_text()) for p in (root / 'tickers').glob('*.json')]
    requested = [r for r in records if r['symbol'] in set(request['symbols'])]
    summary = {'phase': args.phase, 'processed_this_call': count, 'requested': len(request['symbols']), 'checked': len(requested),
               'classes': dict(Counter(r['primary'] for r in requested)), 'final': dict(Counter(r['final']['status'] for r in requested)),
               'reason_counts': dict(Counter(x for r in requested for x in r.get('additional_reasons', [])))}
    save(root / 'summary.json', summary)
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
