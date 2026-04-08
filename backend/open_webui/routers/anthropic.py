"""
Anthropic API Router

Native Claude (Anthropic) integration for Open WebUI.

This router:
- Manages Anthropic external connection configs (admin).
- Verifies connections & lists models.
- Implements an OpenAI-compatible /chat/completions endpoint by converting payloads
  to Anthropic Messages API and converting responses back to OpenAI format.

Key features implemented (per official Anthropic docs):
- Messages API (streaming + non-streaming)
- Tool use (OpenAI tools <-> Anthropic tools/tool_use/tool_result)
- Files API integration for user uploaded files (file_id reuse + optional prompt caching)
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import contextlib
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import secrets
import time
import uuid
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from open_webui.config import DEFAULT_ANTHROPIC_VERSION
from open_webui.env import (
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    BYPASS_MODEL_ACCESS_CONTROL,
    SRC_LOG_LEVELS,
)
from open_webui.models.files import Files
from open_webui.models.models import Models
from open_webui.models.users import UserModel
from open_webui.utils.user_connections import (
    get_user_connections,
    set_user_connection_provider_config,
)
from open_webui.storage.provider import Storage
from open_webui.retrieval.document_processing import FILE_PROCESSING_MODE_NATIVE_FILE
from open_webui.utils.access_control import has_access
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_model_system_prompt_to_body,
)
from open_webui.utils.error_handling import build_error_detail

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OPENAI"])

router = APIRouter()


def _stringify_upstream_error_body(body: Any) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False, default=str)
    except Exception:
        return str(body)


def _format_anthropic_upstream_error(
    *, request_url: str, status: int, body: Any
) -> str:
    host = ""
    try:
        host = urlparse(request_url).hostname or ""
    except Exception:
        host = ""

    body_text = _stringify_upstream_error_body(body).strip()
    parts = [
        f"Anthropic upstream error ({status}){f' from {host}' if host else ''}.",
        (f"Upstream response: {body_text}" if body_text else ""),
    ]
    return "\n".join([part for part in parts if part])


def _get_anthropic_user_config(connection_user: Optional[UserModel]) -> tuple[list[str], list[str], dict]:
    """
    Resolve Anthropic connection config for a given user.

    Stored under user.settings.ui.connections.anthropic:
      - ANTHROPIC_API_BASE_URLS
      - ANTHROPIC_API_KEYS
      - ANTHROPIC_API_CONFIGS
    """
    conns = get_user_connections(connection_user)
    cfg = conns.get("anthropic") if isinstance(conns, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}

    base_urls = list(cfg.get("ANTHROPIC_API_BASE_URLS") or [])
    keys = list(cfg.get("ANTHROPIC_API_KEYS") or [])
    configs = cfg.get("ANTHROPIC_API_CONFIGS") or {}
    configs = configs if isinstance(configs, dict) else {}

    if len(keys) != len(base_urls):
        if len(keys) > len(base_urls):
            keys = keys[: len(base_urls)]
        else:
            keys = keys + [""] * (len(base_urls) - len(keys))

    return base_urls, keys, configs

# Official beta header required for Files API.
ANTHROPIC_BETA_FILES_API = "files-api-2025-04-14"


SUPPORTED_DOCUMENT_MIME_TYPES = {"application/pdf", "text/plain"}
SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)


def _normalize_headers(extra_headers: Optional[dict]) -> dict:
    headers: dict = {}
    if isinstance(extra_headers, dict):
        for k, v in extra_headers.items():
            if v is None:
                continue
            headers[str(k)] = str(v)
    return headers


def _lower_keys(headers: dict) -> set[str]:
    return {str(k).lower() for k in (headers or {}).keys()}


def _has_auth_headers(headers: dict) -> bool:
    lk = _lower_keys(headers)
    return any(k in lk for k in ("x-api-key", "authorization"))


def _clean_beta_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [v.strip() for v in re.split(r"[,\s]+", values) if v.strip()]
    if isinstance(values, list):
        return [str(v).strip() for v in values if str(v).strip()]
    return [str(values).strip()] if str(values).strip() else []


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return default


def _build_anthropic_headers(
    key: str,
    api_config: Optional[dict],
    *,
    accept: str = "application/json",
    content_type: Optional[str] = "application/json",
    extra_beta: Optional[list[str]] = None,
) -> dict:
    cfg = api_config or {}
    headers = _normalize_headers(cfg.get("headers"))

    if content_type and "content-type" not in _lower_keys(headers):
        headers["Content-Type"] = content_type
    if accept and "accept" not in _lower_keys(headers):
        headers["Accept"] = accept

    if not _has_auth_headers(headers) and key:
        auth_type = str(cfg.get("auth_type") or "x-api-key").lower().strip()
        if auth_type in ("bearer", "authorization", "bearer_auth", "openai"):
            headers["Authorization"] = f"Bearer {key}"
            # Also send x-api-key for proxies (e.g. Anyrouter) that require it
            # alongside Bearer auth for different endpoints.
            headers["x-api-key"] = key
        elif auth_type in ("none", "custom", "manual"):
            pass  # No auto-auth; user relies on custom headers only
        else:
            headers["x-api-key"] = key  # Default: native Anthropic x-api-key header

    # Anthropic requires anthropic-version on every request.
    if "anthropic-version" not in _lower_keys(headers):
        headers["anthropic-version"] = str(
            cfg.get("anthropic_version") or DEFAULT_ANTHROPIC_VERSION
        )

    betas: list[str] = []
    betas.extend(_clean_beta_list(cfg.get("anthropic_beta")))
    betas.extend(_clean_beta_list(extra_beta))

    seen = set()
    betas_unique: list[str] = []
    for b in betas:
        if b in seen:
            continue
        seen.add(b)
        betas_unique.append(b)

    if betas_unique:
        # Merge with any user-provided header value instead of ignoring required betas.
        existing_key = None
        for k in headers.keys():
            if str(k).lower() == "anthropic-beta":
                existing_key = k
                break

        if existing_key is not None:
            existing = _clean_beta_list(headers.get(existing_key))
            merged: list[str] = []
            seen = set()
            for b in existing + betas_unique:
                if b in seen:
                    continue
                seen.add(b)
                merged.append(b)
            headers[existing_key] = ",".join(merged)
        else:
            headers["anthropic-beta"] = ",".join(betas_unique)

    return headers


def _openai_chunk(
    stream_id: str,
    model_id: str,
    delta: dict,
    finish_reason: Optional[str] = None,
    index: int = 0,
    usage: Optional[dict] = None,
) -> dict:
    chunk = {
        "id": stream_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": index,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    if usage:
        chunk["usage"] = usage
    return chunk


MAX_STREAM_CHUNK_SIZE = 32 * 1024

# Extra params passthrough: allow adding new Anthropic request fields safely,
# but prevent overriding core/generated fields to avoid breaking Open WebUI's conversion layer.
_EXTRA_BODY_FORBIDDEN_KEYS = {"model", "messages", "system", "stream"}

_ANTHROPIC_FAMILY_FIRST_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:claude[-_/ ]+)?(?P<family>opus|sonnet|haiku)"
    r"(?:[-_/ ]+(?P<major>\d))(?:[-_. /]*(?P<minor>\d))?",
    re.IGNORECASE,
)
_ANTHROPIC_VERSION_FIRST_RE = re.compile(
    r"(?:^|[^a-z0-9])claude(?:[-_/ ]+(?P<major>\d))(?:[-_. /]*(?P<minor>\d))?"
    r"(?:[-_/ ]+(?P<family>opus|sonnet|haiku))",
    re.IGNORECASE,
)
_ANTHROPIC_MAX_TOKENS_FALLBACK = 8192
_ANTHROPIC_THINKING_MAX_TOKENS_FALLBACK = 16384


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _normalize_anthropic_model_text(model_id: str) -> str:
    text = str(model_id or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"(?<=\d)\.(?=\d)", "-", text)
    text = re.sub(r"\s+", "-", text)
    return text


def _parse_anthropic_model_signature(model_id: str) -> tuple[Optional[str], Optional[int], Optional[int], bool]:
    text = _normalize_anthropic_model_text(model_id)
    is_mythos = "mythos" in text

    match = _ANTHROPIC_FAMILY_FIRST_RE.search(text)
    if not match:
        match = _ANTHROPIC_VERSION_FIRST_RE.search(text)

    if not match:
        return None, None, None, is_mythos

    family = (match.group("family") or "").lower() or None
    major = _coerce_positive_int(match.group("major"))
    minor = _coerce_positive_int(match.group("minor"))
    return family, major, minor, is_mythos


def _extract_model_meta_output_cap(model_meta: Any) -> Optional[int]:
    if not isinstance(model_meta, dict):
        return None

    candidates: list[Any] = [
        model_meta.get("max_tokens"),
        model_meta.get("max_output_tokens"),
        model_meta.get("output_token_limit"),
    ]

    capabilities = model_meta.get("capabilities")
    if isinstance(capabilities, dict):
        candidates.extend(
            [
                capabilities.get("max_tokens"),
                capabilities.get("max_output_tokens"),
                capabilities.get("output_token_limit"),
            ]
        )

    for candidate in candidates:
        parsed = _coerce_positive_int(candidate)
        if parsed is not None:
            return parsed
    return None


def _build_anthropic_model_profile(model_id: str, model_meta: Any = None) -> dict[str, Any]:
    family, major, minor, is_mythos = _parse_anthropic_model_signature(model_id)
    max_output_cap = _extract_model_meta_output_cap(model_meta)

    supports_display = False
    supports_effort = False
    prefers_adaptive = False

    if is_mythos:
        supports_display = True
        supports_effort = True
        prefers_adaptive = True
    elif family in {"sonnet", "opus"} and major == 4 and minor == 6:
        supports_display = True
        supports_effort = True
        prefers_adaptive = True
    elif major == 4:
        supports_display = True

    if max_output_cap is None:
        if family == "opus" and major == 4 and minor == 6:
            max_output_cap = 128000
        elif is_mythos:
            max_output_cap = 64000
        elif major in {3, 4} or family in {"sonnet", "opus", "haiku"}:
            max_output_cap = 64000
        else:
            max_output_cap = 64000

    return {
        "family": family,
        "major": major,
        "minor": minor,
        "is_mythos": is_mythos,
        "supports_display": supports_display,
        "supports_effort": supports_effort,
        "prefers_adaptive": prefers_adaptive,
        "max_output_cap": max_output_cap,
    }


def _normalize_reasoning_effort_value(value: Any) -> tuple[Optional[str], Optional[int]]:
    if value is None:
        return None, None

    if isinstance(value, (int, float)):
        numeric = _coerce_positive_int(value)
        return None, numeric

    text = str(value).strip().lower()
    if not text:
        return None, None

    aliases = {
        "off": "none",
        "disabled": "none",
        "false": "none",
        "0": "none",
        "med": "medium",
        "xh": "xhigh",
    }
    text = aliases.get(text, text)

    numeric = _coerce_positive_int(text)
    if numeric is not None:
        return None, numeric

    return text, None


def _thinking_effort_to_budget_tokens(effort: Optional[str]) -> int:
    return {
        "none": 0,
        "minimal": 1024,
        "low": 2048,
        "medium": 8192,
        "high": 16384,
        "xhigh": 20480,
        "max": 24576,
    }.get(str(effort or "").lower(), 8192)


def _normalize_effort_for_supported_model(effort: Optional[str]) -> Optional[str]:
    if effort is None:
        return None

    text = str(effort).strip().lower()
    if text in {"", "none"}:
        return None
    if text == "minimal":
        return "low"
    if text == "xhigh":
        return "high"
    if text in {"low", "medium", "high", "max"}:
        return text
    return None


def _payload_requests_thinking(payload: dict) -> bool:
    explicit_thinking = payload.get("thinking")
    if isinstance(explicit_thinking, dict):
        etype = str(explicit_thinking.get("type") or "").lower()
        if etype in {"enabled", "adaptive"}:
            return True

    reasoning_effort, numeric_budget = _normalize_reasoning_effort_value(
        payload.get("reasoning_effort")
    )
    if numeric_budget is not None and numeric_budget > 0:
        return True
    return reasoning_effort not in (None, "", "none")


def _estimate_requested_thinking_budget(payload: dict) -> Optional[int]:
    explicit_thinking = payload.get("thinking")
    if isinstance(explicit_thinking, dict):
        etype = str(explicit_thinking.get("type") or "").lower()
        if etype == "enabled":
            explicit_budget = _coerce_positive_int(explicit_thinking.get("budget_tokens"))
            return explicit_budget or 8192
        if etype == "adaptive":
            return None

    reasoning_effort, numeric_budget = _normalize_reasoning_effort_value(
        payload.get("reasoning_effort")
    )
    if numeric_budget is not None:
        return numeric_budget
    if reasoning_effort in (None, "", "none"):
        return None
    return _thinking_effort_to_budget_tokens(reasoning_effort)


def _resolve_anthropic_max_tokens(payload: dict, model_profile: dict[str, Any]) -> int:
    max_output_cap = _coerce_positive_int(model_profile.get("max_output_cap")) or 64000
    requested_max_tokens = _coerce_positive_int(payload.get("max_tokens"))
    wants_thinking = _payload_requests_thinking(payload)

    if requested_max_tokens is not None:
        resolved = requested_max_tokens
    elif wants_thinking:
        desired_budget = _estimate_requested_thinking_budget(payload)
        if desired_budget is not None:
            resolved = max(
                4096,
                desired_budget + max(2048, int(math.ceil(desired_budget * 0.25))),
            )
        else:
            reasoning_effort, _ = _normalize_reasoning_effort_value(
                payload.get("reasoning_effort")
            )
            normalized_effort = _normalize_effort_for_supported_model(reasoning_effort)
            resolved = {
                "low": 8192,
                "medium": 16384,
                "high": 32768,
                "max": min(max_output_cap, 64000),
            }.get(
                normalized_effort,
                _ANTHROPIC_THINKING_MAX_TOKENS_FALLBACK,
            )
    else:
        resolved = _ANTHROPIC_MAX_TOKENS_FALLBACK

    if wants_thinking and resolved <= 1024:
        resolved = 2048

    return max(1, min(resolved, max_output_cap))


def _normalize_final_anthropic_payload(
    anthropic_payload: dict, model_profile: dict[str, Any]
) -> tuple[dict, Optional[int], bool]:
    payload = dict(anthropic_payload)
    max_output_cap = _coerce_positive_int(model_profile.get("max_output_cap")) or 64000
    max_tokens = _coerce_positive_int(payload.get("max_tokens"))
    if max_tokens is None:
        max_tokens = min(_ANTHROPIC_MAX_TOKENS_FALLBACK, max_output_cap)
    if max_tokens <= 0:
        max_tokens = 1

    thinking_budget: Optional[int] = None
    thinking_enabled = False
    thinking = payload.get("thinking")
    if isinstance(thinking, dict):
        thinking = dict(thinking)
        etype = str(thinking.get("type") or "").lower()
        if etype in {"enabled", "adaptive"}:
            thinking_enabled = True

        if etype == "adaptive" and not model_profile.get("prefers_adaptive"):
            etype = "enabled"
            thinking["type"] = "enabled"

        if thinking_enabled and max_tokens <= 1024:
            max_tokens = min(max_output_cap, 2048)

        if etype == "enabled":
            raw_budget = _coerce_positive_int(thinking.get("budget_tokens")) or 8192
            max_budget = max_tokens - 1
            if max_budget < 1024:
                max_tokens = min(max_output_cap, max(2048, 1025))
                max_budget = max_tokens - 1
            budget_tokens = min(max(raw_budget, 1024), max_budget)
            thinking["budget_tokens"] = budget_tokens
            thinking_budget = budget_tokens

        if model_profile.get("supports_display") and thinking_enabled:
            # Product rule: once the user enables thinking, keep it visible by default.
            thinking["display"] = "summarized"
        else:
            thinking.pop("display", None)

        payload["thinking"] = thinking

    output_config = payload.get("output_config")
    if isinstance(output_config, dict):
        if model_profile.get("supports_effort"):
            normalized_effort = _normalize_effort_for_supported_model(
                output_config.get("effort")
            )
            if normalized_effort:
                payload["output_config"] = {**output_config, "effort": normalized_effort}
            else:
                payload.pop("output_config", None)
        else:
            payload.pop("output_config", None)

    payload["max_tokens"] = max_tokens
    return payload, thinking_budget, thinking_enabled


def _resolve_thinking_payload(
    payload: dict, *, model_profile: dict[str, Any]
) -> tuple[Optional[dict], Optional[dict], Optional[int], bool]:
    explicit_thinking = payload.get("thinking")
    thinking_budget: Optional[int] = None

    if isinstance(explicit_thinking, dict):
        thinking = dict(explicit_thinking)
        etype = str(thinking.get("type") or "").lower()
        if etype == "enabled":
            explicit_budget = _coerce_positive_int(thinking.get("budget_tokens")) or 8192
            thinking["budget_tokens"] = explicit_budget
            thinking_budget = explicit_budget
        return thinking, None, thinking_budget, etype in {"enabled", "adaptive"}

    reasoning_effort, numeric_budget = _normalize_reasoning_effort_value(
        payload.get("reasoning_effort")
    )

    if numeric_budget is not None and numeric_budget > 0:
        return (
            {"type": "enabled", "budget_tokens": numeric_budget},
            None,
            numeric_budget,
            True,
        )

    if reasoning_effort in (None, "", "none"):
        return None, None, None, False

    if model_profile.get("supports_effort"):
        normalized_effort = _normalize_effort_for_supported_model(reasoning_effort) or "medium"
        return (
            {"type": "adaptive"},
            {"effort": normalized_effort},
            None,
            True,
        )

    thinking_budget = _thinking_effort_to_budget_tokens(reasoning_effort)
    if thinking_budget <= 0:
        return None, None, None, False

    return (
        {"type": "enabled", "budget_tokens": thinking_budget},
        None,
        thinking_budget,
        True,
    )


def _is_proxy_base_url(base_url: str) -> bool:
    """Check if the base URL points to a proxy rather than the official Anthropic API."""
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return host not in {"api.anthropic.com", ""}


def _is_anyrouter_url(base_url: str) -> bool:
    """Check if the base URL points to an anyrouter proxy."""
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return "anyrouter" in host


# Claude Code request format constants for proxy CC validation.
# Proxies like anyrouter require requests for premium models (opus/sonnet) to
# look like genuine Claude Code CLI requests.
_CC_USER_AGENT = "claude-cli/2.1.74 (external, sdk-cli)"


def _needs_cc_format(model_id: str, base_url: str) -> bool:
    """Check if request to a proxy needs Claude Code request format for validation."""
    if not _is_anyrouter_url(base_url):
        return False
    lower = model_id.lower()
    return "opus" in lower or "sonnet" in lower


_CC_SYSTEM_PROMPT = "You are a Claude agent, built on Anthropic's Claude Agent SDK."


def _apply_cc_format(headers: dict, payload: dict, url: str) -> str:
    """Apply Claude Code request format to headers, payload, and URL.

    Returns the modified URL (with ?beta=true appended).
    """
    # CC identification headers — only two are required by anyrouter
    headers["x-app"] = "cli"
    headers["User-Agent"] = _CC_USER_AGENT

    # --- Body adjustments ---

    # system[0] must be the CC agent prompt (anyrouter checks this)
    system = payload.get("system")
    cc_block = {"type": "text", "text": _CC_SYSTEM_PROMPT}
    if isinstance(system, list):
        payload["system"] = [cc_block] + system
    elif isinstance(system, str) and system:
        payload["system"] = [cc_block, {"type": "text", "text": system}]
    else:
        payload["system"] = [cc_block]

    # metadata.user_id must be CC format: user_<sha256>_account__session_<uuid>
    # accountUuid must be EMPTY — AnyRouter rejects fake/unknown account UUIDs
    uid = payload.get("metadata", {}).get("user_id", "anonymous")
    cc_hash = hashlib.sha256(uid.encode()).hexdigest()
    cc_session = str(uuid.uuid4())
    payload["metadata"] = {
        "user_id": f"user_{cc_hash}_account__session_{cc_session}"
    }

    # Ensure thinking is adaptive (CC uses adaptive, not enabled with budget).
    # Preserve display mode when we already resolved one upstream.
    thinking_display = None
    if isinstance(payload.get("thinking"), dict):
        current_display = payload["thinking"].get("display")
        if isinstance(current_display, str) and current_display.strip():
            thinking_display = current_display.strip()

    if "thinking" not in payload:
        payload["thinking"] = {"type": "adaptive", **({"display": thinking_display} if thinking_display else {})}
    elif isinstance(payload.get("thinking"), dict):
        payload["thinking"] = {"type": "adaptive", **({"display": thinking_display} if thinking_display else {})}

    # Ensure max_tokens is set (CC default)
    if "max_tokens" not in payload:
        payload["max_tokens"] = 32000

    # Append ?beta=true URL parameter (SDK beta.messages.create())
    if "?" not in url:
        url = url + "?beta=true"
    elif "beta=true" not in url:
        url = url + "&beta=true"

    log.info("[ANTHROPIC CC] Applied CC format: model=%s", payload.get("model"))

    # Clean up native_tool_rules if no tools are present (e.g. compatibility fallback).
    # Leaving original tool names in native_tool_rules when there's no tools array
    # can confuse the model or fail proxy validation.
    tools = payload.get("tools")
    has_tools = isinstance(tools, list) and len(tools) > 0
    if not has_tools:
        system_blocks = payload.get("system")
        if isinstance(system_blocks, list):
            payload["system"] = [
                sb for sb in system_blocks
                if not (
                    isinstance(sb, dict)
                    and sb.get("type") == "text"
                    and "native_tool_rules" in (sb.get("text") or "")
                )
            ]

    return url


# ---------------------------------------------------------------------------
# CC Tool Name Mapping for Anyrouter
# ---------------------------------------------------------------------------
# Anyrouter validates that tool names match the CC spec list.
# We map HaloWebUI's native tool names to CC spec names before sending, and
# reverse-map in the response so the middleware sees original names.

_CC_SPEC_TOOL_NAMES = [
    "WebSearch", "WebFetch", "Bash", "Read", "Edit", "Write",
    "Grep", "Glob", "TodoWrite", "NotebookEdit", "Skill",
    "AskUserQuestion", "EnterPlanMode", "ExitPlanMode",
    "KillShell", "Task", "TaskOutput",
]

# Preferred mappings (semantic fit)
_CC_PREFERRED_MAP: dict[str, str] = {
    "search_web": "WebSearch",
    "fetch_url": "WebFetch",
    "fetch_url_rendered": "Bash",
    "search_knowledge_bases": "Grep",
    "search_knowledge_files": "Glob",
    "query_knowledge_bases": "Read",
    "search_chats": "TodoWrite",
    "search_notes": "NotebookEdit",
    "search_memories": "Skill",
}


def _build_cc_tool_mapping(tool_names: list[str]) -> dict[str, str]:
    """Build mapping from original tool names to CC spec names.

    Returns {original_name: cc_name}.
    """
    mapping: dict[str, str] = {}
    used: set[str] = set()

    # Pass 1: preferred / already-CC names
    for name in tool_names:
        if name in _CC_PREFERRED_MAP:
            cc = _CC_PREFERRED_MAP[name]
            mapping[name] = cc
            used.add(cc)
        elif name in _CC_SPEC_TOOL_NAMES:
            mapping[name] = name
            used.add(name)

    # Pass 2: assign remaining tools to unused CC names
    remaining = [n for n in _CC_SPEC_TOOL_NAMES if n not in used]
    for name in tool_names:
        if name not in mapping:
            if remaining:
                cc = remaining.pop(0)
                mapping[name] = cc
                used.add(cc)
            else:
                mapping[name] = name
                log.warning("[CC TOOL MAP] No CC spec name left for tool: %s", name)

    return mapping


def _apply_cc_tool_names(payload: dict) -> dict[str, str]:
    """Map tool names in Anthropic payload to CC spec names for Anyrouter.

    Modifies *payload* in-place.
    Returns reverse mapping {cc_name: original_name}.
    """
    tools = payload.get("tools")
    if not isinstance(tools, list) or not tools:
        return {}

    tool_names = [
        t.get("name", "") for t in tools if isinstance(t, dict) and t.get("name")
    ]
    if not tool_names:
        return {}

    mapping = _build_cc_tool_mapping(tool_names)
    reverse: dict[str, str] = {v: k for k, v in mapping.items()}

    # Rename tool definitions
    for tool in tools:
        if isinstance(tool, dict):
            name = tool.get("name", "")
            if name in mapping:
                tool["name"] = mapping[name]

    # Rename tool_use blocks in assistant message history
    for msg in payload.get("messages", []):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                bname = block.get("name", "")
                if bname in mapping:
                    block["name"] = mapping[bname]

    # Update <native_tool_rules> in system prompt to use CC-mapped names.
    # The middleware injects ALLOWED_TOOL_NAMES with original HaloWebUI names,
    # but tools are now renamed to CC spec names — the mismatch confuses the model.
    system_blocks = payload.get("system")
    if isinstance(system_blocks, list):
        for sblock in system_blocks:
            if not isinstance(sblock, dict) or sblock.get("type") != "text":
                continue
            text = sblock.get("text", "")
            if "native_tool_rules" not in text:
                continue
            # Replace each original tool name in the ALLOWED_TOOL_NAMES list
            for orig, cc in mapping.items():
                text = text.replace(f"- {orig}\n", f"- {cc}\n")
            sblock["text"] = text

    log.info("[CC TOOL MAP] Mapped %d tools: %s", len(mapping), mapping)
    return reverse


def _resolve_proxy_model_alias(model_id: str, base_url: str) -> str:
    """Preserve the upstream model id unless a proxy requires a special case.

    Generic proxy-wide model alias rewriting is intentionally disabled because many relays
    only expose the short alias (e.g. claude-opus-4-6) or their own custom suffixes.
    Anyrouter keeps its own request-shape compatibility path elsewhere in this router.
    """
    if not _is_proxy_base_url(base_url):
        return model_id
    return model_id


def _merge_extra_body_into_payload(payload: dict, extra_body: Any) -> dict:
    """
    Merge a user-provided JSON object into the Anthropic Messages payload.

    Rules:
    - Only dicts are accepted.
    - Forbidden keys (model/messages/system/stream) are ignored.
    - Existing keys in payload are NOT overridden (passthrough is additive).
    """
    if not isinstance(payload, dict):
        return payload
    if not isinstance(extra_body, dict):
        return payload

    merged = dict(payload)
    for k, v in extra_body.items():
        if not isinstance(k, str):
            continue
        if k in _EXTRA_BODY_FORBIDDEN_KEYS:
            log.warning(f"[ANTHROPIC] Ignoring forbidden extra_body key: {k}")
            continue
        if k in merged:
            log.info(f"[ANTHROPIC] Skipping extra_body key that already exists: {k}")
            continue
        merged[k] = v

    return merged


def _yield_content_chunks(content: str, stream_id: str, model_id: str):
    if len(content) <= MAX_STREAM_CHUNK_SIZE:
        yield f"data: {json.dumps(_openai_chunk(stream_id, model_id, {'content': content}), ensure_ascii=False)}\n\n"
        return

    offset = 0
    while offset < len(content):
        part = content[offset : offset + MAX_STREAM_CHUNK_SIZE]
        yield f"data: {json.dumps(_openai_chunk(stream_id, model_id, {'content': part}), ensure_ascii=False)}\n\n"
        offset += MAX_STREAM_CHUNK_SIZE


@contextlib.asynccontextmanager
async def _post_preserve_method(
    session: aiohttp.ClientSession,
    url: str,
    *,
    json_data: dict,
    headers: dict,
    max_redirects: int = 5,
):
    """POST with redirect following that preserves POST method (no downgrade to GET).

    aiohttp (per HTTP spec) converts POST to GET on 301/302 redirects.
    Many API relay services use 301 redirects, causing the POST body to be
    lost.  This context manager follows redirects manually while keeping POST.
    """
    current_url = url
    resp = None
    try:
        for _ in range(max_redirects):
            resp = await session.post(
                current_url, json=json_data, headers=headers, allow_redirects=False
            )
            if resp.status in (301, 302, 307, 308) and resp.headers.get("Location"):
                redirect_url = urljoin(current_url, resp.headers["Location"])
                log.info(
                    "[ANTHROPIC] POST redirect %s: %s -> %s",
                    resp.status, current_url, redirect_url,
                )
                resp.release()
                resp = None
                current_url = redirect_url
                continue
            break
        else:
            raise Exception(f"Too many redirects from {url}")
        yield resp
    finally:
        if resp is not None and not resp.closed:
            resp.release()


async def send_get_request(url: str, key: str = None, api_config: dict = None) -> dict:
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        headers = _build_anthropic_headers(
            key or "",
            api_config,
            accept="application/json",
        )
        log.info(f"[ANTHROPIC] GET {url}")
        async with session.get(url, headers=headers) as response:
            try:
                data = await response.json(content_type=None)
            except Exception:
                text = await response.text()
                raise HTTPException(status_code=response.status, detail=text[:500])

            if response.status >= 400:
                err = data.get("error") if isinstance(data, dict) else None
                if isinstance(err, dict) and err.get("message"):
                    raise HTTPException(
                        status_code=response.status, detail=str(err.get("message"))
                    )
                raise HTTPException(status_code=response.status, detail=str(data)[:500])

            return data


class AnthropicConfigForm(BaseModel):
    ENABLE_ANTHROPIC_API: Optional[bool] = None
    ANTHROPIC_API_BASE_URLS: list[str]
    ANTHROPIC_API_KEYS: list[str]
    ANTHROPIC_API_CONFIGS: dict


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_ANTHROPIC_API": getattr(
            request.app.state.config, "ENABLE_ANTHROPIC_API", False
        ),
        "ANTHROPIC_API_BASE_URLS": getattr(
            request.app.state.config, "ANTHROPIC_API_BASE_URLS", []
        ),
        "ANTHROPIC_API_KEYS": getattr(request.app.state.config, "ANTHROPIC_API_KEYS", []),
        "ANTHROPIC_API_CONFIGS": getattr(
            request.app.state.config, "ANTHROPIC_API_CONFIGS", {}
        ),
    }


@router.post("/config/update")
async def update_config(
    request: Request, form_data: AnthropicConfigForm, user=Depends(get_admin_user)
):
    # Preserve existing per-URL prefix_id to avoid breaking chats when admins edit connections.
    prev_urls = list(
        getattr(request.app.state.config, "ANTHROPIC_API_BASE_URLS", []) or []
    )
    prev_cfgs = getattr(request.app.state.config, "ANTHROPIC_API_CONFIGS", {}) or {}
    prev_prefix_by_url: dict[str, str] = {}
    prev_empty_urls: set[str] = set()

    for idx, prev_url in enumerate(prev_urls):
        url_key = (prev_url or "").rstrip("/")
        if not url_key:
            continue
        cfg = prev_cfgs.get(str(idx), prev_cfgs.get(prev_url, {})) or {}
        raw = cfg.get("prefix_id", None)
        prefix = (
            (raw or "").strip()
            if isinstance(raw, str)
            else (str(raw).strip() if raw is not None else "")
        )
        if prefix:
            prev_prefix_by_url.setdefault(url_key, prefix)
        else:
            prev_empty_urls.add(url_key)

    request.app.state.config.ENABLE_ANTHROPIC_API = form_data.ENABLE_ANTHROPIC_API
    request.app.state.config.ANTHROPIC_API_BASE_URLS = form_data.ANTHROPIC_API_BASE_URLS
    request.app.state.config.ANTHROPIC_API_KEYS = form_data.ANTHROPIC_API_KEYS

    num_urls = len(request.app.state.config.ANTHROPIC_API_BASE_URLS)
    num_keys = len(request.app.state.config.ANTHROPIC_API_KEYS)
    if num_keys != num_urls:
        if num_keys > num_urls:
            request.app.state.config.ANTHROPIC_API_KEYS = (
                request.app.state.config.ANTHROPIC_API_KEYS[:num_urls]
            )
        else:
            request.app.state.config.ANTHROPIC_API_KEYS += [""] * (num_urls - num_keys)

    request.app.state.config.ANTHROPIC_API_CONFIGS = form_data.ANTHROPIC_API_CONFIGS

    # Remove configs not in range
    keys = list(map(str, range(len(request.app.state.config.ANTHROPIC_API_BASE_URLS))))
    request.app.state.config.ANTHROPIC_API_CONFIGS = {
        key: value
        for key, value in (request.app.state.config.ANTHROPIC_API_CONFIGS or {}).items()
        if key in keys
    }

    # Normalize prefix_id uniqueness when multiple connections exist.
    used_prefix_ids = set()
    normalized_configs: dict[str, dict] = {}

    preserved_empty_idx = None
    if len(keys) >= 1:
        for idx_str in keys:
            idx = int(idx_str)
            url = request.app.state.config.ANTHROPIC_API_BASE_URLS[idx]
            url_key = (url or "").rstrip("/")
            if url_key and url_key in prev_empty_urls:
                preserved_empty_idx = idx
                break

    for idx_str in keys:
        idx = int(idx_str)
        url = request.app.state.config.ANTHROPIC_API_BASE_URLS[idx]
        url_key = (url or "").rstrip("/")
        cfg = request.app.state.config.ANTHROPIC_API_CONFIGS.get(idx_str, {}) or {}

        prev_prefix = prev_prefix_by_url.get(url_key) if url_key else None
        prefix_id = (prev_prefix or (cfg.get("prefix_id") or "")).strip()
        if not prefix_id:
            if preserved_empty_idx == idx:
                prefix_id = ""
            elif len(keys) > 1:
                prefix_id = secrets.token_hex(4)

        if prefix_id:
            while prefix_id in used_prefix_ids:
                prefix_id = secrets.token_hex(4)
            used_prefix_ids.add(prefix_id)

        normalized_cfg = {**cfg}
        if prefix_id:
            normalized_cfg["prefix_id"] = prefix_id
        elif preserved_empty_idx == idx:
            normalized_cfg["prefix_id"] = ""
        else:
            normalized_cfg.pop("prefix_id", None)

        normalized_configs[idx_str] = normalized_cfg

    request.app.state.config.ANTHROPIC_API_CONFIGS = normalized_configs

    # Clear model cache when config changes
    from open_webui.utils.models import invalidate_base_model_cache

    request.app.state.BASE_MODELS = None
    request.app.state.ANTHROPIC_MODELS = {}
    request.app.state.MODELS = {}
    invalidate_base_model_cache(user.id)

    return await get_config(request, user=user)


class ConnectionVerificationForm(BaseModel):
    url: str
    key: str
    config: Optional[dict] = None


@router.post("/verify")
async def verify_connection(
    request: Request, form_data: ConnectionVerificationForm, user=Depends(get_verified_user)
):
    url = form_data.url
    key = form_data.key
    cfg = form_data.config or {}

    try:
        return await send_get_request(f"{url}/models", key, cfg)
    except HTTPException:
        raise
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        raise HTTPException(
            status_code=500,
            detail=build_error_detail(e, prefix="Anthropic"),
        )


async def get_all_models_responses(request: Request, user: UserModel) -> list:
    base_urls, keys, cfgs = _get_anthropic_user_config(user)
    if not base_urls:
        return []

    # If multiple connections exist, ensure stable internal prefix_id values and cache a friendly name.
    cfgs_changed = False
    if len(base_urls) > 1:
        used = set()

        preserved_empty_idx = None
        for idx, url in enumerate(base_urls):
            api_config = cfgs.get(str(idx), cfgs.get(url, {})) or {}
            if api_config.get("prefix_id", None) == "":
                preserved_empty_idx = idx
                break

        # Backward compatibility: preserve empty prefix for index 0 if legacy configs omitted prefix_id.
        if preserved_empty_idx is None:
            url0 = base_urls[0]
            cfg0 = cfgs.get("0", cfgs.get(url0, {})) or {}
            if not (cfg0.get("prefix_id") or "").strip():
                preserved_empty_idx = 0

        for idx, url in enumerate(base_urls):
            key = str(idx)
            api_config = cfgs.get(key, cfgs.get(url, {})) or {}

            name = (api_config.get("name") or api_config.get("remark") or "").strip()
            if not name:
                try:
                    name = urlparse(url).hostname or f"Connection {idx + 1}"
                except Exception:
                    name = f"Connection {idx + 1}"

            prefix_id = (api_config.get("prefix_id") or "").strip()
            if not prefix_id:
                if preserved_empty_idx == idx:
                    prefix_id = ""
                else:
                    prefix_id = secrets.token_hex(4)
                    cfgs_changed = True

            if prefix_id:
                if prefix_id in used:
                    # De-conflict duplicates (rare unless user copied configs manually).
                    next_prefix = prefix_id
                    while next_prefix in used:
                        next_prefix = secrets.token_hex(4)
                    prefix_id = next_prefix
                    cfgs_changed = True
                used.add(prefix_id)

            next_cfg = {**api_config, "name": name}
            if prefix_id:
                if next_cfg.get("prefix_id") != prefix_id:
                    cfgs_changed = True
                next_cfg["prefix_id"] = prefix_id
            elif preserved_empty_idx == idx:
                if next_cfg.get("prefix_id") != "":
                    cfgs_changed = True
                next_cfg["prefix_id"] = ""
            else:
                if "prefix_id" in next_cfg:
                    cfgs_changed = True
                next_cfg.pop("prefix_id", None)

            if cfgs.get(key) != next_cfg:
                cfgs_changed = True
            cfgs[key] = next_cfg

    # Persist prefix_id/name normalization when needed (keeps model ids stable across sessions).
    if cfgs_changed and user:
        try:
            set_user_connection_provider_config(
                user.id,
                "anthropic",
                {
                    "ANTHROPIC_API_BASE_URLS": base_urls,
                    "ANTHROPIC_API_KEYS": keys,
                    "ANTHROPIC_API_CONFIGS": cfgs,
                },
            )
        except Exception:
            # Best-effort persistence; do not block model listing.
            pass

    request_tasks = []
    for idx, url in enumerate(base_urls):
        api_config = cfgs.get(str(idx), cfgs.get(url, {})) or {}
        enable = api_config.get("enable", True)
        model_ids = api_config.get("model_ids", []) or []
        prefix_id = (api_config.get("prefix_id") or "").strip() or None

        if not enable:
            request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, None)))
            continue

        if model_ids:
            # Users sometimes copy the internal prefixed model id back into the allowlist.
            # Treat allowlisted entries as "original ids" and strip this connection's prefix if present.
            if prefix_id:
                prefix = f"{prefix_id}."
                model_ids = [
                    (m[len(prefix) :] if isinstance(m, str) and m.startswith(prefix) else m)
                    for m in model_ids
                ]

            model_list = {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "name": model_id,
                        "owned_by": "anthropic",
                        "anthropic": {"id": model_id},
                        "urlIdx": idx,
                    }
                    for model_id in model_ids
                    if model_id
                ],
            }
            request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, model_list)))
            continue

        # Default: fetch upstream model list.
        request_tasks.append(
            send_get_request(
                f"{url.rstrip('/')}/models",
                keys[idx] if idx < len(keys) else "",
                api_config,
            )
        )

    responses = await asyncio.gather(*request_tasks, return_exceptions=True)

    # Degrade gracefully: a single bad connection (401/timeout/etc.) should not break the whole models list.
    for idx, resp in enumerate(responses):
        if isinstance(resp, BaseException):
            url = base_urls[idx] if idx < len(base_urls) else ""
            if isinstance(resp, HTTPException):
                log.warning(
                    f"[ANTHROPIC] models fetch failed (idx={idx}, url={url}) "
                    f"{resp.status_code}: {resp.detail}"
                )
            else:
                log.warning(
                    f"[ANTHROPIC] models fetch failed (idx={idx}, url={url}) {type(resp).__name__}: {resp}"
                )
            responses[idx] = None

    normalized_responses = []
    for idx, response in enumerate(responses):
        if not response:
            normalized_responses.append(None)
            continue

        url = base_urls[idx]
        api_config = cfgs.get(str(idx), cfgs.get(url, {})) or {}

        prefix_id = (api_config.get("prefix_id") or "").strip() or None
        connection_name = (api_config.get("remark") or "").strip()
        if not connection_name:
            try:
                connection_name = urlparse(url).hostname or ""
            except Exception:
                connection_name = ""
        tags = api_config.get("tags", [])
        connection_icon = (api_config.get("icon") or "").strip()

        # Convert upstream /models response into OpenAI-style list.
        if isinstance(response, dict) and response.get("object") == "list" and isinstance(response.get("data"), list):
            model_list = response
        elif isinstance(response, dict) and isinstance(response.get("data"), list):
            model_list = {
                "object": "list",
                "data": [
                    {
                        "id": m.get("id"),
                        "name": m.get("display_name") or m.get("id"),
                        "owned_by": "anthropic",
                        "anthropic": m,
                        "urlIdx": idx,
                    }
                    for m in response.get("data", [])
                    if isinstance(m, dict) and m.get("id")
                ],
            }
        else:
            normalized_responses.append(None)
            continue

        for model in model_list.get("data", []) or []:
            original_id = model.get("id") or model.get("name") or ""
            display_name = model.get("name") or original_id

            if prefix_id:
                model["original_id"] = original_id
                model["id"] = f"{prefix_id}.{original_id}"
                model["name"] = display_name

            if connection_name:
                model["connection_name"] = connection_name
            if connection_icon:
                model["connection_icon"] = connection_icon
            if tags:
                model["tags"] = tags

        normalized_responses.append(model_list)

    return normalized_responses


async def get_filtered_models(models, user):
    filtered_models = []
    for model in models.get("data", []):
        model_info = Models.get_model_by_id(model["id"])
        if model_info:
            if user.id == model_info.user_id or has_access(
                user.id, type="read", access_control=model_info.access_control
            ):
                filtered_models.append(model)
    return filtered_models


async def get_all_models(request: Request, user: UserModel) -> dict:
    log.info("get_all_models() - Anthropic")
    base_urls, _keys, _cfgs = _get_anthropic_user_config(user)
    if not base_urls:
        return {"data": []}

    responses = await get_all_models_responses(request, user=user)

    merged: dict[str, dict] = {}
    for response in responses:
        for model in (response.get("data", []) if isinstance(response, dict) else []):
            mid = model.get("id")
            if mid and mid not in merged:
                merged[mid] = model

    models = {"data": list(merged.values())}
    return models


@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    models = {"data": []}

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        base_urls, keys, cfgs = _get_anthropic_user_config(user)
        if url_idx < 0 or url_idx >= len(base_urls):
            raise HTTPException(status_code=404, detail="Connection not found")

        url = base_urls[url_idx]
        key = keys[url_idx] if url_idx < len(keys) else ""
        api_config = cfgs.get(str(url_idx), cfgs.get(url, {})) or {}

        response = await send_get_request(f"{url.rstrip('/')}/models", key, api_config)
        if response and isinstance(response, dict) and "data" in response:
            prefix_id = (api_config.get("prefix_id") or "").strip() or None
            connection_name = (api_config.get("remark") or "").strip()
            if not connection_name:
                try:
                    connection_name = urlparse(url).hostname or ""
                except Exception:
                    connection_name = ""
            tags = api_config.get("tags", [])
            connection_icon = (api_config.get("icon") or "").strip()

            out = []
            for m in response.get("data", []) or []:
                if not isinstance(m, dict) or not m.get("id"):
                    continue
                original_id = m.get("id")
                model = {
                    "id": original_id,
                    "name": m.get("display_name") or original_id,
                    "owned_by": "anthropic",
                    "anthropic": m,
                    "urlIdx": url_idx,
                }
                if prefix_id:
                    model["original_id"] = original_id
                    model["id"] = f"{prefix_id}.{original_id}"
                if connection_name:
                    model["connection_name"] = connection_name
                if connection_icon:
                    model["connection_icon"] = connection_icon
                if tags:
                    model["tags"] = tags
                out.append(model)

            models = {"data": out}

    return models


def _strip_connection_prefix(model_id: str, prefix_id: Optional[str]) -> str:
    if not model_id:
        return model_id
    if prefix_id:
        prefix = f"{prefix_id}."
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
    return model_id


def _resolve_connection_by_model_id(connection_user: Optional[UserModel], model_id: str) -> tuple[int, str, str, dict]:
    base_urls, keys, cfgs = _get_anthropic_user_config(connection_user)

    chosen_idx = 0
    chosen_cfg = cfgs.get("0", {}) or {}
    chosen_prefix = (chosen_cfg.get("prefix_id") or "").strip() or None

    # Try to match "prefix_id.xxx" across configured connections.
    if isinstance(model_id, str) and "." in model_id and len(base_urls) > 1:
        maybe_prefix, _rest = model_id.split(".", 1)
        for idx, _url in enumerate(base_urls):
            c = cfgs.get(str(idx), cfgs.get(_url, {})) or {}
            p = (c.get("prefix_id") or "").strip() or None
            if p and p == maybe_prefix:
                chosen_idx = idx
                chosen_cfg = c
                chosen_prefix = p
                break

    url = (base_urls[chosen_idx] if chosen_idx < len(base_urls) else "").rstrip("/")
    key = keys[chosen_idx] if chosen_idx < len(keys) else ""
    api_config = chosen_cfg or {}

    # Store the resolved prefix on config for later use (e.g. file cache key)
    api_config = {**api_config, "_resolved_prefix_id": chosen_prefix or ""}

    return chosen_idx, url, key, api_config


async def _fetch_url_as_base64(url: str) -> tuple[str, str]:
    """
    Fetch an HTTP(S) URL and return (media_type, base64_data).
    """
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(url) as response:
            if response.status >= 400:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to fetch image url (status={response.status})",
                )
            data = await response.read()
            media_type = (
                response.headers.get("content-type")
                or mimetypes.guess_type(url)[0]
                or "application/octet-stream"
            )
            b64 = base64.b64encode(data).decode("utf-8")
            return media_type, b64


async def _openai_content_to_anthropic_blocks(content: Any) -> list[dict]:
    """
    Convert OpenAI message.content (string or array of content parts) into Anthropic content blocks.

    Supports:
    - text
    - image_url (data:... base64 or http(s) URL fetched server-side)
    """
    if content is None:
        return []
    if isinstance(content, str):
        if not content.strip():
            return []
        return [{"type": "text", "text": content}]

    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}]

    blocks: list[dict] = []
    for part in content:
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue

        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": str(part.get("text") or "")})
            continue

        if ptype == "image_url":
            image_url = part.get("image_url") or {}
            url = image_url.get("url") if isinstance(image_url, dict) else str(image_url)

            if not url:
                continue

            m = DATA_URL_RE.match(url)
            if m:
                media_type = m.group("mime") or "image/png"
                b64 = m.group("data") or ""
            else:
                media_type, b64 = await _fetch_url_as_base64(url)

            # Anthropic accepts images as base64 sources.
            blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64,
                    },
                }
            )
            continue

        # Fallback: stringify unknown parts
        blocks.append({"type": "text", "text": str(part)})

    return blocks


def _openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    anthropic_tools: list[dict] = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        anthropic_tools.append(
            {
                "name": str(name),
                "description": str(fn.get("description") or ""),
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return anthropic_tools


def _openai_tool_choice_to_anthropic(tool_choice: Any) -> Optional[dict]:
    if tool_choice is None:
        return None
    if isinstance(tool_choice, str):
        v = tool_choice.lower().strip()
        if v in ("auto",):
            return {"type": "auto"}
        if v in ("any", "required"):
            return {"type": "any"}
        if v in ("none",):
            # Anthropic doesn't have a strict "none" - omit tools to disable.
            return {"type": "auto"}
        return None
    if isinstance(tool_choice, dict):
        # OpenAI format: { type: "function", function: { name: "..." } }
        if tool_choice.get("type") == "function":
            fn = tool_choice.get("function") or {}
            if isinstance(fn, dict) and fn.get("name"):
                return {"type": "tool", "name": str(fn.get("name"))}
        return None
    return None


async def _upload_file_to_anthropic(
    *,
    base_url: str,
    key: str,
    api_config: dict,
    local_path: str,
    filename: str,
    content_type: str,
) -> str:
    """
    Upload a local file to Anthropic Files API and return the resulting file_id.
    """
    url = f"{base_url}/files"

    # Files API requires the beta header.
    extra_beta = [ANTHROPIC_BETA_FILES_API]
    headers = _build_anthropic_headers(
        key,
        api_config,
        accept="application/json",
        content_type=None,  # Let aiohttp set multipart boundary.
        extra_beta=extra_beta,
    )

    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        form = aiohttp.FormData()
        with open(local_path, "rb") as f:
            form.add_field(
                "file",
                f,
                filename=filename,
                content_type=content_type or "application/octet-stream",
            )

            async with session.post(url, data=form, headers=headers) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    msg = None
                    if isinstance(data, dict):
                        err = data.get("error")
                        if isinstance(err, dict):
                            msg = err.get("message")
                    raise HTTPException(status_code=response.status, detail=msg or str(data)[:500])

                if not isinstance(data, dict) or not data.get("id"):
                    raise HTTPException(status_code=500, detail="Invalid response from Anthropic Files API")
                return str(data["id"])


def _get_file_cache_key(api_config: dict, url_idx: int) -> str:
    # Prefer stable prefix_id; fall back to url index.
    prefix = (api_config.get("_resolved_prefix_id") or "").strip()
    return prefix if prefix else f"idx:{url_idx}"


def _get_cached_file_id(file_meta: dict, conn_key: str) -> Optional[str]:
    try:
        anth = (file_meta or {}).get("anthropic", {}) or {}
        files_map = anth.get("files", {}) or {}
        entry = files_map.get(conn_key, {}) or {}
        fid = entry.get("file_id")
        return str(fid) if fid else None
    except Exception:
        return None


def _set_cached_file_id(file_id: str, file_meta: dict, conn_key: str, remote_file_id: str) -> dict:
    meta = dict(file_meta or {})
    anth = dict(meta.get("anthropic") or {})
    files_map = dict(anth.get("files") or {})
    files_map[conn_key] = {"file_id": remote_file_id, "uploaded_at": int(time.time())}
    anth["files"] = files_map
    meta["anthropic"] = anth
    Files.update_file_metadata_by_id(file_id, meta)
    return meta


async def _build_attachment_blocks(
    request: Request,
    user: UserModel,
    *,
    files: Optional[list[dict]],
    base_url: str,
    key: str,
    api_config: dict,
    url_idx: int,
) -> list[dict]:
    """
    Convert Open WebUI file attachments (metadata.files) into Anthropic document blocks.

    We only auto-attach uploaded files (type=="file") that exist in the local DB.
    Knowledge-base collections and web docs remain handled by Open WebUI's RAG pipeline.
    """
    if not files:
        return []

    cfg = api_config or {}
    if not cfg.get("files_auto_attach", True):
        return []

    use_files_api = cfg.get("use_files_api", True)
    cache_ttl = str(cfg.get("files_cache_ttl") or "").strip() or None  # "5m" | "1h"
    enable_citations = bool(cfg.get("files_citations", False))

    cache_control = None
    if cache_ttl in ("5m", "1h"):
        cache_control = {"type": "ephemeral", "ttl": cache_ttl}

    conn_key = _get_file_cache_key(cfg, url_idx)

    # De-duplicate by Open WebUI file id.
    seen_ids: set[str] = set()
    attachment_blocks: list[dict] = []

    for f in files:
        if not isinstance(f, dict):
            continue
        if f.get("type") != "file":
            continue
        if str(f.get("processing_mode") or "").strip().lower() != FILE_PROCESSING_MODE_NATIVE_FILE:
            continue

        fid = f.get("id") or (f.get("file") or {}).get("id")
        if not fid:
            continue
        fid = str(fid)
        if fid in seen_ids:
            continue
        seen_ids.add(fid)

        file_obj = Files.get_file_by_id(fid)
        if not file_obj or not file_obj.meta:
            continue

        filename = file_obj.filename or "file"
        content_type = (file_obj.meta or {}).get("content_type") or "application/octet-stream"

        # If format isn't supported by Anthropic document blocks, fall back to extracted text.
        if content_type not in SUPPORTED_DOCUMENT_MIME_TYPES and content_type not in SUPPORTED_IMAGE_MIME_TYPES:
            extracted = (file_obj.data or {}).get("content") or ""
            if extracted:
                text = f"[File: {filename}]\n{extracted}"
                block = {"type": "text", "text": text}
                if cache_control:
                    block["cache_control"] = cache_control
                attachment_blocks.append(block)
            continue

        # Supported types: upload once (cached) via Files API, then reference file_id.
        remote_file_id = _get_cached_file_id(file_obj.meta, conn_key)
        if not remote_file_id:
            if not use_files_api:
                extracted = (file_obj.data or {}).get("content") or ""
                if extracted:
                    block = {"type": "text", "text": f"[File: {filename}]\n{extracted}"}
                    if cache_control:
                        block["cache_control"] = cache_control
                    attachment_blocks.append(block)
                continue

            try:
                if not file_obj.path:
                    raise RuntimeError("File has no local path")
                local_path = Storage.get_file(file_obj.path)
                remote_file_id = await _upload_file_to_anthropic(
                    base_url=base_url,
                    key=key,
                    api_config=cfg,
                    local_path=local_path,
                    filename=filename,
                    content_type=content_type,
                )
                _set_cached_file_id(fid, file_obj.meta, conn_key, remote_file_id)
            except Exception as e:
                log.exception(f"[ANTHROPIC] Failed to upload file {fid}: {e}")
                continue

        if content_type in SUPPORTED_IMAGE_MIME_TYPES:
            block: dict = {
                "type": "image",
                "source": {"type": "file", "file_id": remote_file_id},
            }
        else:
            block = {
                "type": "document",
                "source": {"type": "file", "file_id": remote_file_id},
                "title": filename,
            }
            if enable_citations:
                block["citations"] = {"enabled": True}

        if cache_control:
            block["cache_control"] = cache_control

        attachment_blocks.append(block)

    return attachment_blocks


def _anthropic_message_to_openai(
    message: dict,
    model_id: str,
    tool_reverse_map: dict[str, str] | None = None,
) -> dict:
    """
    Convert an Anthropic non-streaming message response to OpenAI chat.completion format.
    """
    content_blocks = message.get("content") if isinstance(message, dict) else []
    text_parts: list[str] = []
    tool_calls: list[dict] = []

    if isinstance(content_blocks, list):
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(str(block.get("text") or ""))
            elif btype == "tool_use":
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "")
                # Reverse-map CC spec name back to original
                if tool_reverse_map and name in tool_reverse_map:
                    name = tool_reverse_map[name]
                inp = block.get("input")
                try:
                    args = json.dumps(inp if inp is not None else {}, ensure_ascii=False)
                except Exception:
                    args = json.dumps({"_raw": str(inp)}, ensure_ascii=False)
                tool_calls.append(
                    {
                        "id": tool_id,
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                )

    usage = message.get("usage") if isinstance(message, dict) else {}
    openai_usage = None
    if isinstance(usage, dict):
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        openai_usage = {
            "prompt_tokens": inp,
            "completion_tokens": out,
            "total_tokens": inp + out,
        }
        # Preserve Anthropic cache usage fields if present (non-standard OpenAI fields, but useful).
        if usage.get("cache_creation_input_tokens") is not None:
            openai_usage["cache_creation_input_tokens"] = usage.get(
                "cache_creation_input_tokens"
            )
        if usage.get("cache_read_input_tokens") is not None:
            openai_usage["cache_read_input_tokens"] = usage.get("cache_read_input_tokens")

    finish_reason = "stop"
    stop_reason = message.get("stop_reason") if isinstance(message, dict) else None
    if stop_reason == "tool_use":
        finish_reason = "tool_calls"
    elif stop_reason == "max_tokens":
        finish_reason = "length"

    out_message: dict = {
        "role": "assistant",
        "content": "".join(text_parts).strip(),
    }
    if tool_calls:
        out_message["tool_calls"] = tool_calls

    response: dict = {
        "id": f"chatcmpl-anthropic-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": out_message,
                "finish_reason": finish_reason,
            }
        ],
    }
    if openai_usage:
        response["usage"] = openai_usage

    return response


@router.post("/chat/completions")
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
):
    """
    Generate chat completion using Anthropic Messages API.

    This endpoint accepts OpenAI-compatible format and converts it to Anthropic format.
    """
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    payload = {**form_data}
    metadata = payload.pop("metadata", None) or {}

    model_id = payload.get("model", "")
    model_info_db = Models.get_model_by_id(model_id)

    # Apply model overrides/params/system prompt (OpenAI-format) before converting to Anthropic.
    if model_info_db:
        if model_info_db.base_model_id:
            payload["model"] = model_info_db.base_model_id
            model_id = model_info_db.base_model_id

        params = model_info_db.params.model_dump()
        payload = apply_model_params_to_body_openai(params, payload)
        payload = apply_model_system_prompt_to_body(params, payload, metadata, user)

        if not bypass_filter and user.role == "user":
            if not (
                user.id == model_info_db.user_id
                or has_access(
                    user.id, type="read", access_control=model_info_db.access_control
                )
            ):
                raise HTTPException(status_code=403, detail="Not authorized")

    # Resolve Anthropic connection and strip internal prefix for the upstream model id.
    connection_user = getattr(request.state, "connection_user", None) or user
    url_idx, base_url, key, api_config = _resolve_connection_by_model_id(
        connection_user, payload.get("model", "")
    )
    request_models = getattr(request.state, "MODELS", None) or request.app.state.MODELS
    request_model_entry = (
        request_models.get(payload.get("model", ""))
        if isinstance(request_models, dict)
        else None
    )
    upstream_model_id = _strip_connection_prefix(
        payload.get("model", ""),
        (api_config.get("_resolved_prefix_id") or "").strip() or None,
    )
    # Preserve proxy model ids as-is unless a proxy has an explicit compatibility override.
    upstream_model_id = _resolve_proxy_model_alias(upstream_model_id, base_url)
    model_profile = _build_anthropic_model_profile(
        upstream_model_id,
        (request_model_entry or {}).get("anthropic")
        if isinstance(request_model_entry, dict)
        else None,
    )

    if not base_url:
        raise HTTPException(status_code=500, detail="Anthropic base URL not configured")

    stream = bool(payload.get("stream", False))

    # Build system blocks and messages for Anthropic.
    system_texts: list[str] = []
    anthropic_messages: list[dict] = []

    for msg in payload.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        if role == "system":
            # Anthropic supports system as a separate top-level field. Keep it text-only.
            sys_blocks = await _openai_content_to_anthropic_blocks(msg.get("content"))
            sys_text = "".join(
                [b.get("text", "") for b in sys_blocks if b.get("type") == "text"]
            ).strip()
            if sys_text:
                system_texts.append(sys_text)
            continue

        if role == "tool":
            tool_use_id = msg.get("tool_call_id") or msg.get("id") or ""
            content = msg.get("content") or ""
            anthropic_messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(tool_use_id),
                            "content": str(content),
                        }
                    ],
                }
            )
            continue

        if role == "assistant":
            blocks = await _openai_content_to_anthropic_blocks(msg.get("content"))
            tool_calls = msg.get("tool_calls") or []
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    if tc.get("type") != "function":
                        continue
                    fn = tc.get("function") or {}
                    name = (fn.get("name") if isinstance(fn, dict) else None) or ""
                    args = (fn.get("arguments") if isinstance(fn, dict) else None) or "{}"
                    try:
                        inp = json.loads(args) if isinstance(args, str) else (args or {})
                    except Exception:
                        inp = {"_raw": str(args)}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(tc.get("id") or f"toolu_{uuid.uuid4().hex}"),
                            "name": str(name),
                            "input": inp,
                        }
                    )
            anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

        # Default: user
        blocks = await _openai_content_to_anthropic_blocks(msg.get("content"))
        anthropic_messages.append({"role": "user", "content": blocks})

    system = None
    if system_texts:
        system = [{"type": "text", "text": "\n\n".join(system_texts)}]

    # Attach uploaded files (type=="file") as Claude document blocks.
    try:
        attachment_blocks = await _build_attachment_blocks(
            request,
            user,
            files=(metadata or {}).get("files"),
            base_url=base_url,
            key=key,
            api_config=api_config,
            url_idx=url_idx,
        )
    except Exception as e:
        log.exception(f"[ANTHROPIC] Failed to build attachment blocks: {e}")
        attachment_blocks = []

    if attachment_blocks:
        for m in anthropic_messages:
            if m.get("role") == "user":
                content_blocks = m.get("content")
                if isinstance(content_blocks, list):
                    m["content"] = attachment_blocks + content_blocks
                else:
                    m["content"] = attachment_blocks + [{"type": "text", "text": str(content_blocks)}]
                break
        else:
            anthropic_messages.insert(0, {"role": "user", "content": attachment_blocks})

    max_tokens = _resolve_anthropic_max_tokens(payload, model_profile)

    anthropic_payload: dict = {
        "model": upstream_model_id,
        "messages": anthropic_messages,
        "stream": stream,
        "max_tokens": max_tokens,
    }

    thinking, output_config, thinking_budget, thinking_enabled = _resolve_thinking_payload(
        payload, model_profile=model_profile
    )
    if thinking is not None:
        anthropic_payload["thinking"] = thinking
    if output_config is not None:
        anthropic_payload["output_config"] = output_config

    if system:
        anthropic_payload["system"] = system

    # temperature/top_p/top_k are incompatible with thinking mode
    if not thinking_enabled:
        if payload.get("temperature") is not None:
            anthropic_payload["temperature"] = payload.get("temperature")
        if payload.get("top_p") is not None:
            anthropic_payload["top_p"] = payload.get("top_p")
        if payload.get("top_k") is not None:
            anthropic_payload["top_k"] = payload.get("top_k")

    stop = payload.get("stop")
    if isinstance(stop, list) and stop:
        anthropic_payload["stop_sequences"] = stop
    elif isinstance(stop, str) and stop.strip():
        anthropic_payload["stop_sequences"] = [stop.strip()]

    openai_tools = payload.get("tools")
    if isinstance(openai_tools, list) and openai_tools:
        anthropic_payload["tools"] = _openai_tools_to_anthropic(openai_tools)
        tool_choice = _openai_tool_choice_to_anthropic(payload.get("tool_choice"))
        if tool_choice:
            anthropic_payload["tool_choice"] = tool_choice

    metadata_user_id = str(user.id if user else "anonymous")
    anthropic_payload["metadata"] = {"user_id": metadata_user_id}

    # Additive passthrough for future Anthropic fields (e.g., new capabilities),
    # configured per connection as JSON in `anthropic_extra_body`.
    anthropic_payload = _merge_extra_body_into_payload(
        anthropic_payload, api_config.get("anthropic_extra_body")
    )
    anthropic_payload, thinking_budget, thinking_enabled = _normalize_final_anthropic_payload(
        anthropic_payload, model_profile
    )
    max_tokens = anthropic_payload.get("max_tokens")

    # Determine required betas (e.g., Files API) and merge with user configured betas.
    required_betas: list[str] = []
    needs_files_api_beta = any(
        (
            isinstance(b, dict)
            and b.get("type") in ("document", "image")
            and isinstance(b.get("source"), dict)
            and b["source"].get("type") == "file"
        )
        for b in (attachment_blocks or [])
    )
    if needs_files_api_beta:
        required_betas.append(ANTHROPIC_BETA_FILES_API)

    messages_url = f"{base_url}/messages"

    if stream:

        async def stream_generator():
            timeout = aiohttp.ClientTimeout(total=300)
            decoder = codecs.getincrementaldecoder("utf-8")()
            buf = ""
            stream_id = f"chatcmpl-anthropic-{uuid.uuid4().hex}"
            model_for_openai = payload.get("model") or upstream_model_id

            # Capture input/output tokens for usage.
            prompt_tokens: Optional[int] = None
            completion_tokens: Optional[int] = None

            # Track tool_use blocks so we can stream tool_calls deltas (OpenAI format).
            tool_block_index_to_call: dict[int, dict] = {}
            next_tool_call_index = 0

            try:
                async with aiohttp.ClientSession(
                    timeout=timeout,
                    trust_env=True,
                    skip_auto_headers={"Accept-Encoding"},
                ) as session:
                    headers = _build_anthropic_headers(
                        key,
                        api_config,
                        accept="application/json",
                        content_type="application/json",
                        extra_beta=required_betas,
                    )
                    # Keep x-api-key but strip Authorization for messages endpoint.
                    if "x-api-key" in headers:
                        headers.pop("Authorization", None)

                    # Apply CC request format for anyrouter proxy validation
                    actual_url = messages_url
                    cc_tool_reverse: dict[str, str] = {}
                    if _needs_cc_format(upstream_model_id, base_url):
                        actual_url = _apply_cc_format(headers, anthropic_payload, messages_url)
                        cc_tool_reverse = _apply_cc_tool_names(anthropic_payload)

                    # DEBUG: log outgoing headers and payload keys
                    _safe_hdrs = {}
                    for k, v in headers.items():
                        kl = k.lower()
                        if kl == "x-api-key":
                            _safe_hdrs[k] = v[:20] + "..."
                        else:
                            _safe_hdrs[k] = v
                    log.info(f"[ANTHROPIC DEBUG] POST {actual_url} headers={_safe_hdrs}")
                    # Dump full payload body (redact long message content)
                    _dump = dict(anthropic_payload)
                    _dump_msgs = []
                    for _m in _dump.get("messages", []):
                        _mc = _m.get("content")
                        if isinstance(_mc, str) and len(_mc) > 200:
                            _mc = _mc[:200] + "...(truncated)"
                        elif isinstance(_mc, list):
                            _mc = [
                                {**b, "text": b["text"][:200] + "...(truncated)"} if isinstance(b, dict) and isinstance(b.get("text"), str) and len(b.get("text", "")) > 200 else b
                                for b in _mc
                            ]
                        _dump_msgs.append({**_m, "content": _mc})
                    _dump["messages"] = _dump_msgs
                    log.info(f"[ANTHROPIC DEBUG] FULL BODY: {json.dumps(_dump, ensure_ascii=False, default=str)}")

                    async with _post_preserve_method(session, actual_url, json_data=anthropic_payload, headers=headers) as response:
                        log.info(f"[ANTHROPIC DEBUG] response status={response.status}")
                        if response.status >= 400:
                            err_text = await response.text()
                            log.warning(f"[ANTHROPIC DEBUG] ERROR {response.status}: {err_text[:500]}")
                            error_chunk = {
                                "error": {
                                    "message": _format_anthropic_upstream_error(
                                        request_url=actual_url,
                                        status=response.status,
                                        body=err_text,
                                    ),
                                    "type": "api_error",
                                    "code": f"http_{response.status}",
                                }
                            }
                            yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                            yield "data: [DONE]\n\n"
                            return

                        # Send role first (OpenAI-style)
                        yield f"data: {json.dumps(_openai_chunk(stream_id, model_for_openai, {'role': 'assistant'}), ensure_ascii=False)}\n\n"

                        _first_chunk_logged = False
                        async for raw in response.content.iter_any():
                            if not raw:
                                continue
                            chunk_str = decoder.decode(raw, False)
                            if not chunk_str:
                                continue
                            if not _first_chunk_logged:
                                log.info(f"[ANTHROPIC DEBUG] first chunk: {chunk_str[:500]}")
                                _first_chunk_logged = True
                            buf += chunk_str

                            while "\n\n" in buf:
                                event_str, buf = buf.split("\n\n", 1)
                                if not event_str.strip():
                                    continue

                                data_lines = []
                                for line in event_str.splitlines():
                                    line = line.rstrip("\r")
                                    if line.startswith("data:"):
                                        data_lines.append(line[5:].lstrip())
                                if not data_lines:
                                    continue

                                data_str = "\n".join(data_lines).strip()
                                if not data_str:
                                    continue

                                try:
                                    ev = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue

                                if not isinstance(ev, dict):
                                    continue

                                ev_type = ev.get("type")
                                if ev_type == "ping":
                                    continue

                                if ev_type == "message_start":
                                    msg = ev.get("message") or {}
                                    usage = msg.get("usage") if isinstance(msg, dict) else {}
                                    if isinstance(usage, dict) and usage.get("input_tokens") is not None:
                                        prompt_tokens = int(usage.get("input_tokens") or 0)
                                    continue

                                if ev_type == "content_block_start":
                                    idx = ev.get("index")
                                    cb = ev.get("content_block") or {}
                                    if isinstance(idx, int) and isinstance(cb, dict) and cb.get("type") == "tool_use":
                                        tool_name = cb.get("name") or ""
                                        # Reverse-map CC spec name back to original
                                        if cc_tool_reverse and tool_name in cc_tool_reverse:
                                            tool_name = cc_tool_reverse[tool_name]
                                        tool_call = {
                                            "index": next_tool_call_index,
                                            "id": cb.get("id") or f"toolu_{uuid.uuid4().hex}",
                                            "type": "function",
                                            "function": {
                                                "name": tool_name,
                                                "arguments": "",
                                            },
                                        }
                                        tool_block_index_to_call[idx] = tool_call
                                        next_tool_call_index += 1
                                        tool_chunk = _openai_chunk(stream_id, model_for_openai, {"tool_calls": [tool_call]})
                                        yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n"
                                    continue

                                if ev_type == "content_block_delta":
                                    idx = ev.get("index")
                                    delta = ev.get("delta") or {}
                                    if not isinstance(delta, dict):
                                        continue

                                    d_type = delta.get("type")
                                    if d_type == "text_delta":
                                        text = delta.get("text") or ""
                                        if text:
                                            for content_chunk in _yield_content_chunks(str(text), stream_id, model_for_openai):
                                                yield content_chunk
                                        continue

                                    if d_type == "thinking_delta":
                                        thinking = delta.get("thinking") or ""
                                        if thinking:
                                            thinking_chunk = _openai_chunk(
                                                stream_id,
                                                model_for_openai,
                                                {"reasoning_content": str(thinking)},
                                            )
                                            yield f"data: {json.dumps(thinking_chunk, ensure_ascii=False)}\n\n"
                                        continue

                                    if d_type == "input_json_delta":
                                        partial = delta.get("partial_json") or ""
                                        if isinstance(idx, int) and idx in tool_block_index_to_call and partial:
                                            tool_call = tool_block_index_to_call[idx]
                                            delta_call = {
                                                "index": tool_call.get("index"),
                                                "id": tool_call.get("id"),
                                                "type": "function",
                                                "function": {"arguments": str(partial)},
                                            }
                                            tool_chunk = _openai_chunk(stream_id, model_for_openai, {"tool_calls": [delta_call]})
                                            yield f"data: {json.dumps(tool_chunk, ensure_ascii=False)}\n\n"
                                        continue

                                    continue

                                if ev_type == "message_delta":
                                    delta = ev.get("delta") or {}
                                    stop_reason = (delta.get("stop_reason") if isinstance(delta, dict) else None) or None

                                    usage = ev.get("usage") or {}
                                    if isinstance(usage, dict) and usage.get("output_tokens") is not None:
                                        completion_tokens = int(usage.get("output_tokens") or 0)

                                    if stop_reason:
                                        finish = "stop"
                                        if stop_reason == "tool_use":
                                            finish = "tool_calls"
                                        elif stop_reason == "max_tokens":
                                            finish = "length"

                                        usage_obj = None
                                        if prompt_tokens is not None and completion_tokens is not None:
                                            usage_obj = {
                                                "prompt_tokens": prompt_tokens,
                                                "completion_tokens": completion_tokens,
                                                "total_tokens": prompt_tokens + completion_tokens,
                                            }

                                        fin_chunk = _openai_chunk(stream_id, model_for_openai, {}, finish_reason=finish, usage=usage_obj)
                                        yield f"data: {json.dumps(fin_chunk, ensure_ascii=False)}\n\n"
                                    continue

                                if ev_type == "message_stop":
                                    yield "data: [DONE]\n\n"
                                    return

                        yield "data: [DONE]\n\n"

            except Exception as e:
                log.exception(f"Error in Anthropic stream generator: {e}")
                error_chunk = {
                    "error": {
                        "message": str(e),
                        "type": "stream_error",
                        "code": "internal_error",
                    }
                }
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    # Non-streaming
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(
        timeout=timeout,
        trust_env=True,
        skip_auto_headers={"Accept-Encoding"},
    ) as session:
        headers = _build_anthropic_headers(
            key,
            api_config,
            accept="application/json",
            content_type="application/json",
            extra_beta=required_betas,
        )
        if "x-api-key" in headers:
            headers.pop("Authorization", None)

        # Apply CC request format for anyrouter proxy validation
        actual_url_ns = messages_url
        cc_tool_reverse_ns: dict[str, str] = {}
        if _needs_cc_format(upstream_model_id, base_url):
            actual_url_ns = _apply_cc_format(headers, anthropic_payload, messages_url)
            cc_tool_reverse_ns = _apply_cc_tool_names(anthropic_payload)

        log.info(
            "[ANTHROPIC DEBUG] cfg stream=false max_tokens=%s thinking_budget=%s betas=%s",
            max_tokens,
            thinking_budget,
            _clean_beta_list(headers.get("anthropic-beta")),
        )

        async with _post_preserve_method(session, actual_url_ns, json_data=anthropic_payload, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise HTTPException(
                    status_code=response.status,
                    detail=_format_anthropic_upstream_error(
                        request_url=actual_url_ns,
                        status=response.status,
                        body=data,
                    ),
                )

            return _anthropic_message_to_openai(
                data,
                payload.get("model") or upstream_model_id,
                tool_reverse_map=cc_tool_reverse_ns,
            )
