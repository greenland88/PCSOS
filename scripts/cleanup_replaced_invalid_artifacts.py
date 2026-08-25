"""Delete only invalid replay directories with verified PASS replacements."""
from __future__ import annotations
import json, shutil
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pcs.research.integrity_contract import validate_reproducibility_manifest

PAIRS = {
    "research_outputs/general_pcs_comparison/fixed/general_pcs_qqq_fixed_execution": "research_outputs/system_integrity/corrected_fixed_general/QQQ",
    "research_outputs/general_pcs_comparison/fixed/general_pcs_meta_execution": "research_outputs/system_integrity/corrected_fixed_general/META",
    "research_outputs/general_pcs_comparison/fixed/general_pcs_meta_fixed_execution": "research_outputs/system_integrity/corrected_fixed_general/META",
    "research_outputs/general_pcs_comparison/fixed/general_pcs_nvda_fixed_execution": "research_outputs/system_integrity/corrected_fixed_general/NVDA",
    "research_outputs/general_pcs_execution/general_pcs_meta_execution": "research_outputs/system_integrity/corrected_fixed_general/META",
    "research_outputs/cost_pcs_discovery_broad_2024": "research_outputs/cost_pcs_discovery_agent/2024",
    "research_outputs/cost_pcs_discovery_broad_2025": "research_outputs/cost_pcs_discovery_agent/2025",
}

def main() -> None:
    base = (ROOT / "research_outputs").resolve()
    done = []
    for old_rel, replacement_rel in PAIRS.items():
        old = (ROOT / old_rel).resolve()
        replacement = (ROOT / replacement_rel).resolve()
        if not old.is_dir():
            continue
        if not str(old).startswith(str(base) + os.sep):
            raise RuntimeError(f"unsafe old path: {old}")
        manifests = list(replacement.rglob("artifact_manifest.json"))
        if not manifests:
            print(f"SKIP_NO_REPRODUCIBILITY_MANIFEST {replacement}")
            continue
        for manifest in manifests:
            validate_reproducibility_manifest(json.loads(manifest.read_text(encoding="utf-8")))
        shutil.rmtree(old)
        if old.exists():
            raise RuntimeError(f"deletion verification failed: {old}")
        done.append(old_rel)
    print(json.dumps({"deleted": done, "count": len(done)}, indent=2))

if __name__ == "__main__": main()
