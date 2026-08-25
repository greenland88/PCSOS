import zipfile

import pytest

from pcs.data.onboarding import HistoricalTxtZipAdapter


def test_normal_zip_records_original_provenance(tmp_path):
    archive = tmp_path / "2026_q3_option_chain_test.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ZZZ_2026_q3_option_chain.txt", "2026-08-02,100.25,2026-09-18,p,1,.9,1.1,.3,.31,1000,200,-.25,.01,.02,-.03,-.01\n")
    _, meta = HistoricalTxtZipAdapter(tmp_path).read_period("ZZZ", 2026, 3)
    assert meta["authoritative_source"] == "original_purchased_zip_member"
    assert meta["extraction_method"] == "native_zipfile"
    assert meta["source_member"].startswith("ZZZ_")


def test_deflate64_path_fails_closed_without_reader(monkeypatch, tmp_path):
    adapter = HistoricalTxtZipAdapter(tmp_path)
    monkeypatch.setattr("pcs.data.onboarding.shutil.which", lambda _: None)
    with pytest.raises(RuntimeError, match="VENDOR_ARCHIVE_UNREADABLE_DEFLATE64"):
        adapter._read_deflate64(tmp_path / "archive.zip", "member.txt")
