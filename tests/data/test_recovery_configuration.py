import os
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.control_plane import MarketDataRequirements, MarketDataControlPlane, default_import_handlers


def test_options_load_authorized_env_before_client_and_use_defaults(tmp_path, monkeypatch):
    env = tmp_path / 'authorized.env'
    env.write_text('CLICKHOUSE_PASSWORD=fixture-only\n')
    monkeypatch.setenv('PCS_ENV_FILE', str(env))
    for key in ('CLICKHOUSE_PASSWORD', 'CLICKHOUSE_URL', 'CLICKHOUSE_USER'):
        monkeypatch.delenv(key, raising=False)
    seen = []
    class Client:
        def __init__(self, url, user, password):
            assert url == 'http://db.base32.cn:8123/' and user == 'hisdata230'
            assert password == 'fixture-only'
        def fetch_options_coverage(self, symbol, start, end):
            seen.append((start, end))
            return {'status': 'BLOCKED', 'reason_codes': ['AUTHORIZED_SOURCE_NO_ROWS']}
    monkeypatch.setattr('pcs.data.clickhouse.PCSClickHouseClient', Client)
    access = PCSDataAccess.isolated(manifest_path=tmp_path/'manifest.csv', parquet_root=tmp_path/'parquet')
    req = MarketDataRequirements('AUPH', '2026-09-04', '2026-09-04', ('options',), min_dte=30, max_dte=45)
    result = MarketDataControlPlane(access).ensure_market_data(req)
    assert result.status == 'BLOCKED'
    assert 'AUTHORIZED_SOURCE_NO_ROWS' in result.reason_codes
    assert seen == [('2026-09-04', '2026-09-04')]
    assert result.coverage_plan['required_option_periods'] == ('2026Q3',)


def test_explicit_environment_preserves_inherited_values(tmp_path, monkeypatch):
    from pcs.data.massive_client import load_project_environment
    env=tmp_path/'authorized.env'; env.write_text('CLICKHOUSE_PASSWORD=file-fixture\n')
    monkeypatch.setenv('PCS_ENV_FILE',str(env))
    monkeypatch.setenv('CLICKHOUSE_PASSWORD','inherited-fixture')
    load_project_environment()
    assert os.environ['CLICKHOUSE_PASSWORD']=='inherited-fixture'


def test_september_quote_october_expiry_uses_quote_partition(tmp_path):
    # Expiry is a contract attribute; it never expands required quote dates.
    access=PCSDataAccess.isolated(manifest_path=tmp_path/'manifest.csv',parquet_root=tmp_path/'parquet')
    req=MarketDataRequirements.from_mapping('AUPH',{'required_start':'2026-09-04','required_end':'2026-09-04',
        'datasets':['options'],'decision_as_of':'2026-09-04','min_dte':30,'max_dte':45,
        'exact_contract_quote_keys':[{'quote_date':'2026-09-04','expiration_date':'2026-10-09','strike':10,'call_put':'p'}]})
    plan=MarketDataControlPlane(access).plan(req)
    assert plan.required_option_periods==('2026Q3',)
    assert plan.requirements.required_end=='2026-09-04'


def test_incremental_success_is_not_reported_as_adapter_failure(tmp_path):
    from pcs.data.control_plane import ImportCoordinator
    access=PCSDataAccess.isolated(manifest_path=tmp_path/'manifest.csv',parquet_root=tmp_path/'parquet')
    coordinator=ImportCoordinator(MarketDataControlPlane(access),handlers={
        'daily': lambda plan: {'status':'SUCCESS','reason_codes':['DERIVED_INVALIDATION_MARKED']}})
    result=coordinator.run(MarketDataRequirements('TEST',datasets=('daily',)))
    assert result['outcomes'][0]['status']=='IMPORTED'
    # A successful handler alone cannot claim canonical readiness.
    assert result['result']['final_canonical_status']!='READY'
