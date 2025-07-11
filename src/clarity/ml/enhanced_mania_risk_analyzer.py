"""Enhanced Mania Risk Analyzer - Extended Rule-Based Implementation.

This version adds additional heuristic rules inspired by research findings:
- Circadian phase shift detection (sleep timing changes)
- Activity variability monitoring (day-to-day fluctuations)
- Personal baseline tracking (individual patterns)

IMPORTANT: This is still a RULE-BASED system. We are NOT achieving the
accuracy reported in research papers. Those require labeled training data
and validated ML models, which we do not have.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clarity.ml.circadian_phase_detector import (
    CircadianPhaseDetector,
    CircadianPhaseResult,
)
from clarity.ml.mania_risk_analyzer import (
    ManiaRiskAnalyzer,
    ManiaRiskConfig,
    ManiaRiskResult,
)
from clarity.ml.personal_baseline_tracker import (
    PersonalBaseline,
    PersonalBaselineTracker,
)
from clarity.ml.processors.sleep_processor import SleepFeatures
from clarity.ml.variability_analyzer import VariabilityAnalyzer, VariabilityResult
from clarity.models.health_data import HealthMetric


@dataclass
class EnhancedRiskConfig(ManiaRiskConfig):
    """Enhanced configuration with research-based weights."""

    def __post_init__(self) -> None:
        # Weights based on clinical literature - see weight_rationale.md
        # NOTE: Effect sizes from papers, NOT validated on our data
        self.weights = {
            # Circadian: Wehr 2018 - phase advance in 65% of mania
            "circadian_phase_advance": 0.40,
            # Circadian: Geoffroy 2014 - phase delay in 60% of depression
            "circadian_phase_delay": 0.35,
            # Activity: Ortiz 2021 (approx) - variability 7d before episodes
            "activity_variability_spike": 0.35,
            # Secondary predictors
            "severe_sleep_loss": 0.30,
            "acute_sleep_loss": 0.25,  # Added for compatibility
            "sleep_variability_increase": 0.25,
            "activity_surge": 0.15,
            # Supporting indicators
            "rapid_sleep_onset": 0.10,
            "circadian_disruption": 0.20,
            "sleep_inconsistency": 0.15,
            "activity_fragmentation": 0.15,
            "elevated_hr": 0.05,
            "low_hrv": 0.05,
            # Personal deviation scores
            "sleep_deviation": 0.20,
            "activity_deviation": 0.20,
            "circadian_deviation": 0.25,
        }


class EnhancedManiaRiskAnalyzer(ManiaRiskAnalyzer):
    """Enhanced analyzer with state-of-the-art bipolar monitoring."""

    def __init__(self, config_path: Path | None = None, user_id: str | None = None) -> None:
        """Initialize enhanced analyzer with additional modules."""
        # Initialize base analyzer with enhanced config
        super().__init__(config_path, user_id)

        # Override with enhanced config
        self.config = EnhancedRiskConfig()

        # Initialize new modules
        self.phase_detector = CircadianPhaseDetector()
        self.variability_analyzer = VariabilityAnalyzer()
        self.baseline_tracker = PersonalBaselineTracker()

        # Constants for magic values
        self.MIN_RECENT_DAYS = 2
        self.DEVIATION_THRESHOLD = 2.0  # Standard deviations
        self.CIRCADIAN_ADVANCE_THRESHOLD = -1.0  # hours
        self.HIGH_RISK_SCORE_THRESHOLD = 0.7
        self.MODERATE_RISK_SCORE_THRESHOLD = 0.5
        self.NEXT_DAY_RISK = 1
        self.HIGH_RISK_DAYS = 2
        self.MODERATE_RISK_DAYS = 4

        self.logger.info(
            "Enhanced ManiaRiskAnalyzer initialized with advanced modules",
            extra={
                "modules": [
                    "CircadianPhaseDetector",
                    "VariabilityAnalyzer",
                    "PersonalBaselineTracker",
                ],
                "research_basis": "Lim2024, Ortiz2025, Lipschitz2025",
            },
        )

    def analyze(
        self,
        sleep_features: SleepFeatures | None = None,
        pat_metrics: dict[str, float] | None = None,
        activity_stats: dict[str, Any] | None = None,
        cardio_stats: dict[str, float] | None = None,
        historical_baseline: dict[str, float] | None = None,
        user_id: str | None = None,
        recent_health_metrics: list[HealthMetric] | None = None,
        baseline_health_metrics: list[HealthMetric] | None = None,
    ) -> ManiaRiskResult:
        """Enhanced analysis with circadian phase shifts and variability detection.

        Additional Args:
            recent_health_metrics: Last 7-14 days of raw health data
            baseline_health_metrics: 14-28 days of baseline data
        """
        # Use base analysis as foundation
        base_result = super().analyze(
            sleep_features=sleep_features,
            pat_metrics=pat_metrics,
            activity_stats=activity_stats,
            cardio_stats=cardio_stats,
            historical_baseline=historical_baseline,
            user_id=user_id,
        )

        # If we don't have raw metrics for advanced analysis, return base result
        if not recent_health_metrics:
            return base_result

        # Get user ID
        uid = user_id or self.user_id

        # Update personal baseline if we have baseline data
        personal_baseline = None
        if baseline_health_metrics and uid:
            personal_baseline = self.baseline_tracker.update_baseline(
                uid, baseline_health_metrics
            )
        elif uid:
            personal_baseline = self.baseline_tracker.get_baseline(uid)

        # Perform advanced analyses
        enhanced_score = base_result.risk_score
        enhanced_factors = list(base_result.contributing_factors)
        enhanced_confidence = base_result.confidence

        # 1. Circadian Phase Analysis (Lim et al., 2024)
        phase_result = self._analyze_circadian_phase(
            recent_health_metrics, baseline_health_metrics
        )
        if phase_result:
            phase_score, phase_factors = self._score_phase_shift(phase_result)
            enhanced_score += phase_score
            enhanced_factors.extend(phase_factors)
            enhanced_confidence *= phase_result.confidence

        # 2. Variability Analysis (Ortiz et al., 2025)
        variability_result = self._analyze_variability(
            recent_health_metrics, personal_baseline
        )
        if variability_result:
            var_score, var_factors = self._score_variability(variability_result)
            enhanced_score += var_score
            enhanced_factors.extend(var_factors)
            enhanced_confidence *= variability_result.confidence

        # 3. Personal Deviation Analysis (Lipschitz et al., 2025)
        if personal_baseline and any([sleep_features, pat_metrics, activity_stats]):
            dev_score, dev_factors = self._analyze_personal_deviations(
                sleep_features, pat_metrics, activity_stats, personal_baseline
            )
            enhanced_score += dev_score
            enhanced_factors.extend(dev_factors)

        # Clamp score and determine alert level
        enhanced_score = max(0.0, min(1.0, enhanced_score))
        alert_level = self._determine_alert_level(enhanced_score, enhanced_confidence)

        # Generate time-to-event prediction
        days_until_episode = self._predict_time_to_episode(
            phase_result, variability_result, enhanced_score
        )

        # Enhanced clinical insight
        clinical_insight = self._generate_enhanced_insight(
            alert_level, enhanced_factors, enhanced_score, days_until_episode
        )

        # Enhanced recommendations
        recommendations = self._generate_enhanced_recommendations(
            alert_level, enhanced_factors, phase_result, variability_result
        )

        self.logger.info(
            "Enhanced mania risk analysis completed",
            extra={
                "user_id": self._sanitize_user_id(uid),
                "enhanced_score": round(enhanced_score, 3),
                "base_score": round(base_result.risk_score, 3),
                "alert_level": alert_level,
                "days_until_episode": days_until_episode,
                "phase_shift": (
                    phase_result.phase_shift_direction if phase_result else "none"
                ),
                "variability_trend": (
                    variability_result.variability_trend
                    if variability_result
                    else "none"
                ),
            },
        )

        return ManiaRiskResult(
            risk_score=enhanced_score,
            alert_level=alert_level,
            contributing_factors=enhanced_factors,
            confidence=enhanced_confidence,
            clinical_insight=clinical_insight,
            recommendations=recommendations,
        )

    def _analyze_circadian_phase(
        self,
        recent_metrics: list[HealthMetric],
        baseline_metrics: list[HealthMetric] | None,
    ) -> CircadianPhaseResult | None:
        """Analyze circadian phase shifts using Lim et al. method."""
        try:
            # Filter for sleep metrics
            recent_sleep = [m for m in recent_metrics if m.sleep_data]
            baseline_sleep = (
                [m for m in baseline_metrics if m.sleep_data]
                if baseline_metrics
                else None
            )

            if len(recent_sleep) < self.MIN_RECENT_DAYS:
                return None

            return self.phase_detector.detect_phase_shift(
                recent_sleep, baseline_sleep
            )

        except (ValueError, KeyError, AttributeError) as e:
            self.logger.warning("Circadian phase analysis failed: %s", e)
            return None

    def _analyze_variability(
        self,
        recent_metrics: list[HealthMetric],
        personal_baseline: PersonalBaseline | None,
    ) -> VariabilityResult | None:
        """Analyze activity/sleep variability using Ortiz et al. method."""
        try:
            # Separate activity and sleep metrics
            activity_metrics = [m for m in recent_metrics if m.activity_data]
            sleep_metrics = [m for m in recent_metrics if m.sleep_data]

            if not activity_metrics and not sleep_metrics:
                return None

            # Convert baseline to dict format
            baseline_stats = None
            if personal_baseline:
                baseline_stats = {
                    "activity_variability_baseline": personal_baseline.activity_variability_baseline,
                    "sleep_variability_baseline": personal_baseline.sleep_variability_baseline,
                }

            return self.variability_analyzer.analyze_variability(
                activity_metrics, sleep_metrics, baseline_stats
            )

        except (ValueError, KeyError, AttributeError) as e:
            self.logger.warning("Variability analysis failed: %s", e)
            return None

    def _score_phase_shift(
        self, phase_result: CircadianPhaseResult
    ) -> tuple[float, list[str]]:
        """Score circadian phase shift based on Lim et al. findings."""
        score = 0.0
        factors = []

        if phase_result.clinical_significance in {"high", "moderate"}:
            if phase_result.phase_shift_direction == "advance":
                # Phase advance predicts mania (AUC 0.98)
                score = self.config.weights["circadian_phase_advance"]
                factors.append(
                    f"Circadian phase advance: {abs(phase_result.phase_shift_hours):.1f}h earlier"
                )
            elif phase_result.phase_shift_direction == "delay":
                # Phase delay predicts depression (note: we're in mania analyzer)
                # Still relevant as it reduces mania risk
                score = -self.config.weights["circadian_phase_delay"] * 0.5
                factors.append(
                    f"Circadian phase delay: {abs(phase_result.phase_shift_hours):.1f}h later (protective)"
                )

        return score, factors

    def _score_variability(
        self, var_result: VariabilityResult
    ) -> tuple[float, list[str]]:
        """Score variability patterns based on Ortiz et al. findings."""
        score = 0.0
        factors = []

        if var_result.spike_detected:
            score = self.config.weights["activity_variability_spike"]

            if var_result.risk_type == "hypomania":
                factors.append(
                    f"Sleep variability spike detected ({var_result.days_until_risk} days warning)"
                )
            elif var_result.risk_type == "depression":
                # Activity variability predicts depression
                # For mania analyzer, this is protective
                score *= -0.3
                factors.append(
                    "Activity variability increase (suggests depression risk, not mania)"
                )

        if var_result.variability_trend == "increasing":
            score += self.config.weights["sleep_variability_increase"] * 0.5
            factors.append("Increasing behavioral variability")

        return score, factors

    def _analyze_personal_deviations(
        self,
        sleep_features: SleepFeatures | None,
        pat_metrics: dict[str, float] | None,
        activity_stats: dict[str, Any] | None,
        baseline: PersonalBaseline,
    ) -> tuple[float, list[str]]:
        """Analyze deviations from personal baseline."""
        score = 0.0
        factors = []

        # Prepare current metrics
        current_metrics = {}

        if sleep_features:
            current_metrics["sleep_hours"] = sleep_features.total_sleep_minutes / 60
        elif pat_metrics and "total_sleep_time" in pat_metrics:
            current_metrics["sleep_hours"] = pat_metrics["total_sleep_time"]

        if activity_stats and "avg_daily_steps" in activity_stats:
            current_metrics["daily_steps"] = activity_stats["avg_daily_steps"]

        # Calculate deviations
        deviations = self.baseline_tracker.calculate_deviation_scores(
            baseline.user_id, current_metrics
        )

        # Score deviations
        if "sleep_duration_z" in deviations:
            z = deviations["sleep_duration_z"]
            if z < -self.DEVIATION_THRESHOLD:  # Significantly less sleep than personal norm
                score += self.config.weights["sleep_deviation"]
                factors.append(f"Sleep {abs(z):.1f} SD below personal baseline")

        if "activity_z" in deviations:
            z = deviations["activity_z"]
            if z > self.DEVIATION_THRESHOLD:  # Significantly more active than personal norm
                score += self.config.weights["activity_deviation"]
                factors.append(f"Activity {z:.1f} SD above personal baseline")

        if "circadian_shift_hours" in deviations:
            shift = deviations["circadian_shift_hours"]
            if shift < self.CIRCADIAN_ADVANCE_THRESHOLD:  # Phase advance from personal norm
                score += self.config.weights["circadian_deviation"]
                factors.append(f"Sleep timing {abs(shift):.1f}h earlier than usual")

        return score, factors

    def _predict_time_to_episode(
        self,
        phase_result: CircadianPhaseResult | None,
        var_result: VariabilityResult | None,
        risk_score: float,
    ) -> int | None:
        """Predict days until potential episode based on research."""
        predictions = []

        # Phase shifts give immediate risk (next day per Lim)
        if phase_result and phase_result.clinical_significance == "high" and phase_result.phase_shift_direction == "advance":
            predictions.append(self.NEXT_DAY_RISK)  # Next day risk

        # Variability gives advance warning (Ortiz)
        if var_result and var_result.days_until_risk:
            predictions.append(var_result.days_until_risk)

        # High risk score suggests imminent risk
        if risk_score > self.HIGH_RISK_SCORE_THRESHOLD:
            predictions.append(self.HIGH_RISK_DAYS)
        elif risk_score > self.MODERATE_RISK_SCORE_THRESHOLD:
            predictions.append(self.MODERATE_RISK_DAYS)

        return min(predictions) if predictions else None

    def _generate_enhanced_insight(
        self, level: str, factors: list[str], score: float, days_until: int | None
    ) -> str:
        """Generate enhanced clinical insight with timing prediction."""
        if level == "high":
            timing = f" Risk window: {days_until} days." if days_until else ""
            return (
                f"High mania risk detected (score: {score:.2f}).{timing} "
                f"Primary indicators: {', '.join(factors[:2])}. "
                "Immediate consultation with healthcare provider recommended."
            )
        if level == "moderate":
            timing = f" Monitor closely for {days_until} days." if days_until else ""
            return (
                f"Moderate mania warning signs (score: {score:.2f}).{timing} "
                "Early intervention may prevent episode escalation."
            )
        return super()._generate_clinical_insight(level, factors, score)

    def _generate_enhanced_recommendations(
        self,
        level: str,
        factors: list[str],
        phase_result: CircadianPhaseResult | None,
        var_result: VariabilityResult | None,
    ) -> list[str]:
        """Generate enhanced recommendations based on specific patterns."""
        recommendations: list[str] = []

        if level in {"high", "moderate"}:
            # Immediate actions for high risk
            if level == "high":
                recommendations.extend(("Contact healthcare provider within 24 hours", "Consider activating your crisis plan"))

            # Phase-specific recommendations
            if phase_result and phase_result.phase_shift_direction == "advance":
                recommendations.extend(("Delay bedtime by 30 minutes tonight", "Avoid morning bright light exposure", "Consider evening light therapy (consult provider)"))

            # Variability-specific recommendations
            if var_result and var_result.spike_detected:
                recommendations.extend(("Maintain strict daily routine", "Set consistent sleep/wake alarms", "Limit stimulating activities"))

            # Sleep-specific
            if any("sleep" in f.lower() for f in factors):
                recommendations.extend(("Prioritize 7-8 hours sleep tonight", "Avoid caffeine after 2 PM", "Create calming bedtime routine"))

            # General high-risk
            if level == "high":
                recommendations.extend(("Avoid major decisions for 48 hours", "Inform a trusted person of your risk status"))

        return recommendations
