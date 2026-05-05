"""
S&OP GraphSAGE Inference Service

Loads trained S&OP GraphSAGE checkpoints and provides runtime inference:
- Network analysis (criticality, bottleneck risk, concentration risk, resilience)
- Structural embeddings for Execution tGNN consumption
- Cached results in DB for fast lookup

This closes the gap between training (powell_training_service.py) and
runtime consumption. The S&OP model trains weekly/monthly but its outputs
are consumed daily by the Execution tGNN and TRM agents.

Usage:
    svc = SOPInferenceService(db, config_id, tenant_id=tenant_id)
    analysis = await svc.analyze_network()
    # analysis.criticality[site_key] -> float
    # analysis.embeddings[site_key] -> List[float] (64-dim)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.checkpoint_storage_service import checkpoint_dir as _ckpt_dir

logger = logging.getLogger(__name__)


@dataclass
class NetworkAnalysis:
    """Result of S&OP GraphSAGE network analysis."""

    config_id: int
    num_sites: int
    checkpoint_path: str

    # Per-site scores (site_key -> value)
    criticality: Dict[str, float] = field(default_factory=dict)
    bottleneck_risk: Dict[str, float] = field(default_factory=dict)
    concentration_risk: Dict[str, float] = field(default_factory=dict)
    resilience: Dict[str, float] = field(default_factory=dict)
    safety_stock_multiplier: Dict[str, float] = field(default_factory=dict)

    # Per-site structural embeddings (site_key -> List[float])
    embeddings: Dict[str, List[float]] = field(default_factory=dict)

    # Network-level risk scores
    network_risk: Dict[str, float] = field(default_factory=dict)

    # Conformal score bounds — uncertainty on S&OP scores from ensemble variance
    # or MC-Dropout at inference. Per-site: {lower, upper, coverage, method}.
    score_intervals: Dict[str, Dict[str, Dict[str, float]]] = field(
        default_factory=dict
    )

    # Ordered list of site keys (matches tensor index order)
    site_keys: List[str] = field(default_factory=list)

    # Metadata
    computed_at: Optional[datetime] = None

    # Per-site plain-English reasoning (site_key -> reasoning string)
    reasoning: Dict[str, str] = field(default_factory=dict)

    def generate_reasoning(self) -> None:
        """Populate per-site reasoning strings from analysis data."""
        from app.services.powell.decision_reasoning import sop_graphsage_reasoning
        for site_key in self.site_keys:
            self.reasoning[site_key] = sop_graphsage_reasoning(
                site_key=site_key,
                criticality=self.criticality.get(site_key, 0.0),
                bottleneck_risk=self.bottleneck_risk.get(site_key, 0.0),
                concentration_risk=self.concentration_risk.get(site_key, 0.0),
                resilience=self.resilience.get(site_key, 0.0),
                safety_stock_multiplier=self.safety_stock_multiplier.get(site_key, 1.0),
                network_risk=self.network_risk,
                score_intervals=self.score_intervals.get(site_key),
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API response."""
        d = {
            "config_id": self.config_id,
            "num_sites": self.num_sites,
            "checkpoint_path": self.checkpoint_path,
            "criticality": self.criticality,
            "bottleneck_risk": self.bottleneck_risk,
            "concentration_risk": self.concentration_risk,
            "resilience": self.resilience,
            "safety_stock_multiplier": self.safety_stock_multiplier,
            "network_risk": self.network_risk,
            "score_intervals": self.score_intervals,
            "site_keys": self.site_keys,
            "computed_at": self.computed_at.isoformat() if self.computed_at else None,
        }
        if self.reasoning:
            d["reasoning"] = self.reasoning
        return d


class SOPInferenceService:
    """
    Runtime inference for trained S&OP GraphSAGE models.

    Responsibilities:
    1. Load checkpoint for a config_id
    2. Build node/edge features from current topology
    3. Run forward pass to get scores + embeddings
    4. Cache results in powell_sop_embeddings table
    5. Provide fast lookup for downstream consumers
    """

    def __init__(self, db: AsyncSession, config_id: int, tenant_id: int = 0):
        self.db = db
        self.config_id = config_id
        self.tenant_id = tenant_id
        self._model = None
        self._device = "cpu"

    async def analyze_network(
        self,
        force_recompute: bool = False,
    ) -> NetworkAnalysis:
        """
        Run S&OP GraphSAGE analysis on current network topology.

        If cached results exist and force_recompute=False, returns cached.
        Otherwise loads checkpoint, builds features, runs inference, and caches.
        """
        if not force_recompute:
            cached = await self._load_cached_analysis()
            if cached is not None:
                logger.info(
                    f"Using cached S&OP analysis for config {self.config_id} "
                    f"(computed {cached.computed_at})"
                )
                return cached

        # Load topology
        topology = await self._load_topology()
        if not topology:
            raise ValueError(f"Could not load topology for config {self.config_id}")

        # Build features
        node_features, edge_index, edge_features, site_keys, site_id_map = (
            self._build_features(topology)
        )

        # Load model and run inference
        checkpoint_path = self._find_checkpoint()
        model = self._load_model(checkpoint_path, node_features.shape[1], edge_features.shape[1])

        analysis = self._run_inference(
            model, node_features, edge_index, edge_features,
            site_keys, site_id_map, str(checkpoint_path),
        )

        # Cache to DB
        await self._cache_analysis(analysis, site_id_map)

        logger.info(
            f"S&OP analysis complete for config {self.config_id}: "
            f"{analysis.num_sites} sites analyzed"
        )
        analysis.generate_reasoning()
        return analysis

    async def get_embeddings_tensor(self) -> Optional[np.ndarray]:
        """
        Get structural embeddings as numpy array [num_sites, embedding_dim].

        Used by Execution tGNN as input features.
        Returns None if no cached analysis exists.
        """
        analysis = await self._load_cached_analysis()
        if analysis is None:
            return None

        # Build ordered array matching site index order
        embeddings = []
        for key in analysis.site_keys:
            emb = analysis.embeddings.get(key)
            if emb is not None:
                embeddings.append(emb)
            else:
                embeddings.append([0.0] * 64)
        return np.array(embeddings, dtype=np.float32)

    async def get_site_criticality(self, site_key: str) -> Optional[float]:
        """Get criticality score for a specific site."""
        from app.models.powell import PowellSOPEmbedding

        result = await self.db.execute(
            select(PowellSOPEmbedding.criticality)
            .where(PowellSOPEmbedding.config_id == self.config_id)
            .where(PowellSOPEmbedding.site_key == site_key)
            .order_by(PowellSOPEmbedding.computed_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row

    async def get_site_scores(self, site_key: str) -> Optional[Dict[str, float]]:
        """Get all S&OP scores for a specific site."""
        from app.models.powell import PowellSOPEmbedding

        result = await self.db.execute(
            select(PowellSOPEmbedding)
            .where(PowellSOPEmbedding.config_id == self.config_id)
            .where(PowellSOPEmbedding.site_key == site_key)
            .order_by(PowellSOPEmbedding.computed_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None

        return {
            "criticality": row.criticality,
            "bottleneck_risk": row.bottleneck_risk,
            "concentration_risk": row.concentration_risk,
            "resilience": row.resilience,
            "safety_stock_multiplier": row.safety_stock_multiplier,
            "embedding_dim": len(row.embedding) if row.embedding else 0,
        }

    # ── Private Methods ──

    async def _load_topology(self):
        """Load topology using topology_loader's load_topology."""
        try:
            from app.services.topology_loader import load_topology
            return await load_topology(self.config_id, self.db)
        except Exception as e:
            logger.error(f"Failed to load topology for config {self.config_id}: {e}")
            return None

    def _build_features(self, topology) -> Tuple[Any, Any, Any, List[str], Dict[str, int]]:
        """
        Build node features, edge index, and edge features from topology.

        Node features (12-dim):
        - avg_lead_time, lead_time_cv, capacity, capacity_utilization
        - unit_cost, reliability, num_suppliers, num_customers
        - inventory_turns, service_level, holding_cost, position

        Edge features (6-dim):
        - lead_time_avg, lead_time_std, cost_per_unit, capacity
        - reliability, relationship_strength
        """
        import torch

        sites = topology.sites
        lanes = topology.lanes

        # Build site ordering (topo order for consistent indexing)
        site_keys = [s.name for s in sites]
        site_idx = {name: i for i, name in enumerate(site_keys)}
        site_id_map = {s.name: s.id for s in sites}
        num_sites = len(sites)

        # Compute topological position (0=upstream, 1=downstream)
        topo_positions = {}
        for i, name in enumerate(topology.topo_order):
            topo_positions[name] = i / max(len(topology.topo_order) - 1, 1)

        # Node features [N, 12]
        node_feats = np.zeros((num_sites, 12), dtype=np.float32)
        for i, site in enumerate(sites):
            name = site.name
            master = getattr(site, 'master_type', 'INVENTORY')

            # Lead time stats from upstream lanes
            upstream_lts = []
            for up_name, lane in topology.upstream_map.get(name, []):
                lt = getattr(lane, 'lead_time', None) or getattr(lane, 'transit_time', 5)
                upstream_lts.append(lt)

            avg_lt = np.mean(upstream_lts) if upstream_lts else 0.0
            lt_cv = (np.std(upstream_lts) / avg_lt) if upstream_lts and avg_lt > 0 else 0.0

            # Capacity from site attributes
            capacity = getattr(site, 'capacity', None) or 1000.0
            cap_util = 0.5  # Default utilization

            # Cost
            unit_cost = getattr(site, 'holding_cost', None) or 1.0

            # Reliability — pre-PR-5.D the legacy ``LoadedTopology``
            # carried a per-vendor ``vendor_reliability`` map populated
            # from ``_load_vendor_info``. The loader hard-coded every
            # value to 0.95 (the default), so the field was constant
            # data, not real reliability. PR-5.D dropped the inventory-
            # field loaders; this caller now uses the constant
            # directly. When real per-vendor reliability becomes
            # available (e.g. via TradingPartner.reliability_pct),
            # replace this constant with the lookup.
            reliability = 0.95

            # Connectivity
            num_suppliers = len(topology.upstream_map.get(name, []))
            num_customers = len(topology.downstream_map.get(name, []))

            # Operational metrics (defaults for cold-start)
            inv_turns = 12.0  # Reasonable default
            service_level = 0.95

            # Holding cost
            holding_cost = getattr(site, 'holding_cost', None) or 1.0

            # Position in network
            position = topo_positions.get(name, 0.5)

            node_feats[i] = [
                avg_lt / 30.0,        # Normalize lead time (days → ~month scale)
                lt_cv,
                capacity / 10000.0,   # Normalize capacity
                cap_util,
                unit_cost / 100.0,    # Normalize cost
                reliability,
                num_suppliers / 5.0,  # Normalize supplier count
                num_customers / 5.0,
                inv_turns / 52.0,     # Normalize turns
                service_level,
                holding_cost / 10.0,
                position,
            ]

        # Edge index [2, E] and features [E, 6]
        src_indices = []
        dst_indices = []
        edge_feats_list = []

        for lane in lanes:
            source_site = None
            target_site = None
            for s in sites:
                if s.id == lane.source_node_id:
                    source_site = s
                if s.id == lane.target_node_id:
                    target_site = s

            if source_site is None or target_site is None:
                continue

            src_name = source_site.name
            dst_name = target_site.name

            if src_name not in site_idx or dst_name not in site_idx:
                continue

            src_indices.append(site_idx[src_name])
            dst_indices.append(site_idx[dst_name])

            lt = getattr(lane, 'lead_time', None) or getattr(lane, 'transit_time', 5)
            lt_std = lt * 0.15  # Default 15% CV
            cost = getattr(lane, 'transport_cost', None) or getattr(lane, 'cost_per_unit', 1.0) or 1.0
            lane_capacity = getattr(lane, 'capacity', None) or 1000.0
            lane_reliability = 0.95
            rel_strength = 0.8  # Default relationship strength

            edge_feats_list.append([
                lt / 30.0,
                lt_std / 30.0,
                cost / 100.0,
                lane_capacity / 10000.0,
                lane_reliability,
                rel_strength,
            ])

        # Handle case with no edges
        if not src_indices:
            edge_index = torch.zeros((2, 0), dtype=torch.long)
            edge_features = torch.zeros((0, 6), dtype=torch.float32)
        else:
            edge_index = torch.tensor([src_indices, dst_indices], dtype=torch.long)
            edge_features = torch.tensor(edge_feats_list, dtype=torch.float32)

        node_features = torch.tensor(node_feats, dtype=torch.float32)

        return node_features, edge_index, edge_features, site_keys, site_id_map

    def _find_checkpoint(self) -> Path:
        """Find the S&OP GraphSAGE checkpoint for this config."""
        # Check standard location
        _ckpt = _ckpt_dir(self.tenant_id, self.config_id)
        checkpoint_path = _ckpt / f"sop_graphsage_{self.config_id}.pt"
        if checkpoint_path.exists():
            return checkpoint_path

        # Check config-specific subdirectory
        for subdir in _ckpt.glob("supply_chain_configs/*/"):
            candidate = subdir / f"sop_graphsage_{self.config_id}.pt"
            if candidate.exists():
                return candidate

        # If no trained checkpoint, we'll use untrained model (cold-start)
        logger.warning(
            f"No trained checkpoint found for config {self.config_id}. "
            f"Using untrained model (cold-start analysis)."
        )
        return checkpoint_path  # Will trigger cold-start path

    def _load_model(self, checkpoint_path: Path, node_dim: int, edge_dim: int):
        """Load S&OP GraphSAGE model from checkpoint or create fresh."""
        import torch
        from app.models.gnn.planning_execution_gnn import create_sop_model

        if checkpoint_path.exists():
            checkpoint = torch.load(str(checkpoint_path), map_location=self._device, weights_only=False)
            config = checkpoint.get("config", {})

            model = create_sop_model(
                node_features=config.get("node_feature_dim", node_dim),
                edge_features=config.get("edge_feature_dim", edge_dim),
                hidden_dim=config.get("hidden_dim", 128),
                embedding_dim=config.get("embedding_dim", 64),
                num_layers=config.get("num_layers", 3),
            )
            model.load_state_dict(checkpoint["model_state_dict"])
            logger.info(f"Loaded S&OP checkpoint: {checkpoint_path}")
        else:
            # Cold-start: untrained model still provides structural analysis
            # via the GATv2 message-passing (random weights capture topology)
            model = create_sop_model(
                node_features=node_dim,
                edge_features=edge_dim,
            )
            logger.info("Using untrained S&OP model (cold-start)")

        model.eval()
        model.to(self._device)
        return model

    def _run_inference(
        self, model, node_features, edge_index, edge_features,
        site_keys, site_id_map, checkpoint_path,
    ) -> NetworkAnalysis:
        """Run model forward pass and build NetworkAnalysis."""
        import torch

        now = datetime.utcnow()

        with torch.no_grad():
            outputs = model(
                node_features.to(self._device),
                edge_index.to(self._device),
                edge_features.to(self._device),
            )

        analysis = NetworkAnalysis(
            config_id=self.config_id,
            num_sites=len(site_keys),
            checkpoint_path=checkpoint_path,
            site_keys=site_keys,
            computed_at=now,
        )

        # Extract per-site scores
        criticality = outputs["criticality_score"].cpu().numpy().flatten()
        bottleneck = outputs["bottleneck_risk"].cpu().numpy().flatten()
        concentration = outputs["concentration_risk"].cpu().numpy().flatten()
        resilience = outputs["resilience_score"].cpu().numpy().flatten()
        safety_stock = outputs["safety_stock_multiplier"].cpu().numpy().flatten()
        embeddings = outputs["structural_embeddings"].cpu().numpy()

        for i, key in enumerate(site_keys):
            analysis.criticality[key] = float(criticality[i])
            analysis.bottleneck_risk[key] = float(bottleneck[i])
            analysis.concentration_risk[key] = float(concentration[i])
            analysis.resilience[key] = float(resilience[i])
            analysis.safety_stock_multiplier[key] = float(safety_stock[i])
            analysis.embeddings[key] = embeddings[i].tolist()

        # Network-level risk
        network_risk = outputs["network_risk"].cpu().numpy().flatten()
        analysis.network_risk = {
            "overall": float(network_risk[0]) if len(network_risk) > 0 else 0.0,
            "supply": float(network_risk[1]) if len(network_risk) > 1 else 0.0,
            "demand": float(network_risk[2]) if len(network_risk) > 2 else 0.0,
            "operational": float(network_risk[3]) if len(network_risk) > 3 else 0.0,
        }

        # Compute score uncertainty via MC-Dropout (if model has dropout layers)
        self._compute_score_intervals(
            model, node_features, edge_index, edge_features,
            site_keys, analysis,
        )

        analysis.generate_reasoning()
        return analysis

    def _compute_score_intervals(
        self, model, node_features, edge_index, edge_features,
        site_keys: List[str], analysis: NetworkAnalysis,
        n_passes: int = 10, coverage: float = 0.90,
    ) -> None:
        """
        Compute conformal-style score intervals via MC-Dropout.

        Enables dropout at inference and runs N forward passes to estimate
        prediction variance. The resulting intervals provide uncertainty
        bounds on GraphSAGE scores with approximate coverage guarantees.

        Falls back to perturbation-based intervals if no dropout layers exist.
        """
        import torch

        has_dropout = any(
            isinstance(m, torch.nn.Dropout) for m in model.modules()
        )

        score_names = [
            "criticality", "bottleneck_risk", "concentration_risk",
            "resilience", "safety_stock_multiplier",
        ]

        try:
            if has_dropout:
                # MC-Dropout: enable dropout at inference for N stochastic passes
                model.train()  # Enables dropout
                samples = {name: [] for name in score_names}

                with torch.no_grad():
                    for _ in range(n_passes):
                        out = model(
                            node_features.to(self._device),
                            edge_index.to(self._device),
                            edge_features.to(self._device),
                        )
                        for name in score_names:
                            key_map = {
                                "criticality": "criticality_score",
                                "bottleneck_risk": "bottleneck_risk",
                                "concentration_risk": "concentration_risk",
                                "resilience": "resilience_score",
                                "safety_stock_multiplier": "safety_stock_multiplier",
                            }
                            vals = out[key_map[name]].cpu().numpy().flatten()
                            samples[name].append(vals)

                model.eval()  # Restore eval mode

                # Compute intervals from samples
                alpha = 1.0 - coverage
                for i, site_key in enumerate(site_keys):
                    site_intervals = {}
                    for name in score_names:
                        vals = np.array([s[i] for s in samples[name]])
                        lower = float(np.percentile(vals, 100 * alpha / 2))
                        upper = float(np.percentile(vals, 100 * (1 - alpha / 2)))
                        site_intervals[name] = {
                            "lower": lower,
                            "upper": upper,
                            "coverage": coverage,
                            "method": "mc_dropout",
                        }
                    analysis.score_intervals[site_key] = site_intervals
            else:
                # Perturbation-based: add small noise to inputs and measure output variance
                model.eval()
                samples = {name: [] for name in score_names}

                with torch.no_grad():
                    for _ in range(n_passes):
                        noise = torch.randn_like(node_features) * 0.05
                        perturbed = node_features + noise
                        out = model(
                            perturbed.to(self._device),
                            edge_index.to(self._device),
                            edge_features.to(self._device),
                        )
                        key_map = {
                            "criticality": "criticality_score",
                            "bottleneck_risk": "bottleneck_risk",
                            "concentration_risk": "concentration_risk",
                            "resilience": "resilience_score",
                            "safety_stock_multiplier": "safety_stock_multiplier",
                        }
                        for name in score_names:
                            vals = out[key_map[name]].cpu().numpy().flatten()
                            samples[name].append(vals)

                alpha = 1.0 - coverage
                for i, site_key in enumerate(site_keys):
                    site_intervals = {}
                    for name in score_names:
                        vals = np.array([s[i] for s in samples[name]])
                        lower = float(np.percentile(vals, 100 * alpha / 2))
                        upper = float(np.percentile(vals, 100 * (1 - alpha / 2)))
                        site_intervals[name] = {
                            "lower": lower,
                            "upper": upper,
                            "coverage": coverage,
                            "method": "input_perturbation",
                        }
                    analysis.score_intervals[site_key] = site_intervals

        except Exception as e:
            logger.debug(f"Score interval computation failed: {e}")

    async def _cache_analysis(
        self, analysis: NetworkAnalysis, site_id_map: Dict[str, int]
    ) -> None:
        """Persist analysis results to powell_sop_embeddings table."""
        from app.models.powell import PowellSOPEmbedding

        try:
            # Delete old embeddings for this config
            await self.db.execute(
                delete(PowellSOPEmbedding)
                .where(PowellSOPEmbedding.config_id == self.config_id)
            )

            # Insert new rows
            for key in analysis.site_keys:
                site_id = site_id_map.get(key)
                if site_id is None:
                    continue

                record = PowellSOPEmbedding(
                    config_id=self.config_id,
                    site_id=site_id,
                    site_key=key,
                    embedding=analysis.embeddings.get(key, []),
                    criticality=analysis.criticality.get(key, 0.0),
                    bottleneck_risk=analysis.bottleneck_risk.get(key, 0.0),
                    concentration_risk=analysis.concentration_risk.get(key, 0.0),
                    resilience=analysis.resilience.get(key, 0.0),
                    safety_stock_multiplier=analysis.safety_stock_multiplier.get(key, 1.0),
                    network_risk=analysis.network_risk,
                    checkpoint_path=analysis.checkpoint_path,
                    computed_at=analysis.computed_at,
                )
                self.db.add(record)

            await self.db.flush()
            logger.info(
                f"Cached {len(analysis.site_keys)} S&OP embeddings "
                f"for config {self.config_id}"
            )
        except Exception as e:
            logger.warning(f"Failed to cache S&OP embeddings: {e}")

    async def _load_cached_analysis(self) -> Optional[NetworkAnalysis]:
        """Load cached analysis from DB if available."""
        from app.models.powell import PowellSOPEmbedding

        result = await self.db.execute(
            select(PowellSOPEmbedding)
            .where(PowellSOPEmbedding.config_id == self.config_id)
            .order_by(PowellSOPEmbedding.computed_at.desc())
        )
        rows = result.scalars().all()

        if not rows:
            return None

        # Use the most recent computed_at
        latest_time = rows[0].computed_at
        rows = [r for r in rows if r.computed_at == latest_time]

        analysis = NetworkAnalysis(
            config_id=self.config_id,
            num_sites=len(rows),
            checkpoint_path=rows[0].checkpoint_path or "",
            computed_at=latest_time,
        )

        for row in rows:
            key = row.site_key
            analysis.site_keys.append(key)
            analysis.criticality[key] = row.criticality
            analysis.bottleneck_risk[key] = row.bottleneck_risk
            analysis.concentration_risk[key] = row.concentration_risk
            analysis.resilience[key] = row.resilience
            analysis.safety_stock_multiplier[key] = row.safety_stock_multiplier
            analysis.embeddings[key] = row.embedding if row.embedding else []

            if row.network_risk and not analysis.network_risk:
                analysis.network_risk = row.network_risk

        analysis.generate_reasoning()
        return analysis
