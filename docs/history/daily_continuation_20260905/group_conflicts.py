from pathlib import Path
import sys,json,hashlib,csv
from collections import Counter
import pandas as pd
import numpy as np
from pcs.data.access import PCSDataAccess
from pcs.data.canonical_generations import _validate_migrated_daily_file
root=Path(__file__).parent
access=PCSDataAccess.canonical()
migration=pd.read_csv('data/manifests/daily_universe_migration.csv',low_memory=False)
provenance=pd.read_csv('data/manifests/data_provenance_manifest.csv',low_memory=False)
records=[]; types=Counter(); matched_sources=Counter()
for path in (root/'baseline_tickers').glob('*.json'):
 r=json.loads(path.read_text())
 if r['primary']!='D':continue
 sym=r['symbol'];frames={};identities=[]
 catalog=migration[migration.symbol.eq(sym)]
 for meta in r['physical']:
  p=Path(meta['path']);actual=hashlib.sha256(p.read_bytes()).hexdigest()
  assert actual==meta['physical_sha256'],sym
  f,m=_validate_migrated_daily_file(p,sym,access);frames[str(p)]=f.set_index('date')[['open','high','low','close','volume']]
  named=p.name==f'{sym}_{meta["year"]}.parquet'
  prov=provenance[(provenance.symbol.eq(sym)) & provenance.dataset.fillna('').eq('daily')]
  bound=prov[prov.parquet_path.fillna('').str.replace('\\','/',regex=False).eq(str(p).replace('\\','/'))]
  source='QFQ_MIGRATION_PATH_LINK' if named and len(catalog) else 'SOURCE_IDENTITY_UNBOUND'
  if len(bound):source='EXACT_PATH_PROVENANCE_AVAILABLE'
  matched_sources[source]+=1
  gen=p.parent/'generations'/p.name
  identities.append({'path':str(p),'physical_sha256':actual,'semantic_hash':m['semantic_content_hash'],
   'source_evidence':source,'migration_catalog_source':catalog.source.tolist() if named else [],
   'exact_path_provenance_rows':len(bound),'duplicate_generation_file_exists':gen.exists(),
   'generation_file_same_hash':hashlib.sha256(gen.read_bytes()).hexdigest()==actual if gen.exists() else None,
   'declared_price_basis':'canonical_adjusted','declared_corporate_action_version':'canonical_identity',
   'provider_adjustment_version':'NOT_PROVEN_BY_DECLARATION'})
 pairs=[]
 for rel in r['conflict_evidence']['relationships']:
  a,b=frames[rel['left']],frames[rel['right']];idx=a.index.intersection(b.index)
  aa,bb=a.loc[idx],b.loc[idx];diff=aa.ne(bb);pricecols=['open','high','low','close'];price=diff[pricecols].any(axis=1);vol=diff.volume
  count=int(diff.any(axis=1).sum());assert count==rel['conflicting_rows'],sym
  if not count:kind='IDENTICAL_OVERLAP_DIFFERENT_COVERAGE'
  elif not price.any():kind='VOLUME_ONLY'
  elif not vol.any():kind='OHLC_ONLY'
  else:kind='OHLC_AND_VOLUME'
  ratios=bb[pricecols].to_numpy()/aa[pricecols].to_numpy();valid=ratios[np.isfinite(ratios)]
  scale=bool(len(valid) and np.allclose(valid,valid[0],rtol=1e-8,atol=1e-10) and not np.isclose(valid[0],1))
  absdelta=(aa[pricecols]-bb[pricecols]).abs().to_numpy()
  types[kind]+=1
  pairs.append({'left':rel['left'],'right':rel['right'],'difference_type':kind,'overlap_rows':len(idx),
   'conflicting_rows':count,'price_different_rows':int(price.sum()),'volume_different_rows':int(vol.sum()),
   'uniform_price_scaling_pattern':scale,'price_ratio_min':float(valid.min()) if len(valid) else None,
   'price_ratio_max':float(valid.max()) if len(valid) else None,'max_absolute_price_difference':float(np.nanmax(absdelta)) if absdelta.size else 0,
   'first_conflict':rel['first_conflict'],'last_conflict':rel['last_conflict']})
 records.append({'symbol':sym,'identities':identities,'pairs':pairs,
  'classification':'+'.join(sorted({x['difference_type'] for x in pairs if x['conflicting_rows']})),
  'decision':'RETAIN_CONFLICT','automatic_rule':'identical overlap/superset only; differing overlap rejected by _reconcile_migrated_candidates',
  'missing_evidence':['Hash-bound origin and producer version for unbound candidates','Source-specific split/dividend adjustment factor/version and effective date','Approved authority rule binding the conflicting identities; source priority alone cannot identify an unbound file']})
for record in records:
 for identity in record['identities']:
  identity['upstream_version_evidence']='MIGRATION_CATALOG_WITHOUT_UPSTREAM_REVISION' if identity['source_evidence']=='QFQ_MIGRATION_PATH_LINK' else 'SOURCE_REVISION_UNBOUND'
(root/'conflict_groups.json').write_text(json.dumps(records,indent=2))
summary={'ticker_count':len(records),'ticker_groups':dict(Counter(r['classification'] for r in records)),
 'pair_difference_types':dict(types),'file_source_evidence':dict(matched_sources),
 'uniform_scaling_pairs':sum(x['uniform_price_scaling_pattern'] for r in records for x in r['pairs']),
 'automatic_adjudications':0,'all_prior_physical_hashes_unchanged':True}
summary['upstream_version_groups']=dict(Counter(i['upstream_version_evidence'] for r in records for i in r['identities']))
summary['identical_retained_generation_copy_files']=sum(i['generation_file_same_hash'] is True for r in records for i in r['identities'])
summary['provider_adjustment_version_proven_files']=0
(root/'conflict_group_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary))
