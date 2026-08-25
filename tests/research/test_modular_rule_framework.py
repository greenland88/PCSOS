from pcs.research.rules.core import ResearchRule,RuleResult,RuleStatus,canonical_hash,validate_chain,evaluate_chain,resolve_scenario
def ok(c,p): return RuleResult(RuleStatus.PASS)
def fail(c,p): return RuleResult(RuleStatus.FAIL)
def test_pass_fail_unknown_and_determinism():
 r=ResearchRule("x","1","X",("a",),evaluator=ok)
 assert r.evaluate({"a":1},{ }).status==RuleStatus.PASS
 assert r.evaluate({},{}).status==RuleStatus.UNKNOWN
 assert canonical_hash({"a":1})==canonical_hash({"a":1})
def test_short_circuit_and_full_audit():
 a=ResearchRule("a","1","X",evaluator=fail);b=ResearchRule("b","1","X",evaluator=ok); reg={"a":a,"b":b};ch=[{"rule_id":"a"},{"rule_id":"b"}]
 assert evaluate_chain(ch,reg,{},"FULL_AUDIT")[1][1].status==RuleStatus.PASS
 assert evaluate_chain(ch,reg,{},"PRODUCTION_SHORT_CIRCUIT")[1][1].status==RuleStatus.NOT_EVALUATED
def test_validation_enable_disable_duplicate_unknown_and_override_hash():
 r=ResearchRule("a","1","X",evaluator=ok);reg={"a":r}
 assert validate_chain([{"rule_id":"a","enabled":False}],reg)[0]["enabled"] is False
 try: validate_chain([{"rule_id":"bad"}],reg);assert False
 except ValueError: pass
 try: validate_chain([{"rule_id":"a"},{"rule_id":"a"}],reg);assert False
 except ValueError: pass
 assert resolve_scenario({"scenario_id":"x","ticker_overrides":{"QQQ":{"x":1}}})["scenario_hash"]!=resolve_scenario({"scenario_id":"x","ticker_overrides":{"QQQ":{"x":2}}})["scenario_hash"]

def test_fail_result_is_preserved():
 r=ResearchRule("x","1","X",evaluator=fail)
 assert r.evaluate({},{}).status==RuleStatus.FAIL

def test_required_field_is_unknown_not_fail():
 r=ResearchRule("x","1","X",("missing",),evaluator=fail)
 assert r.evaluate({},{}).status==RuleStatus.UNKNOWN

def test_disabled_rule_not_evaluated():
 r=ResearchRule("x","1","X",evaluator=ok)
 assert evaluate_chain([{"rule_id":"x","enabled":False}],{"x":r},{})[0][1].status==RuleStatus.NOT_EVALUATED

def test_dependency_requires_predecessor():
 r=ResearchRule("b","1","X",dependencies=("a",),evaluator=ok)
 try: validate_chain([{"rule_id":"b"}],{"b":r}); assert False
 except ValueError as e: assert "MISSING_DEPENDENCY" in str(e)

def test_dependency_not_pass_blocks_downstream():
 a=ResearchRule("a","1","X",evaluator=fail); b=ResearchRule("b","1","X",dependencies=("a",),evaluator=ok)
 assert evaluate_chain([{"rule_id":"a"},{"rule_id":"b"}],{"a":a,"b":b},{})[1][1].status==RuleStatus.NOT_EVALUATED

def test_unsupported_param_rejected():
 r=ResearchRule("x","1","X",evaluator=ok,allowed_params=("yes",))
 try: validate_chain([{"rule_id":"x","params":{"no":1}}],{"x":r}); assert False
 except ValueError as e: assert "UNSUPPORTED_PARAMETER" in str(e)

def test_version_replacement_changes_scenario_hash():
 a=resolve_scenario({"scenario_id":"x","entry_rule_chain":[{"rule_id":"a","version":"1"}]})
 b=resolve_scenario({"scenario_id":"x","entry_rule_chain":[{"rule_id":"a","version":"2"}]})
 assert a["scenario_hash"] != b["scenario_hash"]

def test_hash_order_independent():
 assert canonical_hash({"b":2,"a":1})==canonical_hash({"a":1,"b":2})

def test_full_audit_evaluates_independent_rules_after_failure():
 a=ResearchRule("a","1","X",evaluator=fail); b=ResearchRule("b","1","X",evaluator=ok)
 result=dict((r.rule_id,v.status) for r,v in evaluate_chain([{"rule_id":"a"},{"rule_id":"b"}],{"a":a,"b":b},{},"FULL_AUDIT"))
 assert result=={"a":RuleStatus.FAIL,"b":RuleStatus.PASS}
