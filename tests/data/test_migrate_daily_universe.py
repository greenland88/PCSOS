import pandas as pd
from pcs.data.migrate_daily_universe import migrate


def test_daily_universe_migration_is_restartable(tmp_path):
    raw=tmp_path/"raw"; raw.mkdir()
    d=pd.DataFrame({"日期":["2025-01-01","2025-01-02"],"开盘价":[1,1],"最高价":[2,2],"最低价":[1,1],"收盘价":[2,2],"成交量":[1,1]})
    d.to_csv(raw/"AAA_daily_qfq.csv",index=False,encoding="utf-8-sig")
    out=tmp_path/"parquet"; manifest=tmp_path/"manifest.csv"
    first=migrate(raw,out,manifest); second=migrate(raw,out,manifest)
    assert first["success"]==1 and second["success"]==1
