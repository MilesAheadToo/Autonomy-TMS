# CLAUDE.md — Autonomy TMS

Project rules for Claude Code. Detailed architecture in
[docs/CLAUDE_REFERENCE.md](docs/CLAUDE_REFERENCE.md).

## CRITICAL: Pre-launch — always make the right architectural decision

**Autonomy is not yet live in production.** When a placement, design, or dependency decision presents a tradeoff between (a) the architecturally-correct shape and (b) the locally-expedient shortcut, **always choose (a)**. The right shape now is cheaper than the right shape later, and the cost of "later" is exactly what this directive prevents accumulating.

No "for now" placements, no PAT / scope widening to mask misplaced deps, no "merge it green and circle back" on CI failures that reveal architectural smells, no defaulting to `MIGRATION_REGISTER.md` as a parking lot. This directive sits above every other rule in this file and is mirrored from `Autonomy-Core/CLAUDE.md`. If any rule conflicts with it, this one wins.

---

> Cross-product engineering rules (placement, AWS SC DM, SOC II, AIIO,
> terminology, plan separation, planning cascade, hierarchy drilldown,
> governance, AATP) are defined in `Autonomy-Core/CLAUDE.md`. This repo
> carries the TMS-specific addenda. When a TMS rule conflicts with
> Core, Core wins.

> **Host roles:** TMS code dev happens on **msi-stealth** (same host the
> runtime lives on — edit → build → restart → check loop closes there).
> TMS training (TRM, GraphSAGE/tGNN, scenario gen) happens on
> **acer-nitro**. Both boxes have identical RTX 4060 8GB GPUs; the
> earlier "msi-stealth is the GPU box" framing is retired. Source of
> truth: `../Autonomy-Core/docs/dev/MACHINE_ROLES.md`.

## On session start — check Core for pending adoptions

Before proposing "what's next" or picking work, read
[`../Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md`](../Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md)
for recent entries and look for rows where **Autonomy-TMS** is listed
as `pending`. Those are TMS adoption gaps that Core has already shipped
and every new session should surface them first. Session-fresh threads
(whatever felt urgent in the last conversation) are secondary to
`pending` gating items.

Also scan [`../Autonomy-Core/docs/MIGRATION_REGISTER.md`](../Autonomy-Core/docs/MIGRATION_REGISTER.md)
for items at status `SHIPPED_CORE` that haven't moved to `COMPLETE` —
those need TMS-side adoption before they close.

## The rule — every TMS change

**TMS is a policy repo**, not a platform repo. Only Transport-plane
decision modules land here. Everything else — canonical state, ERP
connectors, digital twin, scenario engine, conformal framework, LLM
narration, BSC framework, governance framework, provisioning framework,
training infrastructure — lives in Autonomy-Core.

**TMS is a sibling product, not a fork** of SCP. No shared `Base`, no
SCP imports, no git upstream remote, no direct HTTP calls to SCP
(use MCP). See [.claude/rules/core-vs-product-placement.md](.claude/rules/core-vs-product-placement.md).

Apply Rule 1 (cross-product) or Rule 2 (substrate) on every change.

## Rules (modular)

- [core-vs-product-placement.md](.claude/rules/core-vs-product-placement.md) — R1/R2 + sibling-not-fork anti-patterns
- [transport-plane-invariant.md](.claude/rules/transport-plane-invariant.md) — what TMS can and can't own
- [aws-sc-data-model.md](.claude/rules/aws-sc-data-model.md) — SC→TMS entity mapping, transport modes
- [soc2-compliance.md](.claude/rules/soc2-compliance.md) — RLS, pgaudit, tenant-scoped checkpoints
- [no-fallbacks.md](.claude/rules/no-fallbacks.md) — no phantom numbers, safety-critical
- [terminology.md](.claude/rules/terminology.md) — SC→TMS term mapping
- [trm-mapping.md](.claude/rules/trm-mapping.md) — 11 SC TRMs → 11 TMS TRMs
- [aiio-model.md](.claude/rules/aiio-model.md) — Agents Always Act, no approval workflow
- [planning-hierarchy-terms.md](.claude/rules/planning-hierarchy-terms.md) — Strategic/Tactical/Operational/Execution
- [plan-separation.md](.claude/rules/plan-separation.md) — `plan_version` discipline
- [planning-cascade.md](.claude/rules/planning-cascade.md) — auto-execution schedule
- [hierarchy-drilldown.md](.claude/rules/hierarchy-drilldown.md) — geography, commodity, carrier
- [governance-pipeline.md](.claude/rules/governance-pipeline.md) — 4-step, 5-dimension scoring
- [provisioning.md](.claude/rules/provisioning.md) — 17 steps adapted for transport
- [external-integrations.md](.claude/rules/external-integrations.md) — project44, carrier APIs, weather, ports, rates
- [cross-plane-mcp-only.md](.claude/rules/cross-plane-mcp-only.md) — any cross-plane DB read goes through MCP; existing direct-DB ETL is a migration target (MIGRATION_REGISTER §3.7)

## Specialised agents

- `transport-plane-auditor` — blocks substrate landing in TMS + catches SCP cross-contamination
- `trm-reviewer` — transport-TRM invariants, MCP-only cross-app integration
- `governance-reviewer` — SOC II, AIIO, plan separation, no-fallbacks
- See [.claude/agents/](.claude/agents/)

## Guardrails

[.claude/hooks/validate-bash.sh](.claude/hooks/validate-bash.sh) blocks:
- Adding a git remote to `Autonomy-SCP` or pulling/fetching/merging from SCP
- `make rebuild-db` / `db-reset` / `reseed-db` without confirmation
- DDL from the shell (Alembic only)
- `alembic downgrade base`
- Docker volume nukes
- `git push --force` / history rewrites

## Cross-repo coordination

- [Autonomy-Core/docs/MIGRATION_REGISTER.md](../Autonomy-Core/docs/MIGRATION_REGISTER.md) — items migrating to Core (including TMS items slated for extraction)
- [Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md](../Autonomy-Core/docs/CONSUMER_ADOPTION_LOG.md) — Core changes TMS must pick up
- [Autonomy-Core/docs/SPRINT_1_EXECUTION.md](../Autonomy-Core/docs/SPRINT_1_EXECUTION.md) — current partition sprint (2026-04-21 → 2026-06-01)
- [Autonomy-Core/docs/TMS_ADOPTION_GUIDE_20260420.md](../Autonomy-Core/docs/adoption/TMS_ADOPTION_GUIDE_20260420.md) — **directional alert. Read before next commit.** Contains STOP + PREPARE lists.
- [Autonomy-DP/CLAUDE.md](../Autonomy-DP/CLAUDE.md) — sibling product (Demand Planning). TMS lane-volume forecaster reads DP via MCP at `DP_MCP_URL=http://msi-stealth.local:8290` (the cross-plane rule applies — never direct DB). **Clone `Autonomy-DP` next to `Autonomy-TMS` on every dev box** — see `CONSUMER_ADOPTION_LOG.md` 2026-04-30 entry "Clone `Autonomy-DP` on every dev box."

## Target architecture (2026-04-10)

```
github.com/azirella-ltd/
├── Autonomy-Core/        ← Monorepo: packages/autonomy-frontend (npm),
│                           packages/data-model (Python), packages/powell-core (Python).
│                           Published to GitHub Packages.
├── Autonomy-SCP/         ← SCP product (own DB, own backend, own frontend)
└── Autonomy-TMS/         ← TMS product (own DB, own backend, own frontend)
```

| Layer | Sharing mechanism |
|---|---|
| Frontend components | `@azirella-ltd/autonomy-frontend` npm package |
| Decision types | Plugin registry — each app registers its own at boot |
| Backend patterns | `@azirella-ltd/data-model` + `@azirella-ltd/powell-core` Python packages |
| Cross-app data exchange | **MCP** (Model Context Protocol) |
| DB schemas | **Independent** — TMS has its own `tms-db` container |
| Migrations | Independent — TMS Alembic chain never references SCP |
| SQLAlchemy `Base` | Independent — no shared tables, no shared `Base` |

The repo still contains legacy SCP code from the original fork. See
[docs/internal/plans/TMS_INDEPENDENCE_PLAN.md](docs/internal/plans/TMS_INDEPENDENCE_PLAN.md)
for the 5-phase migration.

---

## Vibe-coding discipline (for this repo)

This codebase follows the "leaf-node vibe coding" model from Anthropic's Erik Schluntz.

**Before executing any non-trivial task:**
1. Explore first. Identify the relevant files, classes, and patterns before writing a line.
2. Produce a written plan. List what changes, what patterns to follow, what constraints apply.
3. Classify placement: is this task touching a leaf node or core architecture?
   - Leaf node (end feature, nothing depends on it): vibe coding is acceptable.
   - Core architecture (trunks, foundational systems): requires human review of the shape before execution.

**Design for verifiability:**
- Every non-trivial change should have human-verifiable inputs and outputs.
- Prefer end-to-end tests (happy path + 2 error cases) over implementation-specific unit tests.
- When asked to write tests, default to minimalist end-to-end tests unless told otherwise.

**Context management:**
- When planning a large task, write the plan to a document before executing so it survives a /compact.
- After a major logical milestone, suggest a compact point.

---

## Prerequisite: GitHub PAT for `@azirella-ltd/autonomy-frontend`

The frontend Docker image installs `@azirella-ltd/autonomy-frontend`
from GitHub Packages.

1. Create a **classic** PAT with `read:packages` scope on the
   `azirella-ltd` org (fine-grained PATs don't support Packages as of 2026).
2. Save to `~/.config/autonomy/gh_token_packages` with `chmod 600` (no
   trailing newline).
3. `make rebuild-frontend` picks it up automatically via the BuildKit
   secret mount. Token never lands in an image layer.

Verify with `wc -c ~/.config/autonomy/gh_token_packages` — should be 40.

## Tech stack

FastAPI (Python 3.10+), SQLAlchemy 2.0, PyTorch 2.2.0, PyTorch Geometric,
React 18, Material-UI 5, Recharts, D3-Sankey, Mapbox / Leaflet (geo),
PostgreSQL 15+, Docker Compose, Nginx proxy. LLM via OpenAI-compatible
API (default `qwen3-8b`).

**External data**: project44 (visibility), weather APIs, port / terminal
APIs. See [external-integrations.md](.claude/rules/external-integrations.md).

## Development commands

```bash
# Start
make up                    # CPU mode
make up FORCE_GPU=1        # GPU mode
make up-dev                # Dev with hot-reload

# Lifecycle
make down
make logs
make restart-backend
make rebuild-backend
make rebuild-frontend

# Database  (blocked by hook without confirmation)
make db-bootstrap
make db-reset
make rebuild-db
make reseed-db
make reset-admin

# Training
make generate-simpy-data
make train-gnn
make train-default-gpu TRAIN_EPOCHS=50 TRAIN_DEVICE=cuda

# Proxy / LLM
make proxy-restart
make proxy-logs
make llm-check
```

**LLM env vars**: `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL_NAME`
(default `qwen3-8b`).

## Services

| Service | URL |
|---|---|
| Frontend | http://localhost:8089 |
| Backend API | http://localhost:8089/api |
| API Docs | http://localhost:8010/docs |
| pgAdmin | http://localhost:5051 (admin@autonomy.com / admin) |
| MCP server | http://localhost:8011 |

> **Port allocation.** TMS host ports are deliberately offset from SCP
> so both can run on the same machine:
> - SCP: 8088 / 3000 / 8000 / 5050 / 8001 / 8443
> - TMS: 8089 / 3001 / 8010 / 5051 / 8011 / 8444
>
> Container-internal ports unchanged. All host ports are env-driven
> (`PROXY_HOST_PORT`, `BACKEND_HOST_PORT`, `MCP_HOST_PORT`, …). Copy
> `deployments/.env.example` to `.env` to override at deploy time.
> See [Autonomy-Core/docs/DEPLOYMENT_PORTS.md](../Autonomy-Core/docs/architecture/DEPLOYMENT_PORTS.md).

**Default login**: `systemadmin@autonomy.com` / `Autonomy@2026`

## Key implementation details

- **Auth**: JWT + HTTP-only cookies, CSRF double-submit, capability-based permissions.
- **Routing**: Nginx proxy (host port `8089`) — `/api/*` → `backend:8000`, `/*` → `frontend:3000`.
- **Backend entry**: [backend/main.py](backend/main.py).
- **Migrations**: `backend/migrations/versions/` (not `backend/alembic/versions/`).
- **Sessions**: `SessionLocal` is async; use `sync_session_factory` for sync.
- **Docker Compose**: base `docker-compose.yml` + overlays (dev, gpu, prod, apps, db). Layer with `-f`.
- **Env setup**: `make init-env` to create `.env` from template.

## Architecture brief

> Full details: [docs/CLAUDE_REFERENCE.md](docs/CLAUDE_REFERENCE.md)

**Four Pillars**: AI Agents (TRM / GNN / LLM), Conformal Prediction,
Digital Twin, Causal AI.

**Five-Layer Agent Coordination** (shared with SC Planning):

- **L4 Strategic** — S&OP GraphSAGE → carrier portfolio, lane strategy, mode mix (weekly)
- **L3 Tactical** — Network tGNN → inter-facility directives, priority allocations (daily)
- **L2 Operational** — Site tGNN → intra-facility cross-TRM coordination, always-on
- **L1 Execution** — 11 TRMs (<10ms): CapacityPromise, EquipmentReposition,
  FreightProcurement, ShipmentTracking, LoadBuild, IntermodalTransfer,
  ExceptionManagement, DockScheduling, BrokerRouting, DemandSensing,
  CapacityBuffer. See [trm-mapping.md](.claude/rules/trm-mapping.md).
- **AAP** — cross-authority agent negotiation (seconds–minutes)
- **Escalation Arbiter** — persistent drift detection routes up

**DAG model**: 4 master types — Carrier (capacity providers), Shipper
(origins), Terminal (intermediate), Consignee (destinations). Facilities
connected by lanes.

**Key backend paths** (evolving — some still carry SC naming):

- `services/aws_sc_planning/` → will become `services/transportation_planning/`
- `services/powell/` — 11 TRM services, heuristic library, scenario engine, RL trainer
- `models/` — transportation entities, network config, user, tenant, rbac
- `api/endpoints/` — loads, shipments, carriers, rates, appointments, provisioning, auth, websocket
- `integrations/project44/` — OAuth connector, tracking service, webhook handler, data mapper, config service

**Key frontend paths**:

- `components/TwoTierNav.jsx`, `CategoryBar.jsx`, `PageBar.jsx`
- `pages/planning/` — load board, shipment tracker, lane analytics, dock schedule
- `pages/admin/` — carrier management, rate management, network config, governance
- `services/api.js` — Axios, `baseURL=/api`, `withCredentials`

## Navigation

- **Admin**: Network Config → Carrier Management → Rate Management →
  Decision Governance → User Management → Role Management → Context
  Engine → TMS Data Management → Stochastic Parameters → Metric
  Configuration → BSC Configuration
- **Tactical Planning**: Load Planning → Shipment Management → Carrier
  Procurement → Dock Scheduling → Lane Analytics → Exception Management
