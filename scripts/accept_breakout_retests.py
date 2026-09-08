"""Verify saved Step 6 artifacts without canonical/provider reads."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from pcs.pool.artifacts import _write_atomic
from pcs.pool.opportunities import (opportunities_to_markdown, opportunity_to_ai_view,
    read_opportunity_bundle)
from pcs.trend.breakout_retest import detect_breakout_retest
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.selection_models import (BreakoutRetestInput, EntryOpportunity,
    OpportunityInput)
from pcs.validation import ValidationRun


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory")
    args=parser.parse_args()
    root=Path(args.output_directory)
    code=Path(__file__).resolve().parents[1]
    guard=ValidationRun(code,tuple((code/"src/pcs/trend").glob("*.py"))+
        (code/"src/pcs/pool/opportunities.py",root/"artifact_manifest.json"))
    manifest,documents=read_opportunity_bundle(root)
    assert manifest["tracked_source_dirty"] is False
    results=[EntryOpportunity.model_validate(x) for x in documents["entry_opportunities.json"]]
    inputs={x["call_context"]["symbol"]:OpportunityInput.model_validate(x)
        for x in documents["prepared_opportunity_inputs.json"]}
    audit=documents["read_audit.json"]
    expected={"NVDA","PLTR","MSFT","HOOD","UBER","MDLZ","AAL","AAOI"}
    assert set([r.symbol for r in results]+[f["symbol"] for f in audit["failures"]])==expected
    assert len(results)+len(audit["failures"])==8
    assert json.loads((root/"entry_opportunities.ai.json").read_text(encoding="utf-8"))==[
        opportunity_to_ai_view(r) for r in results]
    assert (root/"entry_opportunities.zh-CN.md").read_text(encoding="utf-8")==opportunities_to_markdown(results)
    children=[c for r in results for c in r.family_results]
    assert [c.family for c in children[0:3]]==[
        "HEALTHY_PULLBACK","SHALLOW_PULLBACK","BREAKOUT_RETEST"]
    assert json.loads((root/"family_opportunities.json").read_text(encoding="utf-8"))==[
        c.model_dump(mode="json") for c in children]
    breakouts=[c for c in children if c.family=="BREAKOUT_RETEST"]
    assert json.loads((root/"breakout_events.json").read_text(encoding="utf-8"))==[
        {"symbol":c.symbol,**e.model_dump(mode="json")} for c in breakouts
        for e in c.breakout_result.events]
    assert json.loads((root/"breakout_retest_timeline.json").read_text(encoding="utf-8"))==[
        {"symbol":c.symbol,**d.model_dump(mode="json")} for c in breakouts
        for d in c.breakout_result.timeline]
    report=[]
    for result in results:
        inp=inputs[result.symbol]
        independent=evaluate_entry_opportunity(inp)
        assert independent.result_id==result.result_id
        for child in result.family_results:
            single=evaluate_entry_opportunity(inp.model_copy(update={"enabled_families":[child.family]}))
            assert single.result_id==child.result_id
        breakout=result.family_results[2]
        raw=detect_breakout_retest(BreakoutRetestInput(call_context=inp.call_context,
            feature_view=inp.feature_view,effective_policy=inp.breakout_policy,
            opportunity_policy=inp.effective_policy))
        assert raw.result_id==breakout.breakout_result.result_id
        cut=breakout.timeline[len(breakout.timeline)//2].session
        prefix_context=inp.call_context.model_copy(update={"requested_as_of":cut,
            "effective_daily_session":cut})
        prefix=evaluate_entry_opportunity(inp.model_copy(update={"call_context":prefix_context}))
        resumed=evaluate_entry_opportunity(inp.model_copy(update={
            "prior_family_results":prefix.family_results}))
        same_day=evaluate_entry_opportunity(inp.model_copy(update={
            "prior_family_results":result.family_results}))
        assert resumed.result_id==same_day.result_id==result.result_id
        report.append({"symbol":result.symbol,"checks":"PASS",
            "breakouts":len(raw.events),"retests":sum(e.retest_session is not None for e in raw.events),
            "confirmations":sum(e.confirmation_session is not None for e in raw.events),
            "invalidated":sum(e.state=="INVALIDATED" for e in raw.events),
            "expired":sum(e.state=="EXPIRED" for e in raw.events),
            "state":breakout.state,"eligible":breakout.eligible_at_requested_time,
            "result_id":breakout.result_id})
    read_opportunity_bundle(root)
    guard.add_output(root)
    assert guard.finish(root/"acceptance_validation_run.json").value=="VALID"
    _write_atomic(root/"step_06_acceptance.json",json.dumps({
        "source_commit":manifest["source_commit"],
        "checks":"PASS: hashes, typed models, views, details, independent APIs, family switches, prefix/same-day restore",
        "origin_source_verification":audit["source_unchanged"],
        "results":report,"failures":audit["failures"]},ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
