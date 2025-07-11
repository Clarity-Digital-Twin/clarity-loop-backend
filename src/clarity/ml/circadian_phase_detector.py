"""Circadian Phase Shift Detector for Bipolar Episode Prediction.

Based on Lim et al. (2024) Nature Digital Medicine research showing that
circadian phase shifts are the strongest predictors of mood episodes:
- Phase advances (earlier sleep) → predict mania (AUC 0.98)
- Phase delays (later sleep) → predict depression (AUC 0.80)

This module detects these critical phase shifts from sleep timing data.
"""

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

import numpy as np

from clarity.models.health_data import HealthMetric


@dataclass
class CircadianPhaseResult:
    """Result of circadian phase analysis."""

    phase_shift_hours: float  # Positive = advance (earlier), Negative = delay (later)
    phase_shift_direction: str  # "advance", "delay", or "stable"
    confidence: float  # 0-1 confidence in detection
    baseline_midpoint: float | None  # Baseline sleep midpoint (hours from midnight)
    current_midpoint: float | None  # Current sleep midpoint
    trend_days: int  # Number of days used in trend analysis
    clinical_significance: str  # "high", "moderate", "low", "none"


class CircadianPhaseDetector:
    """Detects circadian phase shifts that predict mood episodes.

    Implements the key finding from Lim et al. (2024) that daily circadian
    phase shifts are the strongest predictors of next-day mood episodes.
    """

    # Minimum data requirements
    MIN_RECENT_DAYS = 2
    MIN_BASELINE_DAYS = 5
    MIN_VARIANCE_DAYS = 3

    # Phase shift thresholds (hours)
    PHASE_SHIFT_STABLE_THRESHOLD = 0.5
    PHASE_SHIFT_WRAPAROUND_THRESHOLD = 12

    # Clinical significance thresholds
    CLINICAL_SIGNIFICANCE_MIN_CONFIDENCE = 0.5
    CLINICAL_SIGNIFICANCE_HIGH_THRESHOLD = 0.75
    CLINICAL_SIGNIFICANCE_MODERATE_THRESHOLD = 0.5

    # Variability trend thresholds
    VARIABILITY_TREND_MIN_DAYS = 3
    VARIABILITY_TREND_INCREASE_FACTOR = 1.5
    VARIABILITY_TREND_DECREASE_FACTOR = 0.7

    def __init__(self) -> None:
        """Initialize the detector."""
        self.logger = logging.getLogger(__name__)

        # Thresholds based on literature
        self.SIGNIFICANT_ADVANCE_HOURS = 1.0  # 1+ hour advance predicts mania
        self.SIGNIFICANT_DELAY_HOURS = 1.5  # 1.5+ hour delay predicts depression
        self.MIN_CONFIDENCE_DAYS = 3  # Need 3+ days for reliable detection

    def detect_phase_shift(
        self,
        recent_sleep_metrics: list[HealthMetric],
        baseline_sleep_metrics: list[HealthMetric] | None = None,
    ) -> CircadianPhaseResult:
        """Detect circadian phase shifts from sleep data.

        Args:
            recent_sleep_metrics: Last 3-7 days of sleep data
            baseline_sleep_metrics: Historical baseline (14-28 days)

        Returns:
            CircadianPhaseResult with phase shift detection
        """
        # Extract sleep timing data
        recent_midpoints = self._extract_sleep_midpoints(recent_sleep_metrics)

        if len(recent_midpoints) < self.MIN_RECENT_DAYS:
            return CircadianPhaseResult(
                phase_shift_hours=0.0,
                phase_shift_direction="stable",
                confidence=0.0,
                baseline_midpoint=None,
                current_midpoint=None,
                trend_days=len(recent_midpoints),
                clinical_significance="none",
            )

        # Calculate baseline midpoint
        baseline_midpoint: float | None = None
        if baseline_sleep_metrics:
            baseline_midpoints = self._extract_sleep_midpoints(baseline_sleep_metrics)
            if baseline_midpoints:
                baseline_midpoint = float(np.median(baseline_midpoints))
        # Use first half of recent data as baseline
        elif len(recent_midpoints) >= self.MIN_BASELINE_DAYS:
            baseline_midpoint = float(np.median(
                recent_midpoints[: len(recent_midpoints) // 2]
            ))
        else:
            baseline_midpoint = float(recent_midpoints[0])

        # Calculate current midpoint (last 3 days)
        current_midpoint = float(np.median(recent_midpoints[-3:]))

        # Calculate phase shift
        if baseline_midpoint is None:
            baseline_midpoint = current_midpoint
        phase_shift_hours = current_midpoint - baseline_midpoint

        # Handle circadian wraparound (e.g., 23:00 to 01:00)
        if phase_shift_hours > self.PHASE_SHIFT_WRAPAROUND_THRESHOLD:
            phase_shift_hours -= 24
        elif phase_shift_hours < -self.PHASE_SHIFT_WRAPAROUND_THRESHOLD:
            phase_shift_hours += 24

        # Determine direction
        if phase_shift_hours > self.PHASE_SHIFT_STABLE_THRESHOLD:
            direction = "delay"  # Later sleep
        elif phase_shift_hours < -self.PHASE_SHIFT_STABLE_THRESHOLD:
            direction = "advance"  # Earlier sleep
        else:
            direction = "stable"

        # Calculate confidence based on data consistency
        confidence = min(len(recent_midpoints) / self.MIN_CONFIDENCE_DAYS, 1.0)

        # Determine clinical significance
        clinical_significance = self._assess_clinical_significance(
            phase_shift_hours, direction, confidence
        )

        self.logger.info(
            "Circadian phase analysis completed",
            extra={
                "phase_shift_hours": round(phase_shift_hours, 2),
                "direction": direction,
                "confidence": round(confidence, 2),
                "clinical_significance": clinical_significance,
                "data_days": len(recent_midpoints),
            },
        )

        return CircadianPhaseResult(
            phase_shift_hours=phase_shift_hours,
            phase_shift_direction=direction,
            confidence=confidence,
            baseline_midpoint=baseline_midpoint,
            current_midpoint=current_midpoint,
            trend_days=len(recent_midpoints),
            clinical_significance=clinical_significance,
        )

    def _extract_sleep_midpoints(
        self, sleep_metrics: list[HealthMetric]
    ) -> list[float]:
        """Extract sleep midpoints from metrics.

        Returns:
            List of sleep midpoints (hours from midnight, 0-24)
        """
        midpoints = []

        for metric in sleep_metrics:
            if (
                metric.sleep_data
                and metric.sleep_data.sleep_start
                and metric.sleep_data.sleep_end
            ):
                sleep_data = metric.sleep_data

                # Calculate midpoint
                sleep_start = sleep_data.sleep_start
                sleep_end = sleep_data.sleep_end

                # Handle sleep crossing midnight
                if sleep_end < sleep_start:
                    # Add 24 hours to end time
                    duration = (
                        sleep_end + timedelta(days=1) - sleep_start
                    ).total_seconds() / 3600
                else:
                    duration = (sleep_end - sleep_start).total_seconds() / 3600

                # Calculate midpoint as hours from midnight
                start_hour = sleep_start.hour + sleep_start.minute / 60
                midpoint_hour = (start_hour + duration / 2) % 24

                midpoints.append(midpoint_hour)

        return midpoints

    def _assess_clinical_significance(
        self, phase_shift_hours: float, direction: str, confidence: float
    ) -> str:
        """Assess clinical significance of phase shift.

        Based on Lim et al. (2024) findings on phase shift magnitudes.
        """
        if confidence < self.CLINICAL_SIGNIFICANCE_MIN_CONFIDENCE:
            return "none"  # Not enough data

        abs_shift = abs(phase_shift_hours)

        if direction == "advance" and abs_shift >= self.SIGNIFICANT_ADVANCE_HOURS:
            return "high"  # Strong predictor of mania
        if direction == "delay" and abs_shift >= self.SIGNIFICANT_DELAY_HOURS:
            return "high"  # Strong predictor of depression
        if abs_shift >= self.CLINICAL_SIGNIFICANCE_HIGH_THRESHOLD:
            return "moderate"
        if abs_shift >= self.CLINICAL_SIGNIFICANCE_MODERATE_THRESHOLD:
            return "low"
        return "none"

    def calculate_phase_variability(
        self, sleep_metrics: list[HealthMetric], window_hours: int = 12
    ) -> dict[str, Any]:
        """Calculate sleep phase variability metrics.

        Based on Ortiz et al. (2025) finding that 12-hour sleep pattern
        variability achieved 87% accuracy in predicting hypomania.

        Args:
            sleep_metrics: Sleep data for analysis
            window_hours: Window size for variability calculation (default 12)

        Returns:
            Dictionary with variability metrics
        """
        midpoints = self._extract_sleep_midpoints(sleep_metrics)

        if len(midpoints) < self.MIN_VARIANCE_DAYS:
            return {
                "phase_variability": 0.0,
                "max_phase_change": 0.0,
                "variability_trend": "stable",
            }

        # Calculate rolling variability
        phase_changes = []
        for i in range(1, len(midpoints)):
            change = midpoints[i] - midpoints[i - 1]
            # Handle wraparound
            if change > self.PHASE_SHIFT_WRAPAROUND_THRESHOLD:
                change -= 24
            elif change < -self.PHASE_SHIFT_WRAPAROUND_THRESHOLD:
                change += 24
            phase_changes.append(abs(change))

        # Calculate metrics
        phase_variability = float(np.std(phase_changes)) if phase_changes else 0.0
        max_phase_change = float(max(phase_changes)) if phase_changes else 0.0

        # Determine trend
        if len(phase_changes) >= self.VARIABILITY_TREND_MIN_DAYS:
            recent_var = np.std(phase_changes[-self.VARIABILITY_TREND_MIN_DAYS:])
            older_var = (
                np.std(phase_changes[:-self.VARIABILITY_TREND_MIN_DAYS])
                if len(phase_changes) > self.VARIABILITY_TREND_MIN_DAYS
                else recent_var
            )

            if recent_var > older_var * self.VARIABILITY_TREND_INCREASE_FACTOR:
                trend = "increasing"
            elif recent_var < older_var * self.VARIABILITY_TREND_DECREASE_FACTOR:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "stable"

        return {
            "phase_variability": phase_variability,
            "max_phase_change": max_phase_change,
            "variability_trend": trend,
        }
