"""Verify saved Step 5 results without repeating canonical reads or scanning."""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from pcs.pool.artifacts import _write_atomic
from pcs.pool.opportunities import (read_opportunity_bundle, opportunity_to_ai_view,
    opportunities_to_markdown, _csv_view)
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.selection_models import EntryOpportunity, OpportunityInput, ShallowPullbackInput
from pcs.trend.setup_detectors import detect_shallow_pullback
from pcs.validation import ValidationRun


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_directory")
    args = parser.parse_args()
    root = Path(args.output_directory)
    code = Path(__file__).resolve().parents[1]
    guard = ValidationRun(code, tuple((code/"src/pcs/trend").glob("*.py")) +
        (code/"src/pcs/pool/opportunities.py", root/"artifact_manifest.json"))
    manifest, documents = read_opportunity_bundle(root)
    assert manifest["tracked_source_dirty"] is False
    results = [EntryOpportunity.model_validate(x) for x in documents["entry_opportunities.json"]]
    inputs = {x["call_context"]["symbol"]: OpportunityInput.model_validate(x)
        for x in documents["prepared_opportunity_inputs.json"]}
    audit = json.loads((root/"read_audit.json").read_text(encoding="utf-8"))
    assert set([r.symbol for r in results]+[f["symbol"] for f in audit["failures"]]) == {
        "NVDA", "PLTR", "MSFT", "HOOD", "UBER", "MDLZ", "AAL", "AAOI"}
    assert len(results)+len(audit["failures"]) == 8
    assert json.loads((root/"entry_opportunities.ai.json").read_text(encoding="utf-8")) == [
        opportunity_to_ai_view(r) for r in results]
    assert (root/"entry_opportunities.zh-CN.md").read_text(encoding="utf-8") == opportunities_to_markdown(results)
    assert (root/"entry_opportunities.csv").read_text(encoding="utf-8") == _csv_view(results).replace("\r\n", "\n")
    children = [c for r in results for c in (r.family_results or [r])]
    assert json.loads((root/"family_opportunities.json").read_text(encoding="utf-8")) == [
        c.model_dump(mode="json") for c in children]
    for filename, attribute in (("opportunity_timeline.json", "timeline"),
            ("opportunity_transitions.json", "transitions")):
        assert json.loads((root/filename).read_text(encoding="utf-8")) == [
            {"symbol": c.symbol, "result_id": c.result_id, "family": c.family,
             **item.model_dump(mode="json")} for c in children for item in getattr(c, attribute)]
    assert json.loads((root/"opportunity_conditions.json").read_text(encoding="utf-8")) == [
        {"symbol": c.symbol, "result_id": c.result_id, "family": c.family, "day": day.session,
         **item.model_dump(mode="json")} for c in children for day in c.timeline for item in day.conditions]
    report = []
    for result in results:
        inp = inputs[result.symbol]
        independent = evaluate_entry_opportunity(inp)
        assert independent.result_id == result.result_id
        for child in result.family_results:
            single = evaluate_entry_opportunity(inp.model_copy(update={"enabled_families": [child.family]}))
            assert single.result_id == child.result_id and single.episodes == child.episodes
        cut = result.timeline[len(result.timeline)//2].session
        prefix_ctx = inp.call_context.model_copy(update={"effective_daily_session": cut, "requested_as_of": cut})
        prefix = evaluate_entry_opportunity(inp.model_copy(update={"call_context": prefix_ctx}))
        resumed = evaluate_entry_opportunity(inp.model_copy(update={"prior_family_results": prefix.family_results}))
        same_day = evaluate_entry_opportunity(inp.model_copy(update={"prior_family_results": result.family_results}))
        assert resumed.result_id == same_day.result_id == result.result_id
        assert resumed.family_results[1].episodes == result.family_results[1].episodes
        shallow = result.family_results[1]
        for evidence in shallow.shallow_pullback_timeline:
            if evidence.next_state and evidence.session == evidence.next_state.touch_session:
                ctx = inp.call_context.model_copy(update={"effective_daily_session": evidence.session})
                direct = detect_shallow_pullback(ShallowPullbackInput(call_context=ctx,
                    feature_view=inp.feature_view, support_facts=inp.support_facts,
                    effective_policy=inp.shallow_policy, shared_policy=inp.effective_policy))
                assert direct.result_id == evidence.result_id
        report.append({"symbol": result.symbol, "batch_single_restore": "PASS",
            "result_id": result.result_id, "families": [
                {"family": c.family, "state": c.state, "eligible": c.eligible_at_requested_time,
                 "status": c.status, "episodes": len(c.episodes),
                 "confirmations": sum(e.confirmation_date is not None for e in c.episodes),
                 "coverage_missing": len(c.coverage_missing_evidence)}
                for c in result.family_results]})
    read_opportunity_bundle(root)  # Immutable outputs still match the original manifest.
    guard.add_output(root)
    assert guard.finish(root/"acceptance_validation_run.json").value == "VALID"
    _write_atomic(root/"step_05_acceptance.json", json.dumps({"source_commit": manifest["source_commit"],
        "checks": "PASS: hashes, typed models, all views/details, independent APIs, family switches, prefix/same-day restore",
        "source_unchanged": audit["source_unchanged"], "results": report, "failures": audit["failures"]},
        ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
