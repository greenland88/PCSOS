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
