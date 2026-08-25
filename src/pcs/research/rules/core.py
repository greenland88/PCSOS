"""Side-effect-free research rule contract and scenario resolver."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any, Callable
import json

class RuleStatus(StrEnum):
    PASS="PASS"; FAIL="FAIL"; UNKNOWN="UNKNOWN"; NOT_EVALUATED="NOT_EVALUATED"
@dataclass(frozen=True)
class RuleResult:
    status: RuleStatus; raw_values: dict[str,Any]=field(default_factory=dict); reason_codes:list[str]=field(default_factory=list); evidence:dict[str,Any]=field(default_factory=dict); missing_fields:list[str]=field(default_factory=list)
@dataclass(frozen=True)
class ResearchRule:
    rule_id:str; rule_version:str; stage:str; required_fields:tuple[str,...]=(); dependencies:tuple[str,...]=(); evaluator:Callable[[dict[str,Any],dict[str,Any]],RuleResult]|None=None; allowed_params:tuple[str,...]=()
    def evaluate(self, context, params):
        missing=[x for x in self.required_fields if context.get(x) is None]
        if missing:return RuleResult(RuleStatus.UNKNOWN,reason_codes=["REQUIRED_FIELD_UNAVAILABLE"],missing_fields=missing)
        return self.evaluator(context,params) if self.evaluator else RuleResult(RuleStatus.UNKNOWN,reason_codes=["RULE_NOT_IMPLEMENTED"])
def canonical_hash(value):return sha256(json.dumps(value,sort_keys=True,default=str,separators=(",",":" )).encode()).hexdigest()
def resolve_scenario(raw,base=None):
    out={**(base or {}),**raw};out["entry_rule_chain"]=raw.get("entry_rule_chain",(base or {}).get("entry_rule_chain",[]));out["ticker_overrides"]=raw.get("ticker_overrides",(base or {}).get("ticker_overrides",{}));out["scenario_hash"]=canonical_hash({k:v for k,v in out.items() if k!="scenario_hash"});return out
def validate_chain(chain,registry):
    seen=set();audit=[]
    for spec in chain:
        rid=spec["rule_id"]
        if rid in seen:raise ValueError(f"DUPLICATE_RULE_INSTANCE:{rid}")
        seen.add(rid)
        if rid not in registry:raise ValueError(f"UNKNOWN_RULE_ID:{rid}")
        rule=registry[rid];unknown=set(spec.get("params",{}))-set(rule.allowed_params)
        if unknown:raise ValueError(f"UNSUPPORTED_PARAMETER:{rid}:{','.join(sorted(unknown))}")
        if [d for d in rule.dependencies if d not in seen]:raise ValueError(f"MISSING_DEPENDENCY:{rid}")
        audit.append({"rule_id":rid,"enabled":spec.get("enabled",True),"rule_version":rule.rule_version,"stage":rule.stage})
    return audit
def evaluate_chain(chain,registry,context,mode="FULL_AUDIT"):
    prior={};out=[]
    for spec in chain:
        rule=registry[spec["rule_id"]]
        if not spec.get("enabled",True):r=RuleResult(RuleStatus.NOT_EVALUATED,reason_codes=["RULE_DISABLED"])
        elif mode=="PRODUCTION_SHORT_CIRCUIT" and any(x.status==RuleStatus.FAIL for x in prior.values()):r=RuleResult(RuleStatus.NOT_EVALUATED,reason_codes=["PRIOR_RULE_FAILED"])
        elif any(prior[d].status!=RuleStatus.PASS for d in rule.dependencies):r=RuleResult(RuleStatus.NOT_EVALUATED,reason_codes=["DEPENDENCY_NOT_PASS"])
        else:r=rule.evaluate(context,spec.get("params",{}))
        prior[rule.rule_id]=r;out.append((rule,r))
    return out
