import pandas as pd
import pytest

from pcs.data.access import DataQualityError
from pcs.data.onboarding import activate_authoritative_route


def test_route_promotion_rejects_unreadable_manifest_partition(tmp_path):
    manifest = tmp_path / "manifest.csv"
    missing = tmp_path / "missing.parquet"
    pd.DataFrame([{
        "dataset": "options", "symbol": "QQQ", "status": "SUCCESS",
        "parquet_path": str(missing),
    }]).to_csv(manifest, index=False)
    routes = tmp_path / "routes.yaml"
    with pytest.raises(DataQualityError, match="CANONICAL_FILE_NOT_READABLE"):
        activate_authoritative_route("QQQ", dataset="options", manifest_path=str(manifest),
                                    parquet_root=str(tmp_path), routes_path=routes)
    assert not routes.exists()

