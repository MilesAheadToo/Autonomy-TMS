"""
GNN Orchestration Service — Layer 2 Multi-Site Coordination

Ties together:
    1. S&OP GraphSAGE inference (weekly/monthly, cached)
    2. Execution Temporal GNN inference (daily)
    3. Directive broadcast to all SiteAgents

This is the orchestration layer that converts GNN model outputs into
tGNNSiteDirectives and injects them into the intra-hive signal buses.

Usage:
    orchestrator = GNNOrchestrationService(db, config_id)
    result = await orchestrator.run_full_cycle()
    # result["directives_generated"] = 5
    # result["broadcast_success"] = 5
    # result["feedback"] = {site_key: HiveFeedbackFeatures}

Scheduled: Daily via APScheduler (registered in relearning_jobs.py).
Also callable via POST /site-agent/gnn/run-cycle API.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class GNNOrchestrationService:
    """
    Orchestrates the full GNN inference → directive broadcast cycle.

    Layer 2 (Network tGNN) of the 5-layer coordination stack:
        Layer 1:   Intra-Hive (HiveSignalBus) — <10ms, within site
        Layer 1.5: Site tGNN — hourly, intra-site cross-TRM coordination
        Layer 2:   Network tGNN Inter-Hive (this service) — daily, cross-site
        Layer 3:   AAP Cross-Authority — seconds-minutes, ad hoc
        Layer 4:   S&OP Consensus Board — weekly, policy parameters
    """

    def __init__(self, db: AsyncSession, config_id: int, tenant_id: int = 0):
        self.db = db
        self.config_id = config_id
        self.tenant_id = tenant_id

    async def run_full_cycle(
        self,
        force_recompute: bool = False,
    ) -> Dict[str, Any]:
        """
        Run complete GNN → Directive → Broadcast → Feedback cycle.

        Steps:
            1. Run S&OP GraphSAGE analysis (may use cache)
            2. Run Execution tGNN inference with S&OP embeddings
            3. Merge outputs into unified gnn_outputs dict
            4. Generate tGNNSiteDirectives via DirectiveBroadcastService
            5. Broadcast directives to all registered SiteAgents
            6. Collect feedback from hives

        Returns:
            Dict with cycle results and timing.
        """
        cycle_start = datetime.utcnow()
        result = {
            "config_id": self.config_id,
            "cycle_start": cycle_start.isoformat(),
            "sop_analysis": None,
            "execution_output": None,
            "directives_generated": 0,
            "broadcast_success": 0,
            "broadcast_failed": 0,
            "feedback": {},
            "errors": [],
        }

        # --- Step 1: S&OP GraphSAGE ---
        sop_analysis = None
        sop_embeddings = None
        try:
            from app.services.powell.sop_inference_service import SOPInferenceService

            sop_svc = SOPInferenceService(self.db, self.config_id, tenant_id=self.tenant_id)
            sop_analysis = await sop_svc.analyze_network(
                force_recompute=force_recompute,
            )
            sop_embeddings = await sop_svc.get_embeddings_tensor()
            result["sop_analysis"] = {
                "num_sites": sop_analysis.num_sites,
                "computed_at": (
                    sop_analysis.computed_at.isoformat()
                    if sop_analysis.computed_at else None
                ),
            }
            logger.info(
                f"S&OP analysis: {sop_analysis.num_sites} sites, "
                f"network risk = {sop_analysis.network_risk}"
            )
        except Exception as e:
            logger.warning(f"S&OP inference failed (continuing with defaults): {e}")
            result["errors"].append(f"sop_inference: {e}")

        # --- Step 2: Tactical Hive Coordinator (3-parallel tGNNs) ---
        # PR-5.E (2026-05-05) — TacticalHiveCoordinator + the 4 SCP-shape
        # tactical tGNN services were deleted. The tactical phase is a
        # no-op until §1.13 / §3.41 land TMS-shape tactical-tier
        # services in Core. Downstream methods (_merge_outputs,
        # _apply_planning_trm_adjustments, _persist_directive_reviews,
        # _broadcast_directives) already short-circuit when
        # ``tactical_output is None``.
        tactical_output = None
        exec_output = None  # kept for _persist_directive_reviews compatibility
        result["execution_output"] = {
            "skipped": "tactical_hive_coordinator removed in PR-5.E; "
            "awaiting Core port of TMS-shape tactical tier"
        }

        # --- Step 3: Merge outputs ---
        gnn_outputs = self._merge_outputs(sop_analysis, tactical_output)
        if not gnn_outputs:
            result["errors"].append("No GNN outputs available for broadcast")
            return result

        # --- Step 3.7: Apply Planning TRM adjustments ---
        # Each domain TRM applies in-cycle corrections to the GNN baseline output.
        # GNN generates the baseline plan; Planning TRM adjusts for short-term context
        # (recent forecast bias, capacity signals, supplier confirmation rates, etc.)
        try:
            trm_stats = self._apply_planning_trm_adjustments(gnn_outputs, tactical_output)
            result["planning_trm_adjustments"] = trm_stats
            logger.info(
                f"Planning TRM adjustments applied: "
                f"{trm_stats.get('demand_adjusted', 0)} demand, "
                f"{trm_stats.get('inventory_adjusted', 0)} inventory, "
                f"{trm_stats.get('supply_adjusted', 0)} supply sites adjusted"
            )
        except Exception as e:
            logger.warning(f"Planning TRM adjustments failed (non-blocking): {e}")
            result["errors"].append(f"planning_trm: {e}")

        # --- Step 3.5: Persist directives for human review ---
        try:
            reviews_created = await self._persist_directive_reviews(
                gnn_outputs, sop_analysis, tactical_output,
            )
            result["reviews_created"] = reviews_created
            logger.info(f"Persisted {reviews_created} GNN directive reviews for human review")
        except Exception as e:
            logger.warning(f"Failed to persist directive reviews: {e}")
            result["errors"].append(f"review_persistence: {e}")

        # --- Step 4+5: Generate directives and broadcast ---
        try:
            from app.services.powell.directive_broadcast_service import (
                DirectiveBroadcastService,
            )

            # Build network topology adjacency from config
            topology = await self._build_topology_adjacency()

            broadcast_svc = DirectiveBroadcastService()

            # Register site agents if available
            await self._register_site_agents(broadcast_svc)

            cycle_result = broadcast_svc.run_cycle(
                gnn_outputs=gnn_outputs,
                network_topology=topology,
            )

            result["directives_generated"] = cycle_result.get("directives_generated", 0)
            result["broadcast_success"] = cycle_result.get("broadcast_success", 0)
            result["broadcast_failed"] = cycle_result.get("broadcast_failed", 0)

            logger.info(
                f"Directive broadcast: {result['directives_generated']} generated, "
                f"{result['broadcast_success']} delivered"
            )

        except Exception as e:
            logger.error(f"Directive broadcast failed: {e}")
            result["errors"].append(f"broadcast: {e}")

        # --- Step 6: Collect feedback ---
        try:
            if hasattr(broadcast_svc, "collect_feedback"):
                feedback = broadcast_svc.collect_feedback()
                result["feedback"] = {
                    k: v.to_dict() if hasattr(v, "to_dict") else v
                    for k, v in feedback.items()
                }
        except Exception as e:
            logger.warning(f"Feedback collection failed: {e}")

        result["cycle_duration_ms"] = int(
            (datetime.utcnow() - cycle_start).total_seconds() * 1000
        )
        return result

    def _merge_outputs(
        self,
        sop_analysis,
        tactical_output,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Merge S&OP and TacticalHiveOutput into unified per-site dict.

        Format: {site_key: {criticality_score, bottleneck_risk, demand_forecast, ...}}
        This is what DirectiveBroadcastService.generate_directives_from_gnn() expects.

        When tactical_output is a TacticalHiveOutput, the merged_per_site dict
        produced by TacticalHiveCoordinator.merge_outputs() is used directly and
        overlaid on top of the S&OP values. This preserves 100% backward
        compatibility with DirectiveBroadcastService consumers.
        """
        merged: Dict[str, Dict[str, Any]] = {}

        # Populate from S&OP analysis
        if sop_analysis:
            for site_key in sop_analysis.site_keys:
                merged[site_key] = {
                    "criticality_score": sop_analysis.criticality.get(site_key, 0.5),
                    "bottleneck_risk": sop_analysis.bottleneck_risk.get(site_key, 0.3),
                    "concentration_risk": sop_analysis.concentration_risk.get(site_key, 0.2),
                    "resilience_score": sop_analysis.resilience.get(site_key, 0.7),
                    "safety_stock_multiplier": sop_analysis.safety_stock_multiplier.get(site_key, 1.0),
                }

        # PR-5.E (2026-05-05) — overlay path for TacticalHiveOutput
        # removed along with tactical_hive_coordinator. ``tactical_output``
        # is permanently ``None`` until §1.13 / §3.41 land TMS-shape
        # tactical-tier services in Core. The legacy ExecutionGNNOutput
        # branch is preserved below in case execution_gnn_inference_service
        # output is wired in directly during the interim.
        if tactical_output is not None:
            # Legacy ExecutionGNNOutput path — direct
            # ExecutionGNNInferenceService output (no TacticalHiveCoordinator).
            exec_output = tactical_output
            for site_key in exec_output.site_keys:
                if site_key not in merged:
                    merged[site_key] = {
                        "criticality_score": 0.5,
                        "bottleneck_risk": 0.3,
                        "concentration_risk": 0.2,
                        "resilience_score": 0.7,
                        "safety_stock_multiplier": 1.0,
                    }
                merged[site_key].update({
                    "demand_forecast": exec_output.demand_forecast.get(site_key, []),
                    "exception_probability": exec_output.exception_probability.get(site_key, 0.1),
                    "order_recommendation": exec_output.order_recommendation.get(site_key, 0),
                    "confidence": exec_output.confidence.get(site_key, 0.5),
                    "propagation_impact": exec_output.propagation_impact.get(site_key, []),
                })

        return merged

    async def _build_topology_adjacency(self) -> Dict[str, List[str]]:
        """Build {site_key: [neighbor_site_keys]} from supply chain config."""
        from app.models.supply_chain_config import Site, TransportationLane

        sites_result = await self.db.execute(
            Site.__table__.select().where(Site.config_id == self.config_id)
        )
        sites = {row.id: row.site_key or f"site_{row.id}" for row in sites_result}

        lanes_result = await self.db.execute(
            TransportationLane.__table__.select().where(
                TransportationLane.config_id == self.config_id
            )
        )

        adjacency: Dict[str, List[str]] = {sk: [] for sk in sites.values()}

        for lane in lanes_result:
            src = sites.get(lane.source_site_id)
            tgt = sites.get(lane.target_site_id)
            if src and tgt:
                if tgt not in adjacency.get(src, []):
                    adjacency.setdefault(src, []).append(tgt)
                if src not in adjacency.get(tgt, []):
                    adjacency.setdefault(tgt, []).append(src)

        return adjacency

    async def _persist_directive_reviews(
        self,
        gnn_outputs: Dict[str, Dict[str, Any]],
        sop_analysis,
        tactical_output,
    ) -> int:
        """
        Persist GNN outputs as GNNDirectiveReview rows with status=PROPOSED.

        Creates one row per site per directive scope (sop_policy and/or
        tactical_directive), allowing humans to review before application.

        Accepts TacticalHiveOutput (new) or legacy ExecutionGNNOutput.

        Returns count of review rows created.
        """
        from app.models.gnn_directive_review import GNNDirectiveReview
        from datetime import timedelta

        # Resolve per-site reasoning from tactical output
        # For TacticalHiveOutput, compose a merged reasoning string per site.
        tactical_reasoning_map: Dict[str, Optional[str]] = {}
        tactical_confidence_map: Dict[str, float] = {}

        # PR-5.E (2026-05-05) — TacticalHiveOutput reasoning composition
        # removed along with tactical_hive_coordinator. Falls through to
        # the legacy ExecutionGNNOutput path; tactical_reasoning_map /
        # tactical_confidence_map stay empty when ``tactical_output``
        # is None (the post-PR-5.E default).
        if tactical_output is not None:
            # Legacy ExecutionGNNOutput — direct ExecutionGNNInferenceService
            # output. No TacticalHiveCoordinator.
            exec_output = tactical_output
            if hasattr(exec_output, "reasoning") and exec_output.reasoning:
                for sk in getattr(exec_output, "site_keys", []):
                    tactical_reasoning_map[sk] = exec_output.reasoning.get(sk)
                    tactical_confidence_map[sk] = exec_output.confidence.get(sk, 0.5)

        count = 0
        now = datetime.utcnow()

        for site_key, values in gnn_outputs.items():
            # S&OP policy parameters (if S&OP analysis was available)
            sop_keys = {
                "criticality_score", "bottleneck_risk", "concentration_risk",
                "resilience_score", "safety_stock_multiplier",
            }
            sop_values = {k: v for k, v in values.items() if k in sop_keys}
            if sop_values and sop_analysis:
                # Get plain-English reasoning from S&OP GraphSAGE output
                sop_reasoning = None
                if hasattr(sop_analysis, "reasoning") and sop_analysis.reasoning:
                    sop_reasoning = sop_analysis.reasoning.get(site_key)
                review = GNNDirectiveReview(
                    config_id=self.config_id,
                    site_key=site_key,
                    directive_scope="sop_policy",
                    proposed_values=sop_values,
                    proposed_reasoning=sop_reasoning,
                    model_type="sop_graphsage",
                    model_confidence=sop_values.get("resilience_score", 0.5),
                    status="PROPOSED",
                    expires_at=now + timedelta(hours=24),
                )
                self.db.add(review)
                count += 1

            # Tactical directives (from TacticalHiveCoordinator or legacy ExecutionGNN)
            exec_keys = {
                "demand_forecast", "exception_probability",
                "order_recommendation", "confidence",
            }
            exec_values = {k: v for k, v in values.items() if k in exec_keys}
            if exec_values and tactical_output is not None:
                exec_reasoning = tactical_reasoning_map.get(site_key)
                confidence = tactical_confidence_map.get(site_key, exec_values.get("confidence", 0.5))
                # PR-5.E (2026-05-05) — model_type was a ternary on
                # TacticalHiveOutput vs ExecutionGNNOutput. Tactical
                # branch removed along with tactical_hive_coordinator;
                # only the execution branch remains.
                model_type = "execution_tgnn"
                review = GNNDirectiveReview(
                    config_id=self.config_id,
                    site_key=site_key,
                    directive_scope="execution_directive",
                    proposed_values=exec_values,
                    proposed_reasoning=exec_reasoning,
                    model_type=model_type,
                    model_confidence=confidence,
                    status="PROPOSED",
                    expires_at=now + timedelta(hours=12),
                )
                self.db.add(review)
                count += 1

        try:
            self.db.flush()
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to persist directive reviews: {e}")
            self.db.rollback()
            raise

        return count

    async def _register_site_agents(self, broadcast_svc) -> None:
        """Register any active SiteAgents with the broadcast service."""
        # In production, SiteAgents are long-lived objects.
        # For demo/testing, we skip registration — broadcast_svc
        # will generate directives but won't deliver them to agents.
        # Agents that are already running pick up directives via
        # the next decision cycle's apply_directive() call.
        pass

    async def run_site_tgnn_cycle(
        self,
        site_agent: Any,
    ) -> Dict[str, Any]:
        """Run Site tGNN (Layer 1.5) hourly inference for a single site.

        This is called hourly by the scheduler for each active SiteAgent
        that has Site tGNN initialized. The Site tGNN modulates the
        UrgencyVector before the next decision cycle.

        Args:
            site_agent: SiteAgent instance with signal_bus and urgency_vector

        Returns:
            Dict with inference results
        """
        if not hasattr(site_agent, "_site_tgnn_service") or not site_agent._site_tgnn_service:
            return {"status": "skipped", "reason": "site_tgnn not enabled"}

        try:
            from app.services.powell.hive_feedback import compute_feedback_features

            feedback = compute_feedback_features(
                urgency_snapshot=(
                    site_agent.signal_bus.urgency.snapshot()
                    if site_agent.signal_bus and hasattr(site_agent.signal_bus, "urgency")
                    else None
                ),
                signal_bus=site_agent.signal_bus,
            )

            output = site_agent._site_tgnn_service.infer(
                hive_signal_bus=site_agent.signal_bus,
                urgency_vector=(
                    site_agent.signal_bus.urgency
                    if site_agent.signal_bus and hasattr(site_agent.signal_bus, "urgency")
                    else None
                ),
                recent_decisions=getattr(site_agent, "_recent_decisions_cache", {}),
                hive_feedback=feedback,
            )

            return {
                "status": "completed",
                "site_key": site_agent.site_key,
                "output": output.to_dict(),
            }
        except Exception as e:
            logger.warning(f"Site tGNN cycle failed for {site_agent.site_key}: {e}")
            return {"status": "error", "error": str(e)}

    def _apply_planning_trm_adjustments(
        self,
        gnn_outputs,
        tactical_output,
    ):
        """No-op in TMS after SCP-fork Planning-TRM retirement (2026-04-23).

        The SCP-era DemandAdjustment / InventoryAdjustment / SupplyAdjustment
        TRMs produced per-site corrections to SCP Demand / Inventory / Supply
        Planning GNN outputs. None of those concepts are in the TMS
        transport plane. Wiring transport-plane GNN-correction TRMs (when /
        if they exist) is item 1.13 scope.

        Returns the original stats shape with zero counts so callers keep
        working during the transition.
        """
        return {
            "demand_adjusted": 0,
            "inventory_adjusted": 0,
            "supply_adjusted": 0,
            "sites_processed": len(gnn_outputs or {}),
        }

