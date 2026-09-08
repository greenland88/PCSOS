"""Bounded read-only acceptance for Step 4 opportunity artifacts."""
import argparse
import csv
from hashlib import sha256
import io
import json
from pathlib import Path

from pcs.analysis_contracts import CallContext
from pcs.pool.artifacts import _write_atomic
from pcs.pool.opportunities import (
    OpportunityDataReader, load_opportunity_state, opportunities_to_markdown,
    opportunity_to_ai_view,
)
from pcs.trend.opportunity_engine import evaluate_entry_opportunity
from pcs.trend.selection_models import EntryOpportunity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory")
    args = parser.parse_args()
    root = Path(args.output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    assert all(sha256((root/name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["sha256"].items())
    results = [EntryOpportunity.model_validate(x) for x in json.loads(
        (root/"entry_opportunities.json").read_text(encoding="utf-8"))]
    audit = json.loads((root/"read_audit.json").read_text(encoding="utf-8"))
    expected = {"NVDA", "PLTR", "MSFT", "HOOD", "UBER", "MDLZ", "AAL", "AAOI"}
    actual = [r.symbol for r in results]
    failed = [x["symbol"] for x in audit["failures"]]
    assert len(actual+failed) == 8 and set(actual+failed) == expected
    assert len(actual) == len(set(actual)) and set(actual).isdisjoint(failed)
    assert json.loads((root/"entry_opportunities.ai.json").read_text(encoding="utf-8")) == [
        opportunity_to_ai_view(r) for r in results]
    assert (root/"entry_opportunities.zh-CN.md").read_text(encoding="utf-8") == opportunities_to_markdown(results)
    flat_timeline = json.loads((root/"opportunity_timeline.json").read_text(encoding="utf-8"))
    flat_conditions = json.loads((root/"opportunity_conditions.json").read_text(encoding="utf-8"))
    flat_transitions = json.loads((root/"opportunity_transitions.json").read_text(encoding="utf-8"))
    assert flat_timeline == [{"symbol": r.symbol, "result_id": r.result_id,
        **d.model_dump(mode="json")} for r in results for d in r.timeline]
    assert flat_conditions == [{"symbol": r.symbol, "result_id": r.result_id,
        "day": d.session, **c.model_dump(mode="json")} for r in results for d in r.timeline for c in d.conditions]
    assert flat_transitions == [{"symbol": r.symbol, "result_id": r.result_id,
        **t.model_dump(mode="json")} for r in results for t in r.transitions]
    csv_rows = list(csv.DictReader(io.StringIO(
        (root/"entry_opportunities.csv").read_text(encoding="utf-8"))))
    assert len(csv_rows) == len(flat_timeline)
    for row, expected_row in zip(csv_rows, flat_timeline):
        assert (row["symbol"], row["session"], row["state"], row["capability_status"]) == (
            expected_row["symbol"], expected_row["session"], expected_row["state"] or "",
            expected_row["capability_status"])
        expected_eligible = "" if expected_row["eligible"] is None else str(expected_row["eligible"]).lower()
        assert row["eligible"] == expected_eligible

    batch = next(r for r in results if r.symbol == "NVDA")
    reader = OpportunityDataReader()
    context = CallContext(symbol="NVDA", requested_as_of="2026-09-04",
        effective_daily_session="2026-09-04", mode="HISTORICAL",
        run_id="step04-independent", request_id="step04-independent:NVDA",
        scope="ENTRY_OPPORTUNITY_V2_OBSERVATION")
    prepared = reader.load(context)
    independent = evaluate_entry_opportunity(prepared)
    assert independent.result_id == batch.result_id
    assert independent.episodes == batch.episodes
    assert independent.timeline == batch.timeline
    assert independent.transitions == batch.transitions
    saved = load_opportunity_state(root, "NVDA")
    resumed = evaluate_entry_opportunity(prepared.model_copy(update={"prior_state": saved}))
    assert resumed.result_id == batch.result_id
    assert resumed.episodes == batch.episodes and resumed.timeline == batch.timeline
    assert resumed.next_state == batch.next_state
    assert resumed.call_diagnostics == ["PRIOR_STATE_COMPATIBLE_REPLAY_VERIFIED"]
    independent_verification = reader.verify_unchanged()
    original_verification = audit["source_unchanged"]
    for path, digest in original_verification["file_sha256"].items():
        assert sha256(Path(path).read_bytes()).hexdigest() == digest
    assert sha256(Path(original_verification["manifest_path"]).read_bytes()).hexdigest() == original_verification["manifest_sha256"]
    summary = {"status": "PARTIAL" if failed else "PASS",
        "source_commit": manifest["source_commit"],
        "source_clean": not manifest["tracked_source_dirty"],
        "results": len(results), "failures": audit["failures"],
        "symbols_unique_and_complete": True, "typed_roundtrip": True,
        "artifact_hashes_valid": True, "views_same_source": True,
        "csv_common_fields_equal": True, "detail_content_equal": True,
        "independent_nvda_equal": True, "saved_nvda_resume_equal": True,
        "source_hashes_unchanged": True,
        "source_file_count": len(original_verification["file_sha256"]),
        "independent_nvda_result_id": independent.result_id,
        "independent_source_verification": independent_verification,
        "per_symbol": [{"symbol": r.symbol, "status": r.status.value,
            "state": r.state.value if r.state else None,
            "eligible": r.eligible_at_requested_time,
            "episodes": len(r.episodes), "transitions": len(r.transitions),
            "evaluated_through": r.coverage.evaluated_through,
            "missing_sessions": r.coverage.missing_sessions,
            "result_id": r.result_id} for r in results]}
    assert summary["source_clean"]
    if (root/"acceptance.json").exists():
        raise ValueError("ACCEPTANCE_FILE_ALREADY_EXISTS")
    _write_atomic(root/"acceptance.json", json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in summary.items()
                      if k != "independent_source_verification"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
