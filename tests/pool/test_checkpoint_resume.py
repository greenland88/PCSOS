import json
from pathlib import Path
import pandas as pd
from types import SimpleNamespace
from pcs.pool.runner import run_pcs_pool
import pcs.pool.runner as runner
from pcs.pool.models import TickerScanResult, EligibilityStatus, TimingStatus, FinalAction

class Access:
    def read_verified_dataset(self, handle, **kw):
        return pd.DataFrame({'date':pd.date_range('2025-01-01', periods=220), 'close':100.})

def resolver(symbol, *args, **kw):
    return SimpleNamespace(ticker=symbol)

def test_resume_reuses_completed_engine_calls(tmp_path, monkeypatch):
    calls=[]
    def evaluate(symbol, **kw):
        calls.append(symbol)
        return TickerScanResult(symbol,kw['run_id'],kw['asof'],EligibilityStatus.PCS_ELIGIBLE,
                                TimingStatus.WATCH, final_action=FinalAction.WATCH)
    monkeypatch.setattr(runner,'_evaluate_symbol',evaluate)
    args=dict(symbols=['AAA','BBB'],as_of='2025-08-08',mode='EOD',data_access=Access(),
              daily_handle_resolver=resolver,output_directory=tmp_path,max_workers=1)
    a=run_pcs_pool(**args)
    b=run_pcs_pool(**args)
    assert calls == ['AAA','BBB']
    assert a.snapshot.run_id == b.snapshot.run_id
    assert b.counters['checkpoint_hits'] == 2
    checkpoint=next((tmp_path/'.checkpoints').glob('*.json'))
    state=json.loads(checkpoint.read_text())
    state['ticker_results'].pop('BBB')
    checkpoint.write_text(json.dumps(state))
    c=run_pcs_pool(**args)
    assert calls == ['AAA','BBB','BBB']
    assert len(c.ticker_results)==2
    assert c.counters['checkpoint_hits']==1

def test_universe_membership_is_part_of_checkpoint_identity(tmp_path, monkeypatch):
    calls=[]
    def evaluate(symbol, **kw):
        calls.append(symbol)
        return TickerScanResult(symbol,kw['run_id'],kw['asof'],EligibilityStatus.PCS_ELIGIBLE)
    monkeypatch.setattr(runner,'_evaluate_symbol',evaluate)
    args=dict(as_of='2025-08-08',mode='EOD',data_access=Access(),daily_handle_resolver=resolver,output_directory=tmp_path)
    run_pcs_pool(symbols=['AAA'],**args)
    run_pcs_pool(symbols=['BBB'],**args)
    assert calls == ['AAA','BBB']

def test_only_changed_symbol_recomputes_and_shared_change_invalidates_all(tmp_path, monkeypatch):
    class VersionedAccess(Access):
        manifest_path='fixture-manifest.csv'
        versions={'AAA':'a1','BBB':'b1','QQQ':'q1'}
        def _read_manifest(self, path):
            return pd.DataFrame([{'dataset':'daily','symbol':k,'active_generation':v}
                                 for k,v in self.versions.items()])
    access=VersionedAccess()
    calls=[]
    def evaluate(symbol, **kw):
        calls.append(symbol)
        return TickerScanResult(symbol,kw['run_id'],kw['asof'],EligibilityStatus.PCS_ELIGIBLE,
                                TimingStatus.WATCH,final_action=FinalAction.WATCH)
    monkeypatch.setattr(runner,'_evaluate_symbol',evaluate)
    args=dict(symbols=['AAA','BBB'],as_of='2025-08-08',mode='EOD',data_access=access,
              daily_handle_resolver=resolver,output_directory=tmp_path,max_workers=1)
    run_pcs_pool(**args)
    access.versions['AAA']='a2'
    second=run_pcs_pool(**args)
    assert calls==['AAA','BBB','AAA']
    assert second.counters['checkpoint_hits']==1
    access.versions['QQQ']='q2'
    run_pcs_pool(**args)
    assert calls==['AAA','BBB','AAA','AAA','BBB']

def test_saved_timing_continues_without_recomputing_indicators(monkeypatch):
    import pytest
    from pcs.pool.runtime import PoolRuntime
    frame=pd.DataFrame({'date':pd.date_range('2025-01-01',periods=220),
                        'open':100.,'high':102.,'low':99.,'close':101.,'volume':10000})
    access=Access()
    access.read_verified_dataset=lambda *a,**kw: frame.copy()
    runtime=PoolRuntime(access=access,daily_handle_resolver=resolver)
    calls=[]
    original=runner.build_trend_snapshot
    def build(*a,**kw):
        calls.append(1)
        return original(*a,**kw)
    monkeypatch.setattr(runner,'build_trend_snapshot',build)
    saved=[]
    class Stop(BaseException): pass
    def stop(row):
        saved.append(row)
        raise Stop()
    args=dict(run_id='r',asof='2025-08-08',access=access,benchmark=frame,benchmark_symbol='QQQ',
              options_reader=None,option_rules=None,runtime=runtime,daily_handle_resolver=resolver,
              daily_asof='2025-08-08')
    with pytest.raises(Stop):
        runner._evaluate_symbol('AAA',on_stage=stop,**args)
    result=runner._evaluate_symbol('AAA',saved_stage=saved[0],**args)
    assert calls==[1]
    assert result.checkpoint_stage=='COMPLETE'
    assert result.cache_hits==('CHECKPOINT:TIMING',)
    assert result.timing_status==saved[0].timing_status

def test_checkpoint_reencodes_only_replaced_rows(tmp_path, monkeypatch):
    from dataclasses import replace
    from pcs.pool.models import PoolRunSnapshot
    snapshot=PoolRunSnapshot('r','2025-08-08','EOD','2025-08-08','u')
    rows={s:TickerScanResult(s,'r',snapshot.as_of,EligibilityStatus.PCS_ELIGIBLE) for s in ('AAA','BBB')}
    encoded={}; calls=[]; original=runner.asdict
    def track(value):
        if isinstance(value,TickerScanResult):calls.append(value.symbol)
        return original(value)
    monkeypatch.setattr(runner,'asdict',track)
    path=tmp_path/'checkpoint.json'
    args=dict(identity='i',run_id='r',snapshot=snapshot,stage='DAILY_TIMING',encoded_rows=encoded)
    runner._write_scan_checkpoint(path,rows=rows,**args)
    rows['BBB']=replace(rows['BBB'],latency_ms=7)
    runner._write_scan_checkpoint(path,rows=rows,**args)
    assert calls==['AAA','BBB','BBB']
    run,restored=runner._load_scan_checkpoint(path,'i')
    assert run=='r' and restored==rows
    del rows['AAA']
    runner._write_scan_checkpoint(path,rows=rows,**args)
    assert 'AAA' not in encoded
    assert set(runner._load_scan_checkpoint(path,'i')[1])=={'BBB'}
