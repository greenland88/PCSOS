"""Freeze research scenario and source identities after TRAIN; research-only."""
from pathlib import Path
from hashlib import sha256
import json, subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"research_outputs"/"spy_qqq_modular_rule_research_20260821"
FILES=[ROOT/"config/pcs_rules.yaml",ROOT/"src/pcs/research/rules/core.py",ROOT/"src/pcs/research/rules/registry.py",ROOT/"scripts/run_spy_qqq_modular_monthly_replay.py"]
SCENARIOS=sorted((ROOT/"research_configs/pcs_rule_scenarios").glob("*.yaml"))
def digest(p): return sha256(p.read_bytes()).hexdigest()
def main():
    try: commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    except Exception: commit="UNAVAILABLE"
    snapshot=OUT/"frozen_scenarios";snapshot.mkdir(exist_ok=True)
    identities={str(p.relative_to(ROOT)):digest(p) for p in FILES+SCENARIOS if p.exists()}
    for p in SCENARIOS:(snapshot/p.name).write_bytes(p.read_bytes())
    payload={"status":"FROZEN_RESEARCH_SCENARIOS","git_commit":commit,"identities":identities,"train_manifest":json.loads((OUT/"train_manifest.json").read_text()),"final_oos_read":False,"research_only":True}
    payload["freeze_hash"]=sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    (OUT/"train_scenario_freeze.json").write_text(json.dumps(payload,indent=2),encoding="utf8")
    print(payload["freeze_hash"])
if __name__=="__main__":main()
