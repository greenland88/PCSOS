import pandas as pd
import pytest
from pcs.data.access import PCSDataAccess, PromotionReceipt
from pcs.data.generation_cache import GenerationCache

def frame(symbol="ZZZ", bid=1.0):
    return pd.DataFrame({"symbol":[symbol,symbol],"trade_date":pd.to_datetime(["2026-01-02"]*2),"expiration_date":pd.to_datetime(["2026-02-06"]*2),"call_put":["p","p"],"strike":[90.,85.],"bid":[bid,.5],"ask":[bid+.1,.6],"open_interest":[1000,1000],"volume":[200,200],"delta":[-.2,-.1],"bid_iv":[.3,.3],"ask_iv":[.31,.31],"last":[bid,.5],"gamma":[0.,0.],"vega":[0.,0.],"theta":[0.,0.],"rho":[0.,0.]})
def access(tmp_path): return PCSDataAccess(manifest_path=tmp_path/"manifest.csv",parquet_root=tmp_path/"parquet")

def daily_frame(symbol="ZZZ", dates=("2024-01-02", "2024-01-03")):
    return pd.DataFrame({"symbol":[symbol]*len(dates),"date":pd.to_datetime(list(dates)),"open":[1.0]*len(dates),"high":[1.1]*len(dates),"low":[.9]*len(dates),"close":[1.0]*len(dates),"volume":[10]*len(dates)})

def test_promotion_creates_real_generation(tmp_path):
    r=access(tmp_path).promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture")
    assert isinstance(r,PromotionReceipt) and r.generation_id and r.generation_id not in {"UNKNOWN","ZZZ"}
    assert r.promoted_generation_id == r.generation_id and r.dataset_type == "options" and r.manifest_version
    payload=r.to_dict(); assert payload["promoted_generation_id"]==payload["manifest_active_generation_id"]==payload["read_back_generation_id"]==r.generation_id

def test_underlying_promotion_creates_real_generation(tmp_path):
    a=access(tmp_path); x=pd.DataFrame({"symbol":["ZZZ"],"date":pd.to_datetime(["2026-01-02"]),"open":[1.],"high":[1.],"low":[1.],"close":[1.],"volume":[10]})
    r=a.promote_generation(x,"daily","ZZZ","year=2026",source_version="fixture")
    assert isinstance(r,PromotionReceipt) and r.generation_id and pd.read_csv(tmp_path/"manifest.csv").iloc[0].active_generation==r.generation_id

def test_manifest_has_single_active_generation(tmp_path):
    a=access(tmp_path); a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="a"); a.promote_generation(frame(bid=1.2),"options","ZZZ","year=2026/quarter=1",source_version="b")
    m=pd.read_csv(tmp_path/"manifest.csv"); assert len(m)==1 and m.active_generation.notna().sum()==1

def test_read_back_matches_promoted_generation(tmp_path):
    a=access(tmp_path); r=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture"); x=pd.read_parquet(r.path)
    assert r.generation_id==pd.read_csv(tmp_path/"manifest.csv").iloc[0].active_generation and r.row_count==len(x) and r.checksum==a.semantic_content_hash(x)

def test_generation_pinned_read_uses_active_generation(tmp_path):
    a=access(tmp_path); r=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture")
    assert len(a.read_pinned_generation("options","ZZZ","year=2026/quarter=1",r.generation_id))==2
    with pytest.raises(Exception): a.read_pinned_generation("options","ZZZ","year=2026/quarter=1","other")

def test_manifest_contains_generation_lifecycle_fields(tmp_path):
    a=access(tmp_path); r=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture"); row=pd.read_csv(tmp_path/"manifest.csv").iloc[0]
    for field in ("staging_generation_id","promoted_generation_id","manifest_active_generation_id","read_back_generation_id","content_hash","row_count","created_at","promoted_at","source","source_lineage","partition_ids"):
        assert field in row.index and str(row[field]) not in {"", "nan"}
    assert all(str(row[x])==r.generation_id for x in ("staging_generation_id","promoted_generation_id","manifest_active_generation_id","read_back_generation_id"))
    assert str(row.partition_ids)=="year=2026/quarter=1" and r.partition_ids==("year=2026/quarter=1",)

def test_noop_promotion_does_not_create_receipt(tmp_path):
    a=access(tmp_path); a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture"); second=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture")
    assert not isinstance(second, PromotionReceipt)

def test_checksum_mismatch_blocks_ready():
    from pcs.data.strategy_readiness import validate_generation_evidence
    assert validate_generation_evidence(promoted_generation_id="g",manifest_active_generation_id="g",read_back_generation_id="g",expected_checksum="a",read_back_checksum="b",expected_row_count=2,read_back_row_count=2)=="READ_BACK_CHECKSUM_MISMATCH"

def test_generation_mismatch_blocks_ready():
    from pcs.data.strategy_readiness import validate_generation_evidence
    assert validate_generation_evidence(promoted_generation_id="g1",manifest_active_generation_id="g2",read_back_generation_id="g1",expected_checksum="a",read_back_checksum="a",expected_row_count=2,read_back_row_count=2)=="MANIFEST_GENERATION_MISMATCH"

def test_failed_promotion_preserves_previous_active(tmp_path, monkeypatch):
    a=access(tmp_path); r=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="fixture")
    monkeypatch.setattr(a, "update_manifest", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("manifest failure")))
    with pytest.raises(RuntimeError): a.promote_generation(frame(bid=1.2),"options","ZZZ","year=2026/quarter=1",source_version="bad")
    assert pd.read_csv(tmp_path/"manifest.csv").iloc[0].active_generation==r.generation_id

def test_failed_read_back_preserves_previous_active(tmp_path, monkeypatch):
    a=access(tmp_path); old=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="old")
    original=pd.read_parquet
    calls=[0]
    def corrupt(path, *args, **kwargs):
        out=original(path,*args,**kwargs); calls[0]+=1
        return out.assign(bid=out.bid+99) if calls[0] > 1 else out
    monkeypatch.setattr(pd, "read_parquet", corrupt)
    with pytest.raises(Exception): a.promote_generation(frame(bid=1.2),"options","ZZZ","year=2026/quarter=1",source_version="new")
    assert pd.read_csv(tmp_path/"manifest.csv").iloc[0].active_generation==old.generation_id

def test_promotion_invalidates_old_generation_cache(tmp_path):
    a=access(tmp_path); r1=a.promote_generation(frame(),"options","ZZZ","year=2026/quarter=1",source_version="a"); a.generation_cache.put("options","ZZZ","year=2026/quarter=1",r1.generation_id,"old"); r2=a.promote_generation(frame(bid=1.2),"options","ZZZ","year=2026/quarter=1",source_version="b")
    assert a.generation_cache.get("options","ZZZ","year=2026/quarter=1",r1.generation_id) is None and r1.generation_id!=r2.generation_id

def test_cache_key_requires_generation():
    with pytest.raises(ValueError): GenerationCache.key("options","ZZZ","q1","")

def test_incomplete_handle_cannot_be_ready():
    from pcs.data.strategy_readiness import ReadinessResult, VerifiedDataHandle
    assert ReadinessResult("ZZZ","PCS","2026-01-01","CORRUPTED","DATA_BLOCKED","VERIFIED_DATA_HANDLE_MISSING",None).verified_data_handle is None
    with pytest.raises(ValueError, match="INCOMPLETE_VERIFIED_DATA_HANDLE"):
        VerifiedDataHandle("ZZZ","PCS","2026-01-01","LIVE","","g",(),(),{}, {},"basis","ca","now","2026-01-01",None,())

def test_manifest_scalar_partition_is_not_split_into_characters():
    from pcs.data.strategy_readiness import VerifiedDatasetHandle
    handle = VerifiedDatasetHandle(
        "options", "ZZZ", "generation-1", ("year=2026/quarter=1",),
        "checksum", 1, ("canonical.parquet",), {"min_date":"2026-01-01"}, (),
        dataset_fingerprint="fingerprint", min_date="2026-01-01", max_date="2026-01-01",
        schema_version="1", price_basis="canonical_adjusted", corporate_action_version="canonical_identity")
    assert handle.partitions == ("year=2026/quarter=1",)

def test_pinned_daily_read_has_no_legacy_fallback_and_checks_coverage(tmp_path):
    from pcs.data.strategy_readiness import VerifiedDatasetHandle
    a=access(tmp_path); r=a.promote_generation(daily_frame(),"daily","ZZZ","year=2024",source_version="fixture")
    h=VerifiedDatasetHandle("daily","ZZZ",r.generation_id,("year=2024",),r.checksum,r.row_count,(r.path,),{"min_date":"2024-01-02"},dataset_fingerprint="f",min_date="2024-01-02",max_date="2024-01-03",schema_version="1",price_basis="canonical_adjusted",corporate_action_version="canonical_identity" )
    out=a.read_prices("ZZZ","2024-01-02","2024-01-03",verified_handle=h)
    assert len(out)==2 and out.date.duplicated().sum()==0
    with pytest.raises(Exception, match="PINNED_GENERATION_COVERAGE_INSUFFICIENT"):
        a.read_prices("ZZZ","2023-01-01","2024-01-03",verified_handle=h)

def test_daily_duplicate_canonical_key_fails_closed(tmp_path):
    from pcs.data.strategy_readiness import VerifiedDatasetHandle
    a=access(tmp_path); x=daily_frame(dates=("2024-01-02","2024-01-02")); p=tmp_path/"dup.parquet"; x.to_parquet(p,index=False)
    digest=a.semantic_content_hash(x); gid=digest[:24]
    a.update_manifest("daily","ZZZ",x,p,"fixture","year=2024",replace_existing=True,active_generation=gid,content_hash=digest)
    h=VerifiedDatasetHandle("daily","ZZZ",gid,("year=2024",),digest,len(x),(str(p),),{"min_date":"2024-01-02"},dataset_fingerprint="f",min_date="2024-01-02",max_date="2024-01-02",schema_version="1",price_basis="canonical_adjusted",corporate_action_version="canonical_identity")
    with pytest.raises(Exception, match="DUPLICATE_CANONICAL_PRICE_KEY"):
        a.read_prices("ZZZ","2024-01-02","2024-01-02",verified_handle=h)

def test_non_nvda_ticker_uses_same_pinned_path(tmp_path):
    from pcs.data.strategy_readiness import VerifiedDatasetHandle
    a=access(tmp_path); r=a.promote_generation(daily_frame("QQQ"),"daily","QQQ","year=2024",source_version="fixture")
    h=VerifiedDatasetHandle("daily","QQQ",r.generation_id,("year=2024",),r.checksum,r.row_count,(r.path,),{"min_date":"2024-01-02"},dataset_fingerprint="f",min_date="2024-01-02",max_date="2024-01-03",schema_version="1",price_basis="canonical_adjusted",corporate_action_version="canonical_identity")
    assert len(a.read_prices("QQQ","2024-01-02","2024-01-03",verified_handle=h))==2
