"""Copy validated AMD TXT pilot partitions into a fresh onboarding namespace."""
from pathlib import Path
from pcs.data.import_boundary import reject_legacy_import_entrypoint

if __name__ == "__main__":
    reject_legacy_import_entrypoint()
import pandas as pd
from pcs.data.access import PCSDataAccess

SRC = Path("data/parquet/options_v2_pilot_vendor_txt_20260820_run2")
DST = Path("data/parquet/options_v2_onboarding_amd_20260820")
DATASET = "options_v2_onboarding_amd_20260820"
MANIFEST = Path("data/manifests/options_v2_onboarding_amd_20260820.csv")
PROV = Path("data/manifests/options_v2_onboarding_amd_20260820_provenance.csv")

if MANIFEST.exists() or DST.exists():
    raise RuntimeError("isolated onboarding namespace already exists; refusing overwrite")

access = PCSDataAccess(manifest_path=MANIFEST, parquet_root=DST.parent)
for path in sorted((SRC / "symbol=AMD").glob("year=*/quarter=*/*.parquet")):
    parts = path.parts
    year = next(x for x in parts if x.startswith("year="))
    quarter = next(x for x in parts if x.startswith("quarter="))
    frame = pd.read_parquet(path)
    rel = f"{year}/{quarter}"
    target = DST / "symbol=AMD" / rel / path.name
    version = "historical-vendor-txt:AMD:VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW"
    access.write_partition(frame, DATASET, "AMD", rel, source_version=version, filename=path.name, update_manifest=False)
    access.update_manifest(DATASET, "AMD", frame, target, version, rel)
    access.record_provenance({"source":"validated isolated TXT pilot", "symbol":"AMD", "dataset":DATASET,
        "source_path":str(path), "resolution_policy":"VENDOR_CONFLICT_RESOLVED_BY_FIRST_RAW_ROW",
        "clickhouse_used":False, "rows":len(frame), "status":"HISTORICAL_BASE_COPIED"}, PROV)
print("copied", len(list((SRC / 'symbol=AMD').glob('year=*/quarter=*/*.parquet'))), "partitions")
