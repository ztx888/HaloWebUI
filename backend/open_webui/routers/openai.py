import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Optional, overload
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import aiohttp
from aiocache import cached
import requests


from fastapi import Depends, FastAPI, HTTPException, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from open_webui.models.models import Models
from open_webui.models.files import Files
from open_webui.config import (
    CACHE_DIR,
)
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    BYPASS_MODEL_ACCESS_CONTROL,
    REQUESTS_VERIFY,
)
from open_webui.models.users import UserModel
from open_webui.storage.provider import Storage
from open_webui.utils.user_connections import (
    get_user_connections,
    set_user_connection_provider_config,
)

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import ENV, SRC_LOG_LEVELS


from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_model_system_prompt_to_body,
    merge_additive_payload_fields,
)
from open_webui.utils.misc import (
    convert_logit_bias_input_to_json,
)
from open_webui.utils.error_handling import (
    build_error_detail,
    read_aiohttp_error_payload,
    read_requests_error_payload,
)
from open_webui.utils.openai_responses import (
    convert_chat_completions_to_responses_payload,
    convert_responses_to_chat_completions,
    iter_responses_events,
    responses_events_to_chat_completions_sse,
)
from open_webui.utils.native_web_search import (
    build_native_web_search_support,
    resolve_effective_native_web_search_support,
    strip_model_prefix,
)

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OPENAI"])

OPENAI_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
AZURE_OPENAI_V1_SEGMENT = "/openai/v1"
AZURE_OPENAI_DEPLOYMENTS_SEGMENT = "/openai/deployments/"

NATIVE_FILE_INPUT_STATUS_SUPPORTED = "supported"
NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG = "disabled_by_config"
NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED = "protocol_not_attempted"
NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED = "upload_failed"
NATIVE_FILE_INPUT_STATUS_UPSTREAM_REJECTED = "upstream_rejected"

_NATIVE_FILE_INPUT_PROBE_TTL_SECONDS = 60
_NATIVE_FILE_INPUT_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CUSTOM_PARAM_FORBIDDEN_KEYS = {"model", "messages", "input", "stream"}


def _is_official_openai_connection(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        host = ""
    return host == "api.openai.com" or host.endswith(".openai.com")


def _is_azure_openai_connection(
    url: str, api_config: Optional[dict] = None
) -> bool:
    if bool((api_config or {}).get("azure")):
        return True

    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except Exception:
        host = ""

    return (
        host.endswith(".openai.azure.com")
        or host.endswith(".cognitiveservices.azure.com")
        or host.endswith(".cognitive.microsoft.com")
    )


def _replace_url_path_and_query(parsed, path: str, query: Optional[str] = None) -> str:
    normalized_path = path or ""
    if normalized_path and not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"

    return urlunparse(
        parsed._replace(
            path=normalized_path,
            query="" if query is None else query,
            params="",
            fragment="",
        )
    )


def _strip_known_openai_suffixes(path: str) -> str:
    normalized_path = (path or "").rstrip("/")

    for suffix in (
        "/responses",
        "/models",
        OPENAI_CHAT_COMPLETIONS_SUFFIX,
        "/completions",
    ):
        if normalized_path.endswith(suffix):
            normalized_path = normalized_path[: -len(suffix)].rstrip("/")

    return normalized_path


def _normalize_azure_openai_base_url(
    url: str, api_config: Optional[dict] = None
) -> str:
    normalized_url = (url or "").strip().rstrip("/")
    if not normalized_url:
        return normalized_url

    if _is_force_mode_connection(normalized_url, api_config):
        return normalized_url

    parsed = urlparse(normalized_url)
    path = _strip_known_openai_suffixes(parsed.path or "")

    if AZURE_OPENAI_DEPLOYMENTS_SEGMENT in path:
        prefix, remainder = path.split(AZURE_OPENAI_DEPLOYMENTS_SEGMENT, 1)
        deployment = remainder.split("/", 1)[0].strip()
        deployment_path = (
            f"{prefix}{AZURE_OPENAI_DEPLOYMENTS_SEGMENT}{deployment}"
            if deployment
            else f"{prefix}{AZURE_OPENAI_V1_SEGMENT}"
        )
        return _replace_url_path_and_query(parsed, deployment_path, "")

    if path.endswith(AZURE_OPENAI_V1_SEGMENT):
        azure_path = path
    elif path.endswith("/openai"):
        azure_path = f"{path}/v1"
    elif path.endswith("/v1"):
        azure_path = f"{path[: -len('/v1')]}/openai/v1"
    else:
        azure_path = f"{path}{AZURE_OPENAI_V1_SEGMENT}" if path else AZURE_OPENAI_V1_SEGMENT

    return _replace_url_path_and_query(parsed, azure_path, "")


def _get_azure_openai_resource_url(
    url: str, api_config: Optional[dict] = None
) -> str:
    normalized_url = (url or "").strip().rstrip("/")
    if not normalized_url:
        return normalized_url

    parsed = urlparse(normalized_url)
    path = _strip_known_openai_suffixes(parsed.path or "")

    if AZURE_OPENAI_DEPLOYMENTS_SEGMENT in path:
        path = path.split(AZURE_OPENAI_DEPLOYMENTS_SEGMENT, 1)[0]
    elif path.endswith(AZURE_OPENAI_V1_SEGMENT):
        path = path[: -len(AZURE_OPENAI_V1_SEGMENT)]
    elif path.endswith("/openai"):
        path = path[: -len("/openai")]
    elif path.endswith("/v1"):
        path = path[: -len("/v1")]

    return _replace_url_path_and_query(parsed, path, "")


def _append_query_param(url: str, key: str, value: Optional[str]) -> str:
    if not value:
        return url

    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k != key]
    query.append((key, value))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


def _get_azure_api_version(api_config: Optional[dict]) -> Optional[str]:
    value = (api_config or {}).get("api_version")
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _get_azure_openai_chat_completions_urls(
    url: str,
    api_config: Optional[dict] = None,
    *,
    model_id: Optional[str] = None,
) -> list[str]:
    normalized_base = _normalize_azure_openai_base_url(url, api_config)
    if not normalized_base:
        return []

    urls: list[str] = []
    resource_url = _get_azure_openai_resource_url(url, api_config)
    api_version = _get_azure_api_version(api_config)
    urls.append(f"{resource_url}{AZURE_OPENAI_V1_SEGMENT}{OPENAI_CHAT_COMPLETIONS_SUFFIX}")

    if api_version and model_id:
        deployment = quote(str(model_id).strip(), safe="")
        if deployment:
            urls.append(
                _append_query_param(
                    f"{resource_url}{AZURE_OPENAI_DEPLOYMENTS_SEGMENT}{deployment}{OPENAI_CHAT_COMPLETIONS_SUFFIX}",
                    "api-version",
                    api_version,
                )
            )

    deduped_urls: list[str] = []
    seen = set()
    for candidate in urls:
        if candidate and candidate not in seen:
            seen.add(candidate)
            deduped_urls.append(candidate)

    return deduped_urls


def _is_force_mode_connection(url: str, api_config: Optional[dict] = None) -> bool:
    normalized_url = (url or "").rstrip("/")
    return bool((api_config or {}).get("force_mode")) or normalized_url.endswith(
        OPENAI_CHAT_COMPLETIONS_SUFFIX
    )


def _get_openai_models_url(url: str, api_config: Optional[dict] = None) -> str:
    normalized_url = (url or "").rstrip("/")
    if not normalized_url:
        return normalized_url

    if _is_azure_openai_connection(normalized_url, api_config):
        azure_base = _normalize_azure_openai_base_url(normalized_url, api_config)
        azure_path = urlparse(azure_base).path or ""
        if AZURE_OPENAI_DEPLOYMENTS_SEGMENT in azure_path:
            resource_url = _get_azure_openai_resource_url(normalized_url, api_config)
            return f"{resource_url}{AZURE_OPENAI_V1_SEGMENT}/models"
        return f"{azure_base}/models"

    if _is_force_mode_connection(normalized_url, api_config):
        if normalized_url.endswith(OPENAI_CHAT_COMPLETIONS_SUFFIX):
            return (
                f"{normalized_url[:-len(OPENAI_CHAT_COMPLETIONS_SUFFIX)]}/models"
            )
        return normalized_url

    return f"{normalized_url}/models"


def _get_openai_chat_completions_url(
    url: str, api_config: Optional[dict] = None
) -> str:
    normalized_url = (url or "").rstrip("/")
    if _is_azure_openai_connection(normalized_url, api_config):
        candidates = _get_azure_openai_chat_completions_urls(
            normalized_url, api_config
        )
        return candidates[0] if candidates else normalized_url
    if _is_force_mode_connection(normalized_url, api_config):
        return normalized_url
    return f"{normalized_url}/chat/completions"


def _connection_supports_native_web_search(url: str, api_config: dict) -> bool:
    support = build_native_web_search_support("openai", url=url, api_config=api_config)
    return support.get("supported") is True


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _get_openai_user_config(connection_user: Optional[UserModel]) -> tuple[list[str], list[str], dict]:
    """
    Resolve OpenAI-compatible connection config for a given user.

    Stored under user.settings.ui.connections.openai:
      - OPENAI_API_BASE_URLS
      - OPENAI_API_KEYS
      - OPENAI_API_CONFIGS

    Returns (base_urls, keys, configs).
    """
    conns = get_user_connections(connection_user)
    cfg = conns.get("openai") if isinstance(conns, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}

    base_urls = list(cfg.get("OPENAI_API_BASE_URLS") or [])
    keys = list(cfg.get("OPENAI_API_KEYS") or [])
    configs = cfg.get("OPENAI_API_CONFIGS") or {}
    configs = configs if isinstance(configs, dict) else {}

    # Keep array lengths aligned (do not mutate user settings here).
    if len(keys) != len(base_urls):
        if len(keys) > len(base_urls):
            keys = keys[: len(base_urls)]
        else:
            keys = keys + [""] * (len(base_urls) - len(keys))

    return base_urls, keys, configs


def _resolve_openai_connection_by_model_id(
    model_id: str, base_urls: list[str], keys: list[str], cfgs: dict
) -> tuple[int, str, str, dict]:
    """
    Pick a connection index based on an internal `prefix_id` embedded in the model id.

    - If model_id is "prefix_id.xxx", we match that prefix against cfgs[idx].prefix_id.
    - Otherwise we use idx=0.
    """
    chosen_idx = 0
    chosen_cfg = (cfgs.get("0") or {}) if isinstance(cfgs, dict) else {}
    chosen_prefix = (chosen_cfg.get("prefix_id") or "").strip() or None

    if isinstance(model_id, str) and "." in model_id and len(base_urls) > 1 and isinstance(cfgs, dict):
        maybe_prefix, _rest = model_id.split(".", 1)
        for idx, _url in enumerate(base_urls):
            c = cfgs.get(str(idx), {}) or {}
            p = (c.get("prefix_id") or "").strip() or None
            if p and p == maybe_prefix:
                chosen_idx = idx
                chosen_cfg = c
                chosen_prefix = p
                break

    url = (base_urls[chosen_idx] if chosen_idx < len(base_urls) else "").rstrip("/")
    key = keys[chosen_idx] if chosen_idx < len(keys) else ""
    api_config = chosen_cfg or {}
    api_config = {**api_config, "_resolved_prefix_id": chosen_prefix or ""}

    return chosen_idx, url, key, api_config


async def _is_user_visible_model(
    request: Request, user: UserModel, model_id: str
) -> bool:
    state = getattr(request, "state", None)
    models_map = getattr(state, "MODELS", None) if state is not None else None
    if isinstance(models_map, dict) and model_id in models_map:
        return True

    try:
        # Avoid a module-level import here because utils.models imports this router.
        from open_webui.utils.models import get_all_models as load_user_models

        await load_user_models(request, user=user)
    except Exception as e:
        log.debug(
            "Failed to load user-scoped models while validating %s: %s: %s",
            model_id,
            type(e).__name__,
            e,
        )
        return False

    state = getattr(request, "state", None)
    models_map = getattr(state, "MODELS", None) if state is not None else None
    return isinstance(models_map, dict) and model_id in models_map


##########################################
#
# Utility functions
#
##########################################


def _truncate_text(text: str, limit: int = 2000) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _stringify_upstream_body(body) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    try:
        return json.dumps(body, ensure_ascii=False, default=str)
    except Exception:
        return str(body)


def _get_native_web_search_tool_type(api_config: dict) -> str:
    """
    Responses API web search tool types vary across providers/proxies.
    Prefer explicit config, otherwise default to the canonical 'web_search'.
    """
    if not isinstance(api_config, dict):
        return "web_search"

    tool_types = api_config.get("native_web_search_tool_types")
    if isinstance(tool_types, list):
        for t in tool_types:
            if t and str(t).strip():
                return str(t).strip()

    tool_type = api_config.get("native_web_search_tool_type")
    if tool_type and str(tool_type).strip():
        return str(tool_type).strip()

    return "web_search"


def _should_use_responses_api(
    url: str,
    api_config: Optional[dict],
    model_id: Optional[str],
    native_web_search: bool = False,
    native_file_inputs: bool = False,
) -> bool:
    cfg = api_config or {}
    if _is_azure_openai_connection(url, cfg):
        return False
    use_responses_api = bool(
        cfg.get("use_responses_api", False) or native_web_search or native_file_inputs
    )
    if _is_force_mode_connection(url, cfg):
        return False
    if use_responses_api and not native_web_search and not native_file_inputs:
        exclude_patterns = cfg.get("responses_api_exclude_patterns", [])
        if isinstance(exclude_patterns, list):
            model_lower = (model_id or "").lower()
            if any(
                isinstance(pattern, str)
                and pattern.strip()
                and pattern.strip().lower() in model_lower
                for pattern in exclude_patterns
            ):
                return False
    return use_responses_api


def _get_native_file_input_capability(
    url: str, api_config: Optional[dict]
) -> dict[str, Any]:
    cfg = api_config or {}
    explicit = cfg.get("native_file_inputs_enabled")
    responses_configured = _coerce_bool(cfg.get("use_responses_api"), False)
    official = _is_official_openai_connection(url)

    capability: dict[str, Any] = {
        "status": NATIVE_FILE_INPUT_STATUS_SUPPORTED,
        "reason": "supported",
        "message": "Native file inputs can be attempted for this connection.",
        "official": official,
        "responses_configured": responses_configured,
        "force_responses_required": not responses_configured,
    }

    if _is_azure_openai_connection(url, cfg):
        capability.update(
            {
                "status": NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
                "reason": "azure_connection",
                "message": (
                    "The current Azure OpenAI connection does not expose the OpenAI "
                    "Files/Responses protocol required for native file inputs."
                ),
                "force_responses_required": False,
            }
        )
        return capability

    if _is_force_mode_connection(url, cfg):
        capability.update(
            {
                "status": NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
                "reason": "force_mode_connection",
                "message": (
                    "The current force-mode connection cannot auto-route through the "
                    "OpenAI Files/Responses protocol required for native file inputs."
                ),
                "force_responses_required": False,
            }
        )
        return capability

    if explicit is not None:
        if _coerce_bool(explicit, False):
            capability["reason"] = "explicitly_enabled"
            return capability

        capability.update(
            {
                "status": NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG,
                "reason": "disabled_by_config",
                "message": "Native file inputs are disabled for the current connection.",
                "force_responses_required": False,
            }
        )
        return capability

    if official:
        capability["reason"] = "official_openai_default"
        return capability

    capability.update(
        {
            "status": NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG,
            "reason": "third_party_opt_in_required",
            "message": (
                "Native file inputs are not enabled for this third-party "
                "OpenAI-compatible connection."
            ),
            "force_responses_required": False,
        }
    )
    return capability


def _connection_supports_native_file_inputs(
    url: str, api_config: Optional[dict]
) -> bool:
    return (
        _get_native_file_input_capability(url, api_config).get("status")
        == NATIVE_FILE_INPUT_STATUS_SUPPORTED
    )


def _get_default_responses_reasoning_summary(
    api_config: Optional[dict],
) -> Optional[str]:
    cfg = api_config or {}

    setting = cfg.get("responses_reasoning_summary")
    if setting is None:
        setting = cfg.get("reasoning_summary")

    if setting is None:
        # Default to "auto" which selects the richest summarizer each model
        # supports (e.g. "detailed" for o4-mini, "concise" for computer-use
        # models).  GPT-5 series does NOT support "concise" — sending it
        # causes a silent empty summary — so "auto" is the safe default.
        return "auto"

    if isinstance(setting, bool):
        return "auto" if setting else None

    if isinstance(setting, str):
        normalized = setting.strip().lower()
        if not normalized or normalized in {"false", "off", "no", "disabled", "none"}:
            return None
        if normalized in {"true", "on", "yes"}:
            return "auto"
        if normalized in {"auto", "concise", "detailed"}:
            return normalized

    return "auto"


def _has_explicit_reasoning_summary_setting(chat_payload: Optional[dict]) -> bool:
    if not isinstance(chat_payload, dict):
        return False

    if isinstance(chat_payload.get("reasoning_summary"), str) and chat_payload.get(
        "reasoning_summary", ""
    ).strip():
        return True

    reasoning = chat_payload.get("reasoning")
    return bool(
        isinstance(reasoning, dict)
        and isinstance(reasoning.get("summary"), str)
        and reasoning.get("summary", "").strip()
    )


def _strip_reasoning_summary_from_payload(payload: Optional[dict]) -> tuple[dict, bool]:
    if not isinstance(payload, dict):
        return {}, False

    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, dict) or "summary" not in reasoning:
        return dict(payload), False

    next_payload = dict(payload)
    next_reasoning = dict(reasoning)
    next_reasoning.pop("summary", None)

    if next_reasoning:
        next_payload["reasoning"] = next_reasoning
    else:
        next_payload.pop("reasoning", None)

    return next_payload, True


def _looks_like_reasoning_summary_incompatible(status: int, body) -> bool:
    if status not in (400, 422):
        return False

    text = _stringify_upstream_body(body).lower()
    if not text:
        return False

    if "reasoning.summary" in text:
        return True

    if "reasoning" not in text or "summary" not in text:
        return False

    return any(
        token in text
        for token in (
            "unsupported",
            "unknown",
            "invalid",
            "unexpected",
            "additional properties",
            "extra fields not permitted",
            "not allowed",
            "not permitted",
            "unrecognized",
            "should not exist",
        )
    )


def _build_upstream_headers(
    base_url: str,
    key: str,
    api_config: dict,
    user: Optional[UserModel] = None,
    *,
    accept: Optional[str] = "application/json",
    content_type: Optional[str] = "application/json",
) -> dict:
    headers: dict = {}

    custom_headers = api_config.get("headers") if isinstance(api_config, dict) else None
    if isinstance(custom_headers, dict):
        for k, v in custom_headers.items():
            if v is None:
                continue
            headers[str(k)] = str(v)

    lower = {k.lower(): k for k in headers.keys()}
    auth_type = str((api_config or {}).get("auth_type") or "").strip().lower()
    if key and ("authorization" not in lower) and ("api-key" not in lower):
        if auth_type in {"none", "custom", "custom_headers_only"}:
            pass
        elif auth_type in {"api-key", "x-api-key"} or (
            _is_azure_openai_connection(base_url, api_config)
            and auth_type not in {"bearer", "authorization", "azure_ad", "microsoft_entra_id"}
        ):
            headers["api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"

    if accept and "accept" not in lower:
        headers["Accept"] = accept

    if content_type and "content-type" not in lower:
        headers["Content-Type"] = content_type

    if "openrouter.ai" in (base_url or ""):
        if "http-referer" not in lower:
            headers["HTTP-Referer"] = "https://openwebui.com/"
        if "x-title" not in lower:
            headers["X-Title"] = "Open WebUI"

    if ENABLE_FORWARD_USER_INFO_HEADERS and user:
        for k, v in {
            "X-OpenWebUI-User-Name": user.name,
            "X-OpenWebUI-User-Id": user.id,
            "X-OpenWebUI-User-Email": user.email,
            "X-OpenWebUI-User-Role": user.role,
        }.items():
            if k.lower() not in lower:
                headers[k] = v

    return headers


def _get_openai_file_cache_key(api_config: dict, url_idx: int) -> str:
    prefix = (api_config.get("_resolved_prefix_id") or "").strip()
    return prefix if prefix else f"idx:{url_idx}"


def _get_cached_openai_file_id(file_meta: dict, conn_key: str) -> Optional[str]:
    try:
        provider_meta = (file_meta or {}).get("openai", {}) or {}
        files_map = provider_meta.get("files", {}) or {}
        entry = files_map.get(conn_key, {}) or {}
        remote_id = entry.get("file_id")
        return str(remote_id) if remote_id else None
    except Exception:
        return None


def _set_cached_openai_file_id(
    file_id: str, file_meta: dict, conn_key: str, remote_file_id: str
) -> dict:
    meta = dict(file_meta or {})
    provider_meta = dict(meta.get("openai") or {})
    files_map = dict(provider_meta.get("files") or {})
    files_map[conn_key] = {
        "file_id": remote_file_id,
        "uploaded_at": int(time.time()),
    }
    provider_meta["files"] = files_map
    meta["openai"] = provider_meta
    Files.update_file_metadata_by_id(file_id, meta)
    return meta


async def _upload_file_to_openai(
    *,
    base_url: str,
    key: str,
    api_config: dict,
    local_path: str,
    filename: str,
    content_type: str,
    purpose: str = "user_data",
    user: Optional[UserModel] = None,
) -> str:
    upload_url = f"{base_url}/files"
    headers = _build_upstream_headers(
        base_url,
        key,
        api_config,
        user=user,
        accept="application/json",
        content_type=None,
    )

    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        form = aiohttp.FormData()
        form.add_field("purpose", purpose)

        with open(local_path, "rb") as file_handle:
            form.add_field(
                "file",
                file_handle,
                filename=filename,
                content_type=content_type or "application/octet-stream",
            )

            async with session.post(
                upload_url,
                data=form,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 400:
                    message = None
                    if isinstance(data, dict):
                        error = data.get("error")
                        if isinstance(error, dict):
                            message = error.get("message")
                        elif isinstance(error, str):
                            message = error
                        else:
                            message = data.get("message")
                    raise HTTPException(
                        status_code=response.status,
                        detail=message or str(data)[:500],
                    )

                if not isinstance(data, dict) or not data.get("id"):
                    raise HTTPException(
                        status_code=500,
                        detail="Invalid response from OpenAI Files API",
                    )

                return str(data["id"])


async def _safe_read_upstream_body(response: aiohttp.ClientResponse):
    try:
        return await response.json()
    except Exception:
        try:
            return await response.text()
        except Exception:
            return None


def _extract_upstream_error_detail(status: int, body) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if error is not None:
            return f"External Error: {error}"

        for key in ("detail", "message"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    text = _truncate_text(_stringify_upstream_body(body), 1200).strip()
    return text if text else f"HTTP Error: {status}"


def _coerce_model_list_entry(item) -> Optional[dict]:
    if isinstance(item, str) and item.strip():
        return {"id": item.strip(), "object": "model"}

    if isinstance(item, dict):
        model_id = item.get("id") or item.get("name") or item.get("model")
        if model_id:
            normalized = dict(item)
            normalized.setdefault("id", model_id)
            normalized.setdefault("object", "model")
            return normalized

    return None


def _normalize_openai_models_response(body) -> Optional[dict]:
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return body

        models = body.get("models")
        if isinstance(models, list):
            normalized = [
                entry
                for item in models
                if (entry := _coerce_model_list_entry(item)) is not None
            ]
            result = dict(body)
            result.setdefault("object", "list")
            result["data"] = normalized
            return result

    if isinstance(body, list):
        normalized = [
            entry
            for item in body
            if (entry := _coerce_model_list_entry(item)) is not None
        ]
        return {"object": "list", "data": normalized}

    return None


def _apply_native_web_search_support_to_models_response(
    body: Optional[dict],
    *,
    url: str,
    api_config: Optional[dict],
    connection_name: Optional[str] = None,
) -> Optional[dict]:
    if not isinstance(body, dict):
        return body

    connection_support = build_native_web_search_support(
        "openai",
        url=url,
        api_config=api_config,
        connection_name=connection_name,
    )

    data = body.get("data")
    if isinstance(data, list):
        for model in data:
            if not isinstance(model, dict):
                continue
            model_support = resolve_effective_native_web_search_support(
                connection_support,
                provider="openai",
                model_id=model.get("original_id")
                or strip_model_prefix(
                    model.get("id") or "", (api_config or {}).get("_resolved_prefix_id")
                ),
                model_name=model.get("name") or "",
            )
            model["native_web_search_supported"] = model_support.get("supported") is True
            model["native_web_search_support"] = dict(model_support)

    meta = body.get("_openwebui")
    body["_openwebui"] = meta if isinstance(meta, dict) else {}
    body["_openwebui"]["native_web_search_support"] = dict(connection_support)
    return body


def _is_dashscope_compatible_connection(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        path = (parsed.path or "").rstrip("/")
    except Exception:
        host = ""
        path = ""

    if host == "coding.dashscope.aliyuncs.com":
        return path == "/v1"

    if host == "dashscope.aliyuncs.com" or (
        host.startswith("dashscope-") and host.endswith(".aliyuncs.com")
    ):
        return path == "/compatible-mode/v1"

    return False


def _looks_like_models_listing_unsupported(status: int, body) -> bool:
    if status not in (404, 405):
        return False

    text = _stringify_upstream_body(body).lower().strip()
    if not text:
        return True

    if text.startswith("<!doctype html") or text.startswith("<html"):
        return True

    if any(
        term in text
        for term in (
            "endpoint not found",
            "route not found",
            "resource not found",
            "404 page not found",
        )
    ):
        return True

    if "models" in text and any(
        term in text for term in ("not found", "unsupported", "not support", "unknown", "no route")
    ):
        return True

    return False


def _build_manual_model_discovery_response(
    provider: Optional[str] = None,
) -> dict:
    return {
        "object": "list",
        "data": [],
        "_openwebui": {
            "manual_model_ids_required": True,
            "reason": "models_endpoint_unsupported",
            **({"provider": provider} if provider else {}),
        },
    }


def _build_models_listing_fallback(
    *,
    url: str,
    api_config: Optional[dict],
    purpose: str,
    status: int,
    body,
) -> Optional[dict]:
    if not _looks_like_models_listing_unsupported(status, body):
        return None

    if _is_dashscope_compatible_connection(url):
        log.info(
            "Upstream %s does not expose /models; using DashScope-compatible fallback for %s",
            url,
            purpose,
        )
        if purpose == "models":
            return _build_manual_model_discovery_response(provider="dashscope")

        return {
            "ok": True,
            "_openwebui": {
                "verification_succeeded": True,
                "provider": "dashscope",
                "models_endpoint_supported": False,
            },
        }

    if _is_azure_openai_connection(url, api_config):
        log.info(
            "Azure upstream %s does not expose a compatible /models list; falling back to manual deployment entry for %s",
            url,
            purpose,
        )
        if purpose == "models":
            return _build_manual_model_discovery_response(provider="azure")

        return {
            "ok": True,
            "_openwebui": {
                "verification_succeeded": True,
                "provider": "azure",
                "models_endpoint_supported": False,
                "manual_model_ids_required": True,
            },
        }

    return None


def _looks_like_responses_endpoint_unsupported(status: int, body_text: str) -> bool:
    if status in (404, 405):
        return True
    text = (body_text or "").lower()
    if "/responses" in text and ("not found" in text or "unknown" in text):
        return True
    if "unsupported" in text and "responses" in text:
        return True
    return False


def _build_native_file_input_probe_cache_key(
    url: str, api_config: Optional[dict]
) -> str:
    cfg = api_config or {}
    payload = {
        "url": (url or "").rstrip("/"),
        "auth_type": str(cfg.get("auth_type") or ""),
        "headers": cfg.get("headers") or {},
        "prefix_id": str(
            cfg.get("_resolved_prefix_id") or cfg.get("prefix_id") or ""
        ),
        "azure": bool(cfg.get("azure")),
        "force_mode": bool(cfg.get("force_mode")),
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def _probe_responses_support_for_native_file_inputs(
    *,
    url: str,
    key: str,
    api_config: Optional[dict],
    user: Optional[UserModel] = None,
    model_id: Optional[str] = None,
) -> dict[str, Any]:
    cache_key = _build_native_file_input_probe_cache_key(url, api_config)
    cached_result = _NATIVE_FILE_INPUT_PROBE_CACHE.get(cache_key)
    if cached_result and (time.time() - cached_result[0]) < _NATIVE_FILE_INPUT_PROBE_TTL_SECONDS:
        return dict(cached_result[1])

    cfg = api_config or {}
    headers = _build_upstream_headers(url, key or "", cfg, user=user)
    chosen_model = str(model_id or "gpt-4o-mini")
    prefix_id = str(cfg.get("_resolved_prefix_id") or cfg.get("prefix_id") or "").strip()
    if prefix_id:
        prefix = f"{prefix_id}."
        if chosen_model.startswith(prefix):
            chosen_model = chosen_model[len(prefix) :]
    if not chosen_model:
        chosen_model = "gpt-4o-mini"

    request_url = f"{(url or '').rstrip('/')}/responses"
    payload = {
        "model": chosen_model,
        "input": [{"role": "user", "content": "ping"}],
        "stream": False,
    }

    result: dict[str, Any]
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
            trust_env=True,
        ) as session:
            async with session.post(
                request_url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False, default=str),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                body = await _safe_read_upstream_body(response)
                body_text = _truncate_text(_stringify_upstream_body(body), 1200)

                if response.status < 400:
                    result = {
                        "supported": True,
                        "status": NATIVE_FILE_INPUT_STATUS_SUPPORTED,
                        "reason": "responses_probe_succeeded",
                        "message": "Responses endpoint probe succeeded for native file inputs.",
                        "http_status": response.status,
                        "body_preview": body_text,
                    }
                elif _looks_like_responses_endpoint_unsupported(response.status, body_text):
                    result = {
                        "supported": False,
                        "status": NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
                        "reason": "responses_endpoint_unsupported",
                        "message": (
                            "The current connection does not support the OpenAI "
                            "Responses endpoint required for native file inputs."
                        ),
                        "http_status": response.status,
                        "body_preview": body_text,
                    }
                else:
                    result = {
                        "supported": None,
                        "status": NATIVE_FILE_INPUT_STATUS_SUPPORTED,
                        "reason": "responses_probe_inconclusive",
                        "message": (
                            "Responses endpoint probing was inconclusive; native "
                            "file inputs will be attempted anyway."
                        ),
                        "http_status": response.status,
                        "body_preview": body_text,
                    }
    except Exception as exc:
        result = {
            "supported": None,
            "status": NATIVE_FILE_INPUT_STATUS_SUPPORTED,
            "reason": "responses_probe_failed",
            "message": (
                "Responses endpoint probing failed; native file inputs will be "
                "attempted anyway."
            ),
            "detail": _truncate_text(str(exc), 1200),
        }

    _NATIVE_FILE_INPUT_PROBE_CACHE[cache_key] = (time.time(), dict(result))
    return result


def _looks_like_chat_completions_endpoint_unsupported(status: int, body) -> bool:
    if status in (404, 405):
        return True

    text = _stringify_upstream_body(body).lower().strip()
    if not text:
        return False

    return (
        "chat/completions" in text
        and any(term in text for term in ("not found", "unknown", "unsupported", "no route"))
    )


def _build_chat_completion_request_attempts(
    *,
    url: str,
    api_config: Optional[dict],
    model_id: Optional[str],
    payload_dict: Optional[dict],
) -> list[tuple[str, Optional[dict]]]:
    if not isinstance(payload_dict, dict):
        return [(_get_openai_chat_completions_url(url, api_config), payload_dict)]

    if not _is_azure_openai_connection(url, api_config):
        return [(_get_openai_chat_completions_url(url, api_config), payload_dict)]

    attempts: list[tuple[str, Optional[dict]]] = []
    for request_url in _get_azure_openai_chat_completions_urls(
        url, api_config, model_id=model_id
    ):
        next_payload = payload_dict
        if AZURE_OPENAI_DEPLOYMENTS_SEGMENT in (urlparse(request_url).path or ""):
            next_payload = dict(payload_dict)
            next_payload.pop("model", None)
        attempts.append((request_url, next_payload))

    return attempts or [(_get_openai_chat_completions_url(url, api_config), payload_dict)]


def _format_responses_upstream_error(
    *,
    request_url: str,
    status: int,
    body,
) -> str:
    host = ""
    try:
        host = urlparse(request_url).hostname or ""
    except Exception:
        host = ""

    body_text = _truncate_text(_stringify_upstream_body(body), 2400)

    hint = ""
    if _looks_like_responses_endpoint_unsupported(status, body_text):
        hint = (
            "Hint: This upstream likely does not support the Responses API endpoint (/responses). "
            "Disable 'Responses API' for this connection or switch upstream."
        )

    parts = [
        f"Responses API upstream error ({status}){f' from {host}' if host else ''}.",
        hint,
        (f"Upstream response: {body_text}" if body_text else ""),
    ]
    return "\n".join([p for p in parts if p])


def _get_chat_upstream_error_message(*, status: int, body) -> str:
    body_text = _truncate_text(_stringify_upstream_body(body), 2400).strip()
    if body_text:
        return body_text
    return f"HTTP Error: {status}"


async def error_sse_generator(message: str, *, code: str = "upstream_error"):
    yield (
        "data: "
        + json.dumps(
            {"error": {"message": message, "type": "api_error", "code": code}},
            ensure_ascii=False,
        )
        + "\n\n"
    )
    yield "data: [DONE]\n\n"


async def send_get_request(
    url,
    key=None,
    user: UserModel = None,
    api_config: Optional[dict] = None,
):
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(
                url,
                headers=_build_upstream_headers(url, key or "", api_config or {}, user=user),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                body = await _safe_read_upstream_body(response)
                if response.status == 200:
                    normalized = _normalize_openai_models_response(body)
                    return normalized if normalized is not None else body

                fallback = _build_models_listing_fallback(
                    url=url,
                    api_config=api_config,
                    purpose="models",
                    status=response.status,
                    body=body,
                )
                if fallback is not None:
                    return fallback

                log.warning(
                    "Model fetch failed from %s with status=%s body=%s",
                    url,
                    response.status,
                    _truncate_text(_stringify_upstream_body(body), 800),
                )
                return None
    except Exception as e:
        # Handle connection error here
        log.error(f"Connection error: {e}")
        return None


async def cleanup_response(
    response: Optional[aiohttp.ClientResponse],
    session: Optional[aiohttp.ClientSession],
):
    if response:
        response.close()
    if session:
        await session.close()


def openai_o1_o3_handler(payload):
    """
    Handle o1, o3 specific parameters
    """
    if "max_tokens" in payload:
        # Remove "max_tokens" from the payload
        payload["max_completion_tokens"] = payload["max_tokens"]
        del payload["max_tokens"]

    # Fix: o1 and o3 do not support the "system" role directly.
    # For older models like "o1-mini" or "o1-preview", use role "user".
    # For newer o1/o3 models, replace "system" with "developer".
    if payload["messages"][0]["role"] == "system":
        model_lower = payload["model"].lower()
        if model_lower.startswith("o1-mini") or model_lower.startswith("o1-preview"):
            payload["messages"][0]["role"] = "user"
        else:
            payload["messages"][0]["role"] = "developer"

    return payload


##########################################
#
# API routes
#
##########################################

router = APIRouter()


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_OPENAI_API": request.app.state.config.ENABLE_OPENAI_API,
        "OPENAI_API_BASE_URLS": request.app.state.config.OPENAI_API_BASE_URLS,
        "OPENAI_API_KEYS": request.app.state.config.OPENAI_API_KEYS,
        "OPENAI_API_CONFIGS": request.app.state.config.OPENAI_API_CONFIGS,
    }


class OpenAIConfigForm(BaseModel):
    ENABLE_OPENAI_API: Optional[bool] = None
    OPENAI_API_BASE_URLS: list[str]
    OPENAI_API_KEYS: list[str]
    OPENAI_API_CONFIGS: dict


@router.post("/config/update")
async def update_config(
    request: Request, form_data: OpenAIConfigForm, user=Depends(get_admin_user)
):
    # Preserve existing per-URL prefix_id to avoid breaking chats when admins edit connections.
    # prefix_id is an internal stable identifier used for uniqueness/routing and should not be user-editable.
    prev_urls = list(getattr(request.app.state.config, "OPENAI_API_BASE_URLS", []) or [])
    prev_cfgs = getattr(request.app.state.config, "OPENAI_API_CONFIGS", {}) or {}
    prev_prefix_by_url: dict[str, str] = {}
    prev_empty_urls: set[str] = set()

    for idx, prev_url in enumerate(prev_urls):
        url_key = (prev_url or "").rstrip("/")
        if not url_key:
            continue
        cfg = prev_cfgs.get(str(idx), prev_cfgs.get(prev_url, {})) or {}
        raw = cfg.get("prefix_id", None)
        prefix = (raw or "").strip() if isinstance(raw, str) else (str(raw).strip() if raw is not None else "")
        if prefix:
            prev_prefix_by_url.setdefault(url_key, prefix)
        else:
            prev_empty_urls.add(url_key)

    request.app.state.config.ENABLE_OPENAI_API = form_data.ENABLE_OPENAI_API
    request.app.state.config.OPENAI_API_BASE_URLS = form_data.OPENAI_API_BASE_URLS
    request.app.state.config.OPENAI_API_KEYS = form_data.OPENAI_API_KEYS

    # Check if API KEYS length is same than API URLS length
    if len(request.app.state.config.OPENAI_API_KEYS) != len(
        request.app.state.config.OPENAI_API_BASE_URLS
    ):
        if len(request.app.state.config.OPENAI_API_KEYS) > len(
            request.app.state.config.OPENAI_API_BASE_URLS
        ):
            request.app.state.config.OPENAI_API_KEYS = (
                request.app.state.config.OPENAI_API_KEYS[
                    : len(request.app.state.config.OPENAI_API_BASE_URLS)
                ]
            )
        else:
            request.app.state.config.OPENAI_API_KEYS += [""] * (
                len(request.app.state.config.OPENAI_API_BASE_URLS)
                - len(request.app.state.config.OPENAI_API_KEYS)
            )

    request.app.state.config.OPENAI_API_CONFIGS = form_data.OPENAI_API_CONFIGS

    # Remove the API configs that are not in the API URLS
    keys = list(map(str, range(len(request.app.state.config.OPENAI_API_BASE_URLS))))
    request.app.state.config.OPENAI_API_CONFIGS = {
        key: value
        for key, value in request.app.state.config.OPENAI_API_CONFIGS.items()
        if key in keys
    }

    # Normalize configs:
    # - add default name (display only)
    # - ensure prefix_id uniqueness when present
    # - preserve an empty prefix_id only if it already existed for a URL (backward compatibility)
    used_prefix_ids = set()
    normalized_configs = {}

    preserved_empty_idx = None
    if len(keys) >= 1:
        for idx_str in keys:
            idx = int(idx_str)
            url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
            url_key = (url or "").rstrip("/")
            if url_key and url_key in prev_empty_urls:
                preserved_empty_idx = idx
                break

    for idx_str in keys:
        idx = int(idx_str)
        url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
        url_key = (url or "").rstrip("/")
        cfg = request.app.state.config.OPENAI_API_CONFIGS.get(idx_str, {}) or {}

        # User-facing name (display only)
        name = (cfg.get("name") or "").strip()
        if not name:
            try:
                parsed = urlparse(url)
                name = parsed.hostname or f"Connection {idx + 1}"
            except Exception:
                name = f"Connection {idx + 1}"

        # Internal prefix_id used for uniqueness/routing (hidden from users)
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

        normalized_cfg = {**cfg, "name": name}
        if prefix_id:
            normalized_cfg["prefix_id"] = prefix_id
        elif preserved_empty_idx == idx:
            # Store explicit empty marker so runtime normalization can preserve this legacy "no prefix" connection.
            normalized_cfg["prefix_id"] = ""
        else:
            # omit empty prefix_id to keep configs clean
            normalized_cfg.pop("prefix_id", None)

        normalized_configs[idx_str] = normalized_cfg

    request.app.state.config.OPENAI_API_CONFIGS = normalized_configs

    # Refresh model list cache when config changes
    from open_webui.utils.models import invalidate_base_model_cache

    request.app.state.BASE_MODELS = None
    request.app.state.OPENAI_MODELS = {}
    request.app.state.MODELS = {}
    invalidate_base_model_cache(user.id)

    return {
        "ENABLE_OPENAI_API": request.app.state.config.ENABLE_OPENAI_API,
        "OPENAI_API_BASE_URLS": request.app.state.config.OPENAI_API_BASE_URLS,
        "OPENAI_API_KEYS": request.app.state.config.OPENAI_API_KEYS,
        "OPENAI_API_CONFIGS": request.app.state.config.OPENAI_API_CONFIGS,
    }


@router.post("/audio/speech")
async def speech(request: Request, user=Depends(get_verified_user)):
    idx = None
    try:
        idx = request.app.state.config.OPENAI_API_BASE_URLS.index(
            "https://api.openai.com/v1"
        )

        body = await request.body()
        name = hashlib.sha256(body).hexdigest()

        SPEECH_CACHE_DIR = CACHE_DIR / "audio" / "speech"
        SPEECH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        file_path = SPEECH_CACHE_DIR.joinpath(f"{name}.mp3")
        file_body_path = SPEECH_CACHE_DIR.joinpath(f"{name}.json")

        # Check if the file already exists in the cache
        if file_path.is_file():
            return FileResponse(file_path)

        url = request.app.state.config.OPENAI_API_BASE_URLS[idx]

        r = None
        try:
            r = requests.post(
                url=f"{url}/audio/speech",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {request.app.state.config.OPENAI_API_KEYS[idx]}",
                    **(
                        {
                            "HTTP-Referer": "https://openwebui.com/",
                            "X-Title": "Open WebUI",
                        }
                        if "openrouter.ai" in url
                        else {}
                    ),
                    **(
                        {
                            "X-OpenWebUI-User-Name": user.name,
                            "X-OpenWebUI-User-Id": user.id,
                            "X-OpenWebUI-User-Email": user.email,
                            "X-OpenWebUI-User-Role": user.role,
                        }
                        if ENABLE_FORWARD_USER_INFO_HEADERS
                        else {}
                    ),
                },
                stream=True,
                verify=REQUESTS_VERIFY,
            )

            r.raise_for_status()

            # Save the streaming content to a file
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

            with open(file_body_path, "w") as f:
                json.dump(json.loads(body.decode("utf-8")), f)

            # Return the saved file
            return FileResponse(file_path)

        except Exception as e:
            log.exception(e)

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=build_error_detail(
                    read_requests_error_payload(r),
                    e,
                ),
            )

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def get_all_models_responses(request: Request, user: UserModel) -> list:
    base_urls, keys, cfgs = _get_openai_user_config(user)
    if not base_urls:
        return []

    num_urls = len(base_urls)

    # If multiple OpenAI-compatible connections exist, ensure every connection has a stable internal prefix_id.
    # This avoids id collisions and prevents UI crashes due to duplicate keyed list items.
    cfgs = cfgs or {}
    cfgs_changed = False
    if num_urls > 1:
        cfgs = dict(cfgs)
        used = set()

        preserved_empty_idx = None
        # Prefer an explicit empty marker stored in config.
        for idx, url in enumerate(base_urls):
            api_config = cfgs.get(str(idx), cfgs.get(url, {})) or {}
            if api_config.get("prefix_id", None) == "":
                preserved_empty_idx = idx
                break

        # Backward compatibility: for legacy configs that omitted prefix_id, preserve empty for index 0.
        if preserved_empty_idx is None and len(base_urls) >= 1:
            url0 = base_urls[0]
            cfg0 = cfgs.get("0", cfgs.get(url0, {})) or {}
            if not (cfg0.get("prefix_id") or "").strip():
                preserved_empty_idx = 0

        for idx, url in enumerate(base_urls):
            key = str(idx)
            api_config = cfgs.get(key, cfgs.get(url, {})) or {}

            name = (api_config.get("remark") or "").strip()
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
                # Store explicit empty marker.
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
                "openai",
                {
                    "OPENAI_API_BASE_URLS": base_urls,
                    "OPENAI_API_KEYS": keys,
                    "OPENAI_API_CONFIGS": cfgs,
                },
            )
        except Exception:
            # Best-effort persistence; do not block model listing.
            pass

    request_tasks = []
    for idx, url in enumerate(base_urls):
        idx_key = str(idx)
        api_config = cfgs.get(idx_key, cfgs.get(url, {})) or {}

        enable = api_config.get("enable", True)
        model_ids = api_config.get("model_ids", [])
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
                    (m[len(prefix) :] if isinstance(m, str) and m.startswith(prefix) else m) for m in model_ids
                ]

            model_list = {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "name": model_id,
                        "owned_by": "openai",
                        "openai": {"id": model_id},
                        "urlIdx": idx,
                    }
                    for model_id in model_ids
                ],
            }

            request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, model_list)))
            continue

        request_tasks.append(
            send_get_request(
                _get_openai_models_url(url, api_config),
                keys[idx] if idx < len(keys) else "",
                user=user,
                api_config=api_config,
            )
        )

    responses = await asyncio.gather(*request_tasks, return_exceptions=True)

    # Degrade gracefully: a single bad connection (401/timeout/etc.) should not break the whole models list.
    for idx, resp in enumerate(responses):
        if isinstance(resp, BaseException):
            url = base_urls[idx] if idx < len(base_urls) else ""
            if isinstance(resp, HTTPException):
                log.warning(
                    f"[OPENAI] models fetch failed (idx={idx}, url={url}) "
                    f"{resp.status_code}: {resp.detail}"
                )
            else:
                log.warning(
                    f"[OPENAI] models fetch failed (idx={idx}, url={url}) {type(resp).__name__}: {resp}"
                )
            responses[idx] = None

    for idx, response in enumerate(responses):
        if not response:
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
        connection_support = build_native_web_search_support(
            "openai",
            url=url,
            api_config=api_config,
            connection_name=connection_name,
        )
        tags = api_config.get("tags", [])
        connection_icon = (api_config.get("icon") or "").strip()

        for model in response if isinstance(response, list) else response.get("data", []):
            original_id = model.get("id") or model.get("name") or ""

            # Avoid showing/stacking internal prefixes in display names.
            if prefix_id:
                prefix = f"{prefix_id}."
                if isinstance(original_id, str) and original_id.startswith(prefix):
                    original_id = original_id[len(prefix) :]
                display_name = model.get("name") or original_id
                if isinstance(display_name, str) and display_name.startswith(prefix):
                    display_name = display_name[len(prefix) :]
            else:
                display_name = model.get("name") or original_id

            if connection_name:
                model["connection_name"] = connection_name
            if connection_icon:
                model["connection_icon"] = connection_icon
            model["name"] = display_name
            model_support = resolve_effective_native_web_search_support(
                connection_support,
                provider="openai",
                model_id=original_id,
                model_name=display_name,
            )
            model["native_web_search_supported"] = (
                model_support.get("supported") is True
            )
            model["native_web_search_support"] = dict(model_support)

            if prefix_id:
                model["original_id"] = original_id
                model["id"] = f"{prefix_id}.{original_id}"

        if tags:
            for model in response if isinstance(response, list) else response.get("data", []):
                model["tags"] = tags

    log.debug(f"get_all_models:responses() {responses}")
    return responses


async def get_filtered_models(models, user):
    # Filter models based on user access control
    filtered_models = []
    for model in models.get("data", []):
        model_info = Models.get_model_by_id(model["id"])
        if model_info:
            if user.id == model_info.user_id or has_access(
                user.id, type="read", access_control=model_info.access_control
            ):
                filtered_models.append(model)
    return filtered_models


async def get_all_models(request: Request, user: UserModel) -> dict[str, list]:
    log.info("get_all_models()")

    base_urls, _keys, _cfgs = _get_openai_user_config(user)
    if not base_urls:
        return {"data": []}

    responses = await get_all_models_responses(request, user=user)

    def extract_data(response):
        if response and "data" in response:
            return response["data"]
        if isinstance(response, list):
            return response
        return None

    def merge_models_lists(model_lists):
        log.debug(f"merge_models_lists {model_lists}")
        merged_list = []

        for idx, models in enumerate(model_lists):
            if models is not None and "error" not in models:

                merged_list.extend(
                    [
                        {
                            **model,
                            "name": model.get("name", model["id"]),
                            "owned_by": "openai",
                            "openai": model,
                            "urlIdx": idx,
                        }
                        for model in models
                        if (model.get("id") or model.get("name"))
                    ]
                )

        return merged_list

    models = {"data": merge_models_lists(map(extract_data, responses))}
    log.debug(f"models: {models}")
    return models


@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    models = {
        "data": [],
    }

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        base_urls, keys, _cfgs = _get_openai_user_config(user)
        if url_idx < 0 or url_idx >= len(base_urls):
            raise HTTPException(status_code=404, detail="Connection not found")

        url = base_urls[url_idx]
        key = keys[url_idx] if url_idx < len(keys) else ""
        api_config = _cfgs.get(str(url_idx), _cfgs.get(url, {})) or {}
        models_url = _get_openai_models_url(url, api_config)

        r = None
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
        ) as session:
            try:
                async with session.get(
                    models_url,
                    headers=_build_upstream_headers(url, key, api_config, user=user),
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    response_data = await _safe_read_upstream_body(r)
                    if r.status != 200:
                        fallback = _build_models_listing_fallback(
                            url=url,
                            api_config=api_config,
                            purpose="models",
                            status=r.status,
                            body=response_data,
                        )
                        if fallback is not None:
                            models = fallback
                            response_data = None
                        else:
                            raise HTTPException(
                                status_code=400,
                                detail=_extract_upstream_error_detail(r.status, response_data),
                            )

                    if response_data is None:
                        pass
                    else:
                        normalized_response = _normalize_openai_models_response(response_data)
                        if normalized_response is None:
                            raise HTTPException(
                                status_code=400,
                                detail="Invalid response from upstream /models endpoint",
                            )
                        response_data = normalized_response

                    if response_data is not None:
                        # Check if we're calling OpenAI API based on the URL
                        if "api.openai.com" in url:
                            # Filter models according to the specified conditions
                            response_data["data"] = [
                                model
                                for model in response_data.get("data", [])
                                if not any(
                                    name in model["id"]
                                    for name in [
                                        "babbage",
                                        "dall-e",
                                        "davinci",
                                        "embedding",
                                        "tts",
                                        "whisper",
                                    ]
                                )
                            ]

                        models = _apply_native_web_search_support_to_models_response(
                            response_data,
                            url=url,
                            api_config=api_config,
                        )
            except aiohttp.ClientError as e:
                # ClientError covers all aiohttp requests issues
                log.exception(f"Client error: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail=build_error_detail(e),
                )
            except Exception as e:
                log.exception(f"Unexpected error: {e}")
                error_detail = f"Unexpected error: {str(e)}"
                raise HTTPException(status_code=500, detail=error_detail)

    if user.role == "user" and not BYPASS_MODEL_ACCESS_CONTROL:
        models["data"] = await get_filtered_models(models, user)

    return models


class ConnectionVerificationForm(BaseModel):
    url: str
    key: str
    config: Optional[dict] = None
    purpose: Literal["connection", "models"] = "connection"


class ResponsesVerificationForm(BaseModel):
    url: str
    key: str = ""
    headers: Optional[dict] = None
    model: Optional[str] = None


class HealthCheckForm(BaseModel):
    url: str
    key: str = ""
    config: Optional[dict] = None
    model: Optional[str] = None


async def _discover_openai_probe_model(
    *,
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    api_config: Optional[dict],
) -> Optional[str]:
    models_url = _get_openai_models_url(url, api_config)

    try:
        async with session.get(
            models_url,
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as response:
            body = await _safe_read_upstream_body(response)
            if response.status != 200:
                return None

            normalized = _normalize_openai_models_response(body)
            items = normalized.get("data") if isinstance(normalized, dict) else None
            if not isinstance(items, list) or not items:
                return None

            first = items[0]
            if isinstance(first, dict):
                return first.get("id") or first.get("name")
    except Exception:
        return None

    return None


@router.post("/verify")
async def verify_connection(
    form_data: ConnectionVerificationForm, user=Depends(get_verified_user)
):
    url = form_data.url
    key = form_data.key
    api_config = form_data.config or {}
    purpose = form_data.purpose or "connection"
    models_url = _get_openai_models_url(url, api_config)
    headers = _build_upstream_headers(url, key, api_config, user=user)

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
        trust_env=True,
    ) as session:
        try:
            async with session.get(
                models_url,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as r:
                response_body = await _safe_read_upstream_body(r)

                if r.status == 200:
                    normalized_response = _normalize_openai_models_response(response_body)
                    if normalized_response is not None:
                        return _apply_native_web_search_support_to_models_response(
                            normalized_response,
                            url=url,
                            api_config=api_config,
                        )

                    raise HTTPException(
                        status_code=400,
                        detail="Invalid response from upstream /models endpoint",
                    )

                fallback = _build_models_listing_fallback(
                    url=url,
                    api_config=api_config,
                    purpose=purpose,
                    status=r.status,
                    body=response_body,
                )
                if fallback is not None:
                    return fallback

                raise HTTPException(
                    status_code=400,
                    detail=_extract_upstream_error_detail(r.status, response_body),
                )

        except HTTPException:
            raise
        except aiohttp.ClientError as e:
            # ClientError covers all aiohttp requests issues
            log.exception(f"Client error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=build_error_detail(e),
            )
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            error_detail = f"Unexpected error: {str(e)}"
            raise HTTPException(status_code=500, detail=error_detail)


@router.post("/health_check")
async def health_check_connection(
    form_data: HealthCheckForm, user=Depends(get_verified_user)
):
    url = (form_data.url or "").rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    key = form_data.key or ""
    api_config = form_data.config or {}
    headers = _build_upstream_headers(url, key, api_config, user=user)

    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            chosen_model = form_data.model or await _discover_openai_probe_model(
                session=session,
                url=url,
                headers=headers,
                api_config=api_config,
            )

            if not chosen_model:
                chosen_model = "gpt-4o-mini"

            prefix_id = api_config.get("prefix_id", None)
            if prefix_id and isinstance(chosen_model, str):
                prefix = f"{prefix_id}."
                if chosen_model.startswith(prefix):
                    chosen_model = chosen_model[len(prefix) :]

            use_responses_api = _should_use_responses_api(
                url, api_config, chosen_model, native_web_search=False
            )

            probe_payload = {
                "model": chosen_model,
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
                # OpenAI Responses API can reject extremely small max_output_tokens values
                # (e.g. official OpenAI currently requires >=16). Keep chat probes at 1 token
                # but use a small safe floor for Responses API health checks.
                "max_tokens": 16 if use_responses_api else 1,
            }

            request_url = (
                f"{url}/responses"
                if use_responses_api
                else _get_openai_chat_completions_url(url, api_config)
            )

            auto_reasoning_summary_applied = False
            if use_responses_api:
                payload_dict = convert_chat_completions_to_responses_payload(
                    probe_payload,
                    default_reasoning_summary=_get_default_responses_reasoning_summary(
                        api_config
                    ),
                )
                auto_reasoning_summary_applied = bool(
                    isinstance(payload_dict.get("reasoning"), dict)
                    and payload_dict["reasoning"].get("summary")
                )
                request_attempts = [(request_url, payload_dict)]
            else:
                payload_dict = dict(probe_payload)

                is_o1_o3 = payload_dict["model"].lower().startswith(("o1", "o3-"))
                if is_o1_o3:
                    payload_dict = openai_o1_o3_handler(payload_dict)
                elif "api.openai.com" not in url and "max_completion_tokens" in payload_dict:
                    payload_dict["max_tokens"] = payload_dict["max_completion_tokens"]
                    del payload_dict["max_completion_tokens"]

                if "max_tokens" in payload_dict and "max_completion_tokens" in payload_dict:
                    del payload_dict["max_tokens"]

                request_attempts = _build_chat_completion_request_attempts(
                    url=url,
                    api_config=api_config,
                    model_id=payload_dict.get("model"),
                    payload_dict=payload_dict,
                )

            async def _post_once(target_url: str, payload: dict):
                async with session.post(
                    target_url,
                    headers=headers,
                    data=json.dumps(payload, ensure_ascii=False, default=str),
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as response:
                    body = await _safe_read_upstream_body(response)
                    return response.status, body

            started_at = time.monotonic()
            attempt_idx = 0
            request_url, payload_dict = request_attempts[attempt_idx]
            status, response_body = await _post_once(request_url, payload_dict)

            if not use_responses_api:
                if (
                    status >= 400
                    and attempt_idx + 1 < len(request_attempts)
                    and _looks_like_chat_completions_endpoint_unsupported(
                        status, response_body
                    )
                ):
                    attempt_idx += 1
                    request_url, payload_dict = request_attempts[attempt_idx]
                    status, response_body = await _post_once(request_url, payload_dict)

                if status >= 400:
                    raise HTTPException(
                        status_code=status,
                        detail=_extract_upstream_error_detail(status, response_body),
                    )
            else:
                if (
                    status >= 400
                    and auto_reasoning_summary_applied
                    and _looks_like_reasoning_summary_incompatible(
                        status, response_body
                    )
                ):
                    next_payload_dict, removed = _strip_reasoning_summary_from_payload(
                        payload_dict
                    )
                    if removed:
                        payload_dict = next_payload_dict
                        status, response_body = await _post_once(
                            request_url, payload_dict
                        )

                if status >= 400:
                    raise HTTPException(
                        status_code=status,
                        detail=_format_responses_upstream_error(
                            request_url=request_url,
                            status=status,
                            body=response_body,
                        ),
                    )

            if not isinstance(response_body, dict):
                raise HTTPException(
                    status_code=502,
                    detail="Invalid response from upstream model health check",
                )

            elapsed_ms = max(1, int((time.monotonic() - started_at) * 1000))
            return {
                "ok": True,
                "model": chosen_model,
                "response_time_ms": elapsed_ms,
            }
    except HTTPException:
        raise
    except aiohttp.ClientError as e:
        log.exception(f"Client error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=build_error_detail(e),
        )
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.post("/verify_responses")
async def verify_responses_connection(
    form_data: ResponsesVerificationForm, user=Depends(get_admin_user)
):
    """
    Verifies whether the upstream supports POST {base}/responses.

    Important: This endpoint does NOT fall back to /chat/completions.
    """
    url = (form_data.url or "").rstrip("/")
    key = form_data.key or ""

    # Merge provided headers with defaults.
    api_config = {"headers": form_data.headers or {}}
    headers = _build_upstream_headers(url, key, api_config, user=user)

    chosen_model = form_data.model
    models_error = None

    # Try to discover a model via /models so we don't misclassify "model not found"
    # as "responses not supported".
    if not chosen_model:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
                trust_env=True,
            ) as session:
                async with session.get(
                    f"{url}/models",
                    headers=headers,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    if r.status == 200:
                        data = await r.json()
                        items = data.get("data") if isinstance(data, dict) else None
                        if isinstance(items, list) and items:
                            first = items[0]
                            if isinstance(first, dict):
                                chosen_model = first.get("id") or first.get("name")
                    else:
                        models_error = await _safe_read_upstream_body(r)
        except Exception as e:
            models_error = str(e)

    if not chosen_model:
        # Use a placeholder; response is still useful for detecting 404/405 endpoint errors.
        chosen_model = "gpt-4o-mini"

    request_url = f"{url}/responses"
    payload = {
        "model": chosen_model,
        "input": [{"role": "user", "content": "ping"}],
        "stream": False,
    }

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
        trust_env=True,
    ) as session:
        async with session.post(
            request_url,
            headers=headers,
            data=json.dumps(payload),
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as r:
            body = await _safe_read_upstream_body(r)

            body_text = _stringify_upstream_body(body)
            supports_responses = r.status < 400 and isinstance(body, dict)
            endpoint_supported = None if supports_responses else not _looks_like_responses_endpoint_unsupported(r.status, body_text)

            return {
                "ok": supports_responses,
                "supports_responses": supports_responses,
                "endpoint_supported_guess": endpoint_supported,
                "status": r.status,
                "model_used": chosen_model,
                "models_probe_error": models_error,
                "upstream_body_preview": _truncate_text(body_text, 1200),
            }


@router.post("/chat/completions")
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
):
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    idx = 0

    payload = {**form_data}
    custom_params = payload.pop("custom_params", None)
    metadata = payload.pop("metadata", None)

    model_id = form_data.get("model")
    model_info = Models.get_model_by_id(model_id)

    # Check model info and override the payload
    if model_info:
        if model_info.base_model_id:
            payload["model"] = model_info.base_model_id
            model_id = model_info.base_model_id

        params = model_info.params.model_dump()
        payload = apply_model_params_to_body_openai(params, payload)
        payload = apply_model_system_prompt_to_body(params, payload, metadata, user)

        # Check if user has access to the model
        if not bypass_filter and user.role == "user":
            if not (
                user.id == model_info.user_id
                or has_access(
                    user.id, type="read", access_control=model_info.access_control
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Model not found",
                )
    elif not bypass_filter:
        if user.role != "admin":
            if not await _is_user_visible_model(request, user, model_id):
                raise HTTPException(
                    status_code=403,
                    detail="Model not found",
                )

    # Resolve connection config from the *connection owner* (defaults to the requester).
    connection_user = getattr(getattr(request, "state", None), "connection_user", None) or user
    base_urls, keys, cfgs = _get_openai_user_config(connection_user)
    if not base_urls:
        raise HTTPException(status_code=404, detail="No connections configured")

    idx, url, key, api_config = _resolve_openai_connection_by_model_id(model_id, base_urls, keys, cfgs)
    if not url:
        raise HTTPException(status_code=404, detail="Connection not found")

    prefix_id = api_config.get("prefix_id", None)
    if prefix_id and isinstance(payload.get("model"), str):
        prefix = f"{prefix_id}."
        if payload["model"].startswith(prefix):
            payload["model"] = payload["model"][len(prefix) :]

    url = (url or "").rstrip("/")
    key = key or ""

    # Local-only flags (do not forward as-is).
    native_web_search = payload.pop("native_web_search", False) is True
    native_file_inputs = payload.pop("native_file_inputs", False) is True

    # Responses API routing is strict: if enabled, we only call /responses and surface real errors.
    use_responses_api = _should_use_responses_api(
        url,
        api_config,
        model_id,
        native_web_search=native_web_search,
        native_file_inputs=native_file_inputs,
    )

    request_url = (
        f"{url}/responses"
        if use_responses_api
        else _get_openai_chat_completions_url(url, api_config)
    )

    # Build headers (supports per-connection custom headers).
    headers = _build_upstream_headers(url, key, api_config, user=user)

    payload_dict = None
    auto_reasoning_summary_applied = False
    if use_responses_api:
        web_search_tool_type = _get_native_web_search_tool_type(api_config) if native_web_search else None
        default_reasoning_summary = _get_default_responses_reasoning_summary(
            api_config
        )
        payload_dict = convert_chat_completions_to_responses_payload(
            payload,
            native_web_search_tool_type=web_search_tool_type,
            default_reasoning_summary=default_reasoning_summary,
        )
        auto_reasoning_summary_applied = bool(
            isinstance(payload_dict.get("reasoning"), dict)
            and payload_dict["reasoning"].get("summary")
            and not _has_explicit_reasoning_summary_setting(payload)
        )

        # Responses API: keep to standard fields; conversion already filters most chat-only fields.
    else:
        # Chat Completions compatibility adjustments.
        # Fix: o1,o3 do not support the "max_tokens" parameter; use max_completion_tokens.
        is_o1_o3 = payload["model"].lower().startswith(("o1", "o3-"))
        if is_o1_o3:
            payload = openai_o1_o3_handler(payload)
        elif "api.openai.com" not in url:
            # Remove "max_completion_tokens" from the payload for backward compatibility.
            if "max_completion_tokens" in payload:
                payload["max_tokens"] = payload["max_completion_tokens"]
                del payload["max_completion_tokens"]

        if "max_tokens" in payload and "max_completion_tokens" in payload:
            del payload["max_tokens"]

        if "logit_bias" in payload:
            payload["logit_bias"] = json.loads(
                convert_logit_bias_input_to_json(payload["logit_bias"])
            )

        payload_dict = payload

    payload_dict = merge_additive_payload_fields(
        payload_dict,
        custom_params,
        forbidden_keys=_CUSTOM_PARAM_FORBIDDEN_KEYS,
    )

    request_attempts = (
        [(request_url, payload_dict)]
        if use_responses_api
        else _build_chat_completion_request_attempts(
            url=url,
            api_config=api_config,
            model_id=payload_dict.get("model") if isinstance(payload_dict, dict) else model_id,
            payload_dict=payload_dict,
        )
    )
    attempt_idx = 0
    request_url, payload_dict = request_attempts[attempt_idx]
    payload_json = json.dumps(payload_dict, ensure_ascii=False, default=str)

    # ── Diagnostic logging: key info at INFO, details at DEBUG ──
    _diag_keys = sorted(payload_dict.keys()) if isinstance(payload_dict, dict) else "N/A"
    _msg_count = len(payload_dict.get("messages", [])) if isinstance(payload_dict, dict) else "?"
    _tools_count = len(payload_dict.get("tools", [])) if isinstance(payload_dict, dict) and payload_dict.get("tools") else 0
    _reasoning_info = payload_dict.get("reasoning") if isinstance(payload_dict, dict) else None
    _store_info = payload_dict.get("store") if isinstance(payload_dict, dict) else None
    _include_info = payload_dict.get("include") if isinstance(payload_dict, dict) else None
    log.info(
        "[UPSTREAM REQUEST] POST %s | model=%s | payload_keys=%s | messages=%s | tools=%s | size=%d | reasoning=%s | store=%s | include=%s | native_file_inputs=%s | responses=%s",
        request_url,
        payload_dict.get("model", "?") if isinstance(payload_dict, dict) else "?",
        _diag_keys,
        _msg_count,
        _tools_count,
        len(payload_json),
        _reasoning_info or "none",
        _store_info,
        _include_info,
        native_file_inputs,
        use_responses_api,
    )
    if log.isEnabledFor(logging.DEBUG):
        _diag_headers = {
            k: ("***" if k.lower() in ("authorization", "api-key") else v)
            for k, v in headers.items()
        }
        log.debug("[UPSTREAM REQUEST] headers=%s", _diag_headers)
        # Log full payload (truncate messages content to keep log readable)
        if isinstance(payload_dict, dict):
            _diag_payload = {**payload_dict}
            if "messages" in _diag_payload:
                _diag_msgs = []
                for m in _diag_payload["messages"]:
                    _dm = {**m} if isinstance(m, dict) else m
                    if isinstance(_dm, dict) and isinstance(_dm.get("content"), str) and len(_dm["content"]) > 200:
                        _dm["content"] = _dm["content"][:200] + f"...[truncated, total {len(m['content'])} chars]"
                    _diag_msgs.append(_dm)
                _diag_payload["messages"] = _diag_msgs
            if "tools" in _diag_payload and _diag_payload["tools"]:
                _diag_payload["tools"] = f"[{len(_diag_payload['tools'])} tools, omitted]"
            log.debug("[UPSTREAM REQUEST] payload=%s", json.dumps(_diag_payload, ensure_ascii=False, default=str)[:4000])

    r = None
    session = None
    streaming = False
    response = None

    try:
        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )

        async def _send_current_request(*, retry_reason: Optional[str] = None):
            nonlocal r, payload_json

            if retry_reason:
                log.warning(
                    "[UPSTREAM RETRY] reason=%s | url=%s | model=%s",
                    retry_reason,
                    request_url,
                    payload_dict.get("model", "?") if isinstance(payload_dict, dict) else "?",
                )

            r = await session.request(
                method="POST",
                url=request_url,
                data=payload_json,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            )

            # ── Log upstream response metadata ──
            log.info(
                "[UPSTREAM RESPONSE%s] status=%d | content-type=%s",
                " RETRY" if retry_reason else "",
                r.status,
                r.headers.get("Content-Type", ""),
            )
            if log.isEnabledFor(logging.DEBUG):
                log.debug(
                    "[UPSTREAM RESPONSE%s] headers=%s",
                    " RETRY" if retry_reason else "",
                    {
                        k: v
                        for k, v in r.headers.items()
                        if k.lower() not in ("set-cookie",)
                    },
                )

        await _send_current_request()

        # Chat Completions: normalize non-2xx upstreams into truthful errors
        # instead of letting downstream guess from an empty stream.
        if not use_responses_api and r.status >= 400:
            response = await _safe_read_upstream_body(r)
            if (
                attempt_idx + 1 < len(request_attempts)
                and _looks_like_chat_completions_endpoint_unsupported(r.status, response)
            ):
                attempt_idx += 1
                request_url, payload_dict = request_attempts[attempt_idx]
                payload_json = json.dumps(payload_dict, ensure_ascii=False, default=str)
                response = None
                r.close()
                await _send_current_request(retry_reason="azure_chat_completions_fallback")
                if r.status >= 400:
                    response = await _safe_read_upstream_body(r)

            error_message = _get_chat_upstream_error_message(
                status=r.status, body=response
            )
            client_requested_stream = (
                bool(payload_dict.get("stream"))
                if isinstance(payload_dict, dict)
                else False
            )
            log.warning(
                "[UPSTREAM ERROR] status=%d | url=%s | body=%s",
                r.status,
                request_url,
                error_message[:2000],
            )

            if client_requested_stream:
                streaming = True
                return StreamingResponse(
                    error_sse_generator(
                        error_message,
                        code=f"http_{r.status}",
                    ),
                    media_type="text/event-stream",
                    status_code=200,
                    background=BackgroundTask(
                        cleanup_response, response=r, session=session
                    ),
                )

            raise HTTPException(
                status_code=r.status,
                detail=_extract_upstream_error_detail(r.status, response),
            )

        # Responses API handling (strict mode).
        if use_responses_api:
            client_stream = bool(payload_dict.get("stream"))
            content_type = r.headers.get("Content-Type", "") or ""
            looks_streaming = any(
                t in content_type.lower()
                for t in ("text/event-stream", "application/x-ndjson", "application/ndjson", "application/jsonl")
            )

            if r.status >= 400:
                response = await _safe_read_upstream_body(r)

                if (
                    auto_reasoning_summary_applied
                    and _looks_like_reasoning_summary_incompatible(r.status, response)
                ):
                    next_payload_dict, removed = _strip_reasoning_summary_from_payload(
                        payload_dict
                    )
                    if removed:
                        log.warning(
                            "[RESPONSES RETRY] reasoning.summary rejected by upstream; retrying once without summary"
                        )
                        payload_dict = next_payload_dict
                        payload_json = json.dumps(
                            payload_dict, ensure_ascii=False, default=str
                        )
                        response = None
                        auto_reasoning_summary_applied = False
                        r.close()
                        await _send_current_request(
                            retry_reason="reasoning_summary_incompatible"
                        )

                        if r.status < 400:
                            content_type = r.headers.get("Content-Type", "") or ""
                            looks_streaming = any(
                                t in content_type.lower()
                                for t in (
                                    "text/event-stream",
                                    "application/x-ndjson",
                                    "application/ndjson",
                                    "application/jsonl",
                                )
                            )
                        else:
                            response = await _safe_read_upstream_body(r)

                if r.status >= 400:
                    message = _format_responses_upstream_error(
                        request_url=request_url, status=r.status, body=response
                    )
                    if native_file_inputs:
                        message = (
                            "Native file inputs request failed. "
                            "The upstream rejected the OpenAI Files/Responses "
                            f"native-file flow.\n{message}"
                        )

                    if client_stream:
                        streaming = True
                        return StreamingResponse(
                            error_sse_generator(message, code="responses_api_error"),
                            media_type="text/event-stream",
                            status_code=200,
                            background=BackgroundTask(
                                cleanup_response, response=r, session=session
                            ),
                        )

                    raise HTTPException(status_code=r.status, detail=message)

            # Stream conversion: upstream Responses events -> ChatCompletions SSE for frontend.
            if client_stream and looks_streaming:
                streaming = True
                events = iter_responses_events(
                    r.content.iter_any(), content_type=content_type
                )
                sse_iter = responses_events_to_chat_completions_sse(
                    events, model_id=model_id
                )
                return StreamingResponse(
                    sse_iter,
                    media_type="text/event-stream",
                    status_code=200,
                    background=BackgroundTask(
                        cleanup_response, response=r, session=session
                    ),
                )

            # If the client asked for streaming but upstream returned non-stream JSON,
            # convert to a minimal ChatCompletions SSE (single-shot). This is NOT an endpoint fallback.
            response = await _safe_read_upstream_body(r)
            if not isinstance(response, dict):
                raise HTTPException(
                    status_code=502,
                    detail=_format_responses_upstream_error(
                        request_url=request_url, status=502, body=response
                    ),
                )

            cc = convert_responses_to_chat_completions(response, model_id=model_id)
            if client_stream:
                streaming = True
                stream_id = cc.get("id") or f"chatcmpl-{uuid.uuid4().hex}"
                created = cc.get("created") or int(time.time())

                async def one_shot_sse():
                    try:
                        choice0 = (cc.get("choices") or [{}])[0] if isinstance(cc.get("choices"), list) else {}
                        msg = choice0.get("message") or {}
                        content = msg.get("content") or ""
                        reasoning_content = msg.get("reasoning_content") or ""
                        tool_calls = msg.get("tool_calls")
                        delta = {}
                        if content:
                            delta["content"] = content
                        if reasoning_content:
                            delta["reasoning_content"] = reasoning_content
                        if tool_calls:
                            delta["tool_calls"] = tool_calls
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model_id,
                                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                                },
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "id": stream_id,
                                    "object": "chat.completion.chunk",
                                    "created": created,
                                    "model": model_id,
                                    "choices": [
                                        {
                                            "index": 0,
                                            "delta": {},
                                            "finish_reason": "tool_calls" if tool_calls else "stop",
                                        }
                                    ],
                                },
                                ensure_ascii=False,
                            )
                            + "\n\n"
                        )
                    finally:
                        yield "data: [DONE]\n\n"

                return StreamingResponse(
                    one_shot_sse(),
                    media_type="text/event-stream",
                    status_code=200,
                    background=BackgroundTask(
                        cleanup_response, response=r, session=session
                    ),
                )

            return cc

        # Chat Completions: passthrough SSE or JSON.
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True

            # Always wrap body iterator to log SSE lines for diagnosis.
            _orig_content = r.content

            async def _logging_sse_iterator():
                _line_count = 0
                _data_count = 0
                _first_data_line = None
                _last_data_line = None
                _last_finish_reason = None
                _last_usage = None
                _total_content_len = 0

                # Use iter_any() to avoid aiohttp's readline() buffer
                # limit which crashes on large SSE lines (e.g. base64
                # images from Gemini).  We accumulate a byte buffer and
                # split on b'\n' ourselves, yielding complete lines to
                # the downstream middleware.
                _buf = b""
                async for raw in _orig_content.iter_any():
                    _buf += raw
                    # Split on newline – may produce multiple lines per
                    # raw chunk, or none if the line is still incomplete.
                    while b"\n" in _buf:
                        line_bytes, _buf = _buf.split(b"\n", 1)
                        line_bytes += b"\n"  # restore the delimiter the downstream expects

                        _line_count += 1
                        _text = line_bytes.decode("utf-8", errors="replace")
                        _stripped = _text.strip()

                        # Log first 3 lines always
                        if _line_count <= 3:
                            log.info("[SSE RAW %d] %s", _line_count, _stripped[:500])

                        # Parse data lines for diagnostics
                        if _stripped.startswith("data:") and _stripped != "data: [DONE]":
                            _data_count += 1
                            _last_data_line = _stripped[:500]
                            if _first_data_line is None:
                                _first_data_line = _stripped[:500]
                            try:
                                _payload = json.loads(_stripped[5:].strip())
                                _choices = _payload.get("choices", [])
                                if _choices and isinstance(_choices[0], dict):
                                    _c0 = _choices[0]
                                    _fr = _c0.get("finish_reason")
                                    if _fr is not None:
                                        _last_finish_reason = _fr
                                    _delta = _c0.get("delta", {}) or {}
                                    _ct = _delta.get("content")
                                    if _ct:
                                        _total_content_len += len(_ct)
                                _u = _payload.get("usage")
                                if isinstance(_u, dict) and _u:
                                    _last_usage = _u
                            except Exception:
                                pass

                            if _data_count <= 3 or _data_count % 20 == 0:
                                log.info(
                                    "[SSE DATA #%d] finish_reason=%s content_so_far=%d chunk=%s",
                                    _data_count, _last_finish_reason, _total_content_len,
                                    _stripped[:200],
                                )
                            else:
                                log.debug("[SSE DATA #%d] %s", _data_count, _stripped[:300])

                        elif _stripped == "data: [DONE]":
                            log.info("[SSE] Received data: [DONE]")

                        yield line_bytes

                # Flush any remaining data without a trailing newline
                if _buf:
                    _line_count += 1
                    yield _buf

                # Final summary — always INFO
                log.info(
                    "[SSE DONE] total_lines=%d data_events=%d "
                    "finish_reason=%s content_len=%d usage=%s",
                    _line_count, _data_count,
                    _last_finish_reason, _total_content_len,
                    json.dumps(_last_usage, ensure_ascii=False)[:300] if _last_usage else "(none)",
                )
                if _last_data_line:
                    log.info("[SSE LAST DATA] %s", _last_data_line[:500])
                if _last_finish_reason and _last_finish_reason != "stop":
                    log.warning(
                        "[SSE ABNORMAL FINISH] finish_reason=%s — "
                        "response may be truncated! content_len=%d",
                        _last_finish_reason, _total_content_len,
                    )

            return StreamingResponse(
                _logging_sse_iterator(),
                status_code=r.status,
                headers=dict(r.headers),
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )

        # Non-streaming JSON response
        try:
            response = await r.json()
        except Exception as e:
            log.error(e)
            response = await r.text()

        if log.isEnabledFor(logging.DEBUG):
            _resp_preview = json.dumps(response, ensure_ascii=False, default=str)[:2000] if isinstance(response, dict) else str(response)[:2000]
            log.debug("[UPSTREAM RESPONSE BODY] %s", _resp_preview)

        r.raise_for_status()
        return response
    except HTTPException:
        raise
    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=r.status if r else 500,
            detail=build_error_detail(response, e),
        )
    finally:
        if not streaming and session:
            if r:
                r.close()
            await session.close()


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request, user=Depends(get_verified_user)):
    """
    Deprecated: proxy all requests to OpenAI API
    """

    body = await request.body()

    idx = 0
    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]

    r = None
    session = None
    streaming = False

    try:
        session = aiohttp.ClientSession(trust_env=True)
        r = await session.request(
            method=request.method,
            url=f"{url}/{path}",
            data=body,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS
                    else {}
                ),
            },
        )
        r.raise_for_status()

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                r.content,
                status_code=r.status,
                headers=dict(r.headers),
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )
        else:
            response_data = await r.json()
            return response_data

    except HTTPException:
        raise
    except Exception as e:
        log.exception(e)
        payload = await read_aiohttp_error_payload(r) if r is not None else None
        raise HTTPException(
            status_code=r.status if r else 500,
            detail=build_error_detail(payload, e),
        )
    finally:
        if not streaming and session:
            if r:
                r.close()
            await session.close()
