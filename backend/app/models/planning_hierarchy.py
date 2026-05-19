"""SCP shim — canonical Planning Hierarchy models in Core.

All 10 symbols (PlanningHierarchyConfig + SiteHierarchyNode +
ProductHierarchyNode + TimeBucketConfig + PlanningHorizonTemplate +
AggregatedPlan + 4 enums) now live in
``azirella_data_model.master.planning_hierarchy`` (lifted 2026-05-15
per MIGRATION_REGISTER §1.1.2 phase 1b sub-lift). SCP canonical (~1%
diff vs TMS; SCP carries the ``source`` provenance column that TMS
lacks — TMS picks it up automatically through this shim).
"""
from azirella_data_model.master.planning_hierarchy import (  # noqa: F401
    SiteHierarchyLevel,
    ProductHierarchyLevel,
    TimeBucketType,
    PlanningType,
    PlanningHierarchyConfig,
    SiteHierarchyNode,
    ProductHierarchyNode,
    TimeBucketConfig,
    PlanningHorizonTemplate,
    AggregatedPlan,
    DEFAULT_PLANNING_TEMPLATES,
)


__all__ = [
    "SiteHierarchyLevel",
    "ProductHierarchyLevel",
    "TimeBucketType",
    "PlanningType",
    "PlanningHierarchyConfig",
    "SiteHierarchyNode",
    "ProductHierarchyNode",
    "TimeBucketConfig",
    "PlanningHorizonTemplate",
    "AggregatedPlan",
    "DEFAULT_PLANNING_TEMPLATES",
]
