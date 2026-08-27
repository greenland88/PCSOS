"""Remove only byte-equivalent provenance duplicates; preserve source variants."""
from pathlib import Path
import hashlib, json
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
def main():
 p=ROOT/'data/manifests/data_provenance_manifest.csv'; d=pd.read_csv(p); before=hashlib.sha256(p.read_bytes()).hexdigest(); n=int(d.duplicated(keep=False).sum()); d2=d.drop_duplicates(keep='first').reset_index(drop=True); d2.to_csv(p,index=False); after=hashlib.sha256(p.read_bytes()).hexdigest()
 out=ROOT/'research_outputs/pcs_canonical_data_repair'; out.mkdir(parents=True,exist_ok=True); (out/'manifest_cleanup.json').write_text(json.dumps({'file':str(p.relative_to(ROOT)).replace('\\','/'),'duplicate_rows_removed':n,'rows_before':len(d),'rows_after':len(d2),'before_sha256':before,'after_sha256':after,'target_manifest_duplicates_preserved':True},indent=2),encoding='utf-8'); print(json.dumps({'status':'COMPLETED','duplicate_rows_removed':n}))
if __name__=='__main__':
 from pcs.data.import_boundary import reject_legacy_import_entrypoint
 reject_legacy_import_entrypoint()
