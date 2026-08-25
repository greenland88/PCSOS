from pathlib import Path

import yaml

from pcs.entry.gates import CreditEfficiencyGate, DTEGate, GateStatus, SafeStrikeGate
from pcs.models.trade import TradeCandidate


def rules():
    return yaml.safe_load(Path("config/pcs_rules.yaml").read_text())


def candidate(**overrides):
    values = dict(ticker="QQQ", expiration="2026-09-18", short_strike=455, long_strike=450,
                  underlying_price=485, credit=0.50, dte=35, short_delta=0.10,
                  expected_move=20, support_level=462, normal_daily_move=5,
                  option_volume=1000, open_interest=1000, bid_ask_pct=.10,
                  nearby_strikes=8, later_expirations=5, business_quality=90,
                  trend_score=90, support_score=90, sector_alignment=80,
                  price_confirmation=90, atr=10)
    values.update(overrides)
    return TradeCandidate(**values)


def test_safe_strike_reads_production_config():
    assert rules()["entry"]["safe_strike_atr"] == 2.3
    result = SafeStrikeGate(rules()).evaluate(candidate(short_strike=460, atr=10))
    assert result.status == GateStatus.PASS
    assert result.diagnostics["required_atr"] == 2.3


def test_dte_hard_range_is_not_score_override():
    gate = DTEGate(rules())
    assert gate.evaluate(candidate(dte=29)).status == GateStatus.FAIL
    assert gate.evaluate(candidate(dte=30)).status == GateStatus.PASS
    assert gate.evaluate(candidate(dte=45)).status == GateStatus.PASS
    assert gate.evaluate(candidate(dte=46)).status == GateStatus.FAIL


def test_credit_efficiency_is_hard_gate():
    gate = CreditEfficiencyGate(rules())
    assert gate.evaluate(candidate(credit=.49)).status == GateStatus.FAIL
    assert gate.evaluate(candidate(credit=.50)).status == GateStatus.PASS


def test_delta_is_diagnostic_only():
    assert candidate(short_delta=.01).short_delta == .01
