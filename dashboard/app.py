import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st
from pcs.engine.decision_engine import DecisionEngine, load_rules
from pcs.journal.database import connect
from pcs.journal.repository import JournalRepository
from pcs.providers.mock_provider import MockProvider


st.set_page_config(page_title="PCS Decision Assistant", layout="wide")
st.title("PCS Decision Assistant V1")

rules = load_rules(Path(__file__).resolve().parents[1] / "config" / "pcs_rules.yaml")
provider = MockProvider()
engine = DecisionEngine(rules)
market = provider.get_market_state()
positions = provider.get_positions()
portfolio = provider.get_portfolio() | {"bucket_risk": {"nasdaq_mega": 2200, "semiconductor": 900}}

candidate_decisions = [engine.evaluate_candidate(c, market, portfolio) for c in provider.get_candidates()]
position_decisions = [engine.evaluate_position(p, market) for p in positions]

conn = connect(str(Path(__file__).resolve().parents[1] / "data" / "pcs.db"))
repo = JournalRepository(conn)
for d in candidate_decisions + position_decisions:
    repo.record_decision(d)

regime = candidate_decisions[0].market_regime if candidate_decisions else "N/A"
cols = st.columns(6)
cols[0].metric("Market Regime", regime)
cols[1].metric("PCS Pool", f"${rules['capital']['pcs_pool']:,.0f}")
cols[2].metric("Reserve Cash", f"${rules['capital']['reserve_cash']:,.0f}")
cols[3].metric("Current Planned Risk", f"${portfolio['planned_risk']:,.0f}")
cols[4].metric("Theoretical Max Loss", f"${portfolio['theoretical_max_loss']:,.0f}")
cols[5].metric("Capacity Used", f"{portfolio['planned_risk'] / rules['capital']['pcs_pool']:.0%}")

st.subheader("Current Positions")
st.dataframe([{
    "Ticker": p.ticker,
    "Spread": f"{p.short_strike}/{p.long_strike}",
    "Expiration": p.expiration,
    "P/L": f"{p.profit_capture_pct:.1f}% captured",
    "Status": d.action.value,
    "Risk": p.planned_risk,
    "Roll Watch": d.reason,
} for p, d in zip(positions, position_decisions)], use_container_width=True)

st.subheader("Candidates")
st.dataframe([{
    "Ticker": d.ticker,
    "Score": d.total_score,
    "Suggested spread": f"{d.short_strike}/{d.long_strike}",
    "Credit": d.estimated_credit,
    "POP/Delta": "see delta in provider mock",
    "Liquidity": d.scores.liquidity,
    "Rollability": d.scores.rollability,
    "Size": d.classification.value,
    "Action": d.action.value,
    "Reason": d.reason,
} for d in candidate_decisions], use_container_width=True)

st.subheader("Roll Watch")
st.dataframe([{
    "Ticker": d.ticker,
    "Current spread": f"{d.short_strike}/{d.long_strike}",
    "Structure valid?": "Yes" if p.structure_valid else "No",
    "Roll urgency": d.action.value if d.action.value == "ROLL" else "Watch",
    "Best candidate roll": d.roll_candidate or {},
} for p, d in zip(positions, position_decisions)], use_container_width=True)

