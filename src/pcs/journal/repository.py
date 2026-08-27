import json
from datetime import date
import hashlib
from pcs.models.decision import Decision


class JournalRepository:
    def __init__(self, conn):
        self.conn = conn

    def record_decision(self, decision: Decision):
        payload = decision.model_dump_json()
        decision_date = getattr(decision, "decision_date", None) or date.today().isoformat()
        event_key = hashlib.sha256(f"{decision_date}|{decision.ticker}|{payload}".encode("utf-8")).hexdigest()
        self.conn.execute(
            """INSERT INTO decisions(ticker, action, payload, event_key)
               SELECT ?, ?, ?, ?
               WHERE NOT EXISTS (SELECT 1 FROM decisions WHERE event_key = ?)""",
            (decision.ticker, decision.action.value, payload, event_key, event_key),
        )
        self.conn.commit()

    def list_decisions(self):
        rows = self.conn.execute("SELECT ticker, action, payload FROM decisions ORDER BY id DESC").fetchall()
        return [{"ticker": r[0], "action": r[1], "payload": json.loads(r[2])} for r in rows]

