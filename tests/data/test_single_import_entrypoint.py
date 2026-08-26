from __future__ import annotations

import subprocess
import sys


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
    for module in ("pcs.data.import_daily_snapshot", "pcs.data.import_option_archives", "pcs.data.update_daily"):
        result = subprocess.run(
            [sys.executable, "-m", module], capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "LEGACY_IMPORT_ENTRYPOINT_DISABLED" in (result.stdout + result.stderr)
