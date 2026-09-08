"""Bounded read-only acceptance of saved Step 3 support-zone artifacts."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from pcs.analysis_contracts import CallContext
from pcs.pool.artifacts import _write_atomic
from pcs.pool.support_zones import (
    SupportZoneDataReader, support_zone_to_ai_view, support_zones_to_markdown,
    support_source_details, find_support_source, load_support_zone_state,
)
from pcs.trend.selection_models import SupportZoneResult
from pcs.trend.support_zones import evaluate_support_zones


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory")
    parser.add_argument("--baseline-directory")
    args = parser.parse_args()
    root = Path(args.output_directory)
    manifest = json.loads((root/"artifact_manifest.json").read_text(encoding="utf-8"))
    assert all(sha256((root/name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["sha256"].items())
    results = [SupportZoneResult.model_validate(x) for x in json.loads(
        (root/"support_zone_results.json").read_text(encoding="utf-8"))]
    audit = json.loads((root/"read_audit.json").read_text(encoding="utf-8"))
    expected = {"NVDA", "PLTR", "MSFT", "HOOD", "UBER", "MDLZ", "AAL", "AAOI"}
    actual, failed = [r.symbol for r in results], [x["symbol"] for x in audit["failures"]]
    assert len(actual+failed) == 8 and set(actual+failed) == expected and set(actual).isdisjoint(failed)
    assert json.loads((root/"support_zones.ai.json").read_text(encoding="utf-8")) == [support_zone_to_ai_view(r) for r in results]
    assert (root/"support_zones.zh-CN.md").read_text(encoding="utf-8") == support_zones_to_markdown(results)
    flat_zones = json.loads((root/"support_zones.json").read_text(encoding="utf-8"))
    flat_tests = json.loads((root/"support_tests.json").read_text(encoding="utf-8"))
    flat_history = json.loads((root/"support_history.json").read_text(encoding="utf-8"))
    assert flat_zones == [{"symbol": r.symbol, **z.model_dump(mode="json")} for r in results for z in r.current_zones+r.archived_zones]
    assert flat_tests == [{"symbol": r.symbol, "zone_id": z.zone_id, **t.model_dump(mode="json")} for r in results for z in r.current_zones+r.archived_zones for t in z.tests]
    assert flat_history == [{"symbol": r.symbol, **h.model_dump(mode="json")} for r in results for h in r.support_history]
    flat_sources = json.loads((root/"support_sources.json").read_text(encoding="utf-8"))
    assert flat_sources == [a for r in results for a in support_source_details(r)]
    linked_sources = 0
    for result in results:
        for zone in result.current_zones+result.archived_zones:
            assert zone.observed_sources is not None
            assert zone.observed_source_ids == [a.source_id for a in zone.observed_sources]
            assert len(zone.observed_source_ids) == len(set(zone.observed_source_ids))
            for anchor in zone.creation_sources:
                assert find_support_source(result, zone.zone_id, anchor.source_id) == anchor
            for breach in zone.intraday_breaches:
                matches = [h for h in result.support_history if h.zone_id == zone.zone_id and
                    h.event_type == "INTRADAY_PENETRATION" and h.session == breach.session]
                assert len(matches) == 1
                assert (matches[0].low, matches[0].invalidation_line) == (breach.low, breach.invalidation_line)
        for history in result.support_history:
            for source_id in history.source_ids:
                anchor = find_support_source(result, history.zone_id, source_id)
                assert anchor is not None and anchor.available_at == history.known_at
                linked_sources += 1
            if history.event_type == "INTRADAY_PENETRATION":
                zone = next(z for z in result.next_state.zones if z.zone_id == history.zone_id)
                assert any((b.session, b.low, b.invalidation_line) == (history.session, history.low, history.invalidation_line) for b in zone.intraday_breaches)
    baseline_comparison = []
    baseline_hashes = {}
    if args.baseline_directory:
        baseline_root = Path(args.baseline_directory)
        baseline_manifest = json.loads((baseline_root/"artifact_manifest.json").read_text(encoding="utf-8"))
        baseline_hashes = {str(baseline_root/name): digest for name, digest in baseline_manifest["sha256"].items()}
        baseline_hashes[str(baseline_root/"artifact_manifest.json")] = sha256((baseline_root/"artifact_manifest.json").read_bytes()).hexdigest()
        assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in baseline_hashes.items())
        old_results = json.loads((baseline_root/"support_zone_results.json").read_text(encoding="utf-8"))
        for result in results:
            old = next(r for r in old_results if r["symbol"] == result.symbol)
            added_breaches = 0
            for field in ("current_zones", "archived_zones"):
                new_zones = getattr(result, field)
                assert len(old[field]) == len(new_zones)
                for previous, current in zip(old[field], new_zones):
                    # Match unchanged creation facts, never match using the corrected ID.
                    assert previous["creation_sources"] == [a.model_dump(mode="json") for a in current.creation_sources]
                    for key in ("lower", "upper", "anchor_price", "anchor_atr", "invalidation_line", "formed_at", "available_at", "state", "active", "broken_at", "broken_close", "bound", "archive_reason"):
                        assert previous[key] == getattr(current, key), (result.symbol, field, key)
                    assert [{k: v for k, v in t.items() if k != "test_id"} for t in previous["tests"]] == [t.model_dump(mode="json", exclude={"test_id"}) for t in current.tests]
                    assert previous["zone_id"] != current.zone_id
                    new_breaches = [b.model_dump(mode="json") for b in current.intraday_breaches]
                    assert all(b in new_breaches for b in previous["intraday_breaches"])
                    added_breaches += len(new_breaches)-len(previous["intraday_breaches"])
            baseline_comparison.append({"symbol": result.symbol, "bounds_and_tests_unchanged": True,
                "additional_persisted_breaches": added_breaches,
                "current_zones": len(result.current_zones), "archived_zones": len(result.archived_zones)})
    batch = next(r for r in results if r.symbol == "NVDA")
    reader = SupportZoneDataReader()
    ctx = CallContext(symbol="NVDA", requested_as_of="2026-09-04", effective_daily_session="2026-09-04",
        mode="HISTORICAL", run_id="independent-acceptance", request_id="independent-acceptance", scope="SUPPORT_ZONES")
    independent_input = reader.load(ctx)
    independent = evaluate_support_zones(independent_input)
    assert independent.result_id == batch.result_id
    assert independent.current_zones == batch.current_zones
    assert independent.archived_zones == batch.archived_zones
    assert independent.support_history == batch.support_history
    saved = load_support_zone_state(root, "NVDA")
    resumed = evaluate_support_zones(independent_input.model_copy(update={"prior_state": saved}))
    assert resumed.result_id == batch.result_id and resumed.next_state == batch.next_state
    independent_source = reader.verify_unchanged()
    prior_files = audit["source_unchanged"]["file_sha256"]
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in prior_files.items())
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in baseline_hashes.items())
    assert sha256(Path(audit["source_unchanged"]["manifest_path"]).read_bytes()).hexdigest() == audit["source_unchanged"]["manifest_sha256"]
    report = {"status": "PARTIAL" if failed else "PASS", "source_commit": manifest["source_commit"],
        "source_clean": not manifest["tracked_source_dirty"], "results": len(results),
        "failures": audit["failures"], "symbols_unique_and_complete": True,
        "typed_roundtrip": True, "saved_hashes_valid": True, "views_equal": True,
        "flat_detail_counts_equal": True, "independent_nvda_equal": True,
        "flat_details_content_equal": True, "source_content_and_links_valid": True,
        "source_records": len(flat_sources), "linked_source_records": linked_sources,
        "intraday_history_state_equal": True, "saved_nvda_resume_equal": True,
        "baseline_comparison": baseline_comparison, "baseline_files_unchanged": bool(baseline_hashes),
        "independent_nvda_result_id": independent.result_id,
        "source_hashes_unchanged": True, "source_file_count": len(prior_files),
        "independent_source_verification": independent_source}
    assert report["source_clean"]
    for name, payload in (("independent_nvda.json", independent.model_dump(mode="json")),
                          ("acceptance.json", report)):
        if (root/name).exists():
            raise ValueError("ACCEPTANCE_FILE_ALREADY_EXISTS")
        _write_atomic(root/name, json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "independent_source_verification"}, indent=2))


if __name__ == "__main__":
    main()
