SHELL := /bin/bash

ifeq ($(OS),Windows_NT)
    SETUP_ENV := powershell -ExecutionPolicy Bypass -File scripts/setup_env.ps1
else
    SETUP_ENV := bash scripts/setup_env.sh
endif

# Default to localhost for local development
HOST = localhost
REMOTE_HOST = acer-nitro.local

# Host port allocation — defaults match docker-compose.yml. Override in
# .env (auto-loaded by Compose) or via shell export. See
# Autonomy-Core/docs/DEPLOYMENT_PORTS.md for the cross-product convention.
PROXY_HOST_PORT    ?= 8089
BACKEND_HOST_PORT  ?= 8010
PGADMIN_HOST_PORT  ?= 5051
MCP_HOST_PORT      ?= 8011
DEV_TLS_HOST_PORT  ?= 8444

# Default to GPU build unless explicitly disabled
FORCE_GPU ?= 1

# Docker build arguments for CPU
DOCKER_BUILD_ARGS_CPU = --build-arg FORCE_GPU=0

# Docker build arguments for GPU
DOCKER_BUILD_ARGS_GPU = --build-arg FORCE_GPU=1

# Docker runtime arguments
DOCKER_RUN_ARGS = -e FORCE_GPU=$(FORCE_GPU)

BASE_COMPOSE_FILES := -f docker-compose.yml
COMPOSE_FILES := $(BASE_COMPOSE_FILES)
BACKEND_BUILD_ARGS := $(DOCKER_BUILD_ARGS_CPU)

ifeq ($(FORCE_GPU),1)
    COMPOSE_FILES += -f docker-compose.gpu.yml
    BACKEND_BUILD_ARGS := $(DOCKER_BUILD_ARGS_GPU)
endif

# SCP_COLOCATED=1 opts in to the SCP co-located overlay (joins SCP's external
# Docker network so TMS can reach autonomy-scp-db). Only valid on a host that
# also runs the SCP stack (e.g. msi-stealth). Off-host, leave unset.
# `SCP_OVERLAY` is exposed as a separate variable so dev / proxy targets that
# build their own compose-file lists can append it without re-deriving the
# gate. See docker-compose.scp-colocated.yml header for the full opt-in story.
SCP_OVERLAY :=
ifeq ($(SCP_COLOCATED),1)
    COMPOSE_FILES += -f docker-compose.scp-colocated.yml
    SCP_OVERLAY := -f docker-compose.scp-colocated.yml
endif

COMPOSE_CMD = $(DOCKER_COMPOSE_CMD) $(COMPOSE_FILES)

# Default configuration name and training parameters (overridable via environment)
CONFIG_NAME ?= Default Supply Chain

SIMPY_NUM_RUNS ?= 128
SIMPY_TIMESTEPS ?= 64
SIMPY_WINDOW ?= 52
SIMPY_HORIZON ?= 1

TRAIN_EPOCHS ?= 10
TRAIN_WINDOW ?= 52
TRAIN_HORIZON ?= 1
TRAIN_DEVICE ?= cuda


# Prefer the modern Docker Compose plugin when available, but allow overriding.
DOCKER ?= docker
DOCKER_COMPOSE ?= $(shell if command -v $(DOCKER) >/dev/null 2>&1 && $(DOCKER) compose version >/dev/null 2>&1; then echo "$(DOCKER) compose"; elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; else echo "$(DOCKER) compose"; fi)

# Compose V1 (the standalone docker-compose binary) is incompatible with newer
# Docker Engine releases because the Engine no longer exposes the legacy
# ContainerConfig field in its API. Detect the version early and, when we fall
# back to docker-compose, downgrade the API version so the helper keeps working.
COMPOSE_VERSION := $(shell $(DOCKER_COMPOSE) version --short 2>/dev/null)
COMPOSE_VERSION_NORMALIZED := $(patsubst v%,%,$(COMPOSE_VERSION))
COMPOSE_IS_V1 := 0
ifeq ($(firstword $(DOCKER_COMPOSE)),docker-compose)
    COMPOSE_IS_V1 := 1
else ifneq ($(COMPOSE_VERSION_NORMALIZED),)
    ifneq (,$(filter 1.%,$(COMPOSE_VERSION_NORMALIZED)))
        COMPOSE_IS_V1 := 1
    endif
endif

COMPOSE_ENV :=
ifeq ($(COMPOSE_IS_V1),1)
    COMPOSE_ENV := COMPOSE_API_VERSION=1.44 DOCKER_API_VERSION=1.44
endif

DOCKER_COMPOSE_CMD = $(strip $(COMPOSE_ENV) $(DOCKER_COMPOSE))

.PHONY: up gpu-up up-dev down ps logs reload reload-backend reload-frontend seed reset-admin help init-env proxy-up proxy-down proxy-restart proxy-recreate proxy-logs proxy-url seed-default-group seed-demo-configs seed-three-fg-demo seed-variable-demo all_demo_configs build-create-users db-bootstrap db-reset rebuild-db reseed-db rebuild-gpu train-gnn llm-check generate-site-agent-data train-site-agent train-site-agent-full eval-site-agent test-powell test-engines test-site-agent test-food-dist test-food-dist-trm generate-food-dist train-and-test-food-dist train-and-test-food-dist-quick train-and-test-food-dist-gpu up-llm up-llm-ollama ollama-pull-models openclaw-setup openclaw-up openclaw-down openclaw-logs picoclaw-workspaces picoclaw-fleet picoclaw-up picoclaw-down picoclaw-logs picoclaw-status aws-init aws-plan aws-apply aws-destroy sap-start sap-stop sap-status

# =========================================================================
# LOCAL LLM TARGETS (vLLM + Ollama for RAG)
# =========================================================================

# Start full stack with vLLM (Qwen 3 8B) — requires NVIDIA GPU with >= 8GB VRAM
up-llm:
	@echo "\n[+] Starting full stack with local LLM (vLLM + Qwen 3 8B)..."
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.llm.yml --profile vllm up -d
	@echo "\n[✓] Stack started with local LLM."
	@echo "   App:     http://$(HOST):$(PROXY_HOST_PORT)"
	@echo "   vLLM:    http://$(HOST):8001/v1 (OpenAI-compatible)"
	@echo "   Note:    First start downloads model (~5GB). Check: docker logs autonomy-vllm"

# Start full stack with Ollama (lighter, good for dev)
up-llm-ollama:
	@echo "\n[+] Starting full stack with Ollama..."
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.llm.yml --profile ollama up -d
	@echo "\n[✓] Stack started with Ollama."
	@echo "   App:     http://$(HOST):$(PROXY_HOST_PORT)"
	@echo "   Ollama:  http://$(HOST):11434"
	@echo "   Next:    make ollama-pull-models"

# Pull required models into Ollama
ollama-pull-models:
	@echo "\n[+] Pulling Qwen 3 8B and Nomic Embed Text into Ollama..."
	docker exec autonomy-tms-ollama ollama pull qwen3:8b
	docker exec autonomy-tms-ollama ollama pull nomic-embed-text
	@echo "\n[✓] Models ready."

# Default CPU target
up:
	@echo "\n[+] Building and starting full system (proxy, frontend, backend, db)..."; \
	$(COMPOSE_CMD) build --no-cache $(BACKEND_BUILD_ARGS) backend && \
	$(COMPOSE_CMD) up -d proxy frontend backend db create-users && \
	if [ "$(FORCE_GPU)" = "1" ]; then \
		$(MAKE) --no-print-directory db-bootstrap; \
	fi; \
	mode_label="CPU"; \
	if [ "$(FORCE_GPU)" = "1" ]; then mode_label="GPU"; fi; \
	echo "\n[✓] Local development server started ($${mode_label} mode)."; \
	echo "   URL:     http://$(HOST):$(PROXY_HOST_PORT)"; \
	echo "   SystemAdmin: systemadmin@autonomy.ai / Autonomy@2026"; \
	if [ "$(FORCE_GPU)" = "1" ]; then \
		echo "   GPU:     $$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader 2>/dev/null || echo 'No GPU detected')"; \
	fi

# GPU target
gpu-up:
	@echo "\n[+] Pruning dangling Docker images..."; \
	set -e; \
	$(DOCKER) image prune -f >/dev/null; \
	echo "\n[+] Rebuilding frontend and backend images (GPU)..."; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml build --no-cache $(DOCKER_BUILD_ARGS_GPU) backend frontend; \
	echo "\n[+] Starting GPU stack (DB unchanged)..."; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml up -d proxy frontend backend db; \
	echo "\n[✓] GPU stack started."

up-dev:
	@echo "\n[+] Building and starting full system with dev overrides (proxy, frontend, backend, db)..."; \
	echo "   Build type: CPU (default)"; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) build $(DOCKER_BUILD_ARGS) backend && \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) up -d proxy frontend backend db create-users; \
	echo "\n[✓] Local development server started with dev overrides (CPU mode)."; \
	echo "   URL:     http://$(HOST):$(PROXY_HOST_PORT)"; \
	echo "   SystemAdmin: systemadmin@autonomy.ai / Autonomy@2026"

up-remote:
	@echo "\n[+] Building and starting full system for remote access..."; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) up -d --build proxy frontend backend db create-users; \
	echo "\n[✓] Remote server started."; \
	echo "   URL:     http://$(REMOTE_HOST):$(PROXY_HOST_PORT)"; \
	echo "   SystemAdmin: systemadmin@autonomy.ai / Autonomy@2026"; \
	echo "\n   For local development, use: make up-dev"

up-tls:
	@echo "\n[+] Building and starting full system with TLS proxy on 8443..."; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml --profile tls up -d --build frontend backend db proxy-tls create-users; \
	echo "\n[✓] Local HTTPS server started (self-signed)."; \
	echo "   URL:     https://$(HOST):$(DEV_TLS_HOST_PORT)"; \
	echo "   SystemAdmin: systemadmin@autonomy.ai / Autonomy@2026"; \
	echo "\n   For remote HTTPS access, use: make up-remote-tls"

up-remote-tls:
	@echo "\n[+] Building and starting full system with TLS for remote access..."; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml --profile tls up -d --build frontend backend db proxy-tls create-users; \
	echo "\n[✓] Remote HTTPS server started (self-signed)."; \
	echo "   URL:     https://$(REMOTE_HOST):$(DEV_TLS_HOST_PORT)"; \
	echo "   SystemAdmin: systemadmin@autonomy.ai / Autonomy@2026"

up-tls-only:
	@echo "\n[+] Starting TLS-only proxy (no HTTP proxy on $(PROXY_HOST_PORT))..."; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml --profile tls up -d --build frontend backend db proxy-tls create-users; \
	echo "\n[✓] Started. Open https://172.29.20.187:$(DEV_TLS_HOST_PORT) in your browser (self-signed)."; \
	echo "   SystemAdmin login: systemadmin@autonomy.ai / Autonomy@2026"

rebuild-frontend:
	@if [ ! -f "$${NPM_TOKEN_FILE:-$$HOME/.config/autonomy/gh_token_packages}" ]; then \
		echo "\n[!] Missing GitHub PAT for @azirella-ltd/autonomy-frontend install."; \
		echo "    Expected at: $${NPM_TOKEN_FILE:-$$HOME/.config/autonomy/gh_token_packages}"; \
		echo "    Create a classic PAT with read:packages scope on the azirella-ltd org"; \
		echo "    and save the token (no newline) to that path with chmod 600."; \
		exit 1; \
	fi; \
	echo "\n[+] Rebuilding frontend image with BuildKit secret..."; \
	DOCKER_BUILDKIT=1 $(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) build frontend; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) up -d frontend; \
	$(MAKE) --no-print-directory proxy-restart; \
	echo "\n[✓] Frontend rebuilt and restarted."

rebuild-backend:
	@if [ ! -f "$${AUTONOMY_CORE_TOKEN_FILE:-$$HOME/.config/autonomy/gh_token_autonomy_core}" ]; then \
		echo "\n[!] Missing GitHub PAT for azirella-data-model install."; \
		echo "    Expected at: $${AUTONOMY_CORE_TOKEN_FILE:-$$HOME/.config/autonomy/gh_token_autonomy_core}"; \
		echo "    Create a fine-grained PAT with Contents:Read on azirella-ltd/Autonomy-Core"; \
		echo "    and save the token (no newline) to that path with chmod 600."; \
		exit 1; \
	fi; \
	echo "\n[+] Rebuilding backend image with BuildKit secret..."; \
	echo "   Build type: $(if $(filter 1,$(FORCE_GPU)),GPU,CPU)"; \
	DOCKER_BUILDKIT=1 $(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) build $(DOCKER_BUILD_ARGS) backend; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.dev.yml $(SCP_OVERLAY) up -d backend; \
	echo "\n[✓] Backend rebuilt and restarted."

# GPU-specific targets
gpu-up-dev:
	$(MAKE) $(subst gpu-,,$@) FORCE_GPU=1

# CPU-specific targets
cpu-up cpu-up-dev:
	$(MAKE) $(subst cpu-,,$@) FORCE_GPU=0

gpu-db-up:
	@echo "\n[+] Starting database container (GPU stack)...";
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml up -d db

gpu-migrate:
	@echo "\n[+] Running Alembic migrations (GPU stack)...";
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml run --rm backend alembic upgrade head

gpu-seed:
	@echo "\n[+] Seeding database, generating training data, and training GNN (GPU stack)...";
	agent_flag=""; \
	if [ -n "$(AGENT_STRATEGY)" ]; then agent_flag="--agent-strategy $(AGENT_STRATEGY)"; fi; \
	autonomy_flag=""; \
	if [ -n "$(SKIP_AUTONOMY_GAMES)" ]; then autonomy_flag="--skip-autonomy-games"; fi; \
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml run --rm backend \
		python3 scripts/seed_default_tenant.py --run-dataset --run-training --force-dataset --force-training $$agent_flag $$autonomy_flag

gpu-bootstrap:
	@$(MAKE) down
	@$(MAKE) gpu-db-up
	@$(MAKE) gpu-migrate
	@$(MAKE) gpu-seed

rebuild-gpu:
	@echo "\n[+] Rebuilding GPU stack from scratch..."
	@$(MAKE) --no-print-directory down
	@echo "\n[+] Removing cached images for a clean GPU build..."
	@$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml down --rmi all 2>/dev/null || true
	@$(DOCKER) image prune -f >/dev/null
	@echo "\n[+] Starting GPU stack..."
	@$(MAKE) --no-print-directory gpu-up
	@echo "\n[+] Waiting for database to become available..."
	@$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml exec -T backend python3 scripts/wait_for_db.py
	@echo "\n[+] Seeding default data (humans, Naive, LLM, Autonomy GNN)..."
	@$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml exec -T backend bash -lc 'python3 scripts/seed_default_tenant.py --use-human-players'
	@$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.gpu.yml exec -T backend python3 scripts/ensure_agent_games.py
	@echo "\n[✓] GPU rebuild complete. Default games are ready."

down:
	@echo "\n[+] Stopping containers (keeping volumes)..."; \
	set -e; \
	$(COMPOSE_CMD) stop; \
	echo "\n[+] Pruning old dangling Docker images..."; \
	$(DOCKER) image prune -f >/dev/null; \
	echo "\n[✓] Containers stopped and old images pruned."

ps:
	@$(COMPOSE_CMD) ps

logs:
	@$(COMPOSE_CMD) logs -f --tail=200

reload:
	@echo "\n[+] Reloading all services (proxy, frontend, backend, db, create-users)..."; \
	$(COMPOSE_CMD) restart proxy frontend backend db create-users

reload-backend:
	@echo "\n[+] Reloading backend service..."; \
	$(COMPOSE_CMD) restart backend

reload-frontend:
	@echo "\n[+] Reloading frontend and proxy services..."; \
	$(COMPOSE_CMD) restart frontend proxy

restart-backend:
	@echo "\n[+] Restarting backend service..."; \
	$(COMPOSE_CMD) restart backend

restart-frontend:
	@echo "\n[+] Restarting frontend and proxy services..."; \
	$(COMPOSE_CMD) restart frontend proxy

# Proxy management
proxy-up:
	@echo "\n[+] Starting proxy service..."
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.proxy.yml up -d --no-deps proxy

proxy-down:
	@echo "\n[+] Stopping proxy service..."
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.proxy.yml stop proxy

proxy-clean:
	@echo "\n[+] Removing proxy container..."
	-$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.proxy.yml rm -f proxy

proxy-restart: proxy-down proxy-up

proxy-recreate: proxy-clean
	@echo "\n[+] Recreating proxy service with a fresh container..."
	$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.proxy.yml up -d --build proxy

proxy-logs:
	@$(DOCKER_COMPOSE_CMD) -f docker-compose.yml -f docker-compose.proxy.yml logs -f --tail=200 proxy

seed:
	@echo "\n[+] Seeding default users..."; \
	$(DOCKER_COMPOSE_CMD) run --rm create-users

SEED_ARGS ?=

build-create-users:
	@echo "\n[+] Rebuilding lightweight seeding image..."; \
	pull_flag=""; \
	if [ -n "$(PULL)" ]; then pull_flag="--pull"; fi; \
	$(DOCKER_COMPOSE_CMD) build $$pull_flag create-users; \
	echo "\n[✓] create-users image refreshed."; \
	echo "    Hint: leave requirements*.txt untouched to maximise Docker build caching."

db-bootstrap:
	@echo "\n[+] Bootstrapping Autonomy defaults (config, users, showcase scenarios)..."; \
	$(MAKE) --no-print-directory all_demo_configs

bootstrap-system:
	@echo "\n[+] Running full system bootstrap (DB init, seeding, dataset, GNN training)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python3 scripts/bootstrap_system.py

db-reset:
	@echo "\n[+] Resetting scenarios and rebuilding Autonomy training artifacts..."; \
	$(MAKE) --no-print-directory all_demo_configs SEED_ARGS=""

rebuild-db:
	@echo "\n[+] Rebuilding database container and volume..."; \
	set -e; \
	$(MAKE) --no-print-directory down; \
	$(COMPOSE_CMD) up -d db; \
	echo "\n[✓] Database rebuilt. Run 'make reseed-db' to repopulate defaults."

reseed-db:
	@echo "\n[+] Reseeding Autonomy defaults (skipping dataset/training)..."; \
	$(MAKE) --no-print-directory db-bootstrap; \
	echo "\n[✓] Database reseeded."

seed-default-tenant:
	@$(MAKE) --no-print-directory db-bootstrap

seed-demo-configs:
	@echo "\n[+] Seeding default demo tenant and configs..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python3 scripts/seed_demo_configs.py $(SEED_ARGS)

seed-three-fg-demo:
	@echo "\n[+] Seeding Three FG demo tenant..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python3 scripts/seed_three_fg_demo.py $(SEED_ARGS)

seed-variable-demo:
	@echo "\n[+] Seeding Variable demo tenant..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python3 scripts/seed_variable_demo.py $(SEED_ARGS)

warm-start-all:
	@echo "\n[+] Generating warm start historical data for all configs..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/seed_warm_start_all_configs.py

warm-start-food-dist:
	@echo "\n[+] Generating warm start data for Food Distribution (config_id=22)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/seed_warm_start_all_configs.py --config-id 22

seed-food-dist-pipeline:
	@echo "\n[+] Running forecast pipeline for Food Distribution..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/seed_food_dist_pipeline.py

seed-infor-demo:
	@echo "\n[+] Seeding Infor M3 demo tenants (Midwest Industrial Supply)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python3 scripts/seed_infor_demo.py

generate-infor-demo-data:
	@echo "\n[+] Generating Infor M3 demo data..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python3 scripts/generate_infor_demo_data.py /tmp/infor_export

all_demo_configs:
	@$(MAKE) --no-print-directory seed-demo-configs SEED_ARGS="$(SEED_ARGS)"
	@$(MAKE) --no-print-directory seed-three-fg-demo SEED_ARGS="$(SEED_ARGS)"
	@$(MAKE) --no-print-directory seed-variable-demo SEED_ARGS="$(SEED_ARGS)"
	@$(MAKE) --no-print-directory warm-start-all
	@$(MAKE) --no-print-directory seed-food-dist-pipeline

reset-admin:
	@echo "\n[+] Resetting superadmin password to Autonomy@2026..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/reset_admin_password.py

setup-default-env:
	@echo "\n[+] Setting up default environment..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/setup_default_environment.py

proxy-url:
	@echo "Current host: $(HOST) (set with HOST=ip make ...)"; \
	echo "HTTP:  http://$(HOST):$(PROXY_HOST_PORT)"; \
	echo "HTTPS: https://$(HOST):$(DEV_TLS_HOST_PORT) (enable with: make up-tls)"; \
	echo "Login: systemadmin@autonomy.ai / Autonomy@2026"; \
	echo "To change host: HOST=your-ip make ..."

help:
        @echo "Available targets:"; \
	echo ""; \
	echo "Local Development (CPU/GPU):"; \
	echo "  make up            - build/start HTTP proxy ($(PROXY_HOST_PORT)), frontend, backend, db, seed (CPU mode)"; \
	echo "  make gpu-up        - prune images, rebuild frontend/backend, start GPU stack"; \
	echo "  make up FORCE_GPU=1 - enable GPU support if available (set to 1 to enable)"; \
	echo "  make up-dev        - same as up, with dev overrides"; \
	echo "  make up-tls        - start with HTTPS (8443) using self-signed cert"; \
	echo ""; \
	echo "GPU-Specific Commands:"; \
	echo "  make up FORCE_GPU=1 - build/start with GPU support"; \
	echo "  make up-dev FORCE_GPU=1 - same as above, with dev overrides"; \
	echo "  make up-tls FORCE_GPU=1 - start with HTTPS and GPU support"; \
	echo ""; \
	echo "Remote Server:"; \
	echo "  make up-remote     - start server for remote access (HTTP $(PROXY_HOST_PORT))"; \
	echo "  make up-remote-tls - start with HTTPS (8443) for remote access"; \
	echo ""; \
	echo "Common Operations:"; \
	echo "  make down          - stop containers, keep volumes, prune dangling images"; \
	echo "  make ps            - show container status"; \
	echo "  make logs          - tail logs"; \
	echo "  make restart-backend - restart the backend service"; \
	echo "  make restart-frontend - restart the frontend and proxy services"; \
	echo "  make rebuild-frontend - rebuild frontend, then restart proxy"; \
	echo "  make rebuild-backend  - rebuild and restart only backend"; \
	echo "  make db-bootstrap  - create default config, users, and Autonomy games (skips dataset/training)"; \
	echo "  make bootstrap-system - initialise DB, seed defaults, generate dataset, and train Autonomy GNN"; \
	echo "  make db-reset      - delete games then rerun Autonomy bootstrap"; \
	echo "  make rebuild-db    - drop and recreate the database volume/container"; \
	echo "  make reseed-db     - reseed Autonomy defaults (skips dataset/training)"; \
	echo "  make proxy-up      - start or restart only the proxy container"; \
	echo "  make proxy-recreate - force-rebuild the proxy container without touching deps"; \
	echo "  make proxy-logs    - tail proxy logs"; \
	echo "  make seed          - run user seeder (system administrator user)"; \
	echo "  make reset-admin   - reset system administrator password to Autonomy@2026"; \
	echo "  make proxy-url     - print URLs and login info"; \
        echo "  make init-env      - set up .env from template or host-specific file"; \
        echo "  make llm-check     - test LLM endpoint connectivity"; \
        echo ""; \
        echo ""; \
        echo "AWS Deployment (Autonomy + SAP):"; \
        echo "  make aws-init      - initialize Terraform in deploy/aws/"; \
        echo "  make aws-plan      - plan AWS infrastructure changes"; \
        echo "  make aws-apply     - apply AWS infrastructure"; \
        echo "  make aws-destroy   - tear down all AWS resources"; \
        echo "  make sap-start     - start SAP S/4HANA instance"; \
        echo "  make sap-stop      - stop SAP S/4HANA instance"; \
        echo "  make sap-status    - check SAP instance state and session cost"; \
        echo ""; \
        echo "Advanced Training:"; \
        echo "  make train-setup   - create Python venv and install training deps"; \
        echo "  make train-cpu     - run local CPU training"; \
        echo "  make train-gpu     - run local GPU training"; \
        echo "  make generate-simpy-data - exec backend task to build SimPy dataset"; \
        echo "  make train-default-gpu   - exec backend task to train default model on GPU"; \
        echo "  make train-gnn     - generate naive-agent dataset and train temporal GNN"; \
        echo "  make remote-train  - train on remote server"; \
        echo ""; \
        echo "Environment Variables:"; \
        echo "  FORCE_GPU=1        - Enable GPU support (e.g., make up FORCE_GPU=1)";


llm-check:
	@echo "\n[+] Testing LLM endpoint connectivity..."
	@docker compose exec backend python scripts/check_llm_connection.py || echo "\n[!] LLM check failed. Is the backend running? (make up)"

init-env:
	@$(SETUP_ENV)

# Remote training wrappers (see scripts/remote_train.sh for full help)
REMOTE        ?=
REMOTE_DIR    ?= ~/autonomy
EPOCHS        ?= 50
DEVICE        ?= cuda
WINDOW        ?= 52
HORIZON       ?= 1
NUM_RUNS      ?= 128
T             ?= 64
DATASET       ?=
SAVE_LOCAL    ?= backend/checkpoints/supply_chain_gnn.pth

remote-train:
	@if [ -z "$(REMOTE)" ]; then echo "REMOTE is required, e.g. make remote-train REMOTE=user@host"; exit 1; fi; \
	bash scripts/remote_train.sh \
	  --remote "$(REMOTE)" \
	  --remote-dir "$(REMOTE_DIR)" \
	  --epochs "$(EPOCHS)" \
	  --device "$(DEVICE)" \
	  --window "$(WINDOW)" \
	  --horizon "$(HORIZON)" \
	  --num-runs "$(NUM_RUNS)" \
	  --T "$(T)" \
	  --save-local "$(SAVE_LOCAL)"

remote-train-dataset:
	@if [ -z "$(REMOTE)" ]; then echo "REMOTE is required, e.g. make remote-train-dataset REMOTE=user@host DATASET=..."; exit 1; fi; \
	if [ -z "$(DATASET)" ]; then echo "DATASET is required for remote-train-dataset"; exit 1; fi; \
	bash scripts/remote_train.sh \
	  --remote "$(REMOTE)" \
	  --remote-dir "$(REMOTE_DIR)" \
	  --epochs "$(EPOCHS)" \
	  --device "$(DEVICE)" \
	  --dataset "$(DATASET)" \
	  --save-local "$(SAVE_LOCAL)"

# Local training helpers
train-setup:
	@echo "\n[+] Setting up local training environment (venv + deps)..."; \
	cd backend && bash scripts/setup_training_env.sh

train-cpu:
	@echo "\n[+] Running local CPU training..."; \
	cd backend && bash run_training.sh

train-gpu:
	@echo "\n[+] Running local GPU training..."; \
	cd backend && bash run_training_gpu.sh

generate-simpy-data:
	@echo "\n[+] Generating SimPy training dataset inside backend container..."; \
	set -e; \
	force_flag=""; \
	if [ -n "$(SIMPY_FORCE)" ]; then force_flag="--force"; fi; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/generate_simpy_dataset.py \
	  --config-name "$(CONFIG_NAME)" \
	  --num-runs $(SIMPY_NUM_RUNS) \
	  --timesteps $(SIMPY_TIMESTEPS) \
	  --window $(SIMPY_WINDOW) \
	  --horizon $(SIMPY_HORIZON) \
	  $$force_flag
	@echo "\n[✓] Dataset generation task completed."

train-default-gpu:
	@echo "\n[+] Training default Autonomy agent with GPU inside backend container..."; \
	set -e; \
	dataset_flag=""; \
	force_flag=""; \
	if [ -n "$(TRAIN_DATASET)" ]; then dataset_flag="--dataset $(TRAIN_DATASET)"; fi; \
	if [ -n "$(TRAIN_FORCE)" ]; then force_flag="--force"; fi; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/train_gpu_default.py \
	  --config-name "$(CONFIG_NAME)" \
	  --device "$(TRAIN_DEVICE)" \
	  --epochs $(TRAIN_EPOCHS) \
	  --window $(TRAIN_WINDOW) \
	  --horizon $(TRAIN_HORIZON) \
	  $$dataset_flag $$force_flag
	@echo "\n[✓] GPU training task completed."

train-gnn:
	@echo "\n[+] Generating tGNN training data with Naive agents..."; \
	set -e; \
	force_flag=""; \
	if [ -n "$(SIMPY_FORCE)" ]; then force_flag="--force"; fi; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/generate_simpy_dataset.py \
	  --config-name "$(CONFIG_NAME)" \
	  --num-runs $(SIMPY_NUM_RUNS) \
	  --timesteps $(SIMPY_TIMESTEPS) \
	  --window $(SIMPY_WINDOW) \
	  --horizon $(SIMPY_HORIZON) \
	  --agent-strategy naive \
	  $$force_flag; \
	echo "\n[+] Training temporal GNN..."; \
	train_force=""; \
	if [ -n "$(TRAIN_FORCE)" ]; then train_force="--force"; fi; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/train_gpu_default.py \
	  --config-name "$(CONFIG_NAME)" \
	  --device "$(TRAIN_DEVICE)" \
	  --epochs $(TRAIN_EPOCHS) \
	  --window $(TRAIN_WINDOW) \
	  --horizon $(TRAIN_HORIZON) \
	  $$train_force; \
	echo "\n[✓] tGNN training task completed."

# ============================================================================
# SiteAgent Training Targets (Powell Framework)
# ============================================================================

# Default SiteAgent training parameters
SITE_KEY ?= DEFAULT
SITE_AGENT_EPOCHS ?= 50
SITE_AGENT_BC_EPOCHS ?= 10
SITE_AGENT_BATCH_SIZE ?= 64
SITE_AGENT_DATA_SAMPLES ?= 10000

.PHONY: generate-site-agent-data train-site-agent eval-site-agent test-powell

generate-site-agent-data:
	@echo "\n[+] Generating SiteAgent training data..."; \
	set -e; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/generate_site_agent_data.py \
	  --site-key "$(SITE_KEY)" \
	  --synthetic \
	  --num-samples $(SITE_AGENT_DATA_SAMPLES) \
	  --output data/site_agent_$(SITE_KEY).json \
	  --split-output; \
	echo "\n[✓] SiteAgent data generation completed."

train-site-agent:
	@echo "\n[+] Training SiteAgent model for site $(SITE_KEY)..."; \
	set -e; \
	data_flag=""; \
	val_flag=""; \
	if [ -f "backend/data/site_agent_$(SITE_KEY).train.json" ]; then \
		data_flag="--train-data data/site_agent_$(SITE_KEY).train.json"; \
		val_flag="--val-data data/site_agent_$(SITE_KEY).val.json"; \
	fi; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/train_site_agent.py \
	  --site-key "$(SITE_KEY)" \
	  --epochs $(SITE_AGENT_EPOCHS) \
	  --bc-epochs $(SITE_AGENT_BC_EPOCHS) \
	  --batch-size $(SITE_AGENT_BATCH_SIZE) \
	  --device "$(TRAIN_DEVICE)" \
	  --checkpoint-dir checkpoints/site_agent/$(SITE_KEY) \
	  $$data_flag $$val_flag; \
	echo "\n[✓] SiteAgent training completed."

train-site-agent-full: generate-site-agent-data train-site-agent
	@echo "\n[✓] Full SiteAgent training pipeline completed."

eval-site-agent:
	@echo "\n[+] Evaluating SiteAgent model for site $(SITE_KEY)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python -c "\
from app.services.powell import SiteAgent, SiteAgentConfig; \
import torch; \
config = SiteAgentConfig(site_key='$(SITE_KEY)', model_checkpoint_path='checkpoints/site_agent/$(SITE_KEY)/final.pt'); \
agent = SiteAgent(config); \
status = agent.get_status(); \
print(f'Site: {status[\"site_key\"]}'); \
print(f'TRM loaded: {status[\"model_loaded\"]}'); \
if agent.model: \
    counts = agent.model.get_parameter_count(); \
    print(f'Parameters: {counts}'); \
"
	@echo "\n[✓] SiteAgent evaluation completed."

test-powell:
	@echo "\n[+] Running Powell framework tests..."; \
	$(DOCKER_COMPOSE_CMD) exec backend pytest tests/powell/ -v --tb=short; \
	echo "\n[✓] Powell tests completed."

# Hive trace generation and training
HIVE_EPISODES ?= 10
HIVE_PERIODS ?= 52
HIVE_SEED ?= 42
HIVE_EPOCHS ?= 30
HIVE_SAMPLES ?= 5000
HIVE_XHR_WEIGHT ?= 0.05

.PHONY: generate-hive-traces train-hive-warmstart train-hive-stress train-hive-full validate-hive

generate-hive-traces:
	@echo "\n[+] Generating hive coordination traces..."; \
	set -e; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/generate_hive_traces.py \
	  --site-key "$(SITE_KEY)" \
	  --episodes $(HIVE_EPISODES) \
	  --periods $(HIVE_PERIODS) \
	  --seed $(HIVE_SEED) \
	  --output data/hive_traces_$(SITE_KEY).json; \
	echo "\n[✓] Hive trace generation completed."

train-hive-warmstart:
	@echo "\n[+] Hive warm-start: Signal Phases 1+2 (NO_SIGNALS → URGENCY_ONLY)..."; \
	set -e; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/train_hive_warmstart.py \
	  --config-id $(CONFIG_ID) \
	  --epochs $(HIVE_EPOCHS) \
	  --num-samples $(HIVE_SAMPLES) \
	  --device $(TRAIN_DEVICE) \
	  --results-json data/hive_warmstart_results.json; \
	echo "\n[✓] Hive warm-start training completed."

train-hive-stress: generate-hive-traces
	@echo "\n[+] Hive stress: Signal Phase 3 (FULL_SIGNALS + RL/CQL)..."; \
	set -e; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/training/train_hive_stress.py \
	  --config-id $(CONFIG_ID) \
	  --epochs $(HIVE_EPOCHS) \
	  --num-samples $(HIVE_SAMPLES) \
	  --cross-head-weight $(HIVE_XHR_WEIGHT) \
	  --device $(TRAIN_DEVICE) \
	  --trace-dir data/ \
	  --results-json data/hive_stress_results.json; \
	echo "\n[✓] Hive stress training completed."

train-hive-full: train-hive-warmstart train-hive-stress
	@echo "\n[✓] Full hive training pipeline completed (warm-start + stress)."

HIVE_VALIDATE_PERIODS ?= 52
HIVE_VALIDATE_SITES ?= 4

validate-hive:
	@echo "\n[+] Running hive vs baseline comparison ($(HIVE_VALIDATE_PERIODS) periods, $(HIVE_VALIDATE_SITES) sites)..."
	$(DOCKER_COMPOSE_CMD) exec backend python -m scripts.validation.compare_hive_vs_baseline \
	  --periods $(HIVE_VALIDATE_PERIODS) \
	  --sites $(HIVE_VALIDATE_SITES) \
	  --output data/hive_validation_results.json
	@echo "\n[✓] Hive validation completed. Results in data/hive_validation_results.json"

test-engines:
	@echo "\n[+] Running deterministic engine tests..."; \
	$(DOCKER_COMPOSE_CMD) exec backend pytest tests/powell/test_engines.py -v --tb=short; \
	echo "\n[✓] Engine tests completed."

test-site-agent:
	@echo "\n[+] Running SiteAgent tests..."; \
	$(DOCKER_COMPOSE_CMD) exec backend pytest tests/powell/test_site_agent.py -v --tb=short; \
	echo "\n[✓] SiteAgent tests completed."

test-food-dist:
	@echo "\n[+] Testing SiteAgent with Food Dist configuration..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/test_site_agent_food_dist.py $(FOOD_DIST_ARGS); \
	echo "\n[✓] Food Dist test completed."

test-food-dist-trm:
	@echo "\n[+] Testing SiteAgent with Food Dist (TRM enabled)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/test_site_agent_food_dist.py --with-trm; \
	echo "\n[✓] Food Dist TRM test completed."

generate-food-dist:
	@echo "\n[+] Generating Food Dist configuration..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/generate_food_dist_config.py; \
	echo "\n[✓] Food Dist config generated."

# End-to-end training and testing with Food Dist
# Usage: make train-and-test-food-dist [FOOD_DIST_EPOCHS=50] [FOOD_DIST_SAMPLES=5000] [FOOD_DIST_DEVICE=cpu]
FOOD_DIST_EPOCHS ?= 50
FOOD_DIST_SAMPLES ?= 5000
FOOD_DIST_DEVICE ?= cpu

train-and-test-food-dist:
	@echo "\n[+] Running end-to-end SiteAgent training and testing with Food Dist..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/train_and_test_food_dist.py \
		--epochs $(FOOD_DIST_EPOCHS) \
		--samples $(FOOD_DIST_SAMPLES) \
		--device $(FOOD_DIST_DEVICE); \
	echo "\n[✓] End-to-end pipeline completed."

train-and-test-food-dist-quick:
	@echo "\n[+] Running quick SiteAgent training/testing (10 epochs, 1000 samples)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/train_and_test_food_dist.py \
		--epochs 10 \
		--samples 1000 \
		--device cpu; \
	echo "\n[✓] Quick pipeline completed."

train-and-test-food-dist-gpu:
	@echo "\n[+] Running GPU SiteAgent training/testing..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/train_and_test_food_dist.py \
		--epochs $(FOOD_DIST_EPOCHS) \
		--samples $(FOOD_DIST_SAMPLES) \
		--device cuda; \
	echo "\n[✓] GPU pipeline completed."

# Unified warm-start pipeline for Food Distribution (6 phases)
# Usage: make warm-start-food-dist-full [FOOD_DIST_EPOCHS=30] [FOOD_DIST_DEVICE=cpu]
.PHONY: warm-start-food-dist-full warm-start-food-dist-train warm-start-food-dist-enable warm-start-food-dist-quick

warm-start-food-dist-full:
	@echo "\n[+] Running full 6-phase warm-start pipeline for Food Dist..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/warm_start_food_dist.py \
		--epochs $(FOOD_DIST_EPOCHS) \
		--samples $(FOOD_DIST_SAMPLES) \
		--device $(FOOD_DIST_DEVICE) \
		--results-json data/warm_start_results.json; \
	echo "\n[✓] Full warm-start pipeline completed."

warm-start-food-dist-train:
	@echo "\n[+] Running training phases (1-4) for Food Dist..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/warm_start_food_dist.py \
		--phases 1,2,3,4 \
		--epochs $(FOOD_DIST_EPOCHS) \
		--samples $(FOOD_DIST_SAMPLES) \
		--device $(FOOD_DIST_DEVICE); \
	echo "\n[✓] Training phases completed."

warm-start-food-dist-enable:
	@echo "\n[+] Enabling Site tGNN + seeding demo data (phases 5-6)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/warm_start_food_dist.py \
		--phases 5,6; \
	echo "\n[✓] Site tGNN enabled and demo data seeded."

warm-start-food-dist-quick:
	@echo "\n[+] Quick warm-start (10 epochs, phases 1,3,5,6)..."; \
	$(DOCKER_COMPOSE_CMD) exec backend python scripts/warm_start_food_dist.py \
		--phases 1,3,5,6 \
		--epochs 10 \
		--samples 2000; \
	echo "\n[✓] Quick warm-start completed."



# =========================================================================
# AWS DEPLOYMENT TARGETS (Autonomy + SAP S/4HANA)
# =========================================================================

SAP_INSTANCE_ID ?= $(shell cd deploy/aws && terraform output -raw sap_instance_id 2>/dev/null || echo "")

# Initialize Terraform
aws-init:
	@echo "\n[+] Initializing Terraform..."
	cd deploy/aws && terraform init
	@echo "\n[✓] Terraform initialized. Copy terraform.tfvars.example → terraform.tfvars and customize."

# Plan deployment
aws-plan:
	@echo "\n[+] Planning AWS deployment..."
	cd deploy/aws && terraform plan

# Apply deployment
aws-apply:
	@echo "\n[+] Applying AWS deployment..."
	cd deploy/aws && terraform apply
	@echo "\n[✓] Deployment complete. Outputs:"
	@cd deploy/aws && terraform output

# Destroy all AWS resources
aws-destroy:
	@echo "\n[!] This will destroy ALL AWS resources (Autonomy + SAP)."
	cd deploy/aws && terraform destroy

# Start SAP instance
sap-start:
	@deploy/aws/sap-start.sh $(SAP_INSTANCE_ID)

# Stop SAP instance
sap-stop:
	@deploy/aws/sap-stop.sh $(SAP_INSTANCE_ID)

# Check SAP instance status
sap-status:
	@deploy/aws/sap-status.sh $(SAP_INSTANCE_ID)
