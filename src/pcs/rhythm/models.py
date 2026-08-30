from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

class Availability(str, Enum):
    AVAILABLE="AVAILABLE"; NOT_AVAILABLE="NOT_AVAILABLE"; STALE="STALE"; INSUFFICIENT_HISTORY="INSUFFICIENT_HISTORY"; UNIVERSE_NOT_AUTHORITATIVE="UNIVERSE_NOT_AUTHORITATIVE"
class AxisState(str, Enum):
    UNKNOWN="UNKNOWN"; ACCELERATING_UP="ACCELERATING_UP"; DECELERATING_UP="DECELERATING_UP"; RANGE="RANGE"; DECELERATING_DOWN="DECELERATING_DOWN"; ACCELERATING_DOWN="ACCELERATING_DOWN"; RECOVERING="RECOVERING"; BROADENING="BROADENING"; BROAD="BROAD"; NARROWING="NARROWING"; NARROW="NARROW"; COMPRESSING="COMPRESSING"; NORMAL="NORMAL"; EXPANDING="EXPANDING"; STRESS="STRESS"; LEADING="LEADING"; SYNCHRONOUS="SYNCHRONOUS"; LAGGING="LAGGING"; DIVERGING="DIVERGING"; STABILIZING="STABILIZING"; SUPPORT_FAILURE="SUPPORT_FAILURE"; BREAKDOWN="BREAKDOWN"; RECOVERY="RECOVERY"
@dataclass(frozen=True)
class MetricObservation:
    name:str; value:float|None; as_of_date:str; source_dataset:str; source_symbols:tuple[str,...]; lookback:int|None; price_basis:str; available_at:str|None; calculation_version:str="rhythm_v1"; availability_status:str=Availability.AVAILABLE.value
    def to_dict(self): return asdict(self)
@dataclass(frozen=True)
class RhythmAxisState:
    axis:str; state:str; metrics:dict[str,Any]=field(default_factory=dict); evidence:tuple[str,...]=(); availability_status:str=Availability.AVAILABLE.value
@dataclass(frozen=True)
class RhythmTransition:
    axis:str; previous_state:str; current_state:str; entered_date:str; duration_trading_days:int; confirmed:bool
@dataclass(frozen=True)
class MarketRhythmSnapshot:
    as_of:str; axes:dict[str,RhythmAxisState]; summary_label:str|None=None
@dataclass(frozen=True)
class SectorRhythmSnapshot:
    symbol:str; snapshot:MarketRhythmSnapshot
@dataclass(frozen=True)
class TickerRhythmSnapshot:
    symbol:str; snapshot:MarketRhythmSnapshot
@dataclass(frozen=True)
class RhythmEvidencePackage:
    module:str; version:str; as_of:str; data_timestamp:str|None; calculation_version:str; run_id:str; request_id:str; market:MarketRhythmSnapshot; tickers:tuple[TickerRhythmSnapshot,...]; sectors:tuple[SectorRhythmSnapshot,...]; readiness:dict[str,Any]
@dataclass(frozen=True)
class RhythmReadiness:
    price_ready:bool; volume_ready:bool; breadth_ready:bool; sector_ready:bool; volatility_ready:bool; options_iv_ready:bool; history_ready:bool; universe_authority:str; production_eligible:str="NO"; status:str="PARTIAL_EVIDENCE"
