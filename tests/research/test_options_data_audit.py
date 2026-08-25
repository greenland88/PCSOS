from pathlib import Path

from pcs.research.options_data_audit import quarter_files


def test_symbol_file_discovery_is_scoped():
    files = quarter_files("data/raw/options", "AMZN")
    assert files
    assert all(Path(path).name.startswith("AMZN_") for path in files)


def test_audit_does_not_require_underlying_price_field():
    from pcs.research.options_data_audit import OPTION_COLUMNS
    assert "underlying_price" not in OPTION_COLUMNS
