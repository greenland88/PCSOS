"""Research-only adapters; unavailable context remains UNKNOWN."""
from .core import ResearchRule,RuleResult,RuleStatus,validate_chain
from pcs.entry.gates import DTEGate,SafeStrikeGate,CreditEfficiencyGate
import yaml
RULES=yaml.safe_load(open("config/pcs_rules.yaml",encoding="utf8"))
def gate(adapter,name):
 def run(c,p):
  try:
   x=adapter.evaluate(type("Candidate",(),c)())
   return RuleResult(RuleStatus.PASS if str(x.status)=="PASS" else RuleStatus.FAIL,dict(x.diagnostics),list(x.reason_codes),{"production_adapter":name})
  except Exception as e:return RuleResult(RuleStatus.UNKNOWN,reason_codes=["ADAPTER_INPUT_UNAVAILABLE"],evidence={"error":str(e),"production_adapter":name})
 return run
def width(c,p):
 ok=c["width"] in p.get("priority",[5,10,2]);return RuleResult(RuleStatus.PASS if ok else RuleStatus.FAIL,{"width":c["width"]},[] if ok else ["WIDTH_NOT_ALLOWED"])
def quote(c,p):
 ok=c["short_bid"]>0 and c["short_ask"]>=c["short_bid"] and c["long_bid"]>0 and c["long_ask"]>=c["long_bid"];return RuleResult(RuleStatus.PASS if ok else RuleStatus.FAIL,reason_codes=[] if ok else ["QUOTE_INVALID"])
def unknown(code):return lambda c,p:RuleResult(RuleStatus.UNKNOWN,reason_codes=[code])
RULE_REGISTRY={
"dte_range":ResearchRule("dte_range","production_current","DTE",("dte",),evaluator=gate(DTEGate(RULES),"pcs.entry.gates.DTEGate"),allowed_params=("minimum","maximum")),
"safe_strike_atr":ResearchRule("safe_strike_atr","production_current","SAFE_STRIKE",("atr","underlying_price","short_strike"),evaluator=gate(SafeStrikeGate(RULES),"pcs.entry.gates.SafeStrikeGate"),allowed_params=("atr_multiple",)),
"credit_efficiency":ResearchRule("credit_efficiency","production_current","CREDIT",("credit","short_strike","long_strike"),evaluator=gate(CreditEfficiencyGate(RULES),"pcs.entry.gates.CreditEfficiencyGate"),allowed_params=("minimum_ratio",)),
"spread_width":ResearchRule("spread_width","research_v1","SPREAD_CONSTRUCTION",("width",),evaluator=width,allowed_params=("priority",)),
"quote_validity":ResearchRule("quote_validity","research_v1","DATA_AVAILABILITY",("short_bid","short_ask","long_bid","long_ask"),evaluator=quote),
"liquidity_gate":ResearchRule("liquidity_gate","production_current","LIQUIDITY",("volume","open_interest"),evaluator=unknown("LIQUIDITY_FULL_CONTEXT_UNAVAILABLE")),
"trend_gate":ResearchRule("trend_gate","production_current","MARKET_STATE",(),evaluator=unknown("TREND_CONTEXT_UNAVAILABLE")),
"support_gate":ResearchRule("support_gate","production_current","STRUCTURE",(),evaluator=unknown("SUPPORT_CONTEXT_UNAVAILABLE")),
"event_gate":ResearchRule("event_gate","production_current","EVENT",(),evaluator=unknown("EVENT_CALENDAR_UNAVAILABLE")),
"regime_gate":ResearchRule("regime_gate","production_current","MARKET_STATE",(),evaluator=unknown("MARKET_STATE_UNAVAILABLE")),
"planned_loss":ResearchRule("planned_loss","production_current","PLANNED_LOSS",("credit",),evaluator=lambda c,p:RuleResult(RuleStatus.PASS,{"planned_loss":c["credit"]*100*p.get("credit_multiple",1.0)}),allowed_params=("credit_multiple",)),
}
