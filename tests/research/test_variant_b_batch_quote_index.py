import pandas as pd

from pcs.research import variant_b_replay


def test_batch_quote_index_resolves_ticker_from_legacy_partition_path(tmp_path, monkeypatch):
    calls = []
    quotes = pd.DataFrame({
        "Trade Date": pd.to_datetime(["2026-01-02"]),
        "Expiry Date": pd.to_datetime(["2026-02-20"]),
        "Strike": [100.0],
    })

    def fake_load(symbol, start, end):
        calls.append((symbol, start, end))
        return quotes, {"source": "canonical_partitioned_parquet"}

    monkeypatch.setattr(variant_b_replay, "load_quotes_canonical", fake_load)
    index, metadata = variant_b_replay.build_batch_quote_index(
        tmp_path / "options_v2" / "symbol=qqq",
        "2026-01-01",
        "2026-01-31",
    )

    assert calls[0][0] == "QQQ"
    assert (pd.Timestamp("2026-02-20"), 100.0) in index
    assert metadata["source"] == "canonical_partitioned_parquet"
    assert metadata["compatibility_wrapper"] is True
