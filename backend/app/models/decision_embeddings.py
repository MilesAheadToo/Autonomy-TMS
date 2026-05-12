"""DecisionEmbedding re-export shim.

§3.69 closure: the ``DecisionEmbedding`` ORM lifted to Core under
``azirella_data_model.knowledge_base``. This file is a thin import
shim so existing ``from app.models.decision_embeddings import X``
call sites keep working.
"""
from azirella_data_model.knowledge_base import DecisionEmbedding  # noqa: F401
