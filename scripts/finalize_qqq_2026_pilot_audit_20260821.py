"""Research-only cleanup/audit for the accepted QQQ 2026 rebuild pilot.

Does not touch production, frozen, sealed, or canonical data.  It removes
duplicate *audit rows* caused by repeated executions and records the current
candidate-generator blocker explicitly.
"""
from pathlib import Path
import json
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs/opportunity_state_machine_research_20260821"
RB = OUT / "rebuilt_options_v3_1"

v = pd.read_csv(RB / "partition_validation.csv")
v = v.drop_duplicates(subset=["ticker", "year", "month", "output_file"], keep="last")
v.to_csv(RB / "partition_validation.csv", index=False)

pilot = pd.read_csv(OUT / "pilot_schema_validation.csv")
jan = {
    "ticker": "QQQ", "year": 2026, "month": 1,
    "source_file": "data\\raw\\options\\QQQ\\QQQ_2026_q1_option_chain.csv",
    "input_rows": 161968, "converted_rows": 161968,
    "quarantined_rows": 0, "duplicate_keys": 0, "conflicting_keys": 0,
    "put_rows": 80984, "call_rows": 80984,
    "output_file": str(RB / "options_v2/symbol=QQQ/year=2026/quarter=1/QQQ_2026_q1.parquet"),
    "status": "VALID_PILOT_ACCEPTED",
}
records = [jan] + v.to_dict("records")
for r in records:
    if int(r.get("month", 0)) == 8:
        r["status"] = "SOURCE_MISSING"
        r["replay_allowed"] = False
        r["valid_data_partition"] = False
manifest = {"dataset": "rebuilt_options_v3_1", "research_only": True,
            "ticker": "QQQ", "year": 2026, "status": "VALID_PILOT_ACCEPTED",
            "partitions": records,
            "pilot_schema_validation": pilot.to_dict("records")}
(RB / "rebuilt_options_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

diag = pd.DataFrame([
    {"ticker":"QQQ", "split":"VALIDATION", "year":2026,
     "underlying_days":102, "option_available_days":102,
     "candidate_generated_days":0, "fully_evaluated_days":0,
     "eligible_days":0, "status":"BLOCKED",
     "reason_code":"CURRENT_ENTRY_CONTEXT_NO_READY_DATES",
     "detail":"The deterministic research candidate path returned WAIT/REJECT for every inspected date; no date reached option gates. This does not prove the sealed 102 candidates are absent from the underlying market; it proves the current replay path is not reconciled to the sealed historical path."}
])
diag.to_csv(OUT / "qqq_2026_replay_blocker_diagnosis.csv", index=False)
print(json.dumps({"unique_partitions": len(records), "jan_rows": jan["converted_rows"],
                  "months_2_to_8_rows": int(v["converted_rows"].sum()),
                  "replay_status": "BLOCKED_CURRENT_PATH_NOT_RECONCILED"}, indent=2))
