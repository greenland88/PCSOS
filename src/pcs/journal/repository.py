import json
from pcs.models.decision import Decision


class JournalRepository:
    def __init__(self, conn):
        self.conn = conn

    def record_decision(self, decision: Decision):
        self.conn.execute(
            "INSERT INTO decisions(ticker, action, payload) VALUES (?, ?, ?)",
            (decision.ticker, decision.action.value, decision.model_dump_json()),
        )
        self.conn.commit()

    def list_decisions(self):
        rows = self.conn.execute("SELECT ticker, action, payload FROM decisions ORDER BY id DESC").fetchall()
        return [{"ticker": r[0], "action": r[1], "payload": json.loads(r[2])} for r in rows]

