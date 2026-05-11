"""TMS shim — connectors live in Core ``azirella_data_model.context_engine.connectors``.

This module re-exports the canonical connector implementations so existing
imports (``from app.services.external_signal_connectors import get_connector``)
keep working. See MIGRATION_REGISTER §3.60 for the promotion rationale.
"""

from azirella_data_model.context_engine.connectors import (  # noqa: F401
    BaseConnector,
    ExternalSignalData,
    CONNECTOR_REGISTRY,
    get_connector,
    FREDConnector,
    OpenMeteoConnector,
    EIAConnector,
    GDELTConnector,
    GoogleTrendsConnector,
    OpenFDAConnector,
    NWSAlertsConnector,
    DOTDisruptionConnector,
    RedditSentimentConnector,
)

__all__ = [
    "BaseConnector",
    "ExternalSignalData",
    "CONNECTOR_REGISTRY",
    "get_connector",
    "FREDConnector",
    "OpenMeteoConnector",
    "EIAConnector",
    "GDELTConnector",
    "GoogleTrendsConnector",
    "OpenFDAConnector",
    "NWSAlertsConnector",
    "DOTDisruptionConnector",
    "RedditSentimentConnector",
]
