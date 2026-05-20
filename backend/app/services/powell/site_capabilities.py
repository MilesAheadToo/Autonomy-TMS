"""TMS site capability mapping — TRM hive composition per site type.

Maps ``master_type`` (and optionally ``sc_site_type``) to the set of TMS
TRM agents that are meaningful for that site. A pure-distribution cross-
dock doesn't run carrier-network procurement; a carrier yard doesn't
schedule dock appointments. The mapping is the substrate the TMS
decision-cycle uses to decide which TRMs to instantiate per site.

Cross-plane note: this file is the TMS analogue of
``Autonomy-SCP/backend/app/services/powell/site_capabilities.py``. Two
files, separate domains — SCP runs execution-domain TRMs (PO creation,
inventory buffer, etc.); TMS runs transport-domain TRMs (broker
routing, load build, etc.). The two plane registries share only the
``vendor`` / ``customer`` external-party convention.

Demand-domain TRMs (``forecast_adjustment``, ``forecast_baseline``,
``demand_sensing``) are NOT in this set — they live in DP under the
single-home rule. TMS callers that need forecast-adjustment go through
DP's ``compute_forecast_adjustment`` MCP tool with
``forecast_object_type="lane_volume"``.

Usage:
    from app.services.powell.site_capabilities import get_active_trms

    active = get_active_trms(master_type="inventory")
    # => frozenset of all 10 TMS TRM canonical names (default — most
    # TMS internal sites participate in the full hive)

    active = get_active_trms(master_type="inventory", sc_site_type="CARRIER_YARD")
    # => frozenset narrowed to the carrier-yard TRM subset
"""
from __future__ import annotations

from typing import Dict, FrozenSet, Optional


# ---------------------------------------------------------------------------
# Canonical TRM names (must match decision_cycle phase map + MODEL_REGISTRY)
# ---------------------------------------------------------------------------
# 10 native TMS TRMs. ``demand_sensing`` is intentionally NOT here —
# it's tracked as Phase 3 of the TMS TRM rework (lift to DP under
# single-home).

ALL_TRM_NAMES: FrozenSet[str] = frozenset([
    "broker_routing",        # carrier subcontracting / brokerage
    "capacity_buffer",       # safety capacity (analog of SCP's safety stock)
    "capacity_promise",      # ATP-equivalent: available-capacity-to-promise
    "dock_scheduling",       # facility-level appointment scheduling
    "equipment_reposition",  # empty-mile pressure, container rebalancing
    "exception_management",  # in-transit exception handling (analog of SCP quality)
    "freight_procurement",   # carrier PO creation (analog of SCP po_creation)
    "intermodal_transfer",   # mode-swap decisions (truck ↔ rail ↔ ocean)
    "load_build",            # outbound load construction (analog of SCP to_execution)
    "shipment_tracking",     # in-transit visibility + order-tracking analog
])


# ---------------------------------------------------------------------------
# Master-type capability mapping
# ---------------------------------------------------------------------------
#
# TMS sites are logistics nodes, not production / inventory holders the way
# SCP frames them. Most TMS-internal sites participate in the full TRM
# hive — narrowing happens via ``sc_site_type`` overrides for specialised
# nodes (carrier yards, cross-docks, terminals, lanes).
#
# INVENTORY  — generic internal logistics node (DC, warehouse, hub). Default
#              to full hive.
# CARRIER    — owned/contracted carrier asset (truck, container, fleet).
#              Limited TRM set focused on the dispatch + repositioning side.
# VENDOR     — external supplier (TradingPartner). No TRMs — outside
#              company authority. Convention shared with SCP.
# CUSTOMER   — external consignee. No TRMs.

_MASTER_TYPE_TRMS: Dict[str, FrozenSet[str]] = {
    "inventory": ALL_TRM_NAMES,
    "carrier": frozenset([
        "broker_routing",
        "capacity_promise",
        "equipment_reposition",
        "exception_management",
        "shipment_tracking",
    ]),
    # External parties — outside company authority, no TRM hive.
    "vendor": frozenset(),
    "customer": frozenset(),
    # ``manufacturer`` is an SCP concept; if a TMS deployment shares a
    # site with SCP master_type=manufacturer, treat it as a generic
    # logistics node here (the SCP plane runs its own TRMs separately).
    "manufacturer": ALL_TRM_NAMES,
}


# ---------------------------------------------------------------------------
# SC site type overrides — finer-grained adjustments within master_type
# ---------------------------------------------------------------------------
# Values are uppercase to match the NodeType enum storage convention.

_SC_SITE_TYPE_OVERRIDES: Dict[str, FrozenSet[str]] = {
    # Carrier yard — staging area for carrier-owned equipment. Procurement
    # and broker decisions happen elsewhere; this site is about positioning
    # equipment and promising capacity.
    "CARRIER_YARD": frozenset([
        "capacity_promise",
        "equipment_reposition",
        "capacity_buffer",
        "shipment_tracking",
    ]),

    # Cross-dock — consolidation node; freight in / freight out same day.
    # No long-term inventory; emphasis on load-building + dock scheduling +
    # exception handling.
    "CROSS_DOCK": frozenset([
        "load_build",
        "dock_scheduling",
        "exception_management",
        "shipment_tracking",
    ]),

    # Terminal — port / rail / intermodal hub. Mode-swap is the dominant
    # decision shape; freight procurement happens at the terminal-pair
    # level rather than per-load.
    "TERMINAL": frozenset([
        "intermodal_transfer",
        "load_build",
        "dock_scheduling",
        "exception_management",
        "shipment_tracking",
        "capacity_buffer",
    ]),

    # DC — distribution center; full TMS hive (default for the inventory
    # master_type, listed here for explicitness).
    "DC": ALL_TRM_NAMES,

    # Wholesaler / Distributor — same shape as DC for TMS purposes.
    "WHOLESALER": ALL_TRM_NAMES,
    "DISTRIBUTOR": ALL_TRM_NAMES,

    # Retailer — customer-facing; TMS decisions are mostly about inbound
    # delivery (shipment tracking) + dock scheduling. No outbound load
    # construction.
    "RETAILER": frozenset([
        "shipment_tracking",
        "dock_scheduling",
        "exception_management",
    ]),

    # Supplier — same as the SCP convention; TMS sees them only as origin
    # points for inbound freight.
    "SUPPLIER": frozenset([
        "freight_procurement",
        "shipment_tracking",
    ]),
}


def get_active_trms(
    master_type: str,
    sc_site_type: Optional[str] = None,
) -> FrozenSet[str]:
    """Return the set of TMS TRM canonical names active for a given site type.

    Args:
        master_type: One of ``"inventory"``, ``"carrier"``, ``"vendor"``,
            ``"customer"``, or (for compatibility with SCP-shared sites)
            ``"manufacturer"``. Lowercase as stored in ``Site.master_type``
            or ``Site.tpartner_type``.
        sc_site_type: Optional ``NodeType`` value uppercase (e.g.
            ``"CARRIER_YARD"``, ``"CROSS_DOCK"``, ``"TERMINAL"``). When
            provided AND an override exists, it takes precedence over the
            ``master_type`` default.

    Returns:
        Frozen set of canonical TRM names that should be instantiated for
        this site. Always a subset of :data:`ALL_TRM_NAMES`.

    Raises:
        ValueError: ``master_type`` is not recognized.
    """
    mt = master_type.lower()

    # Check sc_site_type override first — finer granularity wins.
    if sc_site_type:
        override = _SC_SITE_TYPE_OVERRIDES.get(sc_site_type.upper())
        if override is not None:
            return override

    trms = _MASTER_TYPE_TRMS.get(mt)
    if trms is None:
        raise ValueError(
            f"Unknown master_type: {master_type!r}. "
            f"Valid: {sorted(_MASTER_TYPE_TRMS)}"
        )
    return trms


def is_trm_active(
    trm_name: str,
    master_type: str,
    sc_site_type: Optional[str] = None,
) -> bool:
    """Check whether a specific TRM is active for a given site type."""
    return trm_name in get_active_trms(master_type, sc_site_type)


def get_active_trm_indices(
    master_type: str,
    sc_site_type: Optional[str] = None,
) -> list[int]:
    """Return sorted list of TRM slot indices that are active.

    Uses the canonical ordering from ``UrgencyVector.TRM_INDICES``.
    Useful for Site tGNN node masking. Indices that aren't in the
    UrgencyVector mapping are silently filtered — newly-added TRMs
    won't crash this helper before the UrgencyVector is updated.
    """
    from .hive_signal import UrgencyVector

    active = get_active_trms(master_type, sc_site_type)
    indices = []
    for name in active:
        idx = UrgencyVector.TRM_INDICES.get(name)
        if idx is not None:
            indices.append(idx)
    return sorted(set(indices))
