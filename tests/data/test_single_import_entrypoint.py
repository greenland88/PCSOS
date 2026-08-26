from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_cli_exposes_only_unified_market_data_import_commands():
    result = subprocess.run(
        [sys.executable, "-m", "pcs.cli", "--help"],
        capture_output=True, text=True, check=True,
    )
    assert "market-data-status" in result.stdout
    assert "import-market-data" in result.stdout
    for legacy in ("collect-options", "update-data", "onboard", "onboarding-status"):
        assert legacy not in result.stdout


def test_legacy_module_import_entrypoints_are_closed():
    for module in ("pcs.data.import_daily_snapshot", "pcs.data.import_option_archives",
                   "pcs.data.update_daily", "pcs.data.import_options",
                   "pcs.data.migrate_daily_universe", "pcs.data.sync_daily_parquet"):
        result = subprocess.run(
            [sys.executable, "-m", module], capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "LEGACY_IMPORT_ENTRYPOINT_DISABLED" in (result.stdout + result.stderr)


def test_legacy_canonical_mutators_are_explicitly_closed():
    root = Path(__file__).parents[2]
    scripts = (
        "acquire_pool2_recent.py", "authoritative_q3_repair.py",
        "pilot_vendor_txt_first_row_rebuild.py", "prepare_amd_onboarding_isolated.py",
        "promote_legacy_options_routes.py", "promote_safe_options_v2.py",
        "repair_amzn_batch2_confirmed_gaps.py", "repair_historical_vendor_conflicts.py",
        "repair_qqq_canonical_options.py", "repair_daily_provenance.py",
        "clean_manifest_exact_duplicates.py", "persist_options_promotion_provenance.py",
    )
    for name in scripts:
        source = (root / "scripts" / name).read_text(encoding="utf-8")
        assert "reject_legacy_import_entrypoint" in source, name
