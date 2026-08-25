from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'research_outputs/pcs_canonical_data_repair'; out.mkdir(parents=True,exist_ok=True)
rows=[]
for s in ('SPY','QQQ','AMD','TSLA','JPM','MU'):
 txt=sorted((ROOT/'K:/BaiduNetdiskDownload/USDailyOptions/unzipped').glob(f'{s}_*_option_chain.txt')) if False else sorted(Path('K:/BaiduNetdiskDownload/USDailyOptions/unzipped').glob(f'{s}_*_option_chain.txt'))
 legacy=list((ROOT/f'data/parquet/options/symbol={s}').glob('year=*/quarter=*/*.parquet'))
 v2=list((ROOT/f'data/parquet/options_v2/symbol={s}').glob('year=*/quarter=*/*.parquet'))
 rows.append({'ticker':s,'approved_txt_source_files':len(txt),'legacy_option_partitions':len(legacy),'options_v2_partitions':len(v2),'route_activation_required':bool(txt or legacy) and not bool(v2)})
(out/'authoritative_source_inventory.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps(rows,indent=2))
