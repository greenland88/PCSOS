from pathlib import Path
import json
import sys

sys.path.insert(0, "src")
from pcs.validation.frozen_dependency import write_attestation

ROOT = Path("research_outputs/spy_qqq_pcs_baseline_20260821")
result = write_attestation(ROOT / "immutable_oos_config.json", ROOT / "frozen_dependency_equivalence_attestation.json", tool_version="pcs-frozen-resolver-20260821.v1")
print(json.dumps(result, indent=2))
