import pytest

from pcs.data.import_option_archives import import_option_archives


def test_legacy_option_archive_entrypoint_is_disabled():
    with pytest.raises(SystemExit, match="LEGACY_IMPORT_ENTRYPOINT_DISABLED"):
        import_option_archives("archives", "raw", "parquet", "manifest.csv", symbols=["NVDA"])
