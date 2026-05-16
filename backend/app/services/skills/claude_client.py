"""
Thin adapter around ``azirella_assistant.LLMClient`` with runtime
provider switching, vLLM context capping, and Qwen3 ``<think>``
stripping retained.

Migration §3.16 (2026-05-16): every call to an LLM transport now
routes through Core's substrate
(``azirella_assistant.AnthropicClient`` /
``azirella_assistant.OpenAICompatibleClient``) per CLAUDE.md "LLM
usage discipline". The previous direct-httpx implementation is
gone.

Features this adapter retains over a bare ``LLMClient``:

  * **Runtime provider switching** — reads
    ``backend/data/llm_settings.json`` at call time so the admin UI
    can flip between Claude / vLLM without a restart. Field name
    is ``briefing_provider`` or ``skills_provider`` per
    ``purpose=`` constructor arg.
  * **vLLM ``max_model_len`` discovery + context capping** — queries
    ``/v1/models`` once + caches; truncates input + caps
    ``max_tokens`` so context-length limits don't blow up briefings.
  * **Qwen3 ``<think>...</think>`` stripping** — post-processes
    vLLM responses so the residual reasoning blocks don't leak
    through to callers.
  * **``model_tier`` abstraction** — ``"haiku"`` / ``"sonnet"`` →
    actual model name, with the vLLM fallback when Claude isn't
    selected.
  * **JSON-from-markdown helper** — ``parse_json_response`` strips
    `````json`` fences.

What this adapter no longer does (handled by the substrate):

  * Building the HTTP request bodies for Anthropic Messages /
    OpenAI Chat Completions.
  * Anthropic prompt-caching wire format — the substrate's
    ``AnthropicClient`` already wraps the system prompt in a
    cached block.
  * Token-usage extraction — the substrate's ``LLMResponse.raw``
    carries the provider's untouched response.
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
import time
from typing import Any, Optional

import httpx

from azirella_assistant import (
    AnthropicClient,
    ChatMessage,
    OpenAICompatibleClient,
    Workload,
)

logger = logging.getLogger(__name__)

# Model identifiers
CLAUDE_HAIKU = "claude-haiku-4-5-20251001"
CLAUDE_SONNET = "claude-sonnet-4-6"

# Env var overrides
ENV_CLAUDE_API_KEY = "CLAUDE_API_KEY"
ENV_CLAUDE_MODEL_HAIKU = "CLAUDE_MODEL_HAIKU"
ENV_CLAUDE_MODEL_SONNET = "CLAUDE_MODEL_SONNET"
ENV_LLM_API_BASE = "LLM_API_BASE"
ENV_LLM_API_KEY = "LLM_API_KEY"
ENV_LLM_MODEL_NAME = "LLM_MODEL_NAME"


class ClaudeClient:
    """Routes LLM calls through Core's
    :class:`azirella_assistant.LLMClient` substrate while preserving
    SCP-specific features (runtime provider switching, vLLM context
    capping, Qwen3 ``<think>`` stripping).

    Usage:
        client = ClaudeClient(purpose="briefing")
        response = await client.complete(
            system_prompt="You are an ATP decision agent...",
            user_message=json.dumps(state_features),
            model_tier="haiku",
        )
    """

    def __init__(self, force_vllm: bool = False, purpose: str = "briefing"):
        """
        Args:
            force_vllm: Always route to vLLM regardless of other settings.
            purpose: "briefing" or "skills" — determines which provider setting
                     to read from llm_settings.json when in "auto" mode.
        """
        self._llm_api_base = os.getenv(ENV_LLM_API_BASE)
        self._llm_api_key = os.getenv(ENV_LLM_API_KEY, "not-needed")
        self._llm_model_name = os.getenv(ENV_LLM_MODEL_NAME, "qwen3-8b")
        self._haiku_model = os.getenv(ENV_CLAUDE_MODEL_HAIKU, CLAUDE_HAIKU)
        self._sonnet_model = os.getenv(ENV_CLAUDE_MODEL_SONNET, CLAUDE_SONNET)
        self._force_vllm = force_vllm  # bypass Anthropic API even if CLAUDE_API_KEY is set
        self._purpose = purpose  # "briefing" or "skills"
        # Substrate clients are instantiated lazily so process startup
        # isn't paying the import cost when this adapter is unused.
        # Each call resolves the right client based on `uses_claude`.
        self._anthropic: Optional[AnthropicClient] = None
        self._vllm: Optional[OpenAICompatibleClient] = None
        # vLLM context-capping helpers stay here (the substrate
        # doesn't model context-length awareness today).
        self._meta_http: Optional[httpx.AsyncClient] = None
        self._vllm_max_model_len: Optional[int] = None

    @property
    def uses_claude(self) -> bool:
        """Whether we're using Claude API (vs vLLM fallback).

        Reads CLAUDE_API_KEY and the runtime LLM settings file at call time so
        the provider can be switched from the admin UI without a restart.

        Priority:
          1. force_vllm=True on this instance → always vLLM
          2. Runtime settings file (briefing_provider / skills_provider)
          3. CLAUDE_API_KEY present → Claude; absent → vLLM
        """
        if self._force_vllm:
            return False

        api_key = os.getenv(ENV_CLAUDE_API_KEY)

        # Read runtime settings (file written by PUT /api/v1/config/llm)
        provider = self._read_runtime_provider()
        if provider == "claude":
            return bool(api_key)
        if provider == "vllm":
            return False
        # "auto" — use Claude if key is available
        return bool(api_key)

    def _read_runtime_provider(self) -> str:
        """Read the provider setting from the runtime settings file.

        Returns "auto", "claude", or "vllm".
        Reads the field matching self._purpose ("briefing_provider" or "skills_provider").
        """
        import json as _json
        settings_path = os.path.join(
            os.path.dirname(__file__), "../../../data/llm_settings.json"
        )
        settings_path = os.path.abspath(settings_path)
        field = f"{self._purpose}_provider"
        try:
            if os.path.exists(settings_path):
                with open(settings_path) as f:
                    data = _json.load(f)
                return data.get(field, "auto")
        except Exception:
            pass
        return "auto"

    def _get_anthropic(self) -> AnthropicClient:
        if self._anthropic is None:
            workload = (
                Workload.NARRATION if self._purpose == "briefing"
                else Workload.CHAT
            )
            self._anthropic = AnthropicClient(workload=workload)
        return self._anthropic

    def _get_vllm(self) -> OpenAICompatibleClient:
        if self._vllm is None:
            workload = (
                Workload.NARRATION if self._purpose == "briefing"
                else Workload.CHAT
            )
            self._vllm = OpenAICompatibleClient(
                workload=workload,
                base_url=self._llm_api_base,
                api_key=self._llm_api_key,
                model=self._llm_model_name,
            )
        return self._vllm

    def _resolve_model(self, model_tier: str) -> str:
        """Resolve model tier to actual model identifier."""
        if self.uses_claude:
            if model_tier == "haiku":
                return self._haiku_model
            return self._sonnet_model
        return self._llm_model_name

    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        model_tier: str = "haiku",
        temperature: float = 0.1,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """
        Send a completion request through Core's LLMClient substrate.

        Args:
            system_prompt: The SKILL.md content + RAG context
            user_message: JSON-encoded state features
            model_tier: "haiku" or "sonnet"
            temperature: Low for deterministic decisions
            max_tokens: Max response tokens

        Returns:
            dict with keys: content (str), model (str), tokens_used (int)
        """
        model = self._resolve_model(model_tier)
        start_time = time.monotonic()

        if self.uses_claude:
            result = await self._call_claude(
                system_prompt, user_message, model, temperature, max_tokens
            )
        elif self._llm_api_base:
            result = await self._call_vllm(
                system_prompt, user_message, model, temperature, max_tokens
            )
        else:
            raise RuntimeError(
                "No LLM backend configured. Set CLAUDE_API_KEY or LLM_API_BASE."
            )

        elapsed_ms = (time.monotonic() - start_time) * 1000
        logger.info(
            "Skill LLM call: model=%s, tokens=%d, latency=%.0fms",
            result["model"],
            result["tokens_used"],
            elapsed_ms,
        )
        return result

    async def _call_claude(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Call Anthropic Messages API via Core's AnthropicClient.

        The substrate handles prompt-caching wire format
        (``cache_control: ephemeral`` on the system block) — this
        adapter just supplies the messages + model override + decoding.
        """
        client = self._get_anthropic()
        response = await client.complete(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_message),
            ],
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        content = response.content or ""
        # Token usage isn't on LLMResponse directly; read from raw.
        raw = response.raw or {}
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        tokens = (
            int(usage.get("input_tokens", 0) or 0)
            + int(usage.get("output_tokens", 0) or 0)
        )
        return {"content": content, "model": model, "tokens_used": tokens}

    async def _get_vllm_max_model_len(self) -> int:
        """Query vLLM /v1/models to get the served model's max_model_len.

        The substrate's :class:`OpenAICompatibleClient` doesn't expose
        the model's context-length limit (it's a vLLM-specific
        diagnostic, not part of the chat-completions API). Keep the
        ``/v1/models`` query here so vLLM-served briefings don't blow
        their context window.
        """
        if self._vllm_max_model_len is not None:
            return self._vllm_max_model_len
        try:
            if self._meta_http is None or self._meta_http.is_closed:
                self._meta_http = httpx.AsyncClient(timeout=5.0)
            base = self._llm_api_base.rstrip("/") if self._llm_api_base else ""
            resp = await self._meta_http.get(
                f"{base}/models",
                headers={"Authorization": f"Bearer {self._llm_api_key}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                models = data.get("data", [])
                if models:
                    mml = models[0].get("max_model_len")
                    if mml:
                        self._vllm_max_model_len = int(mml)
                        logger.info("vLLM max_model_len=%d", self._vllm_max_model_len)
                        return self._vllm_max_model_len
        except Exception as e:
            logger.debug("Could not query vLLM model info: %s", e)
        # Conservative default if query fails
        self._vllm_max_model_len = 2048
        return self._vllm_max_model_len

    async def _call_vllm(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        """Call vLLM/Ollama via Core's OpenAICompatibleClient.

        Caps inputs to fit vLLM's ``max_model_len`` (queried out-of-band
        via :meth:`_get_vllm_max_model_len`) and strips Qwen3
        ``<think>...</think>`` blocks from the response.
        """
        client = self._get_vllm()

        # Respect the model's max_model_len: cap max_tokens and truncate inputs.
        # Conservative estimate: JSON/structured text ≈ 2.5 chars/token (not 4).
        max_model_len = await self._get_vllm_max_model_len()
        chars_per_token = 2.5
        # Reserve input_reserve tokens for input (40%), rest for output (60%)
        input_reserve = int(max_model_len * 0.40)
        output_budget = max_model_len - input_reserve - 10  # 10 token safety margin
        capped_max_tokens = min(max_tokens, output_budget)

        max_input_chars = int(input_reserve * chars_per_token)
        combined_input = system_prompt + "\n" + user_message
        if len(combined_input) > max_input_chars:
            # Give system prompt up to 50% of budget, remainder to user message
            sys_chars = min(len(system_prompt), max_input_chars // 2)
            usr_chars = max(0, max_input_chars - sys_chars - 1)
            system_prompt = system_prompt[:sys_chars]
            user_message = user_message[:usr_chars]
            logger.warning(
                "vLLM context capped to %d tokens (sys=%d, usr=%d chars, max_out=%d). "
                "Consider restarting vLLM with --max-model-len 8192 for full briefings.",
                max_model_len, sys_chars, usr_chars, capped_max_tokens,
            )

        response = await client.complete(
            messages=[
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=user_message),
            ],
            model=model,
            max_tokens=capped_max_tokens,
            temperature=temperature,
        )
        content = response.content or ""
        # Strip any residual Qwen3 <think>...</think> blocks that sneak through.
        content = _re.sub(r"<think>.*?</think>", "", content, flags=_re.DOTALL).strip()
        # Token-usage from the raw response.
        raw = response.raw or {}
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        tokens = int(usage.get("total_tokens", 0) or 0)
        return {"content": content, "model": model, "tokens_used": tokens}

    async def close(self):
        """Release the metadata HTTP client. The substrate clients
        manage their own lifecycle."""
        if self._meta_http and not self._meta_http.is_closed:
            await self._meta_http.aclose()
        if self._anthropic is not None:
            try:
                await self._anthropic.aclose()
            except Exception:
                pass

    def parse_json_response(self, content: str) -> dict[str, Any]:
        """
        Parse JSON from Claude's response, handling markdown code blocks.

        Claude sometimes wraps JSON in ```json ... ``` blocks.
        """
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines[1:] if not l.strip().startswith("```")]
            text = "\n".join(lines)
        return json.loads(text)
