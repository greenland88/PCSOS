import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "daily_recovery_operator", Path(__file__).resolve().parents[2] / "scripts/pcs_daily_coverage_recovery.py")
operator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(operator)


def test_receipt_identity_digits_are_not_authentication_evidence():
    receipt = {"status": "ALREADY_COMPLETE", "import_outcomes": [
        {"status": "IMPORTED", "result": {"generation_id": "abcd401ef403"}}]}
    assert operator.source_result(receipt) == "IMPORTED"


def test_zero_rows_not_retried_but_reuse_without_provider_is_resumable():
    receipt = {"status": "BLOCKED", "import_outcomes": [
        {"status": "BLOCKED", "detail": "gateway returned no daily data for TEST"}]}
    assert operator.source_result(receipt) == "SOURCE_CONFIRMED_ZERO_ROWS"
    record = {"actions": [{"action": "DAILY_LOADER", "receipt": receipt}]}
    assert operator.source_attempted(record)
    receipt["import_outcomes"] = [{"status": "REUSED"}]
    assert not operator.source_attempted(record)


def test_benchmark_cache_stops_reuse_when_active_generation_changes(tmp_path, monkeypatch):
    import pandas as pd
    from types import SimpleNamespace
    from pcs.data.access import PCSDataAccess
    access = operator.InspectionAccess(manifest_path=tmp_path / 'manifest.csv', parquet_root=tmp_path / 'parquet')
    frame = pd.DataFrame({'symbol': ['SPY'], 'date': pd.to_datetime(['2026-09-04'])})
    physical = tmp_path / 'immutable.parquet'
    physical.write_bytes(b'test-identity')
    access._verified_session_authority = (SimpleNamespace(generation_id='old'), frame,
        [(str(physical), physical.stat().st_mtime_ns, physical.stat().st_size)])
    active = pd.DataFrame({'dataset': ['daily'], 'symbol': ['SPY'], 'active_generation': ['old']})
    monkeypatch.setattr(access, '_read_manifest', lambda path: active)
    called = []
    monkeypatch.setattr(PCSDataAccess, 'read_prices', lambda *a, **k: called.append(True) or 'fresh-read')
    assert access.read_prices('SPY', '2026-09-04', '2026-09-04').equals(frame)
    assert not called
    active.loc[0, 'active_generation'] = 'new'
    assert access.read_prices('SPY', '2026-09-04', '2026-09-04') == 'fresh-read'
    assert called == [True]


def test_old_year_active_and_exhausted_source_do_not_skip_physical_admission(monkeypatch):
    import pandas as pd
    from types import SimpleNamespace
    access = SimpleNamespace(manifest_path='manifest', _read_manifest=lambda path: pd.DataFrame([
        {'dataset': 'daily', 'symbol': 'TEST', 'year': 2024, 'active_generation': 'old'}]))
    monkeypatch.setattr(operator, 'verify', lambda *a: {'status': 'BLOCKED', 'reason_codes': ['DAILY_STALE']})
    admitted = []
    monkeypatch.setattr(operator, 'admit_migrated_daily_symbol', lambda *a, **k: admitted.append(a[0]) or {'status': 'ADMITTED_NEEDS_INCREMENTAL'})
    record = {'symbol': 'TEST', 'primary': 'B', 'physical': [{'year': 2025, 'status': 'VALIDATED'}],
              'actions': [{'action': 'DAILY_LOADER', 'receipt': {'import_outcomes': [{'status': 'BLOCKED'}]}}]}
    result = operator.recover(record, access, {'session': '2026-09-04', 'required_start': '2025-07-11'})
    assert admitted == ['TEST']
    assert [a['action'] for a in result['actions']] == ['DAILY_LOADER', 'ADMISSION']
    assert result['final']['status'] == 'BLOCKED'
