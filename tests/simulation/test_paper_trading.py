import json
import sqlite3

from pcs.engine.decision_engine import load_rules
from pcs.models.decision import Action
from pcs.providers.mock_provider import MockProvider
from pcs.simulation.paper_trading import PaperTradingStatus, run_daily_paper_trading


def test_daily_paper_trading_returns_agent_ready_envelope():
    result = run_daily_paper_trading(
        MockProvider(),
        load_rules(),
        as_of="2026-08-18",
        run_id="run_test",
        request_id="req_test",
    )

    assert result.module == "paper_trading_daily"
    assert result.version == "1.0"
    assert result.symbol == "PORTFOLIO"
    assert result.as_of == "2026-08-18"
    assert result.status == PaperTradingStatus.READY
    assert result.run_id == "run_test"
    assert result.request_id == "req_test"
    assert result.candidate_count == 4
    assert result.position_count == 2
    assert set(result.action_counts) == {action.value for action in Action}
    assert result.action_counts["OPEN"] == 0
    assert result.action_counts["NO_TRADE"] == 4
    assert result.action_counts["WAIT"] == 0
    assert result.action_counts["HOLD"] == 1
    assert result.action_counts["ROLL"] == 1
    assert result.planned_risk_open == 0
    assert result.theoretical_max_loss_open == 0
    assert all(snapshot.action in Action for snapshot in result.snapshots)
    assert all(snapshot.reason_codes for snapshot in result.snapshots)
    hold = next(snapshot for snapshot in result.snapshots if snapshot.action == Action.HOLD)
    assert hold.reason_codes == ["POSITION_HELD"]


def test_daily_paper_trading_persists_outputs(tmp_path):
    db_path = tmp_path / "pcs.db"
    out_dir = tmp_path / "paper"

    result = run_daily_paper_trading(
        MockProvider(),
        load_rules(),
        as_of="2026-08-18",
        run_id="run_test",
        request_id="req_test",
        output_dir=out_dir,
        sqlite_path=db_path,
    )

    json_path = out_dir / "2026-08-18" / "paper_trading_snapshot.json"
    summary_path = out_dir / "2026-08-18" / "paper_trading_summary.csv"
    snapshots_path = out_dir / "2026-08-18" / "paper_trading_snapshots.csv"
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == result.run_id
    assert summary_path.exists()
    assert snapshots_path.exists()

    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT as_of, run_id, status FROM paper_trading_runs").fetchone()
    assert row == ("2026-08-18", "run_test", "READY")
