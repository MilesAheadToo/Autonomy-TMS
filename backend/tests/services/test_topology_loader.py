"""Tests for the slim topology_loader (PR-5.D extraction).

The async ``load_topology`` path is exercised end-to-end via
``sop_inference_service`` (gated on ``TMS_RUN_INTEGRATION_TESTS=1``).
This file covers the pure-Python pieces: dataclass shape, classifier
helpers, and topological sort behaviour. No DB needed.

Loaded the module via ``importlib.spec_from_file_location`` so tests
don't pull in the heavy ``app.services.__init__`` package side
effects (matches the pattern used by the lifecycle-reactor and
lane-forecast-input-builder tests).
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
from dataclasses import fields
from typing import Any, List

import pytest


_LOADER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "app", "services", "topology_loader.py",
)


def _load_topology_loader_module():
    """Load topology_loader.py without triggering app.services.__init__.

    Snapshots sys.modules before installing fake stub packages, exec's
    the module, then restores sys.modules to its pre-call state. The
    returned module object stays alive (loader_module references it)
    and topology_loader's imports are bound to names in its own
    namespace, so post-restore sys.modules cleanliness doesn't break
    anything.

    This avoids the pollution that earlier revisions caused in
    cross-module test runs (this file's stubs leaked into
    test_tms_agent_card.py via the shared ``app.*`` path namespace).
    """
    # Stub out the model imports the loader pulls in. We don't exercise
    # the async load path here — only the pure helpers and dataclass
    # shape — so type-only stubs are sufficient.
    keys_we_might_install = (
        "app",
        "app.models",
        "app.services",
        "app.models.supply_chain_config",
        "app.models.sc_entities",
        "topology_loader_test_loaded",
    )
    snapshot = {k: sys.modules.get(k, _SENTINEL) for k in keys_we_might_install}

    try:
        if "app" not in sys.modules:
            for parent in ("app", "app.models", "app.services"):
                pkg = types.ModuleType(parent)
                pkg.__path__ = []
                sys.modules[parent] = pkg

        if "app.models.supply_chain_config" not in sys.modules:
            scc = types.ModuleType("app.models.supply_chain_config")

            class _StubBase:  # noqa: D401 — type-only stub
                pass

            scc.SupplyChainConfig = type("SupplyChainConfig", (_StubBase,), {})
            scc.Node = type("Node", (_StubBase,), {})
            scc.TransportationLane = type("TransportationLane", (_StubBase,), {})
            sys.modules["app.models.supply_chain_config"] = scc

        if "app.models.sc_entities" not in sys.modules:
            sce = types.ModuleType("app.models.sc_entities")
            sce.Product = type("Product", (object,), {})
            sys.modules["app.models.sc_entities"] = sce

        spec = importlib.util.spec_from_file_location(
            "topology_loader_test_loaded", _LOADER_PATH,
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Restore sys.modules to its pre-call state — keeps test
        # isolation. The loaded module object stays alive via the
        # caller's reference; topology_loader binds its imports
        # (Product, SupplyChainConfig, etc.) to names in its own
        # namespace at exec_module time, so subsequent attribute
        # access on those names doesn't consult sys.modules.
        for key, prev in snapshot.items():
            if prev is _SENTINEL:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = prev


_SENTINEL = object()


loader_module = _load_topology_loader_module()
LoadedTopology = loader_module.LoadedTopology
_is_supply = loader_module._is_supply
_is_demand = loader_module._is_demand
_is_inventory = loader_module._is_inventory
_topological_sort = loader_module._topological_sort


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _Site:
    """Minimal Node stand-in. Only the attributes the classifiers read."""

    def __init__(
        self,
        name: str,
        master_type: str = "",
        node_type: str = "",
        tpartner_type: str = "",
        site_id: int = 0,
    ) -> None:
        self.name = name
        self.id = site_id
        self.master_type = master_type
        self.node_type = node_type
        self.tpartner_type = tpartner_type


class _Lane:
    """Minimal TransportationLane stand-in."""

    def __init__(self, from_site_id: int, to_site_id: int) -> None:
        self.from_site_id = from_site_id
        self.to_site_id = to_site_id


# ---------------------------------------------------------------------------
# LoadedTopology dataclass shape — PR-5.D audit's "slimmed" contract
# ---------------------------------------------------------------------------


class TestLoadedTopologySlimContract:
    def test_dataclass_has_only_graph_fields(self) -> None:
        """The slim ``LoadedTopology`` carries config + sites + lanes +
        products + classification + adjacency + topo_order. The legacy
        inventory fields (``forecasts`` / ``inv_policies`` /
        ``initial_inventory`` / ``vendor_lead_times`` /
        ``vendor_reliability``) are removed."""
        names = {f.name for f in fields(LoadedTopology)}
        assert names == {
            "config",
            "sites",
            "lanes",
            "products",
            "supply_sites",
            "inventory_sites",
            "demand_sites",
            "upstream_map",
            "downstream_map",
            "topo_order",
        }
        # Explicit absence checks for the dropped legacy fields —
        # surfaces clearly if a future refactor accidentally re-adds.
        for legacy in (
            "forecasts",
            "inv_policies",
            "initial_inventory",
            "vendor_lead_times",
            "vendor_reliability",
        ):
            assert legacy not in names


# ---------------------------------------------------------------------------
# Classifier helpers
# ---------------------------------------------------------------------------


class TestClassifierHelpers:
    def test_is_supply_via_master_type(self) -> None:
        assert _is_supply(_Site("v1", master_type="VENDOR"))

    def test_is_supply_via_tpartner_type(self) -> None:
        assert _is_supply(_Site("v1", tpartner_type="vendor"))

    def test_is_supply_master_type_supply_substring(self) -> None:
        assert _is_supply(_Site("v1", master_type="SUPPLY_NODE"))

    def test_is_demand_via_master_type(self) -> None:
        assert _is_demand(_Site("c1", master_type="CUSTOMER"))

    def test_is_demand_via_tpartner_type(self) -> None:
        assert _is_demand(_Site("c1", tpartner_type="customer"))

    def test_is_inventory_default(self) -> None:
        """Anything that's not supply or demand is inventory."""
        assert _is_inventory(_Site("dc1", master_type="INVENTORY"))
        assert _is_inventory(_Site("plant1", master_type="MANUFACTURER"))
        # A site with no classification at all also reads as inventory
        # (per the legacy classifier's contract).
        assert _is_inventory(_Site("unknown"))

    def test_is_supply_excludes_demand_and_vice_versa(self) -> None:
        """Classifiers are mutually exclusive on canonical inputs."""
        v = _Site("v1", master_type="VENDOR")
        c = _Site("c1", master_type="CUSTOMER")
        assert _is_supply(v) and not _is_demand(v) and not _is_inventory(v)
        assert _is_demand(c) and not _is_supply(c) and not _is_inventory(c)


# ---------------------------------------------------------------------------
# Topological sort
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    def test_linear_chain(self) -> None:
        """Vendor → DC → Customer gives [Vendor, DC, Customer]."""
        v = _Site("V", site_id=1)
        dc = _Site("DC", site_id=2)
        c = _Site("C", site_id=3)
        sites = [v, dc, c]
        lanes = [_Lane(1, 2), _Lane(2, 3)]
        site_by_id = {s.id: s for s in sites}

        order = _topological_sort(sites, lanes, site_by_id)
        assert order == ["V", "DC", "C"]

    def test_diamond_dag(self) -> None:
        """V → DC1, V → DC2, DC1 → C, DC2 → C — V first, C last."""
        sites = [
            _Site("V", site_id=1),
            _Site("DC1", site_id=2),
            _Site("DC2", site_id=3),
            _Site("C", site_id=4),
        ]
        lanes = [_Lane(1, 2), _Lane(1, 3), _Lane(2, 4), _Lane(3, 4)]
        site_by_id = {s.id: s for s in sites}

        order = _topological_sort(sites, lanes, site_by_id)
        assert order[0] == "V"
        assert order[-1] == "C"
        assert {"DC1", "DC2"} == set(order[1:3])

    def test_disconnected_node_appears(self) -> None:
        """An island with no edges still appears in the sort."""
        sites = [
            _Site("V", site_id=1),
            _Site("C", site_id=2),
            _Site("ISLAND", site_id=3),
        ]
        lanes = [_Lane(1, 2)]
        site_by_id = {s.id: s for s in sites}

        order = _topological_sort(sites, lanes, site_by_id)
        assert set(order) == {"V", "C", "ISLAND"}

    def test_cycle_emits_partial_order(self) -> None:
        """When a cycle exists, the function returns a partial order
        rather than raising — appended remaining nodes maintain
        deterministic output."""
        sites = [
            _Site("A", site_id=1),
            _Site("B", site_id=2),
            _Site("C", site_id=3),
        ]
        # A → B → C → A — fully cyclic, no zero-in-degree node
        lanes = [_Lane(1, 2), _Lane(2, 3), _Lane(3, 1)]
        site_by_id = {s.id: s for s in sites}

        order = _topological_sort(sites, lanes, site_by_id)
        assert set(order) == {"A", "B", "C"}
