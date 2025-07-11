"""Activity and Sleep Variability Analyzer for Bipolar Episode Prediction.

Based on Ortiz et al. (2025) International Journal of Bipolar Disorders:
- Day-to-day step count variability gives 7 days advance warning for depression
- 12-hour sleep pattern variability achieves 87% accuracy for hypomania detection
- Variability increases are the earliest warning signs

This module calculates multi-timescale variability metrics from wearable data.
"""

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

import numpy as np

from clarity.models.health_data import HealthMetric


@dataclass
class VariabilityResult:
    """Result of variability analysis."""

    # Core metrics
    activity_variability_cv: float  # Coefficient of variation for steps
    sleep_variability_cv: float  # Coefficient of variation for sleep duration

    # Multi-timescale analysis
    variability_12h: dict[str, float]  # 12-hour window metrics
    variability_24h: dict[str, float]  # Daily metrics
    variability_3d: dict[str, float]  # 3-day rolling
    variability_7d: dict[str, float]  # Weekly rolling

    # Trends
    variability_trend: str  # "increasing", "stable", "decreasing"
    spike_detected: bool
    spike_magnitude: float

    # Clinical interpretation
    days_until_risk: int | None  # Based on Ortiz findings
    risk_type: str  # "depression", "hypomania", "none"
    confidence: float


class VariabilityAnalyzer:
    """Analyzes variability in activity and sleep patterns for episode prediction.

    Implements the key findings from Ortiz et al. (2025) showing that
    variability changes precede mood episodes by several days.
    """

    def __init__(self) -> None:
        """Initialize the analyzer."""
        self.logger = logging.getLogger(__name__)

        # Thresholds based on Ortiz et al. findings
        self.DEPRESSION_LEAD_TIME_DAYS = 7  # Activity variability → depression
        self.HYPOMANIA_LEAD_TIME_DAYS = 3  # Sleep variability → hypomania

        # Spike detection thresholds (z-scores)
        self.SPIKE_THRESHOLD_ZSCORE = 2.0  # 2 SD above baseline
        self.HIGH_SPIKE_THRESHOLD = 3.0  # 3 SD for high confidence

        # Constants for magic values
        self.MIN_SERIES_LENGTH = 2
        self.MIN_WEEKLY_DATA_DAYS = 7
        self.HOURS_PER_DAY = 24
        self.MINUTES_TO_HOURS = 60.0
        self.TREND_INCREASE_FACTOR = 1.3
        self.TREND_DECREASE_FACTOR = 0.7
        self.HIGH_VARIABILITY_THRESHOLD = 0.3
        self.MODERATE_VARIABILITY_THRESHOLD = 0.25
        self.UNCERTAIN_TIMING_DAYS = 5  # Average of 3 and 7 days
        self.MIN_BASELINE_DAYS = 14
        self.DATA_CONFIDENCE_WEIGHT = 0.5
        self.HIGH_SPIKE_CONFIDENCE = 1.0
        self.MODERATE_SPIKE_CONFIDENCE = 0.7
        self.LOW_SPIKE_CONFIDENCE = 0.3

    def analyze_variability(
        self,
        activity_metrics: list[HealthMetric],
        sleep_metrics: list[HealthMetric],
        baseline_stats: dict[str, float] | None = None,
    ) -> VariabilityResult:
        """Analyze variability patterns to predict mood episodes.

        Args:
            activity_metrics: Activity data (steps, distance, etc.)
            sleep_metrics: Sleep data (duration, timing, etc.)
            baseline_stats: Historical baseline variability

        Returns:
            VariabilityResult with predictions
        """
        # Extract time series data
        activity_series = self._extract_activity_series(activity_metrics)
        sleep_series = self._extract_sleep_series(sleep_metrics)

        # Calculate basic variability metrics
        activity_cv = self._calculate_coefficient_variation(activity_series)
        sleep_cv = self._calculate_coefficient_variation(sleep_series)

        # Multi-timescale analysis
        var_12h = self._calculate_window_variability(
            activity_series, sleep_series, hours=12
        )
        var_24h = self._calculate_window_variability(
            activity_series, sleep_series, hours=24
        )
        var_3d = self._calculate_window_variability(
            activity_series, sleep_series, hours=72
        )
        var_7d = self._calculate_window_variability(
            activity_series, sleep_series, hours=168
        )

        # Detect spikes and trends
        spike_detected, spike_magnitude = self._detect_variability_spike(
            activity_series, baseline_stats
        )

        trend = self._analyze_trend([var_7d, var_3d, var_24h, var_12h])

        # Clinical interpretation based on Ortiz findings
        days_until_risk, risk_type = self._predict_episode_timing(
            activity_cv, sleep_cv, spike_detected, trend
        )

        # Calculate confidence
        confidence = self._calculate_confidence(
            len(activity_series), len(sleep_series), spike_magnitude
        )

        self.logger.info(
            "Variability analysis completed",
            extra={
                "activity_cv": round(activity_cv, 3),
                "sleep_cv": round(sleep_cv, 3),
                "spike_detected": spike_detected,
                "risk_type": risk_type,
                "days_until_risk": days_until_risk,
                "confidence": round(confidence, 3),
            },
        )

        return VariabilityResult(
            activity_variability_cv=activity_cv,
            sleep_variability_cv=sleep_cv,
            variability_12h=var_12h,
            variability_24h=var_24h,
            variability_3d=var_3d,
            variability_7d=var_7d,
            variability_trend=trend,
            spike_detected=spike_detected,
            spike_magnitude=spike_magnitude,
            days_until_risk=days_until_risk,
            risk_type=risk_type,
            confidence=confidence,
        )

    def _extract_activity_series(self, metrics: list[HealthMetric]) -> list[float]:
        """Extract daily step counts from activity metrics."""
        daily_steps: dict[Any, list[float]] = {}

        for metric in metrics:
            if metric.activity_data and metric.activity_data.steps:
                date = metric.created_at.date()
                if date not in daily_steps:
                    daily_steps[date] = []
                daily_steps[date].append(metric.activity_data.steps)

        # Average multiple readings per day
        series: list[float] = [float(np.mean(daily_steps[date])) for date in sorted(daily_steps.keys())]

        return series

    def _extract_sleep_series(self, metrics: list[HealthMetric]) -> list[float]:
        """Extract sleep duration series from sleep metrics."""
        return [metric.sleep_data.total_sleep_minutes / 60.0 for metric in metrics if metric.sleep_data and metric.sleep_data.total_sleep_minutes]  # Convert to hours

    def _calculate_coefficient_variation(self, series: list[float]) -> float:
        """Calculate coefficient of variation (CV = std/mean)."""
        if len(series) < self.MIN_SERIES_LENGTH:
            return 0.0

        mean_val = np.mean(series)
        if mean_val == 0:
            return 0.0

        return float(np.std(series) / mean_val)

    def _calculate_window_variability(
        self, activity_series: list[float], sleep_series: list[float], hours: int
    ) -> dict[str, float]:
        """Calculate variability metrics for a specific time window."""
        # For this implementation, we'll use daily granularity
        # and approximate windows based on days
        days = hours / 24

        metrics = {}

        # Activity variability
        if len(activity_series) >= days:
            window_activity = activity_series[-int(days) :]
            metrics["activity_cv"] = self._calculate_coefficient_variation(
                window_activity
            )
            metrics["activity_range"] = (
                max(window_activity) - min(window_activity) if window_activity else 0
            )
        else:
            metrics["activity_cv"] = 0.0
            metrics["activity_range"] = 0.0

        # Sleep variability
        if len(sleep_series) >= days:
            window_sleep = sleep_series[-int(days) :]
            metrics["sleep_cv"] = self._calculate_coefficient_variation(window_sleep)
            metrics["sleep_range"] = (
                max(window_sleep) - min(window_sleep) if window_sleep else 0
            )
        else:
            metrics["sleep_cv"] = 0.0
            metrics["sleep_range"] = 0.0

        return metrics

    def _detect_variability_spike(
        self, series: list[float], baseline_stats: dict[str, float] | None
    ) -> tuple[bool, float]:
        """Detect significant spikes in variability using z-score method."""
        if len(series) < self.MIN_WEEKLY_DATA_DAYS:  # Need at least a week of data
            return False, 0.0

        # Calculate rolling standard deviation
        window_size = min(7, len(series) // 2)
        rolling_std = []

        for i in range(window_size, len(series)):
            window = series[i - window_size : i]
            rolling_std.append(np.std(window))

        if not rolling_std:
            return False, 0.0

        # Detect spike in variability
        current_variability = rolling_std[-1]

        if baseline_stats and "activity_variability_baseline" in baseline_stats:
            baseline_var = baseline_stats["activity_variability_baseline"]
        else:
            # Use first half as baseline
            baseline_var = np.mean(rolling_std[: len(rolling_std) // 2])

        if baseline_var == 0:
            return False, 0.0

        # Calculate z-score
        z_score = (current_variability - baseline_var) / baseline_var

        spike_detected = bool(z_score >= self.SPIKE_THRESHOLD_ZSCORE)

        return spike_detected, float(z_score)

    def _analyze_trend(self, variability_windows: list[dict[str, float]]) -> str:
        """Analyze trend across multiple time windows."""
        # Extract activity CV values from each window
        cv_values = [window["activity_cv"] for window in variability_windows if "activity_cv" in window]

        if len(cv_values) < self.MIN_SERIES_LENGTH:
            return "stable"

        # Simple trend detection: compare recent to older
        recent = np.mean(cv_values[:2])  # More recent windows
        older = np.mean(cv_values[2:])  # Older windows

        if recent > older * self.TREND_INCREASE_FACTOR:
            return "increasing"
        if recent < older * self.TREND_DECREASE_FACTOR:
            return "decreasing"
        return "stable"

    def _predict_episode_timing(
        self, activity_cv: float, sleep_cv: float, spike_detected: bool, trend: str
    ) -> tuple[int | None, str]:
        """Predict episode timing based on Ortiz et al. findings."""
        if not spike_detected and trend != "increasing":
            return None, "none"

        # High activity variability → depression (7 days)
        if activity_cv > self.HIGH_VARIABILITY_THRESHOLD and trend == "increasing":
            return self.DEPRESSION_LEAD_TIME_DAYS, "depression"

        # High sleep variability → hypomania (3 days)
        if sleep_cv > self.MODERATE_VARIABILITY_THRESHOLD and spike_detected:
            return self.HYPOMANIA_LEAD_TIME_DAYS, "hypomania"

        # Moderate changes → uncertain timing
        if trend == "increasing":
            return self.UNCERTAIN_TIMING_DAYS, "uncertain"

        return None, "none"

    def _calculate_confidence(
        self, activity_points: int, sleep_points: int, spike_magnitude: float
    ) -> float:
        """Calculate confidence in the prediction."""
        # Base confidence on data availability
        data_confidence = (
            min(activity_points / self.MIN_BASELINE_DAYS, 1.0) * self.DATA_CONFIDENCE_WEIGHT + min(sleep_points / self.MIN_BASELINE_DAYS, 1.0) * self.DATA_CONFIDENCE_WEIGHT
        )

        # Adjust for spike magnitude
        if spike_magnitude > self.HIGH_SPIKE_THRESHOLD:
            spike_confidence = self.HIGH_SPIKE_CONFIDENCE
        elif spike_magnitude > self.SPIKE_THRESHOLD_ZSCORE:
            spike_confidence = self.MODERATE_SPIKE_CONFIDENCE
        else:
            spike_confidence = self.LOW_SPIKE_CONFIDENCE

        return data_confidence * spike_confidence

    def calculate_intraday_variability(
        self, hourly_data: list[tuple[datetime, float]], window_hours: int = 12
    ) -> dict[str, float]:
        """Calculate within-day variability patterns.

        Based on Ortiz finding that 12-hour variability achieved 87% accuracy
        for hypomania detection.
        """
        if len(hourly_data) < window_hours:
            return {"intraday_cv": 0.0, "peak_trough_ratio": 1.0}

        # Group by time windows
        windows = []
        for i in range(0, len(hourly_data) - window_hours, window_hours // 2):
            window_data = [val for _, val in hourly_data[i : i + window_hours]]
            windows.append(window_data)

        # Calculate variability for each window
        window_cvs = []
        for window in windows:
            if len(window) > 1:
                cv = self._calculate_coefficient_variation(window)
                window_cvs.append(cv)

        # Overall metrics
        intraday_cv = float(np.mean(window_cvs)) if window_cvs else 0.0

        # Peak-trough ratio
        all_values = [val for _, val in hourly_data]
        peak_trough_ratio = (
            max(all_values) / min(all_values) if min(all_values) > 0 else 1.0
        )

        return {
            "intraday_cv": intraday_cv,
            "peak_trough_ratio": peak_trough_ratio,
            "max_window_cv": max(window_cvs) if window_cvs else 0.0,
        }
