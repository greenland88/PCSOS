import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY, ticker TEXT, open_date TEXT, expiration TEXT,
  short_strike REAL, long_strike REAL, credit REAL, contracts INTEGER,
  underlying_price_at_entry REAL, iv REAL, short_delta REAL, expected_move REAL,
  qqq_regime TEXT, soxx_regime TEXT, support_level REAL, distance_to_support REAL,
  distance_to_short_strike REAL, opportunity_score REAL, liquidity_score REAL,
  underlying_quality_score REAL, planned_risk REAL, theoretical_max_loss REAL,
  close_date TEXT, close_price REAL, profit REAL, profit_percentage REAL,
  rolled INTEGER, roll_reason TEXT, old_strike REAL, new_strike REAL,
  old_expiration TEXT, new_expiration TEXT, roll_credit_debit REAL,
  ai_reasoning TEXT, human_final_decision TEXT
);
CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY, ticker TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS rolls (id INTEGER PRIMARY KEY, ticker TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS daily_market_state (id INTEGER PRIMARY KEY, date TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS decisions (id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, ticker TEXT, action TEXT, payload TEXT);
CREATE TABLE IF NOT EXISTS ai_analysis (id INTEGER PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP, ticker TEXT, payload TEXT);
"""


def connect(path: str = "data/pcs.db"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    try:
        conn.execute("ALTER TABLE decisions ADD COLUMN event_key TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS decisions_event_key_uq ON decisions(event_key) WHERE event_key IS NOT NULL")
    conn.commit()
    return conn

