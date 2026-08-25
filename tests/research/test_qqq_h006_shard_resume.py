import hashlib

import pandas as pd

from pcs.research.qqq_h006_new_entry_sharded import _SHARD_COLUMNS, _valid_shard_output


def _write_shard(path, rows):
    frame = pd.DataFrame(rows)
    frame.to_parquet(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return frame, {"rows": len(frame), "output_sha256": digest}


def _row():
    return {
        "trade_date": pd.Timestamp("2023-01-03"),
        "ticker": "QQQ",
        "pit_feature_ready": True,
        "signal_date": False,
        "option_chain_available": False,
        "contract_selected": False,
        "lifecycle_completed": False,
        "reason_code": "NOT_SIGNAL",
    }


def test_qqq_h006_resume_requires_matching_output_checksum(tmp_path):
    path = tmp_path / "shard.parquet"
    frame, summary = _write_shard(path, [_row()])
    summary["year"] = 2023
    assert _SHARD_COLUMNS.issubset(frame.columns)
    assert _valid_shard_output(path, summary)

    path.write_bytes(path.read_bytes() + b"corrupt")
    assert not _valid_shard_output(path, summary)


def test_qqq_h006_resume_rejects_row_count_or_schema_mismatch(tmp_path):
    path = tmp_path / "shard.parquet"
    _, summary = _write_shard(path, [_row()])
    summary["year"] = 2023
    assert not _valid_shard_output(path, {**summary, "rows": 2})
    assert not _valid_shard_output(path, {**summary, "output_sha256": "wrong"})


def test_qqq_h006_resume_rejects_wrong_ticker_or_year(tmp_path):
    path = tmp_path / "shard.parquet"
    row = _row()
    row["ticker"] = "MSFT"
    _, summary = _write_shard(path, [row])
    assert not _valid_shard_output(path, {**summary, "year": 2023})

    row["ticker"] = "QQQ"
    row["trade_date"] = pd.Timestamp("2022-01-03")
    _, summary = _write_shard(path, [row])
    assert not _valid_shard_output(path, {**summary, "year": 2023})
