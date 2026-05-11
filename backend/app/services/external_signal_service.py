"""TMS shim for the canonical ExternalSignalService.

The orchestrator lives in Core's ``azirella_data_model.context_engine.service``
— this module exists only to inject TMS's plane-extended
``SOURCE_REGISTRY`` (base free feeds + FMCSA, DOE diesel, CBP, EPA
SmartWay, TSA, DAT, SONAR, Greenscreens, MarineTraffic, Drewry, Xeneta,
Inrix, HERE, OAG, CargoMetrics) and the ``TMS_SEEDING_PROFILE`` so
TMS code that does
``from app.services.external_signal_service import ExternalSignalService``
resolves to the canonical implementation with TMS's transport-side
biases applied.

See Core ``packages/data-model/src/azirella_data_model/context_engine/service.py``
for the implementation, and MIGRATION_REGISTER §3.63 for the seeding-
profile gap this file closes.
"""

from azirella_data_model.context_engine import (
    ExternalSignalService as _CoreExternalSignalService,
    IndustryInferenceRule,
    RelevanceBoostRule,
    SignalSeedingProfile,
    refresh_all_tenants as _core_refresh_all_tenants,
)

from app.models.external_signal import SOURCE_REGISTRY


# TMS-flavoured signal-seeding biases (parallels SCP's SCP_SEEDING_PROFILE
# and DP's DP_SEEDING_PROFILE). The "industry" axis for TMS is *transport
# mode mix* — most TMS tenants don't think of themselves as e.g. "food
# distribution" the way SCP/DP tenants do; they think in trucking / ocean /
# air / rail / cold-chain. We model that as tags inferred from lane,
# carrier, and product descriptors in the supply-chain config.
#
# Signal-type strings in relevance_boost_rules below match
# ``SIGNAL_TMS_IMPACT`` keys in ``app/models/external_signal.py`` — keep
# them in sync.
TMS_SEEDING_PROFILE = SignalSeedingProfile(
    industry_inference_rules=(
        # Trucking / road freight — DAT, SONAR, Greenscreens, FMCSA central.
        IndustryInferenceRule(
            keywords=(
                "trucking", "truckload", "truck", "dry van", "flatbed",
                "ltl", "less-than-truckload", "ground freight",
            ),
            tags=("trucking", "ground_freight"),
        ),
        # Ocean / container — MarineTraffic, Drewry, Xeneta, CargoMetrics.
        IndustryInferenceRule(
            keywords=(
                "ocean", "container", "vessel", "ship", "port", "maritime",
                "transpacific", "transatlantic",
            ),
            tags=("ocean_freight", "container"),
        ),
        # Air cargo — OAG/Cirium, Xeneta air.
        IndustryInferenceRule(
            keywords=(
                "air cargo", "air freight", "airfreight", "belly cargo",
                "freighter",
            ),
            tags=("air_freight",),
        ),
        # Rail / intermodal — Inrix rail data, commodity-price linkages.
        IndustryInferenceRule(
            keywords=(
                "rail", "intermodal", "ramp", "drayage", "boxcar",
            ),
            tags=("rail_freight", "intermodal"),
        ),
        # Cold chain — refrigerated trucking + ocean reefer + recall
        # exposure.
        IndustryInferenceRule(
            keywords=(
                "cold chain", "refrigerated", "reefer", "frozen", "chilled",
                "perishable",
            ),
            tags=("cold_chain", "reefer"),
        ),
        # Cross-border / trade — CBP border-wait, geopolitical exposure.
        IndustryInferenceRule(
            keywords=(
                "border", "cross-border", "customs", "import", "export",
                "international",
            ),
            tags=("cross_border", "international_freight"),
        ),
    ),
    # Most TMS tenants by volume are road-freight-dominant even when
    # multi-modal. "trucking" is the safe fallback when no rule matches.
    default_industry_tag="trucking",
    # TMS doesn't routinely seed openFDA — food-recall signals affect
    # demand (DP) and supply (SCP) far more than transport routing.
    # Cold-chain tenants who *are* sensitive to it get it via the SCP
    # co-licensing path (they share the seeding profile per tenant).
    # Keeping this empty avoids firing recall events into TRMs that
    # don't act on them.
    default_openfda_product_types=(),
    openfda_inference_rules=(),
    # Transport-flavoured GDELT keywords — additions to the universal
    # baseline ("supply chain disruption", "port strike").
    extra_gdelt_keywords=(
        "trucker protest",
        "carrier bankruptcy",
        "fuel shortage",
        "rail strike",
        "freight rates",
        "port congestion",
        "panama canal",
        "suez canal",
    ),
    relevance_boost_rules=(
        # Trucking → freight-market and capacity/safety signals lead.
        RelevanceBoostRule(
            industry_tags=("trucking", "ground_freight", "ltl"),
            signal_types=(
                "tender_rejection_spike",
                "spot_rate_divergence",
                "capacity_index_shift",
                "diesel_price_move",
                "carrier_safety_downgrade",
                "smartway_score_change",
            ),
            boost=0.1,
        ),
        # Ocean → port congestion + geopolitics dominate routing.
        RelevanceBoostRule(
            industry_tags=("ocean_freight", "container"),
            signal_types=(
                "port_congestion",
                "spot_rate_divergence",
                "severe_weather",
                "geopolitical_disruption",
                "oil_price_spike",
            ),
            boost=0.1,
        ),
        # Air → weather grounds flights; geopolitics reshapes lanes.
        RelevanceBoostRule(
            industry_tags=("air_freight",),
            signal_types=(
                "severe_weather",
                "geopolitical_disruption",
                "oil_price_spike",
            ),
            boost=0.1,
        ),
        # Rail / intermodal → weather + commodity prices (rail moves
        # bulk; commodity moves drive lane demand).
        RelevanceBoostRule(
            industry_tags=("rail_freight", "intermodal"),
            signal_types=(
                "severe_weather",
                "commodity_price_change",
                "capacity_index_shift",
            ),
            boost=0.1,
        ),
        # Cold chain → severe weather + recall + sustainability scoring
        # (reefer fuel burn). Higher boost — these tenants are
        # disproportionately exposed.
        RelevanceBoostRule(
            industry_tags=("cold_chain", "reefer"),
            signal_types=(
                "severe_weather",
                "regulatory_recall",
                "smartway_score_change",
                "diesel_price_move",
            ),
            boost=0.15,
        ),
        # Cross-border / international → border wait + geopolitics.
        RelevanceBoostRule(
            industry_tags=("cross_border", "international_freight"),
            signal_types=(
                "border_wait_spike",
                "geopolitical_disruption",
                "port_congestion",
            ),
            boost=0.1,
        ),
    ),
    # TMS signals are stamped "Transport Impact: ..." in their RAG
    # embedding text (vs SCP's "SC Impact" and DP's "Demand Impact").
    embedding_impact_label="Transport Impact",
)


class ExternalSignalService(_CoreExternalSignalService):
    """TMS wrapper — injects TMS's SOURCE_REGISTRY + TMS_SEEDING_PROFILE."""

    def __init__(self, db, tenant_id: int):
        super().__init__(
            db,
            tenant_id,
            source_registry=SOURCE_REGISTRY,
            seeding_profile=TMS_SEEDING_PROFILE,
        )


async def refresh_all_tenants(db) -> dict:
    """TMS scheduler entry point — forwards to Core with TMS's registry + profile."""
    return await _core_refresh_all_tenants(
        db,
        source_registry=SOURCE_REGISTRY,
        seeding_profile=TMS_SEEDING_PROFILE,
    )


__all__ = ["ExternalSignalService", "refresh_all_tenants", "TMS_SEEDING_PROFILE"]
