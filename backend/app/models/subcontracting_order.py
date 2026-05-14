"""TMS subcontracting_order shim — re-exports canonical from Core.

The canonical `SubcontractingOrder` + `SubcontractingOrderLineItem`
live in `azirella_data_model.work_order.subcontracting_order`. Both
SCP and TMS carried byte-identical copies before promotion. Promoted
2026-05-14 per CLAUDE.md Rule 1+2.
"""
from azirella_data_model.work_order.subcontracting_order import (  # noqa: F401
    SubcontractingOrder,
    SubcontractingOrderLineItem,
)


__all__ = [
    "SubcontractingOrder",
    "SubcontractingOrderLineItem",
]
