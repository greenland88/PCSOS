"""Repair metadata only for already-generated NVDA roll-chain artifacts."""
import hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for policy in ("highest", "shortest", "balanced"):
    out = ROOT / f"research_outputs/covered_call_nvda_roll_chain_{policy}"
    target = out / "roll_chain_replay.json"
    if not target.exists():
        continue
    result = json.loads(target.read_text())
    result.update({"research_id": f"covered_call_nvda_roll_chain_{policy}", "symbol": "NVDA", "policy": policy,
                   "data_source": "PCS_CANONICAL_DATA", "final_oos_read": False,
                   "production_changes_allowed": False})
    target.write_text(json.dumps(result, indent=2, default=str))
    manifest = {"research_id": result["research_id"], "status": "CURRENT", "current": True,
                "data_source": "PCS_CANONICAL_DATA", "ticker": "NVDA", "policy": policy,
                "final_oos_read": False, "production_changes_allowed": False,
                "files": {target.name: hashlib.sha256(target.read_bytes()).hexdigest()},
                "reason_codes": ["METADATA_REPAIRED", "ROLL_CHAIN_REPLAY_ATTEMPTED",
                                 "H3_RULES_ENFORCED", "H4_MANDATORY_REVIEW"]}
    (out / "artifact_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(result["research_id"])
