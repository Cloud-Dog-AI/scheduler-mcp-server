"""POST /v1/chains/from-natural-language — W28K-1429 NL chain synthesis.

Calls cloud_dog_llm (when configured) to translate a natural-language
description into a JSON chain definition, then validates it through the
W28K-1417 chain compiler before returning. Persistence is gated behind a
`confirm: true` flag — the default is dry-run (synthesise + validate only),
matching the brief's "human-confirm step before persistence".

Provider, parsing, schema, or compile failures are explicit. Production never
substitutes a deterministic chain or a different model.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from cloud_dog_llm import LLMRequest, Message, ResponseFormat, SessionContext, get_llm_client
from cloud_dog_llm.structured.repair import parse_and_validate, repair_structured
from cloud_dog_logging import get_logger
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from scheduler_mcp import config
from scheduler_mcp.chain import ChainCompileError, compile_chain
from scheduler_mcp.clock import get_clock
from scheduler_mcp.db import get_session_manager
from scheduler_mcp.db.models.chain import Chain, ChainStatus
from scheduler_mcp.idam import AnonymousPrincipal, resolve_principal

router = APIRouter(tags=["chains"])
_log = get_logger(__name__)


SYSTEM_PROMPT = """You are a chain compiler. Translate the user's request into a JSON object
of the form:
  {"steps": [{"step_id": "<id>", "type": "<http|mcp|a2a|wait|sub_chain>",
              "config": {...}, "needs": ["<id>", ...]}]}
Return ONLY the JSON object. Do not include prose, code fences, or markdown.
Use minimal valid step configs. For http: include url + method. For mcp:
include server + tool. For wait: include seconds. Topological order is
enforced by needs[].
"""

CHAIN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "step_id": {"type": "string"},
                    "type": {"type": "string", "enum": ["http", "mcp", "a2a", "wait", "sub_chain"]},
                    "config": {"type": "object"},
                    "needs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["step_id", "type", "config"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}


class NlChainConfigurationError(RuntimeError):
    """Canonical LLM configuration is incomplete or invalid."""


class NlChainSynthesisError(RuntimeError):
    """The configured provider did not produce a valid chain."""


class NlChainCreate(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8192)
    name: str | None = None
    target_services: list[str] | None = None
    confirm: bool = False  # default = dry-run (synthesise + validate only)


def _require(request: Request, scope: str):
    principal = resolve_principal(request)
    if isinstance(principal, AnonymousPrincipal):
        raise HTTPException(status_code=401, detail="Authentication required")
    if not principal.has_scope(scope):
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")
    return principal


def _now() -> datetime:
    n = get_clock().now()
    return n if n.tzinfo else n.replace(tzinfo=timezone.utc)


def _try_llm_synthesis(prompt: str, *, correlation_id: str) -> tuple[dict[str, Any], dict[str, str]]:
    """Synthesize one validated chain through the configured platform client."""
    provider = config.get("llm.provider")
    base_url = config.get("llm.base_url")
    model = config.get("llm.model")
    if not provider or not base_url or not model:
        raise NlChainConfigurationError("llm.provider, llm.base_url, and llm.model are required")
    think = config.get("llm.think", False)
    if not isinstance(think, bool):
        raise NlChainConfigurationError("llm.think must resolve to a boolean")
    retry_limit = min(1, max(0, int(config.get("llm.structured_repair_retries", 1) or 0)))
    timeout_seconds = float(config.get("llm.timeout_seconds", 120) or 120)
    api_key = config.get("llm.key", config.get("llm.api_key", ""))
    try:
        import asyncio

        extra_headers = {
            key: str(value)
            for key, value in {
                "HTTP-Referer": config.get("llm.openrouter.http_referer"),
                "X-Title": config.get("llm.openrouter.x_title"),
            }.items()
            if value
        }
        llm_cfg = {
            "llm": {"default_provider": str(provider)},
            "providers": {
                str(provider): {
                    "base_url": str(base_url),
                    "model": str(model),
                    "api_key": str(api_key or ""),
                    "timeout_seconds": timeout_seconds,
                    "extra_headers": extra_headers,
                }
            },
        }
        client = get_llm_client(llm_cfg)
        session = SessionContext(session_id=correlation_id, correlation_id=correlation_id)
        last_text = ""
        repair_attempts = 0

        def _invoke(user_prompt: str) -> str:
            request = LLMRequest(
                messages=[Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=user_prompt)],
                provider_id=str(provider),
                model=str(model),
                temperature=float(config.get("llm.temperature", 0.1)),
                max_tokens=int(config.get("llm.max_tokens", 1024)),
                think=think,
                think_budget=config.get("llm.think_budget"),
                include_reasoning=False,
                response_format=ResponseFormat(
                    name="scheduler_nl_chain",
                    json_schema=CHAIN_RESPONSE_SCHEMA,
                    strict=True,
                ),
            )
            coroutine = client.chat(request, session)
            try:
                asyncio.get_running_loop()
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    response = executor.submit(lambda: asyncio.run(coroutine)).result(timeout=timeout_seconds + 15)
            except RuntimeError:
                response = asyncio.run(coroutine)
            content = str(getattr(response, "content", "") or "").strip()
            if not content:
                raise NlChainSynthesisError("provider returned empty content")
            return content

        last_text = _invoke(prompt)

        def _repair(_unvalidated_text: str, validation_error: str) -> str:
            nonlocal last_text, repair_attempts
            repair_attempts += 1
            _log.warning(
                f"chain.nl_structured_repair provider={provider} model={model} attempt={repair_attempts}/{retry_limit}"
            )
            last_text = _invoke(
                f"{prompt}\n\nReturn a corrected object that satisfies the declared schema. "
                f"Validation error: {validation_error}"
            )
            return last_text

        definition, validation_error = repair_structured(
            last_text,
            CHAIN_RESPONSE_SCHEMA,
            _repair,
            max_retries=retry_limit,
        )
        if definition is not None:
            try:
                compile_chain("nl-chain-validation", 1, definition)
            except ChainCompileError as compile_error:
                if repair_attempts >= retry_limit:
                    raise NlChainSynthesisError(f"chain compile validation failed: {compile_error}") from compile_error
                last_text = _repair(last_text, f"chain compile validation failed: {compile_error}")
                definition, validation_error = parse_and_validate(last_text, CHAIN_RESPONSE_SCHEMA)
        if definition is None or validation_error is not None:
            raise NlChainSynthesisError(f"structured output validation failed: {validation_error}")
        try:
            compile_chain("nl-chain-validation", 1, definition)
        except ChainCompileError as compile_error:
            raise NlChainSynthesisError(f"chain compile validation failed: {compile_error}") from compile_error
        provenance = {"provider": str(provider), "model": str(model)}
        definition["llm_provenance"] = provenance
        return definition, provenance
    except (NlChainConfigurationError, NlChainSynthesisError):
        raise
    except Exception as error:  # noqa: BLE001 - provider taxonomy is surfaced as one typed API failure
        raise NlChainSynthesisError(f"configured provider call failed: {type(error).__name__}") from error


@router.post("/chains/from-natural-language")
def from_nl(payload: NlChainCreate, request: Request) -> dict[str, Any]:
    principal = _require(request, "schedules.write")
    correlation_id = request.headers.get("X-Correlation-ID") or request.headers.get("X-Request-ID") or uuid.uuid4().hex
    try:
        definition, provenance = _try_llm_synthesis(payload.prompt, correlation_id=correlation_id)
    except NlChainConfigurationError as error:
        raise HTTPException(status_code=503, detail={"reason": "llm_not_configured", "error": str(error)}) from error
    except NlChainSynthesisError as error:
        raise HTTPException(status_code=502, detail={"reason": "llm_synthesis_failed", "error": str(error)}) from error
    chain_id = f"ch-{uuid.uuid4().hex[:16]}"
    try:
        compiled = compile_chain(chain_id, 1, definition)
    except ChainCompileError as e:
        # Audit: failed synthesis
        _log.info(f"chain.nl_created (rejected) actor={principal.username} reason={e}")
        raise HTTPException(status_code=400, detail={"reason": "chain_compile_failed", "error": str(e)}) from e

    response = {
        "chain_id": chain_id if payload.confirm else None,
        "definition": definition,
        "execution_order": compiled.execution_order,
        "validated": True,
        "persisted": False,
        "llm_provenance": provenance,
    }
    if not payload.confirm:
        _log.info(f"chain.nl_created (dry-run) actor={principal.username} steps={len(compiled.steps)}")
        return response

    sm = get_session_manager()
    now = _now()
    with sm.session() as session:
        c = Chain(
            chain_id=chain_id,
            name=payload.name or f"nl-chain-{chain_id[-6:]}",
            description=f"NL synthesis from prompt[:80]={payload.prompt[:80]!r}",
            version=1,
            definition=definition,
            status=ChainStatus.active.value,
            owner_user_id=principal.username,
            created_at=now,
            updated_at=now,
        )
        session.add(c)
        session.commit()
    response["persisted"] = True
    _log.info(f"chain.nl_created (persisted) actor={principal.username} chain_id={chain_id}")
    return response
