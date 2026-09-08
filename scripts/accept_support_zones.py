"""Bounded read-only acceptance of saved Step 3 support-zone artifacts."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from pcs.analysis_contracts import CallContext
from pcs.pool.artifacts import _write_atomic
from pcs.pool.support_zones import (
    SupportZoneDataReader, support_zone_to_ai_view, support_zones_to_markdown,
)
from pcs.trend.selection_models import SupportZoneResult
from pcs.trend.support_zones import evaluate_support_zones


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output_directory")
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
    assert len(flat_zones) == sum(len(r.current_zones)+len(r.archived_zones) for r in results)
    assert len(flat_tests) == sum(len(z.tests) for r in results for z in r.current_zones+r.archived_zones)
    assert len(flat_history) == sum(len(r.support_history) for r in results)
    batch = next(r for r in results if r.symbol == "NVDA")
    reader = SupportZoneDataReader()
    ctx = CallContext(symbol="NVDA", requested_as_of="2026-09-04", effective_daily_session="2026-09-04",
        mode="HISTORICAL", run_id="independent-acceptance", request_id="independent-acceptance", scope="SUPPORT_ZONES")
    independent = evaluate_support_zones(reader.load(ctx))
    assert independent.result_id == batch.result_id
    assert independent.current_zones == batch.current_zones
    assert independent.archived_zones == batch.archived_zones
    assert independent.support_history == batch.support_history
    independent_source = reader.verify_unchanged()
    prior_files = audit["source_unchanged"]["file_sha256"]
    assert all(sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in prior_files.items())
    report = {"status": "PARTIAL" if failed else "PASS", "source_commit": manifest["source_commit"],
        "source_clean": not manifest["tracked_source_dirty"], "results": len(results),
        "failures": audit["failures"], "symbols_unique_and_complete": True,
        "typed_roundtrip": True, "saved_hashes_valid": True, "views_equal": True,
        "flat_detail_counts_equal": True, "independent_nvda_equal": True,
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
