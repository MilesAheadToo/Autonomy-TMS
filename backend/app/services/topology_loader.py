"""Supply-chain topology loader — graph-only.

Loads a :class:`SupplyChainConfig` plus its sites, transportation lanes,
and products from the DB and returns a :class:`LoadedTopology` ready
for graph-shaped consumption (S&OP GraphSAGE inference, lane-level
forecasting, etc.).

History — extracted from the legacy ``dag_simulator.py`` per
[PR-5.D](../../docs/TWIN_PR5D_DAG_SIMULATOR_AUDIT.md). The original
``dag_simulator.py`` was 1,671 lines mixing graph loading,
deterministic SCP-shape simulation, and inventory bookkeeping. The
post-PR-5.B/5.C audit confirmed only the graph half is live — every
caller of ``dag_simulator.load_topology`` consumes graph fields only.
``vendor_reliability`` was an exception in PR-5.D's first audit
(``sop_inference_service.py`` reads it) but a closer look at
``_load_vendor_info`` showed the loader hard-coded every value to
``0.95`` — i.e., constant data, not real reliability. That caller now
reads the constant directly. The inventory loaders
(``_load_forecasts`` / ``_load_inv_policies`` /
``_load_initial_inventory`` / ``_load_vendor_info``) were dead;
removing them shrinks the file from ~1,671 to ~150 lines and removes
the SCP-shape inventory leaks that have no TMS plane consumers.

The single live caller post-extraction is
:mod:`app.services.powell.sop_inference_service`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.sc_entities import Product
from app.models.supply_chain_config import (
    Node,
    SupplyChainConfig,
    TransportationLane,
)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class LoadedTopology:
    """Graph-only view of a supply-chain config.

    Fields the legacy ``dag_simulator.LoadedTopology`` carried that
    were dropped in PR-5.D (no live consumer):

    - ``forecasts`` (per site × product weekly)
    - ``inv_policies`` (per site × product)
    - ``initial_inventory`` (per site × product)
    - ``vendor_lead_times`` (per supplier × product)
    - ``vendor_reliability`` (per supplier; was constant ``0.95``)
    """

    config: SupplyChainConfig
    sites: List[Node]
    lanes: List[TransportationLane]
    products: List[Product]

    # Topology classification by master type
    supply_sites: List[Node]      # vendor TradingPartner endpoints
    inventory_sites: List[Node]   # INVENTORY / MANUFACTURER
    demand_sites: List[Node]      # customer TradingPartner endpoints

    # DAG adjacency: site_name -> [(other_site_name, lane), ...]
    upstream_map: Dict[str, List[Tuple[str, TransportationLane]]]
    downstream_map: Dict[str, List[Tuple[str, TransportationLane]]]

    # Topological sort order — upstream first
    topo_order: List[str]


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------


async def load_topology(config_id: int, db: AsyncSession) -> LoadedTopology:
    """Load and process a supply-chain config into graph-shape format.

    Reads:
    - :class:`SupplyChainConfig` (with sites + transportation_lanes)
    - :class:`Product` rows scoped to ``config_id``

    Builds:
    - per-master-type site classifications
    - upstream / downstream adjacency maps
    - Kahn's-algorithm topological order

    Returns :class:`LoadedTopology`. Raises :class:`ValueError` when
    the config can't be found.
    """
    result = await db.execute(
        select(SupplyChainConfig)
        .where(SupplyChainConfig.id == config_id)
        .options(
            selectinload(SupplyChainConfig.sites),
            selectinload(SupplyChainConfig.transportation_lanes),
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise ValueError(f"SupplyChainConfig {config_id} not found")

    sites = list(config.sites)
    lanes = list(config.transportation_lanes)

    prod_result = await db.execute(
        select(Product).where(Product.config_id == config_id)
    )
    products = list(prod_result.scalars().all())

    site_by_id = {s.id: s for s in sites}

    supply_sites = [s for s in sites if _is_supply(s)]
    inventory_sites = [s for s in sites if _is_inventory(s)]
    demand_sites = [s for s in sites if _is_demand(s)]

    upstream_map: Dict[str, List[Tuple[str, TransportationLane]]] = defaultdict(list)
    downstream_map: Dict[str, List[Tuple[str, TransportationLane]]] = defaultdict(list)

    for lane in lanes:
        source = site_by_id.get(lane.from_site_id)
        target = site_by_id.get(lane.to_site_id)
        if source and target:
            downstream_map[source.name].append((target.name, lane))
            upstream_map[target.name].append((source.name, lane))

    topo_order = _topological_sort(sites, lanes, site_by_id)

    return LoadedTopology(
        config=config,
        sites=sites,
        lanes=lanes,
        products=products,
        supply_sites=supply_sites,
        inventory_sites=inventory_sites,
        demand_sites=demand_sites,
        upstream_map=upstream_map,
        downstream_map=downstream_map,
        topo_order=topo_order,
    )


# ---------------------------------------------------------------------------
# Master-type classifiers — preserved verbatim from dag_simulator.py.
#
# The duplicate enum strings in the OR-chain are intentional: legacy
# data has both 'VENDOR' and 'VENDOR' (post-cleanup) values stored, and
# similarly for CUSTOMER. The assertions are defensive against schema
# drift in old configs.
# ---------------------------------------------------------------------------


def _is_supply(site: Node) -> bool:
    """Site is a supply source (VENDOR / legacy VENDOR / TradingPartner=vendor)."""
    master = getattr(site, "master_type", "") or ""
    node_type = getattr(site, "node_type", "") or ""
    tpartner_type = getattr(site, "tpartner_type", "") or ""
    return (
        master.upper() in ("VENDOR", "VENDOR")
        or node_type.upper() in ("VENDOR", "VENDOR")
        or tpartner_type.lower() == "vendor"
        or "SUPPLY" in master.upper()
    )


def _is_demand(site: Node) -> bool:
    """Site is a demand sink (CUSTOMER / legacy CUSTOMER / TradingPartner=customer)."""
    master = getattr(site, "master_type", "") or ""
    node_type = getattr(site, "node_type", "") or ""
    tpartner_type = getattr(site, "tpartner_type", "") or ""
    return (
        master.upper() in ("CUSTOMER", "CUSTOMER")
        or node_type.upper() in ("CUSTOMER", "CUSTOMER")
        or tpartner_type.lower() == "customer"
        or "DEMAND" in master.upper()
    )


def _is_inventory(site: Node) -> bool:
    """Site is an inventory / processing node (anything not supply or demand)."""
    return not _is_supply(site) and not _is_demand(site)


def _topological_sort(
    sites: List[Node],
    lanes: List[TransportationLane],
    site_by_id: Dict[int, Node],
) -> List[str]:
    """Topological sort of sites in the DAG. Upstream first; suppliers first."""
    in_degree: Dict[str, int] = {s.name: 0 for s in sites}
    adj: Dict[str, List[str]] = {s.name: [] for s in sites}

    for lane in lanes:
        source = site_by_id.get(lane.from_site_id)
        target = site_by_id.get(lane.to_site_id)
        if source and target:
            adj[source.name].append(target.name)
            in_degree[target.name] = in_degree.get(target.name, 0) + 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    result: List[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in adj.get(node, []):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Cycles: append remaining nodes deterministically rather than
    # raising — the caller treats topology as best-effort and we'd
    # rather emit a partial ordering than fail loading entirely.
    remaining = [s.name for s in sites if s.name not in result]
    result.extend(remaining)

    return result


__all__ = [
    "LoadedTopology",
    "load_topology",
]
