"""Knowledge Base re-export shim.

§3.69 closure: the KB-database ORM substrate (KBBase + KBDocument +
KBChunk) lifted to Core under ``azirella_data_model.knowledge_base``.
TMS and SCP previously carried byte-identical copies; the duplication
is closed and this file is now a thin import shim so existing
``from app.models.knowledge_base import X`` call sites keep working.
"""
from azirella_data_model.knowledge_base import (  # noqa: F401
    KBBase,
    KBChunk,
    KBDocument,
)
