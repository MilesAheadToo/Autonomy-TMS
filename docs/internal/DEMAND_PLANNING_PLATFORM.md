# Demand Planning — moved out of TMS

> **This file is a redirect.** This doc was a copy of DP's `DEMAND_PLANNING_PLATFORM.md` from before the multi-product split. It described **item-level** demand planning, which is **not a TMS capability** — it lives in Autonomy-DP. The TMS-scoped equivalent (lane-volume forecasting) is a separate doc.

## Where to go

| If you want to know about… | Go to… |
|---|---|
| **Item-level demand planning** (product × site × period, units) — baseline forecasting, NPI / EOL adjustments, promotion uplift, demand sensing, consensus, the 8-domain `ForecastAdjustmentTRM` | [Autonomy-DP/docs/DEMAND_PLANNING_PLATFORM.md](../../../Autonomy-DP/docs/DEMAND_PLANNING_PLATFORM.md) and the deeper [Autonomy-DP/docs/architecture/FORECAST_ADJUSTMENT_TRM.md](../../../Autonomy-DP/docs/architecture/FORECAST_ADJUSTMENT_TRM.md) |
| **Lane-volume forecasting** (lane × period × mode × equipment × service-class — loads + tonnage + cube) — TMS's `LaneVolumeForecastTRM`, §3.36 segmentation, `LaneVolumePlan` canonical state, `TacticalForecastService` | [TMS docs/LANE_VOLUME_FORECASTING.md](../LANE_VOLUME_FORECASTING.md) |
| **L3 Movement Plan + Constrained Balanced Plan** (downstream consumers of `LaneVolumePlan`) | [TMS_DECISION_HIERARCHY.md](../TMS_DECISION_HIERARCHY.md) §4.2 / §4.3 + [TACTICAL_PLANNING_REARCHITECTURE.md](../TACTICAL_PLANNING_REARCHITECTURE.md) |
| **DP / TMS interaction on demand** (how the two planes' forecasts feed each other) | [Autonomy-DP/docs/architecture/DEMAND_PLANNING.md](../../../Autonomy-DP/docs/architecture/DEMAND_PLANNING.md) — "Scope: this is DP's demand planning, not TMS's" section |

## Why this doc exists as a redirect

When TMS was extracted from the SCP/DP monorepo (§3.6 / §3.27 / §3.28), `DEMAND_PLANNING_PLATFORM.md` came along as part of the file copy and was never trimmed back to the TMS scope. The content described item-level forecasting (`OutboundOrderLine`, `Forecast` table, `forecast_pipeline_service.py`), which is unambiguously DP-plane work — TMS has no such tables today and never will. Keeping the stale copy here was misleading newcomers into thinking TMS owned item-level demand planning.

The redirect approach (rather than `git rm`) preserves the historical link so anyone who has bookmarked this file still lands somewhere useful.
