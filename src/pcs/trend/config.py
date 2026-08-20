from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendIndicatorConfig:
    sma_short_period: int = 20
    sma_medium_period: int = 50
    sma_long_period: int = 200
    atr_period: int = 14
    adx_period: int = 14
    rsi_period: int = 14
    slope_strong_threshold: float = 0.02
    slope_rising_threshold: float = 0.005
    slope_flat_threshold: float = 0.005
    pivot_left_bars: int = 3
    pivot_right_bars: int = 3
    minimum_swing_price_change_pct: float = 0.0
    rs_strong_threshold: float = 0.05
    rs_improving_threshold: float = 0.02
    rs_weakening_threshold: float = -0.02
    rs_weak_threshold: float = -0.05
    rs_benchmark_stable_threshold: float = -0.005
    rs_stock_weak_return_threshold: float = -0.02
    cleanliness_lookback_days: int = 60
    cleanliness_gap_threshold: float = 0.02
    cleanliness_large_move_atr_multiple: float = 1.5
    cleanliness_extreme_move_atr_multiple: float = 2.0
    cleanliness_clean_max_crossings: int = 1
    cleanliness_noisy_min_crossings: int = 4
    cleanliness_chaotic_min_crossings: int = 8
    cleanliness_clean_max_large_move_ratio: float = 0.10
    cleanliness_clean_max_extreme_move_ratio: float = 0.05
    cleanliness_noisy_min_large_move_ratio: float = 0.25
    cleanliness_chaotic_min_extreme_move_ratio: float = 0.15
    cleanliness_clean_max_gap_ratio: float = 0.05
    cleanliness_noisy_min_gap_ratio: float = 0.15
    cleanliness_chaotic_min_gap_ratio: float = 0.30
    cleanliness_clean_max_slope_changes: int = 4
    cleanliness_noisy_min_slope_changes: int = 20
    cleanliness_chaotic_min_slope_changes: int = 30
    cleanliness_atr_pct_elevated_threshold: float = 0.04
    cleanliness_atr_pct_severe_threshold: float = 0.06
    cleanliness_large_move_elevated_threshold: float = 0.10
    cleanliness_large_move_severe_threshold: float = 0.25
    cleanliness_extreme_move_elevated_threshold: float = 0.05
    cleanliness_extreme_move_severe_threshold: float = 0.15
    cleanliness_gap_elevated_threshold: float = 0.20
    cleanliness_gap_severe_threshold: float = 0.30
    pullback_recent_high_lookback: int = 20
    pullback_no_pullback_max_pct: float = 0.01
    pullback_shallow_max_pct: float = 0.05
    pullback_healthy_min_pct: float = 0.05
    pullback_healthy_max_pct: float = 0.15
    pullback_sma20_near_atr: float = 1.5
    pullback_sma50_near_atr: float = 2.0
    pullback_extended_above_sma20_atr: float = 3.0
    pullback_extended_above_sma50_atr: float = 4.0
    pullback_breakdown_below_sma50_atr: float = 1.0
    support_nearby_atr: float = 1.5
    support_nearby_pct: float = 0.03
    support_cluster_tolerance_atr: float = 0.25
    trend_scoring_market_structure_weight: float = 30.0
    trend_scoring_ma_structure_weight: float = 25.0
    trend_scoring_relative_strength_weight: float = 20.0
    trend_scoring_cleanliness_weight: float = 15.0
    trend_scoring_setup_context_weight: float = 10.0
    trend_score_market_bullish: float = 100.0
    trend_score_market_neutral: float = 50.0
    trend_score_market_deteriorating: float = 30.0
    trend_score_market_bearish: float = 0.0
    trend_score_ma_bullish: float = 100.0
    trend_score_ma_mostly_bullish: float = 80.0
    trend_score_ma_mixed: float = 55.0
    trend_score_ma_mostly_bearish: float = 25.0
    trend_score_ma_bearish: float = 0.0
    trend_score_slope_rising: float = 100.0
    trend_score_slope_flat: float = 70.0
    trend_score_slope_falling: float = 40.0
    trend_score_slope_unknown: float = 50.0
    trend_score_rs_strong: float = 100.0
    trend_score_rs_improving: float = 85.0
    trend_score_rs_stable: float = 70.0
    trend_score_rs_weakening: float = 45.0
    trend_score_rs_weak: float = 20.0
    trend_score_clean: float = 100.0
    trend_score_acceptable: float = 80.0
    trend_score_noisy: float = 60.0
    trend_score_chaotic: float = 30.0
    trend_score_pullback_no_pullback: float = 70.0
    trend_score_pullback_shallow: float = 70.0
    trend_score_pullback_healthy: float = 100.0
    trend_score_pullback_unstable: float = 35.0
    trend_score_pullback_extended: float = 45.0
    trend_score_pullback_breakdown: float = 0.0
    trend_score_support_strong: float = 100.0
    trend_score_support_moderate: float = 80.0
    trend_score_support_weak: float = 55.0
    trend_score_support_none: float = 30.0
    strike_normal_move_3d_multiplier: float = 1.7320508075688772
    strike_normal_move_5d_multiplier: float = 2.23606797749979
    strike_min_buffer_atr_pass: float = 2.0
    strike_min_buffer_atr_marginal: float = 1.5
    strike_min_3d_coverage_pass: float = 1.0
    strike_min_3d_coverage_marginal: float = 0.8
    strike_min_5d_coverage_pass: float = 1.0
    strike_support_buffer_atr_pass: float = 0.5
    strike_support_buffer_atr_marginal: float = 0.0

    def validate(self) -> None:
        periods = {
            "sma_short_period": self.sma_short_period,
            "sma_medium_period": self.sma_medium_period,
            "sma_long_period": self.sma_long_period,
            "atr_period": self.atr_period,
            "adx_period": self.adx_period,
            "rsi_period": self.rsi_period,
        }
        invalid = [name for name, value in periods.items() if not isinstance(value, int) or value <= 0]
        if invalid:
            raise ValueError(f"indicator periods must be positive integers: {', '.join(invalid)}")

        thresholds = {
            "slope_strong_threshold": self.slope_strong_threshold,
            "slope_rising_threshold": self.slope_rising_threshold,
            "slope_flat_threshold": self.slope_flat_threshold,
        }
        invalid_thresholds = [
            name for name, value in thresholds.items()
            if not isinstance(value, (int, float)) or value < 0
        ]
        if invalid_thresholds:
            raise ValueError(f"slope thresholds must be non-negative numbers: {', '.join(invalid_thresholds)}")
        if self.slope_strong_threshold < self.slope_rising_threshold:
            raise ValueError("slope_strong_threshold must be >= slope_rising_threshold")

        pivot_periods = {
            "pivot_left_bars": self.pivot_left_bars,
            "pivot_right_bars": self.pivot_right_bars,
        }
        invalid_pivots = [name for name, value in pivot_periods.items() if not isinstance(value, int) or value <= 0]
        if invalid_pivots:
            raise ValueError(f"pivot bars must be positive integers: {', '.join(invalid_pivots)}")
        if not isinstance(self.minimum_swing_price_change_pct, (int, float)) or self.minimum_swing_price_change_pct < 0:
            raise ValueError("minimum_swing_price_change_pct must be a non-negative number")
        rs_thresholds = {
            "rs_strong_threshold": self.rs_strong_threshold,
            "rs_improving_threshold": self.rs_improving_threshold,
            "rs_weakening_threshold": self.rs_weakening_threshold,
            "rs_weak_threshold": self.rs_weak_threshold,
            "rs_benchmark_stable_threshold": self.rs_benchmark_stable_threshold,
            "rs_stock_weak_return_threshold": self.rs_stock_weak_return_threshold,
        }
        if any(not isinstance(value, (int, float)) for value in rs_thresholds.values()):
            raise ValueError("relative strength thresholds must be numbers")
        if not (
            self.rs_strong_threshold > self.rs_improving_threshold > 0
            and self.rs_weakening_threshold > self.rs_weak_threshold
            and self.rs_weak_threshold < 0
        ):
            raise ValueError("relative strength thresholds are inconsistent")
        cleanliness_ints = {
            "cleanliness_lookback_days": self.cleanliness_lookback_days,
            "cleanliness_clean_max_crossings": self.cleanliness_clean_max_crossings,
            "cleanliness_noisy_min_crossings": self.cleanliness_noisy_min_crossings,
            "cleanliness_chaotic_min_crossings": self.cleanliness_chaotic_min_crossings,
            "cleanliness_clean_max_slope_changes": self.cleanliness_clean_max_slope_changes,
            "cleanliness_noisy_min_slope_changes": self.cleanliness_noisy_min_slope_changes,
            "cleanliness_chaotic_min_slope_changes": self.cleanliness_chaotic_min_slope_changes,
        }
        if any(not isinstance(value, int) or value <= 0 for value in cleanliness_ints.values()):
            raise ValueError("cleanliness integer settings must be positive integers")
        cleanliness_numbers = {
            "cleanliness_gap_threshold": self.cleanliness_gap_threshold,
            "cleanliness_large_move_atr_multiple": self.cleanliness_large_move_atr_multiple,
            "cleanliness_extreme_move_atr_multiple": self.cleanliness_extreme_move_atr_multiple,
            "cleanliness_clean_max_large_move_ratio": self.cleanliness_clean_max_large_move_ratio,
            "cleanliness_clean_max_extreme_move_ratio": self.cleanliness_clean_max_extreme_move_ratio,
            "cleanliness_noisy_min_large_move_ratio": self.cleanliness_noisy_min_large_move_ratio,
            "cleanliness_chaotic_min_extreme_move_ratio": self.cleanliness_chaotic_min_extreme_move_ratio,
            "cleanliness_atr_pct_elevated_threshold": self.cleanliness_atr_pct_elevated_threshold,
            "cleanliness_atr_pct_severe_threshold": self.cleanliness_atr_pct_severe_threshold,
            "cleanliness_large_move_elevated_threshold": self.cleanliness_large_move_elevated_threshold,
            "cleanliness_large_move_severe_threshold": self.cleanliness_large_move_severe_threshold,
            "cleanliness_extreme_move_elevated_threshold": self.cleanliness_extreme_move_elevated_threshold,
            "cleanliness_extreme_move_severe_threshold": self.cleanliness_extreme_move_severe_threshold,
            "cleanliness_gap_elevated_threshold": self.cleanliness_gap_elevated_threshold,
            "cleanliness_gap_severe_threshold": self.cleanliness_gap_severe_threshold,
            "cleanliness_clean_max_gap_ratio": self.cleanliness_clean_max_gap_ratio,
            "cleanliness_noisy_min_gap_ratio": self.cleanliness_noisy_min_gap_ratio,
            "cleanliness_chaotic_min_gap_ratio": self.cleanliness_chaotic_min_gap_ratio,
        }
        if any(not isinstance(value, (int, float)) or value < 0 for value in cleanliness_numbers.values()):
            raise ValueError("cleanliness thresholds must be non-negative numbers")
        pullback_ints = {"pullback_recent_high_lookback": self.pullback_recent_high_lookback}
        if any(not isinstance(value, int) or value <= 0 for value in pullback_ints.values()):
            raise ValueError("pullback lookback must be a positive integer")
        pullback_numbers = {
            "pullback_no_pullback_max_pct": self.pullback_no_pullback_max_pct,
            "pullback_shallow_max_pct": self.pullback_shallow_max_pct,
            "pullback_healthy_min_pct": self.pullback_healthy_min_pct,
            "pullback_healthy_max_pct": self.pullback_healthy_max_pct,
            "pullback_sma20_near_atr": self.pullback_sma20_near_atr,
            "pullback_sma50_near_atr": self.pullback_sma50_near_atr,
            "pullback_extended_above_sma20_atr": self.pullback_extended_above_sma20_atr,
            "pullback_extended_above_sma50_atr": self.pullback_extended_above_sma50_atr,
            "pullback_breakdown_below_sma50_atr": self.pullback_breakdown_below_sma50_atr,
        }
        if any(not isinstance(value, (int, float)) or value < 0 for value in pullback_numbers.values()):
            raise ValueError("pullback thresholds must be non-negative numbers")
        if not (
            self.pullback_no_pullback_max_pct <= self.pullback_shallow_max_pct
            and self.pullback_healthy_min_pct <= self.pullback_healthy_max_pct
        ):
            raise ValueError("pullback percentage thresholds are inconsistent")
        support_numbers = {
            "support_nearby_atr": self.support_nearby_atr,
            "support_nearby_pct": self.support_nearby_pct,
            "support_cluster_tolerance_atr": self.support_cluster_tolerance_atr,
        }
        if any(not isinstance(value, (int, float)) or value < 0 for value in support_numbers.values()):
            raise ValueError("support thresholds must be non-negative numbers")
        weights = [
            self.trend_scoring_market_structure_weight,
            self.trend_scoring_ma_structure_weight,
            self.trend_scoring_relative_strength_weight,
            self.trend_scoring_cleanliness_weight,
            self.trend_scoring_setup_context_weight,
        ]
        if any(not isinstance(value, (int, float)) or value < 0 for value in weights) or sum(weights) != 100:
            raise ValueError("trend scoring weights must be non-negative and sum to 100")
        strike_numbers = {
            "strike_normal_move_3d_multiplier": self.strike_normal_move_3d_multiplier,
            "strike_normal_move_5d_multiplier": self.strike_normal_move_5d_multiplier,
            "strike_min_buffer_atr_pass": self.strike_min_buffer_atr_pass,
            "strike_min_buffer_atr_marginal": self.strike_min_buffer_atr_marginal,
            "strike_min_3d_coverage_pass": self.strike_min_3d_coverage_pass,
            "strike_min_3d_coverage_marginal": self.strike_min_3d_coverage_marginal,
            "strike_min_5d_coverage_pass": self.strike_min_5d_coverage_pass,
            "strike_support_buffer_atr_pass": self.strike_support_buffer_atr_pass,
            "strike_support_buffer_atr_marginal": self.strike_support_buffer_atr_marginal,
        }
        if any(not isinstance(value, (int, float)) or value < 0 for value in strike_numbers.values()):
            raise ValueError("strike gate thresholds must be non-negative numbers")
        if self.strike_min_buffer_atr_pass < self.strike_min_buffer_atr_marginal:
            raise ValueError("strike buffer thresholds are inconsistent")

    @property
    def minimum_rows(self) -> int:
        return max(
            self.sma_short_period,
            self.sma_medium_period,
            self.sma_long_period,
            self.atr_period,
            self.adx_period,
            self.rsi_period,
        )
