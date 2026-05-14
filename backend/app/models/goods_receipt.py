"""TMS goods_receipt shim — re-exports canonical from Core.

The canonical `GoodsReceipt` + `GoodsReceiptLineItem` live in
`azirella_data_model.work_order.goods_receipt`. Both SCP and TMS
consumed it (SCP for inventory write-back + PO closure; TMS for
inbound visibility), the files were byte-identical before promotion,
and the `__tablename__ = "goods_receipt"` collision would 500 the
auth path under the AD-13 modular monolith the same way ScenarioUser
+ ProductionOrder did.

Promoted 2026-05-14 per CLAUDE.md Rule 1 (cross-product test passes)
+ Rule 2 (substrate — every plane that touches inbound logistics
consumes it).
"""
from azirella_data_model.work_order.goods_receipt import (  # noqa: F401
    GoodsReceipt,
    GoodsReceiptLineItem,
)


__all__ = [
    "GoodsReceipt",
    "GoodsReceiptLineItem",
]
