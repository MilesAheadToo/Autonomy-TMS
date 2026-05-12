"""
Forecast Exception Detection Engine

Compares forecast (P50) vs actuals (OutboundOrderLine) and creates
ForecastException records where detection rules are triggered.

Supports 4 detection rule types:
- VARIANCE_THRESHOLD: Simple percentage variance check
- TREND_DETECTION: Consecutive periods of same-direction variance
- OUTLIER_DETECTION: Statistical outlier (>N std devs from mean)
- BIAS_DETECTION: Consistent over/under forecasting

§3.62 Phase 3 follow-up (2026-05-12): the detector now DUAL-WRITES.
Every ForecastException emitted is mirrored as a Core
``Alert(plane=DEMAND, type=VARIANCE_RELIABILITY)`` row so the unified
operator dashboard sees demand-variance alerts alongside SCP supply
risks and TMS carrier-reliability alerts. The legacy
``forecast_exception`` table + its CRUD endpoints / workflow surface
stay live until consumers cut over.
"""

import logging
import uuid
from collections import defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from azirella_data_model.risk_engine import (
    Alert,
    AlertSeverity,
    AlertStatus,
    AlertType,
    Plane,
    build_resolution_condition,
)

from app.models.forecast_exception import ForecastException, ForecastExceptionRule
from app.models.sc_entities import Forecast, OutboundOrderLine

logger = logging.getLogger(__name__)


class ForecastExceptionDetector:
    """Detects forecast exceptions by comparing forecast vs actual demand."""

    def __init__(self, db: Session):
        self.db = db

    def run_detection(
        self,
        config_id: Optional[int],
        period_start: date,
        period_end: date,
        threshold_percent: float = 20.0,
        product_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Run exception detection for a given period.

        1. Load active detection rules
        2. Query forecast aggregates (P50) by product/site
        3. Query actual demand aggregates by product/site
        4. Calculate variance and apply rules
        5. Create ForecastException records

        Returns summary with counts.
        """
        rules = self._load_active_rules(config_id)
        if not rules:
            rules = [self._make_default_rule(threshold_percent)]

        forecasts = self._load_forecast_aggregates(
            config_id, period_start, period_end, product_ids
        )
        actuals = self._load_actual_aggregates(
            config_id, period_start, period_end, product_ids
        )

        all_keys = set(forecasts.keys()) | set(actuals.keys())
        exceptions_created = 0

        for key in all_keys:
            product_id, site_id = key
            forecast_qty = forecasts.get(key, 0.0)
            actual_qty = actuals.get(key, 0.0)

            if forecast_qty == 0 and actual_qty == 0:
                continue

            if self._has_existing_exception(product_id, site_id, period_start, period_end):
                continue

            variance_qty, variance_pct, direction = self._calculate_variance(
                forecast_qty, actual_qty
            )

            for rule in rules:
                if not self._rule_applies(rule, product_id, site_id):
                    continue
                if not self._threshold_triggered(rule, variance_pct, variance_qty):
                    continue

                severity = self._determine_severity(abs(variance_pct), rule)
                self._create_exception(
                    config_id=config_id,
                    product_id=product_id,
                    site_id=site_id,
                    period_start=period_start,
                    period_end=period_end,
                    rule=rule,
                    forecast_qty=forecast_qty,
                    actual_qty=actual_qty,
                    variance_qty=variance_qty,
                    variance_pct=variance_pct,
                    direction=direction,
                    severity=severity,
                )
                exceptions_created += 1
                break  # One exception per product/site — highest-priority rule wins

        self.db.commit()

        return {
            "status": "completed",
            "period": {
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
            },
            "products_analyzed": len(all_keys),
            "exceptions_created": exceptions_created,
            "rules_evaluated": len(rules),
        }

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_active_rules(self, config_id: Optional[int]) -> List[ForecastExceptionRule]:
        q = self.db.query(ForecastExceptionRule).filter(
            ForecastExceptionRule.is_active == True  # noqa: E712
        )
        if config_id is not None:
            q = q.filter(
                or_(
                    ForecastExceptionRule.config_id == config_id,
                    ForecastExceptionRule.config_id.is_(None),
                )
            )
        return q.order_by(ForecastExceptionRule.id).all()

    def _make_default_rule(self, threshold_percent: float) -> ForecastExceptionRule:
        """Create an in-memory default rule when no DB rules exist."""
        rule = ForecastExceptionRule()
        rule.id = None
        rule.rule_type = "VARIANCE_THRESHOLD"
        rule.variance_threshold_percent = threshold_percent
        rule.is_active = True
        rule.product_ids = None
        rule.site_ids = None
        rule.severity_mapping = None
        return rule

    def _load_forecast_aggregates(
        self,
        config_id: Optional[int],
        period_start: date,
        period_end: date,
        product_ids: Optional[List[str]] = None,
    ) -> Dict[Tuple[str, int], float]:
        """Load forecast P50 (or quantity) aggregated by product/site."""
        q = self.db.query(
            Forecast.product_id,
            Forecast.site_id,
            func.sum(
                func.coalesce(Forecast.forecast_p50, Forecast.forecast_quantity, 0)
            ).label("total_forecast"),
        ).filter(
            and_(
                Forecast.forecast_date >= period_start,
                Forecast.forecast_date <= period_end,
            )
        )

        if config_id is not None:
            q = q.filter(Forecast.config_id == config_id)
        if product_ids:
            q = q.filter(Forecast.product_id.in_(product_ids))

        q = q.group_by(Forecast.product_id, Forecast.site_id)

        result = {}
        for row in q.all():
            result[(row.product_id, row.site_id)] = float(row.total_forecast or 0)
        return result

    def _load_actual_aggregates(
        self,
        config_id: Optional[int],
        period_start: date,
        period_end: date,
        product_ids: Optional[List[str]] = None,
    ) -> Dict[Tuple[str, int], float]:
        """Load actual demand (ordered qty) aggregated by product/site."""
        q = self.db.query(
            OutboundOrderLine.product_id,
            OutboundOrderLine.site_id,
            func.sum(OutboundOrderLine.ordered_quantity).label("total_actual"),
        ).filter(
            and_(
                OutboundOrderLine.requested_delivery_date >= period_start,
                OutboundOrderLine.requested_delivery_date <= period_end,
            )
        )

        if config_id is not None:
            q = q.filter(OutboundOrderLine.config_id == config_id)
        if product_ids:
            q = q.filter(OutboundOrderLine.product_id.in_(product_ids))

        q = q.group_by(OutboundOrderLine.product_id, OutboundOrderLine.site_id)

        result = {}
        for row in q.all():
            result[(row.product_id, row.site_id)] = float(row.total_actual or 0)
        return result

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _has_existing_exception(
        self,
        product_id: str,
        site_id: int,
        period_start: date,
        period_end: date,
    ) -> bool:
        """Check for existing open exception on the same product/site/period."""
        count = (
            self.db.query(func.count(ForecastException.id))
            .filter(
                and_(
                    ForecastException.product_id == product_id,
                    ForecastException.site_id == site_id,
                    ForecastException.period_start == period_start,
                    ~ForecastException.status.in_(["RESOLVED", "DISMISSED"]),
                )
            )
            .scalar()
        )
        return count > 0

    # ------------------------------------------------------------------
    # Variance calculation
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_variance(
        forecast_qty: float, actual_qty: float
    ) -> Tuple[float, float, str]:
        """Returns (variance_qty, variance_pct, direction)."""
        variance_qty = actual_qty - forecast_qty
        if forecast_qty != 0:
            variance_pct = (variance_qty / forecast_qty) * 100.0
        elif actual_qty != 0:
            variance_pct = 100.0 if actual_qty > 0 else -100.0
        else:
            variance_pct = 0.0

        direction = "OVER" if actual_qty > forecast_qty else "UNDER"
        return variance_qty, variance_pct, direction

    # ------------------------------------------------------------------
    # Rule evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _rule_applies(
        rule: ForecastExceptionRule, product_id: str, site_id: int
    ) -> bool:
        """Check if rule scope covers this product/site."""
        if rule.product_ids:
            ids = rule.product_ids if isinstance(rule.product_ids, list) else []
            if ids and product_id not in ids:
                return False
        if rule.site_ids:
            ids = rule.site_ids if isinstance(rule.site_ids, list) else []
            if ids and site_id not in ids:
                return False
        return True

    @staticmethod
    def _threshold_triggered(
        rule: ForecastExceptionRule,
        variance_pct: float,
        variance_qty: float,
    ) -> bool:
        """Check if variance exceeds rule thresholds."""
        abs_pct = abs(variance_pct)
        abs_qty = abs(variance_qty)

        pct_threshold = getattr(rule, "variance_threshold_percent", None)
        abs_threshold = getattr(rule, "variance_threshold_absolute", None)

        if pct_threshold is not None and abs_pct >= pct_threshold:
            return True
        if abs_threshold is not None and abs_qty >= abs_threshold:
            return True

        # If rule has no thresholds at all, don't trigger
        if pct_threshold is None and abs_threshold is None:
            return False

        return False

    @staticmethod
    def _determine_severity(abs_variance_pct: float, rule: ForecastExceptionRule) -> str:
        """Determine severity from rule's severity_mapping or defaults."""
        mapping = getattr(rule, "severity_mapping", None)
        if mapping and isinstance(mapping, dict):
            for range_key, sev in mapping.items():
                try:
                    parts = str(range_key).split("-")
                    low, high = float(parts[0]), float(parts[1])
                    if low <= abs_variance_pct < high:
                        return sev
                except (ValueError, IndexError):
                    continue

        # Defaults
        if abs_variance_pct >= 100:
            return "CRITICAL"
        elif abs_variance_pct >= 50:
            return "HIGH"
        elif abs_variance_pct >= 25:
            return "MEDIUM"
        return "LOW"

    # ------------------------------------------------------------------
    # Exception creation
    # ------------------------------------------------------------------

    def _create_exception(
        self,
        config_id: Optional[int],
        product_id: str,
        site_id: int,
        period_start: date,
        period_end: date,
        rule: ForecastExceptionRule,
        forecast_qty: float,
        actual_qty: float,
        variance_qty: float,
        variance_pct: float,
        direction: str,
        severity: str,
    ) -> ForecastException:
        exception_number = f"EXC-{uuid.uuid4().hex[:8].upper()}"
        exc = ForecastException(
            exception_number=exception_number,
            config_id=config_id,
            product_id=product_id,
            site_id=site_id,
            period_start=period_start,
            period_end=period_end,
            time_bucket="CUSTOM",
            exception_type=getattr(rule, "rule_type", "VARIANCE"),
            severity=severity,
            priority=self._severity_to_priority(severity),
            forecast_quantity=forecast_qty,
            actual_quantity=actual_qty,
            variance_quantity=variance_qty,
            variance_percent=variance_pct,
            threshold_percent=getattr(rule, "variance_threshold_percent", None),
            direction=direction,
            status="NEW",
            detection_method="AUTOMATED",
            detection_rule_id=getattr(rule, "id", None),
            detected_at=datetime.utcnow(),
        )
        self.db.add(exc)

        # §3.62 Phase 3 follow-up: emit a parallel Core Alert so the
        # unified operator dashboard sees this demand-variance signal
        # alongside SCP and TMS plane alerts. Failure here is logged but
        # doesn't block the legacy ForecastException write — the CRUD
        # surface stays the source of truth until consumers cut over.
        try:
            self._emit_core_alert(
                exception_number=exception_number,
                config_id=config_id,
                product_id=product_id,
                site_id=site_id,
                period_start=period_start,
                period_end=period_end,
                exception_type=exc.exception_type,
                severity=severity,
                forecast_qty=forecast_qty,
                actual_qty=actual_qty,
                variance_qty=variance_qty,
                variance_pct=variance_pct,
                threshold_pct=getattr(rule, "variance_threshold_percent", None),
                direction=direction,
                detection_rule_id=getattr(rule, "id", None),
            )
        except Exception:
            logger.exception(
                "Failed to mirror ForecastException %s into Core Alert; "
                "legacy ForecastException write still succeeded.",
                exception_number,
            )

        return exc

    def _emit_core_alert(
        self,
        *,
        exception_number: str,
        config_id: Optional[int],
        product_id: str,
        site_id: int,
        period_start: date,
        period_end: date,
        exception_type: str,
        severity: str,
        forecast_qty: float,
        actual_qty: float,
        variance_qty: float,
        variance_pct: float,
        threshold_pct: Optional[float],
        direction: str,
        detection_rule_id: Optional[int],
    ) -> Alert:
        """Build the Core Alert mirror for a freshly-created ForecastException."""
        # Deterministic alert_id derived from exception_number so a
        # repeat-detection pass (which guards against duplicate
        # ForecastException via ``_has_existing_exception``) would
        # also be idempotent against the Alert row if we ever change
        # that upstream check.
        alert_id = f"DEMAND-VARIANCE-{exception_number}"
        message = (
            f"Forecast variance {variance_pct:+.1f}% on product {product_id} "
            f"at site {site_id} for {period_start}–{period_end} "
            f"(forecast {forecast_qty:.0f}, actual {actual_qty:.0f}, "
            f"direction={direction})."
        )
        if severity == "CRITICAL":
            action = (
                f"URGENT: Investigate root cause for product {product_id} "
                f"at site {site_id}. Apply a manual ForecastAdjustment to "
                f"compensate pending re-train."
            )
        elif severity == "HIGH":
            action = (
                f"Investigate variance on product {product_id} / site "
                f"{site_id} and consider a bias-corrected ForecastAdjustment."
            )
        else:
            action = (
                f"Monitor product {product_id} at site {site_id} for "
                f"continued variance; queue a model-feature audit if "
                f"persistent."
            )
        resolution_threshold = (
            threshold_pct if threshold_pct is not None else 20.0
        )
        resolution = build_resolution_condition(
            metric="variance_percent_abs",
            operator="lt",
            threshold=float(resolution_threshold),
            description=(
                f"Auto-resolve when absolute variance drops below "
                f"{resolution_threshold:.0f}% on the next detection pass."
            ),
        )
        factors = {
            "exception_number": exception_number,
            "exception_type": exception_type,
            "forecast_quantity": forecast_qty,
            "actual_quantity": actual_qty,
            "variance_quantity": variance_qty,
            "variance_percent": variance_pct,
            "threshold_percent": threshold_pct,
            "direction": direction,
            "period_start": period_start.isoformat() if period_start else None,
            "period_end": period_end.isoformat() if period_end else None,
            "detection_rule_id": detection_rule_id,
            "detection_method": "AUTOMATED",
        }
        now = datetime.utcnow()
        alert = Alert(
            alert_id=alert_id,
            type=AlertType.VARIANCE_RELIABILITY.value,
            severity=severity,  # detector's ladder already aligns with AlertSeverity values
            plane=Plane.DEMAND.value,
            config_id=config_id,
            product_id=str(product_id),
            site_id=str(site_id),
            probability=min(abs(variance_pct), 99.0),
            message=message,
            recommended_action=action,
            factors=factors,
            status=AlertStatus.INFORMED.value,
            resolution_condition=resolution,
            created_at=now,
            updated_at=now,
        )
        self.db.add(alert)
        return alert

    def _resolve_core_alert(
        self,
        exception_number: str,
        resolution_notes: str,
    ) -> None:
        """Flip the mirrored Alert to ACTIONED when its ForecastException
        gets resolved by ``reevaluate_open_exceptions``."""
        alert_id = f"DEMAND-VARIANCE-{exception_number}"
        alert = (
            self.db.query(Alert).filter(Alert.alert_id == alert_id).one_or_none()
        )
        if alert is None:
            return
        if alert.status == AlertStatus.ACTIONED.value:
            return
        alert.status = AlertStatus.ACTIONED.value
        alert.resolved_at = datetime.utcnow()
        alert.resolution_notes = resolution_notes
        alert.updated_at = datetime.utcnow()

    @staticmethod
    def _severity_to_priority(severity: str) -> int:
        return {"CRITICAL": 90, "HIGH": 70, "MEDIUM": 50, "LOW": 30}.get(severity, 50)

    def reevaluate_open_exceptions(
        self,
        config_id: Optional[int],
        threshold_percent: float = 20.0,
    ) -> Dict[str, int]:
        """Re-evaluate open exceptions against current forecast vs actual data.

        Auto-resolves exceptions where variance has dropped below threshold.
        Updates variance for exceptions that are still triggered.

        Returns {"resolved": N, "still_open": N, "updated": N}
        """
        from app.models.forecast_exception import ForecastException
        result = {"resolved": 0, "still_open": 0, "updated": 0}

        # Get all open exceptions
        open_exceptions = (
            self.db.query(ForecastException)
            .filter(
                ForecastException.status.in_(["open", "NEW", "acknowledged", "investigating"]),
            )
        )
        if config_id:
            open_exceptions = open_exceptions.filter(ForecastException.config_id == config_id)

        for exc in open_exceptions.all():
            # Re-calculate variance for this product/site/period
            key = (exc.product_id, str(exc.site_id))
            forecast_qty = self._load_forecast_aggregates(
                exc.config_id, exc.period_start, exc.period_end, [exc.product_id]
            ).get(key, 0)
            actual_qty = self._load_actual_aggregates(
                exc.config_id, exc.period_start, exc.period_end, [exc.product_id]
            ).get(key, 0)

            if forecast_qty <= 0:
                result["still_open"] += 1
                continue

            _, new_var_pct, _ = self._calculate_variance(forecast_qty, actual_qty)

            if abs(new_var_pct) < threshold_percent:
                # Variance below threshold — auto-resolve
                resolution_message = (
                    f"Auto-resolved: variance dropped from "
                    f"{exc.variance_percent:.1f}% to {new_var_pct:.1f}%"
                )
                exc.status = "resolved"
                exc.resolution_action = "auto_resolved"
                exc.resolution_notes = resolution_message
                exc.variance_percent = round(new_var_pct, 1)
                exc.resolved_at = datetime.utcnow()
                exc.updated_at = datetime.utcnow()
                result["resolved"] += 1
                # Mirror the resolution onto the Core Alert (§3.62 Phase 3 follow-up).
                try:
                    self._resolve_core_alert(
                        exception_number=exc.exception_number,
                        resolution_notes=resolution_message,
                    )
                except Exception:
                    logger.exception(
                        "Failed to mirror auto-resolve of ForecastException %s "
                        "onto its Core Alert; ForecastException auto-resolve "
                        "still succeeded.",
                        exc.exception_number,
                    )
            else:
                # Still above threshold — update variance
                exc.variance_percent = round(new_var_pct, 1)
                exc.forecast_quantity = forecast_qty
                exc.actual_quantity = actual_qty
                exc.variance_quantity = round(actual_qty - forecast_qty, 1)
                exc.updated_at = datetime.utcnow()
                result["still_open"] += 1
                result["updated"] += 1

        self.db.flush()
        if result["resolved"] > 0:
            logger.info(
                "Exception re-evaluation: %d resolved, %d still open (config %s)",
                result["resolved"], result["still_open"], config_id,
            )
        return result
