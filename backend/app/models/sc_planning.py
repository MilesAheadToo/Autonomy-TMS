"""SCP shim — canonical Supply Chain Planning models in Core.

``ProductionCapacity`` + ``OrderAggregationPolicy`` + ``AggregatedOrder`` +
``SourcingSchedule`` + ``SourcingScheduleDetails`` now live in
``azirella_data_model.master.sc_planning`` (lifted 2026-05-15 per
MIGRATION_REGISTER §1.1.2 phase 1b sub-lift; SCP and TMS copies were
byte-identical pre-lift).
"""
from azirella_data_model.master.sc_planning import (  # noqa: F401
    ProductionCapacity,
    OrderAggregationPolicy,
    AggregatedOrder,
    SourcingSchedule,
    SourcingScheduleDetails,
    InboundOrderLine,
)


__all__ = [
    "ProductionCapacity",
    "OrderAggregationPolicy",
    "AggregatedOrder",
    "SourcingSchedule",
    "SourcingScheduleDetails",
    "InboundOrderLine",
]
