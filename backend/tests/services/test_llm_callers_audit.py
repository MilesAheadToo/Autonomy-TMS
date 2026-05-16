"""§3.16 LLMClient-consumer-migration drift-prevention guard (TMS plane).

CLAUDE.md "LLM usage discipline" requires every LLM call to go
through Core's ``azirella_assistant.LLMClient`` with a
:class:`Workload` tag. The canonical audit is:

    grep -RIn 'api.anthropic.com\\|api.openai.com\\|/chat/completions' \\
        --include='*.py' backend/app/

Any hit inside ``backend/app/`` (excluding the test tree) is migration
debt that belongs on Core's MIGRATION_REGISTER.md §3.16. The
expected steady state for TMS is **no hits at all** — the substrate
lives in Core, and TMS's transports
(``claude_client.py``, ``sap_schema_agent._resolve_with_llm``,
``llm_suggestion_service``) were migrated to consume
``azirella_assistant.AnthropicClient`` / ``OpenAICompatibleClient``
in commit ``<§3.16 plane-side cleanup>`` (2026-05-16).

A regression here means somebody re-introduced a direct
``api.anthropic.com`` / ``api.openai.com`` / ``/chat/completions``
caller; route the new call through ``azirella_assistant`` instead.
"""
from __future__ import annotations

import pathlib
import re


_AUDIT_PATTERN = re.compile(
    r"api\.anthropic\.com|api\.openai\.com|/chat/completions"
)


def _iter_tms_app_python_files() -> list[pathlib.Path]:
    """Yield every .py file under ``backend/app/``.

    The test lives at ``backend/tests/services/test_llm_callers_audit.py``,
    so ``backend/app/`` is two parents up + ``app``.
    """
    backend_root = pathlib.Path(__file__).resolve().parents[2]
    app_root = backend_root / "app"
    if not app_root.is_dir():
        return []
    return [
        p for p in app_root.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_no_direct_llm_api_callers_in_tms_app() -> None:
    """Scan every Python file under TMS's ``backend/app/``. Any direct
    mention of the audit URLs (``api.anthropic.com``,
    ``api.openai.com``, ``/chat/completions``) is a §3.16 regression
    and fails CI with the full violator list."""
    violators: list[tuple[str, list[int]]] = []
    for path in _iter_tms_app_python_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not _AUDIT_PATTERN.search(content):
            continue
        hit_lines = [
            i for i, line in enumerate(content.splitlines(), start=1)
            if _AUDIT_PATTERN.search(line)
        ]
        violators.append((str(path), hit_lines))

    assert not violators, (
        "§3.16 LLMClient-substrate violation: TMS backend/app/ files "
        "contain direct LLM-API references. Route through "
        "`azirella_assistant.LLMClient` with a `Workload` tag instead. "
        "Violators:\n"
        + "\n".join(f"  {p}: lines {ls}" for p, ls in violators)
    )
