import math

from pcs.data.schemas import ExpectedMoveResult


def calculate_expected_move(
    spot: float,
    short_strike: float,
    atr: float | None = None,
    realized_vol: float | None = None,
    iv: float | None = None,
    dte: int | None = None,
) -> ExpectedMoveResult:
    one_day_candidates = []
    if atr is not None:
        one_day_candidates.append(float(atr))
    if realized_vol is not None:
        one_day_candidates.append(float(spot) * float(realized_vol) / math.sqrt(252))
    if iv is not None:
        one_day_candidates.append(float(spot) * float(iv) / math.sqrt(252))
    if not one_day_candidates:
        raise ValueError("expected move requires ATR, realized_vol, or IV")
    expected_1d = max(one_day_candidates)
    expected_3d = expected_1d * math.sqrt(3)
    expected_5d = expected_1d * math.sqrt(5)
    expiration_move = expected_1d * math.sqrt(dte) if dte else None
    distance = spot - short_strike
    return ExpectedMoveResult(
        distance_to_short_strike=distance,
        expected_move_1d=expected_1d,
        expected_move_3d=expected_3d,
        expected_move_5d=expected_5d,
        expiration_expected_move=expiration_move,
        buffer_ratio=distance / expected_5d if expected_5d else 0,
    )
