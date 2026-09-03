"""One-time bounded adoption of the complete Phase A daily input snapshot."""
from pathlib import Path
import json, hashlib
import pandas as pd
from pcs.data.access import PCSDataAccess
from pcs.data.canonical_generations import canonical_snapshot_descriptor

def main():
    a=PCSDataAccess.canonical(); root=a.parquet_root/"daily"/"symbol=NVDA"
    legacy=pd.concat([pd.read_parquet(root/"year=2024"/"NVDA_2024.parquet"),pd.read_parquet(root/"year=2025"/"NVDA_2025.parquet")],ignore_index=True)
    current=a.read_pinned_generation("daily","NVDA","year=2026","ff063b8d9514f145be013de0")
    frame=pd.concat([legacy,current],ignore_index=True)
    frame["date"]=pd.to_datetime(frame["date"]).dt.normalize()
    frame["symbol"]="NVDA"
    frame=frame.sort_values("date").drop_duplicates("date").reset_index(drop=True)
    if len(frame)!=669: raise RuntimeError(f"FULL_PHASE_A_INPUT_COUNT:{len(frame)}")
    legacy_path=root/"year=2026"/"NVDA_2026.parquet"; fh=hashlib.sha256(legacy_path.read_bytes()).hexdigest()
    desc=canonical_snapshot_descriptor(dataset="daily",symbol="NVDA",frame=frame,file_hash=fh,byte_size=legacy_path.stat().st_size,partition_key="phase_a=2024-01-02_to_2026-09-01")
    receipt=a.promote_generation(frame,"daily","NVDA","year=2026",source_version="PHASE_A_FULL_DAILY_ADOPTION")
    payload={"receipt":receipt.to_dict(),"dataset_fingerprint":desc["dataset_fingerprint"],"snapshot_descriptor":desc}
    Path("research_outputs/nvda_pcs_2026_opportunity_engine/nvda_phase_a_full_generation.json").write_text(json.dumps(payload,indent=2,default=str),encoding="utf-8")
    print(json.dumps(payload,default=str))
if __name__=="__main__": main()
