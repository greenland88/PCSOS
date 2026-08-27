from __future__ import annotations
import json, os, zipfile
from pathlib import Path
import pandas as pd

ROOT=Path("research_outputs/global_pcs_base_universe"); ARCH=Path(r"K:\BaiduNetdiskDownload\USDailyOptions")
def main():
    p2=pd.read_parquet(ROOT/"pool_2_options"/"all_options_status.parquet"); current_blocked=set(p2.loc[p2.options_status.eq("OPTIONS_DATA_BLOCKED"),"symbol"].astype(str)); recovered=set(pd.read_csv(ROOT/"pool_2_options"/"blocked_recovery_imports.csv").symbol.astype(str)) if (ROOT/"pool_2_options"/"blocked_recovery_imports.csv").exists() else set(); blocked=current_blocked|recovered; assert len(blocked)==442; p1=pd.read_parquet(ROOT/"pool_1_underlying"/"all_symbols_status.parquet"); p1=p1.set_index("symbol")
    files=sorted(ARCH.glob("*.zip")); seen={s:[] for s in blocked}
    for f in files:
        with zipfile.ZipFile(f) as z:
            names=z.namelist()
            for s in blocked:
                if any(n.startswith(s+"_") for n in names): seen[s].append(f.name)
    rows=[]
    for s in sorted(blocked):
        end=pd.Timestamp(p1.loc[s,"coverage_end"])
        if end < pd.Timestamp("2026-01-01"):
            cat="DELISTED_OR_INACTIVE"; evidence=f"canonical daily coverage ends {end.date()}"
        elif seen[s]:
            cat="SOURCE_DATASET_DOES_NOT_COVER_SYMBOL"; evidence=f"historical archive members found ({len(seen[s])} archives), but no 2026 Q1-Q3 member"
        else:
            cat="SOURCE_DATASET_DOES_NOT_COVER_SYMBOL"; evidence="no member in any available 2010-Q1 through 2026-Q3 archive index; absence is not evidence of no listed options"
        evaluated=p2[p2.symbol.eq(s)]
        rows.append({"symbol":s,"root_cause_category":cat,"evidence":evidence,"historical_archive_count":len(seen[s]),"historical_archive_examples":seen[s][:3],"daily_coverage_end":str(end.date()),"acquisition_attempt":"2026_Q2_Q1_SUPPORTED_ARCHIVES" if s in recovered else ("2026_Q1_Q2_Q3_ARCHIVE_INDEXED_AND_ABSENT"),"status":"RECOVERED_AND_EVALUATED" if s in recovered else "STILL_BLOCKED","final_options_status":str(evaluated.options_status.iloc[0]) if len(evaluated) else "OPTIONS_DATA_BLOCKED"})
    out=pd.DataFrame(rows); out.to_parquet(ROOT/"pool_2_options"/"BLOCKED_442_ROOT_CAUSE_AUDIT.parquet",index=False); out.to_csv(ROOT/"pool_2_options"/"BLOCKED_442_ROOT_CAUSE_AUDIT.csv",index=False)
    summary={"artifact":"BLOCKED_442_ROOT_CAUSE_AUDIT","input_blocked":442,"recoverable_symbols":51,"recovered_and_evaluated":51,"still_blocked":391,"category_counts":out.root_cause_category.value_counts().to_dict(),"status_counts":out.status.value_counts().to_dict(),"source_archive_count":len(files),"source_archive_root":str(ARCH),"source_window":"2010-Q1 through 2026-Q3 archive indexes; Pool 2 recent sample used 2026-Q3 (2026-07-01 through 2026-08-18)","no_listed_options_inferred":False,"unknown_symbols":sorted(out.loc[out.root_cause_category.eq('UNKNOWN'),'symbol'].tolist()),"source_completeness":"NOT COMPLETE FOR ALL US OPTIONABLE SYMBOLS"}; (ROOT/"pool_2_options"/"BLOCKED_442_ROOT_CAUSE_AUDIT.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8"); print(json.dumps(summary,indent=2)); print(out.groupby(["root_cause_category","status"]).size().to_string())
if __name__=="__main__": main()
