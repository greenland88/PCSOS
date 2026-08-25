"""Deterministic research-only same-ticker admission state."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date

@dataclass(frozen=True)
class Position:
    candidate_id: str
    exit_date: date | None

class SameTickerState:
    def __init__(self): self._open: list[Position] = []
    def release(self, today: date) -> None:
        self._open = [p for p in self._open if p.exit_date is None or p.exit_date >= today]
    def count(self) -> int: return len(self._open)
    def ids(self) -> tuple[str, ...]: return tuple(p.candidate_id for p in self._open)
    def decide(self, candidate_id: str, exit_date: date | None) -> str:
        if self.count() > 0: return "REJECT_SAME_TICKER_ALREADY_OPEN"
        self._open.append(Position(candidate_id, exit_date)); return "OPEN"
