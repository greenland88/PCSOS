from pcs.journal.database import connect
from pcs.journal.repository import JournalRepository
from pcs.models.decision import Action, Decision, ScoreBreakdown, SizeClass


def test_record_decision_is_idempotent(tmp_path):
    repo = JournalRepository(connect(str(tmp_path / "pcs.db")))
    scores = ScoreBreakdown(**{name: 0.0 for name in ScoreBreakdown.model_fields})
    decision = Decision(ticker="JPM", expiration="2025-01-01", short_strike=100,
                        long_strike=95, underlying_price=110, market_regime="RISK_ON",
                        scores=scores, total_score=0, classification=SizeClass.ONE,
                        action=Action.WAIT, reason="test")
    repo.record_decision(decision)
    repo.record_decision(decision)
    assert len(repo.list_decisions()) == 1
