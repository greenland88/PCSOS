import json
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from pcs.data.derived_store import write_research_run


def test_research_run_writes_union_schema_atomically(tmp_path):
    path = tmp_path / "research_runs.csv"
    write_research_run({"run_id": "a", "status": "PASS"}, path)
    write_research_run({"run_id": "b", "status": "PASS", "extra": "x"}, path)

    frame = pd.read_csv(path)
    assert set(frame.columns) == {"run_id", "status", "extra"}
    assert len(frame) == 2
    assert json.loads((tmp_path / "research_runs" / "b.json").read_text())["extra"] == "x"


def test_concurrent_research_run_writes_preserve_all_records(tmp_path):
    path = tmp_path / "research_runs.csv"

    def write(i):
        return write_research_run({"run_id": f"run-{i}", "status": "PASS", "worker": i}, path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))

    frame = pd.read_csv(path)
    assert set(frame.run_id) == {f"run-{i}" for i in range(8)}
