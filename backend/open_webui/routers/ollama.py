# TODO: Implement a more intelligent load balancing mechanism for distributing requests among multiple backend instances.
# Current implementation uses a simple round-robin approach (random.choice). Consider incorporating algorithms like weighted round-robin,
# least connections, or least response time for better resource utilization and performance optimization.

import asyncio
import json
import logging
import os
import random
import re
import secrets
import time
from typing import Optional, Union
from urllib.parse import urlparse
import aiohttp
import requests
from open_webui.models.users import UserModel

from open_webui.env import (
    ENABLE_FORWARD_USER_INFO_HEADERS,
)

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, validator
from starlette.background import BackgroundTask


from open_webui.models.models import Models
from open_webui.utils.misc import (
    calculate_sha256,
)
from open_webui.utils.error_handling import (
    build_error_detail,
    read_aiohttp_error_payload,
    read_requests_error_payload,
)
from open_webui.utils.payload import (
    apply_model_params_to_body_ollama,
    apply_model_params_to_body_openai,
    apply_model_system_prompt_to_body,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_access
from open_webui.utils.user_connections import (
    get_user_connections,
)
from open_webui.utils.model_identity import derive_connection_id, parse_selection_id


from open_webui.config import (
    UPLOAD_DIR,
)
from open_webui.env import (
    ENV,
    SRC_LOG_LEVELS,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    BYPASS_MODEL_ACCESS_CONTROL,
)
from open_webui.constants import ERROR_MESSAGES

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OLLAMA"])


##########################################
#
# Utility functions
#
##########################################


async def send_get_request(url, key=None, user: UserModel = None):
    timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.get(
                url,
                headers={
                    "Content-Type": "application/json",
                    **({"Authorization": f"Bearer {key}"} if key else {}),
                    **(
                        {
                            "X-OpenWebUI-User-Name": user.name,
                            "X-OpenWebUI-User-Id": user.id,
                            "X-OpenWebUI-User-Email": user.email,
                            "X-OpenWebUI-User-Role": user.role,
                        }
                        if ENABLE_FORWARD_USER_INFO_HEADERS and user
                        else {}
                    ),
                },
            ) as response:
                return await response.json()
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


def _format_ollama_error_detail(payload=None, error=None) -> str:
    return build_error_detail(payload, error, prefix="Ollama")


async def _read_ollama_aiohttp_error_detail(response=None, error=None) -> str:
    payload = await read_aiohttp_error_payload(response) if response is not None else None
    return _format_ollama_error_detail(payload, error)


def _read_ollama_requests_error_detail(response=None, error=None) -> str:
    payload = read_requests_error_payload(response)
    return _format_ollama_error_detail(payload, error)


async def send_post_request(
    url: str,
    payload: Union[str, bytes],
    stream: bool = True,
    key: Optional[str] = None,
    content_type: Optional[str] = None,
    user: UserModel = None,
):

    r = None
    try:
        session = aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        )

        r = await session.post(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
        )
        r.raise_for_status()

        if stream:
            response_headers = dict(r.headers)

            if content_type:
                response_headers["Content-Type"] = content_type

            return StreamingResponse(
                r.content,
                status_code=r.status,
                headers=response_headers,
                background=BackgroundTask(
                    cleanup_response, response=r, session=session
                ),
            )
        else:
            res = await r.json()
            await cleanup_response(r, session)
            return res

    except Exception as e:
        raise HTTPException(
            status_code=r.status if r else 500,
            detail=await _read_ollama_aiohttp_error_detail(r, e),
        )


def get_api_key(idx, url, configs):
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    return configs.get(str(idx), configs.get(base_url, {})).get(
        "key", None
    )  # Legacy support


##########################################
#
# API routes
#
##########################################

router = APIRouter()


def _get_ollama_user_config(connection_user: Optional[UserModel]) -> tuple[list[str], dict]:
    """
    Resolve Ollama connection config for a given user.

    Stored under user.settings.ui.connections.ollama:
      - OLLAMA_BASE_URLS
      - OLLAMA_API_CONFIGS
    """
    conns = get_user_connections(connection_user)
    cfg = conns.get("ollama") if isinstance(conns, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}

    base_urls = list(cfg.get("OLLAMA_BASE_URLS") or [])
    configs = cfg.get("OLLAMA_API_CONFIGS") or {}
    configs = configs if isinstance(configs, dict) else {}
    return base_urls, configs


def _resolve_ollama_connection_by_model_id(
    connection_user: Optional[UserModel],
    model_id: str,
    url_idx: Optional[int] = None,
) -> tuple[int, str, dict]:
    """
    Pick an Ollama connection based on url_idx or a `prefix_id` embedded in model_id.

    Returns: (chosen_idx, base_url, api_config)
    """
    base_urls, cfgs = _get_ollama_user_config(connection_user)
    if not base_urls:
        return 0, "", {}

    parsed_selection = parse_selection_id(model_id)
    if parsed_selection and parsed_selection.get("provider") == "ollama":
        model_ref = parsed_selection.get("model_ref") or {}
        ref_index = model_ref.get("connection_index")
        ref_connection_id = str(model_ref.get("connection_id") or "").strip()
        if (
            not ref_connection_id
            and ref_index is not None
            and str(ref_index).strip() != ""
            and len([url for url in base_urls if str(url or "").strip()]) > 1
        ):
            raise HTTPException(status_code=400, detail="模型连接不明确，请重新选择模型。")
        for idx, url in enumerate(base_urls):
            cfg = cfgs.get(str(idx), cfgs.get(url, {})) or {}
            cfg_prefix = str(cfg.get("prefix_id") or "").strip()
            if (
                (ref_connection_id and cfg_prefix == ref_connection_id)
                or (
                    ref_index is not None
                    and str(ref_index).strip() == str(idx)
                )
            ):
                api_config = {
                    **(cfg or {}),
                    "_resolved_prefix_id": cfg_prefix,
                    "_resolved_model_id": parsed_selection["model_id"],
                }
                return idx, base_urls[idx].rstrip("/"), api_config
        raise HTTPException(status_code=400, detail="模型连接已失效，请重新选择模型。")

    if url_idx is not None:
        if url_idx < 0 or url_idx >= len(base_urls):
            raise HTTPException(status_code=404, detail="Connection not found")
        url = base_urls[url_idx].rstrip("/")
        api_config = cfgs.get(str(url_idx), cfgs.get(base_urls[url_idx], {})) or {}
        return url_idx, url, api_config

    chosen_idx = 0
    chosen_cfg = cfgs.get("0", cfgs.get(base_urls[0], {})) or {}
    chosen_prefix = (chosen_cfg.get("prefix_id") or "").strip() or None

    if isinstance(model_id, str) and "." in model_id and len(base_urls) > 1:
        maybe_prefix, _rest = model_id.split(".", 1)
        for idx, url in enumerate(base_urls):
            c = cfgs.get(str(idx), cfgs.get(url, {})) or {}
            p = (c.get("prefix_id") or "").strip() or None
            if p and p == maybe_prefix:
                chosen_idx = idx
                chosen_cfg = c
                chosen_prefix = p
                break

    url = base_urls[chosen_idx].rstrip("/")
    api_config = {**(chosen_cfg or {}), "_resolved_prefix_id": chosen_prefix or ""}
    return chosen_idx, url, api_config


@router.head("/")
@router.get("/")
async def get_status():
    return {"status": True}


class ConnectionVerificationForm(BaseModel):
    url: str
    key: Optional[str] = None


class HealthCheckForm(BaseModel):
    url: str
    key: Optional[str] = None
    config: Optional[dict] = None
    model: Optional[str] = None


@router.post("/verify")
async def verify_connection(
    form_data: ConnectionVerificationForm, user=Depends(get_verified_user)
):
    url = form_data.url
    key = form_data.key

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    ) as session:
        try:
            # Verify the instance is reachable and (when possible) also return the tag list.
            # The UI uses this endpoint to populate "Model Management" for Ollama connections.
            async with session.get(
                f"{url}/api/version",
                headers={
                    **({"Authorization": f"Bearer {key}"} if key else {}),
                    **(
                        {
                            "X-OpenWebUI-User-Name": user.name,
                            "X-OpenWebUI-User-Id": user.id,
                            "X-OpenWebUI-User-Email": user.email,
                            "X-OpenWebUI-User-Role": user.role,
                        }
                        if ENABLE_FORWARD_USER_INFO_HEADERS and user
                        else {}
                    ),
                },
            ) as r:
                if r.status != 200:
                    detail = f"HTTP Error: {r.status}"
                    res = await r.json()

                    if "error" in res:
                        detail = f"External Error: {res['error']}"
                    raise Exception(detail)

                version_data = await r.json()

            models: list = []
            try:
                async with session.get(
                    f"{url}/api/tags",
                    headers={
                        **({"Authorization": f"Bearer {key}"} if key else {}),
                        **(
                            {
                                "X-OpenWebUI-User-Name": user.name,
                                "X-OpenWebUI-User-Id": user.id,
                                "X-OpenWebUI-User-Email": user.email,
                                "X-OpenWebUI-User-Role": user.role,
                            }
                            if ENABLE_FORWARD_USER_INFO_HEADERS and user
                            else {}
                        ),
                    },
                ) as r:
                    if r.status == 200:
                        tags_data = await r.json()
                        if isinstance(tags_data, dict) and isinstance(
                            tags_data.get("models"), list
                        ):
                            models = tags_data["models"]
            except Exception:
                # Non-fatal: the connection is still valid if /api/version works.
                models = []

            # Keep the response stable for the frontend: always include "version" and "models".
            version = (
                version_data.get("version")
                if isinstance(version_data, dict)
                else version_data
            )
            return {"version": version, "models": models}
        except aiohttp.ClientError as e:
            log.exception(f"Client error: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=_format_ollama_error_detail(error=e),
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

    key = form_data.key
    api_config = form_data.config or {}
    chosen_model = form_data.model
    timeout = aiohttp.ClientTimeout(total=15)

    headers = {
        "Content-Type": "application/json",
        **({"Authorization": f"Bearer {key}"} if key else {}),
        **(
            {
                "X-OpenWebUI-User-Name": user.name,
                "X-OpenWebUI-User-Id": user.id,
                "X-OpenWebUI-User-Email": user.email,
                "X-OpenWebUI-User-Role": user.role,
            }
            if ENABLE_FORWARD_USER_INFO_HEADERS and user
            else {}
        ),
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            if not chosen_model:
                async with session.get(f"{url}/api/tags", headers=headers) as response:
                    body = await read_aiohttp_error_payload(response)
                    if response.status >= 400:
                        raise HTTPException(
                            status_code=response.status,
                            detail=_format_ollama_error_detail(body),
                        )

                    if isinstance(body, dict) and isinstance(body.get("models"), list):
                        for model in body["models"]:
                            if not isinstance(model, dict):
                                continue
                            chosen_model = model.get("model") or model.get("name")
                            if chosen_model:
                                break

            if not chosen_model:
                raise HTTPException(status_code=400, detail="Ollama: No compatible model found")

            chosen_model = str(chosen_model)
            resolved_prefix = (api_config.get("_resolved_prefix_id") or api_config.get("prefix_id") or "").strip() or None
            if resolved_prefix and chosen_model.startswith(f"{resolved_prefix}."):
                chosen_model = chosen_model[len(resolved_prefix) + 1 :]
            if ":" not in chosen_model and "/" not in chosen_model:
                chosen_model = f"{chosen_model}:latest"

            payload = {
                "model": chosen_model,
                "messages": [{"role": "user", "content": "Hi"}],
                "stream": False,
                "options": {"num_predict": 1},
            }

            started_at = time.monotonic()
            async with session.post(
                f"{url}/api/chat",
                data=json.dumps(payload),
                headers=headers,
            ) as response:
                try:
                    body = await response.json(content_type=None)
                except Exception:
                    body = await response.text()

                if response.status >= 400:
                    raise HTTPException(
                        status_code=response.status,
                        detail=_format_ollama_error_detail(body),
                    )

                if not isinstance(body, dict):
                    raise HTTPException(
                        status_code=502,
                        detail="Invalid response from Ollama model health check",
                    )

                return {
                    "ok": True,
                    "model": chosen_model,
                    "response_time_ms": max(
                        1, int((time.monotonic() - started_at) * 1000)
                    ),
                }
    except HTTPException:
        raise
    except aiohttp.ClientError as e:
        log.exception(f"Client error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=_format_ollama_error_detail(error=e),
        )
    except Exception as e:
        log.exception(f"Unexpected error: {e}")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_OLLAMA_API": request.app.state.config.ENABLE_OLLAMA_API,
        "OLLAMA_BASE_URLS": request.app.state.config.OLLAMA_BASE_URLS,
        "OLLAMA_API_CONFIGS": request.app.state.config.OLLAMA_API_CONFIGS,
    }


class OllamaConfigForm(BaseModel):
    ENABLE_OLLAMA_API: Optional[bool] = None
    OLLAMA_BASE_URLS: list[str]
    OLLAMA_API_CONFIGS: dict


@router.post("/config/update")
async def update_config(
    request: Request, form_data: OllamaConfigForm, user=Depends(get_admin_user)
):
    # Preserve existing per-URL prefix_id to avoid breaking chats when admins edit connections.
    # prefix_id is an internal stable identifier used for uniqueness/routing and should not be user-editable.
    prev_urls = list(getattr(request.app.state.config, "OLLAMA_BASE_URLS", []) or [])
    prev_cfgs = getattr(request.app.state.config, "OLLAMA_API_CONFIGS", {}) or {}
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

    request.app.state.config.ENABLE_OLLAMA_API = form_data.ENABLE_OLLAMA_API

    request.app.state.config.OLLAMA_BASE_URLS = form_data.OLLAMA_BASE_URLS
    request.app.state.config.OLLAMA_API_CONFIGS = form_data.OLLAMA_API_CONFIGS

    # Remove the API configs that are not in the API URLS
    keys = list(map(str, range(len(request.app.state.config.OLLAMA_BASE_URLS))))
    request.app.state.config.OLLAMA_API_CONFIGS = {
        key: value
        for key, value in request.app.state.config.OLLAMA_API_CONFIGS.items()
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
            url = request.app.state.config.OLLAMA_BASE_URLS[idx]
            url_key = (url or "").rstrip("/")
            if url_key and url_key in prev_empty_urls:
                preserved_empty_idx = idx
                break

    for idx_str in keys:
        idx = int(idx_str)
        url = request.app.state.config.OLLAMA_BASE_URLS[idx]
        cfg = request.app.state.config.OLLAMA_API_CONFIGS.get(idx_str, {}) or {}
        url_key = (url or "").rstrip("/")

        name = (cfg.get("name") or "").strip()
        if not name:
            try:
                name = urlparse(url).hostname or f"Connection {idx + 1}"
            except Exception:
                name = f"Connection {idx + 1}"

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
            normalized_cfg.pop("prefix_id", None)

        normalized_configs[idx_str] = normalized_cfg

    request.app.state.config.OLLAMA_API_CONFIGS = normalized_configs

    # Refresh model list cache when config changes
    from open_webui.utils.models import invalidate_base_model_cache

    request.app.state.BASE_MODELS = None
    request.app.state.OLLAMA_MODELS = {}
    request.app.state.MODELS = {}
    invalidate_base_model_cache(user.id)

    return {
        "ENABLE_OLLAMA_API": request.app.state.config.ENABLE_OLLAMA_API,
        "OLLAMA_BASE_URLS": request.app.state.config.OLLAMA_BASE_URLS,
        "OLLAMA_API_CONFIGS": request.app.state.config.OLLAMA_API_CONFIGS,
    }


async def get_all_models(request: Request, user: UserModel = None):
    log.info("get_all_models()")
    base_urls, cfgs = _get_ollama_user_config(user)
    if not base_urls:
        request.state.OLLAMA_MODELS = {}
        return {"models": []}

    # If multiple Ollama connections exist, derive a stable internal prefix_id in memory.
    # Read paths must stay read-only so model discovery does not mutate user settings revisions.
    cfgs_changed = False
    if len(base_urls) > 1:
        used = set()

        preserved_empty_idx = None
        # Prefer an explicit empty marker set by user config.
        for idx, url in enumerate(base_urls):
            api_config = cfgs.get(str(idx), cfgs.get(url, {})) or {}
            if api_config.get("prefix_id", None) == "":
                preserved_empty_idx = idx
                break

        # Backward compatibility: for legacy configs that omitted prefix_id, preserve empty for index 0.
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
                    prefix_id = derive_connection_id(
                        provider="ollama",
                        source="personal",
                        url=url,
                        api_key=api_config.get("key", None),
                    )

            if prefix_id:
                if prefix_id in used:
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

    request_tasks = []
    for idx, url in enumerate(base_urls):
        api_config = cfgs.get(str(idx), cfgs.get(url, {})) or {}
        enable = api_config.get("enable", True)
        key = api_config.get("key", None)

        if enable:
            request_tasks.append(send_get_request(f"{url.rstrip('/')}/api/tags", key, user=user))
        else:
            request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, None)))

    responses = await asyncio.gather(*request_tasks, return_exceptions=True)

    # Degrade gracefully: a single bad connection (401/timeout/etc.) should not break the whole models list.
    for idx, resp in enumerate(responses):
        if isinstance(resp, BaseException):
            url = base_urls[idx] if idx < len(base_urls) else ""
            if isinstance(resp, HTTPException):
                log.warning(
                    f"[OLLAMA] models fetch failed (idx={idx}, url={url}) "
                    f"{resp.status_code}: {resp.detail}"
                )
            else:
                log.warning(
                    f"[OLLAMA] models fetch failed (idx={idx}, url={url}) {type(resp).__name__}: {resp}"
                )
            responses[idx] = None

    for idx, response in enumerate(responses):
        if not response or not isinstance(response, dict):
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
        model_ids = api_config.get("model_ids", []) or []

        # Users sometimes copy the internal prefixed model id back into the allowlist.
        # Treat allowlisted entries as "original ids" and strip this connection's prefix if present.
        if prefix_id and model_ids:
            prefix = f"{prefix_id}."
            model_ids = [
                (model_id[len(prefix) :] if isinstance(model_id, str) and model_id.startswith(prefix) else model_id)
                for model_id in model_ids
            ]

        if model_ids and "models" in response:
            response["models"] = [m for m in (response.get("models", []) or []) if isinstance(m, dict) and (m.get("model") in model_ids or m.get("name") in model_ids)]

        for model in response.get("models", []) or []:
            if not isinstance(model, dict):
                continue

            original_model = model.get("model") or model.get("name") or ""
            display_name = model.get("name") or original_model

            if prefix_id:
                prefix = f"{prefix_id}."
                if isinstance(original_model, str) and original_model.startswith(prefix):
                    original_model = original_model[len(prefix) :]
                if isinstance(display_name, str) and display_name.startswith(prefix):
                    display_name = display_name[len(prefix) :]

            if connection_name:
                model["connection_name"] = connection_name
            if connection_icon:
                model["connection_icon"] = connection_icon
            model["name"] = display_name

            if prefix_id:
                model["original_model"] = original_model
                model["model"] = f"{prefix_id}.{original_model}"

            if tags:
                model["tags"] = tags

    def merge_models_lists(model_lists):
        merged_models = {}

        for idx, model_list in enumerate(model_lists):
            if model_list is not None:
                for model in model_list:
                    mid = model.get("model")
                    if not mid:
                        continue
                    if mid not in merged_models:
                        model["urls"] = [idx]
                        merged_models[mid] = model
                    else:
                        merged_models[mid]["urls"].append(idx)

        return list(merged_models.values())

    models = {
        "models": merge_models_lists(
            map(lambda r: r.get("models", []) if isinstance(r, dict) else None, responses)
        )
    }

    request.state.OLLAMA_MODELS = {m.get("model"): m for m in models["models"] if isinstance(m, dict) and m.get("model")}
    return models


async def get_filtered_models(models, user):
    # Filter models based on user access control
    filtered_models = []
    for model in models.get("models", []):
        model_info = Models.get_model_by_id(model["model"])
        if model_info:
            if user.id == model_info.user_id or has_access(
                user.id, type="read", access_control=model_info.access_control
            ):
                filtered_models.append(model)
    return filtered_models


@router.get("/api/tags")
@router.get("/api/tags/{url_idx}")
async def get_ollama_tags(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    models = []

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        base_urls, cfgs = _get_ollama_user_config(user)
        if url_idx < 0 or url_idx >= len(base_urls):
            raise HTTPException(status_code=404, detail="Connection not found")
        url = base_urls[url_idx]
        key = get_api_key(url_idx, url, cfgs)

        r = None
        try:
            r = requests.request(
                method="GET",
                url=f"{url}/api/tags",
                headers={
                    **({"Authorization": f"Bearer {key}"} if key else {}),
                    **(
                        {
                            "X-OpenWebUI-User-Name": user.name,
                            "X-OpenWebUI-User-Id": user.id,
                            "X-OpenWebUI-User-Email": user.email,
                            "X-OpenWebUI-User-Role": user.role,
                        }
                        if ENABLE_FORWARD_USER_INFO_HEADERS and user
                        else {}
                    ),
                },
            )
            r.raise_for_status()

            models = r.json()
        except Exception as e:
            log.exception(e)

            detail = None
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        detail = f"Ollama: {res['error']}"
                except Exception:
                    detail = f"Ollama: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=_read_ollama_requests_error_detail(r, e),
            )

    if user.role == "user" and not BYPASS_MODEL_ACCESS_CONTROL:
        models["models"] = await get_filtered_models(models, user)

    return models


@router.get("/api/version")
@router.get("/api/version/{url_idx}")
async def get_ollama_versions(request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)):
    if request.app.state.config.ENABLE_OLLAMA_API:
        base_urls, cfgs = _get_ollama_user_config(user)
        if not base_urls:
            return {"version": False}

        if url_idx is None:
            # returns lowest version
            request_tasks = []

            for idx, url in enumerate(base_urls):
                api_config = cfgs.get(
                    str(idx),
                    cfgs.get(url, {}),
                ) or {}

                enable = api_config.get("enable", True)
                key = api_config.get("key", None)

                if enable:
                    request_tasks.append(
                        send_get_request(
                            f"{url}/api/version",
                            key,
                        )
                    )

            responses = await asyncio.gather(*request_tasks)
            responses = list(filter(lambda x: x is not None, responses))

            if len(responses) > 0:
                lowest_version = min(
                    responses,
                    key=lambda x: tuple(
                        map(int, re.sub(r"^v|-.*", "", x["version"]).split("."))
                    ),
                )

                return {"version": lowest_version["version"]}
            else:
                return {"version": False}
        else:
            if url_idx < 0 or url_idx >= len(base_urls):
                raise HTTPException(status_code=404, detail="Connection not found")
            url = base_urls[url_idx]
            api_config = cfgs.get(str(url_idx), cfgs.get(url, {})) or {}
            key = api_config.get("key", None)

            r = None
            try:
                r = requests.request(method="GET", url=f"{url}/api/version")
                r.raise_for_status()

                return r.json()
            except Exception as e:
                log.exception(e)

                detail = None
                if r is not None:
                    try:
                        res = r.json()
                        if "error" in res:
                            detail = f"Ollama: {res['error']}"
                    except Exception:
                        detail = f"Ollama: {e}"

                raise HTTPException(
                    status_code=r.status_code if r else 500,
                    detail=_read_ollama_requests_error_detail(r, e),
                )
    else:
        return {"version": False}


@router.get("/api/ps")
async def get_ollama_loaded_models(request: Request, user=Depends(get_verified_user)):
    """
    List models that are currently loaded into Ollama memory, and which node they are loaded on.
    """
    if request.app.state.config.ENABLE_OLLAMA_API:
        request_tasks = [
            send_get_request(
                f"{url}/api/ps",
                request.app.state.config.OLLAMA_API_CONFIGS.get(
                    str(idx),
                    request.app.state.config.OLLAMA_API_CONFIGS.get(
                        url, {}
                    ),  # Legacy support
                ).get("key", None),
                user=user,
            )
            for idx, url in enumerate(request.app.state.config.OLLAMA_BASE_URLS)
        ]
        responses = await asyncio.gather(*request_tasks)

        return dict(zip(request.app.state.config.OLLAMA_BASE_URLS, responses))
    else:
        return {}


class ModelNameForm(BaseModel):
    name: str


@router.post("/api/pull")
@router.post("/api/pull/{url_idx}")
async def pull_model(
    request: Request,
    form_data: ModelNameForm,
    url_idx: int = 0,
    user=Depends(get_admin_user),
):
    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    log.info(f"url: {url}")

    # Admin should be able to pull models from any source
    payload = {**form_data.model_dump(exclude_none=True), "insecure": True}

    return await send_post_request(
        url=f"{url}/api/pull",
        payload=json.dumps(payload),
        key=get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS),
        user=user,
    )


class PushModelForm(BaseModel):
    name: str
    insecure: Optional[bool] = None
    stream: Optional[bool] = None


@router.delete("/api/push")
@router.delete("/api/push/{url_idx}")
async def push_model(
    request: Request,
    form_data: PushModelForm,
    url_idx: Optional[int] = None,
    user=Depends(get_admin_user),
):
    if url_idx is None:
        await get_all_models(request, user=user)
        models = request.app.state.OLLAMA_MODELS

        if form_data.name in models:
            url_idx = models[form_data.name]["urls"][0]
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.name),
            )

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    log.debug(f"url: {url}")

    return await send_post_request(
        url=f"{url}/api/push",
        payload=form_data.model_dump_json(exclude_none=True).encode(),
        key=get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS),
        user=user,
    )


class CreateModelForm(BaseModel):
    model: Optional[str] = None
    stream: Optional[bool] = None
    path: Optional[str] = None

    model_config = ConfigDict(extra="allow")


@router.post("/api/create")
@router.post("/api/create/{url_idx}")
async def create_model(
    request: Request,
    form_data: CreateModelForm,
    url_idx: int = 0,
    user=Depends(get_admin_user),
):
    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "create_model summary model=%s stream=%s path=%s",
            form_data.model,
            form_data.stream,
            form_data.path,
        )
    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]

    return await send_post_request(
        url=f"{url}/api/create",
        payload=form_data.model_dump_json(exclude_none=True).encode(),
        key=get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS),
        user=user,
    )


class CopyModelForm(BaseModel):
    source: str
    destination: str


@router.post("/api/copy")
@router.post("/api/copy/{url_idx}")
async def copy_model(
    request: Request,
    form_data: CopyModelForm,
    url_idx: Optional[int] = None,
    user=Depends(get_admin_user),
):
    if url_idx is None:
        await get_all_models(request, user=user)
        models = request.app.state.OLLAMA_MODELS

        if form_data.source in models:
            url_idx = models[form_data.source]["urls"][0]
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.source),
            )

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)

    try:
        r = requests.request(
            method="POST",
            url=f"{url}/api/copy",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
            data=form_data.model_dump_json(exclude_none=True).encode(),
        )
        r.raise_for_status()

        log.debug(f"r.text: {r.text}")
        return True
    except Exception as e:
        log.exception(e)

        detail = None
        if r is not None:
            try:
                res = r.json()
                if "error" in res:
                    detail = f"Ollama: {res['error']}"
            except Exception:
                detail = f"Ollama: {e}"

        raise HTTPException(
            status_code=r.status_code if r else 500,
            detail=_read_ollama_requests_error_detail(r, e),
        )


@router.delete("/api/delete")
@router.delete("/api/delete/{url_idx}")
async def delete_model(
    request: Request,
    form_data: ModelNameForm,
    url_idx: Optional[int] = None,
    user=Depends(get_admin_user),
):
    if url_idx is None:
        await get_all_models(request, user=user)
        models = request.app.state.OLLAMA_MODELS

        if form_data.name in models:
            url_idx = models[form_data.name]["urls"][0]
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.name),
            )

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)

    try:
        r = requests.request(
            method="DELETE",
            url=f"{url}/api/delete",
            data=form_data.model_dump_json(exclude_none=True).encode(),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
        )
        r.raise_for_status()

        log.debug(f"r.text: {r.text}")
        return True
    except Exception as e:
        log.exception(e)

        detail = None
        if r is not None:
            try:
                res = r.json()
                if "error" in res:
                    detail = f"Ollama: {res['error']}"
            except Exception:
                detail = f"Ollama: {e}"

        raise HTTPException(
            status_code=r.status_code if r else 500,
            detail=_read_ollama_requests_error_detail(r, e),
        )


@router.post("/api/show")
async def show_model_info(
    request: Request, form_data: ModelNameForm, user=Depends(get_verified_user)
):
    await get_all_models(request, user=user)
    models = request.app.state.OLLAMA_MODELS

    if form_data.name not in models:
        raise HTTPException(
            status_code=400,
            detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.name),
        )

    url_idx = random.choice(models[form_data.name]["urls"])

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)

    try:
        r = requests.request(
            method="POST",
            url=f"{url}/api/show",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
            data=form_data.model_dump_json(exclude_none=True).encode(),
        )
        r.raise_for_status()

        return r.json()
    except Exception as e:
        log.exception(e)

        detail = None
        if r is not None:
            try:
                res = r.json()
                if "error" in res:
                    detail = f"Ollama: {res['error']}"
            except Exception:
                detail = f"Ollama: {e}"

        raise HTTPException(
            status_code=r.status_code if r else 500,
            detail=_read_ollama_requests_error_detail(r, e),
        )


class GenerateEmbedForm(BaseModel):
    model: str
    input: list[str] | str
    truncate: Optional[bool] = None
    options: Optional[dict] = None
    keep_alive: Optional[Union[int, str]] = None


@router.post("/api/embed")
@router.post("/api/embed/{url_idx}")
async def embed(
    request: Request,
    form_data: GenerateEmbedForm,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
):
    log.info(f"generate_ollama_batch_embeddings {form_data}")

    if url_idx is None:
        await get_all_models(request, user=user)
        models = request.app.state.OLLAMA_MODELS

        model = form_data.model

        if ":" not in model and "/" not in model:
            model = f"{model}:latest"

        if model in models:
            url_idx = random.choice(models[model]["urls"])
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.model),
            )

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)

    try:
        r = requests.request(
            method="POST",
            url=f"{url}/api/embed",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
            data=form_data.model_dump_json(exclude_none=True).encode(),
        )
        r.raise_for_status()

        data = r.json()
        return data
    except Exception as e:
        log.exception(e)

        detail = None
        if r is not None:
            try:
                res = r.json()
                if "error" in res:
                    detail = f"Ollama: {res['error']}"
            except Exception:
                detail = f"Ollama: {e}"

        raise HTTPException(
            status_code=r.status_code if r else 500,
            detail=_read_ollama_requests_error_detail(r, e),
        )


class GenerateEmbeddingsForm(BaseModel):
    model: str
    prompt: str
    options: Optional[dict] = None
    keep_alive: Optional[Union[int, str]] = None


@router.post("/api/embeddings")
@router.post("/api/embeddings/{url_idx}")
async def embeddings(
    request: Request,
    form_data: GenerateEmbeddingsForm,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
):
    log.info(f"generate_ollama_embeddings {form_data}")

    if url_idx is None:
        await get_all_models(request, user=user)
        models = request.app.state.OLLAMA_MODELS

        model = form_data.model

        if ":" not in model and "/" not in model:
            model = f"{model}:latest"

        if model in models:
            url_idx = random.choice(models[model]["urls"])
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.model),
            )

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    key = get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS)

    try:
        r = requests.request(
            method="POST",
            url=f"{url}/api/embeddings",
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {key}"} if key else {}),
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
            data=form_data.model_dump_json(exclude_none=True).encode(),
        )
        r.raise_for_status()

        data = r.json()
        return data
    except Exception as e:
        log.exception(e)

        detail = None
        if r is not None:
            try:
                res = r.json()
                if "error" in res:
                    detail = f"Ollama: {res['error']}"
            except Exception:
                detail = f"Ollama: {e}"

        raise HTTPException(
            status_code=r.status_code if r else 500,
            detail=_read_ollama_requests_error_detail(r, e),
        )


class GenerateCompletionForm(BaseModel):
    model: str
    # keep_alive declared before prompt so the validator can read it from `values`
    keep_alive: Optional[Union[int, str]] = None
    prompt: Optional[str] = None
    suffix: Optional[str] = None
    images: Optional[list[str]] = None
    format: Optional[str] = None
    options: Optional[dict] = None
    system: Optional[str] = None
    template: Optional[str] = None
    context: Optional[list[int]] = None
    stream: Optional[bool] = True
    raw: Optional[bool] = None

    @validator("prompt", always=True)
    @classmethod
    def prompt_required_unless_unload(cls, v, values):
        """Allow prompt to be omitted only when keep_alive=0 (model unload)."""
        keep_alive = values.get("keep_alive")
        if v is None and keep_alive not in (0, "0"):
            raise ValueError("prompt is required (unless keep_alive=0 for model unload)")
        return v


@router.post("/api/generate")
@router.post("/api/generate/{url_idx}")
async def generate_completion(
    request: Request,
    form_data: GenerateCompletionForm,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
):
    if url_idx is None:
        await get_all_models(request, user=user)
        models = request.app.state.OLLAMA_MODELS

        model = form_data.model

        if ":" not in model and "/" not in model:
            model = f"{model}:latest"

        if model in models:
            url_idx = random.choice(models[model]["urls"])
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(form_data.model),
            )

    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    api_config = request.app.state.config.OLLAMA_API_CONFIGS.get(
        str(url_idx),
        request.app.state.config.OLLAMA_API_CONFIGS.get(url, {}),  # Legacy support
    )

    prefix_id = api_config.get("prefix_id", None)
    if prefix_id and isinstance(getattr(form_data, "model", None), str):
        prefix = f"{prefix_id}."
        if form_data.model.startswith(prefix):
            form_data.model = form_data.model[len(prefix) :]

    return await send_post_request(
        url=f"{url}/api/generate",
        payload=form_data.model_dump_json(exclude_none=True).encode(),
        key=get_api_key(url_idx, url, request.app.state.config.OLLAMA_API_CONFIGS),
        user=user,
    )


class ChatMessage(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list[dict]] = None
    images: Optional[list[str]] = None

    @validator("content", pre=True)
    @classmethod
    def check_at_least_one_field(cls, field_value, values, **kwargs):
        # Raise an error if both 'content' and 'tool_calls' are None
        if field_value is None and (
            "tool_calls" not in values or values["tool_calls"] is None
        ):
            raise ValueError(
                "At least one of 'content' or 'tool_calls' must be provided"
            )

        return field_value


class GenerateChatCompletionForm(BaseModel):
    model: str
    messages: list[ChatMessage]
    format: Optional[Union[dict, str]] = None
    options: Optional[dict] = None
    template: Optional[str] = None
    stream: Optional[bool] = True
    keep_alive: Optional[Union[int, str]] = None
    tools: Optional[list[dict]] = None


async def get_ollama_url(request: Request, model: str, url_idx: Optional[int] = None):
    if url_idx is None:
        models = request.app.state.OLLAMA_MODELS
        if model not in models:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.MODEL_NOT_FOUND(model),
            )
        url_idx = random.choice(models[model].get("urls", []))
    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    return url, url_idx


@router.post("/api/chat")
@router.post("/api/chat/{url_idx}")
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
):
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    metadata = form_data.pop("metadata", None)
    try:
        form_data = GenerateChatCompletionForm(**form_data)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    payload = {**form_data.model_dump(exclude_none=True)}
    if "metadata" in payload:
        del payload["metadata"]

    model_id = payload["model"]
    model_info = Models.get_model_by_id(model_id)

    if model_info:
        if model_info.base_model_id:
            payload["model"] = model_info.base_model_id

        params = model_info.params.model_dump()

        if params:
            if payload.get("options") is None:
                payload["options"] = {}

            payload["options"] = apply_model_params_to_body_ollama(
                params, payload["options"]
            )
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
        # Base models coming from a user's own Ollama connections may not have a DB row.
        # Allow and let upstream validate the model id.
        pass

    if ":" not in payload["model"]:
        payload["model"] = f"{payload['model']}:latest"

    connection_user = getattr(request.state, "connection_user", None) or user
    chosen_idx, url, api_config = _resolve_ollama_connection_by_model_id(
        connection_user, payload.get("model", ""), url_idx
    )
    if api_config.get("_resolved_model_id"):
        payload["model"] = api_config["_resolved_model_id"]

    resolved_prefix = (api_config.get("_resolved_prefix_id") or api_config.get("prefix_id") or "").strip() or None
    if resolved_prefix and isinstance(payload.get("model"), str):
        prefix = f"{resolved_prefix}."
        if payload["model"].startswith(prefix):
            payload["model"] = payload["model"][len(prefix) :]
    if ":" not in payload["model"]:
        payload["model"] = f"{payload['model']}:latest"
    # payload["keep_alive"] = -1 # keep alive forever
    base_urls, cfgs = _get_ollama_user_config(connection_user)
    return await send_post_request(
        url=f"{url}/api/chat",
        payload=json.dumps(payload),
        stream=form_data.stream,
        key=get_api_key(chosen_idx, base_urls[chosen_idx] if chosen_idx < len(base_urls) else url, cfgs),
        content_type="application/x-ndjson",
        user=user,
    )


# TODO: we should update this part once Ollama supports other types
class OpenAIChatMessageContent(BaseModel):
    type: str
    model_config = ConfigDict(extra="allow")


class OpenAIChatMessage(BaseModel):
    role: str
    content: Union[Optional[str], list[OpenAIChatMessageContent]]

    model_config = ConfigDict(extra="allow")


class OpenAIChatCompletionForm(BaseModel):
    model: str
    messages: list[OpenAIChatMessage]

    model_config = ConfigDict(extra="allow")


class OpenAICompletionForm(BaseModel):
    model: str
    prompt: str

    model_config = ConfigDict(extra="allow")


@router.post("/v1/completions")
@router.post("/v1/completions/{url_idx}")
async def generate_openai_completion(
    request: Request,
    form_data: dict,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
):
    try:
        form_data = OpenAICompletionForm(**form_data)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    payload = {**form_data.model_dump(exclude_none=True, exclude=["metadata"])}
    if "metadata" in payload:
        del payload["metadata"]

    model_id = form_data.model
    if ":" not in model_id and "/" not in model_id:
        model_id = f"{model_id}:latest"

    model_info = Models.get_model_by_id(model_id)
    if model_info:
        if model_info.base_model_id:
            payload["model"] = model_info.base_model_id
        params = model_info.params.model_dump()

        if params:
            payload = apply_model_params_to_body_openai(params, payload)

        # Check if user has access to the model
        if user.role == "user":
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
    else:
        # Base models coming from a user's own Ollama connections may not have a DB row.
        # Allow and let upstream validate the model id.
        pass

    if ":" not in payload["model"]:
        payload["model"] = f"{payload['model']}:latest"

    connection_user = getattr(request.state, "connection_user", None) or user
    chosen_idx, url, api_config = _resolve_ollama_connection_by_model_id(
        connection_user, payload.get("model", ""), url_idx
    )
    if api_config.get("_resolved_model_id"):
        payload["model"] = api_config["_resolved_model_id"]

    resolved_prefix = (api_config.get("_resolved_prefix_id") or api_config.get("prefix_id") or "").strip() or None
    if resolved_prefix and isinstance(payload.get("model"), str):
        prefix = f"{resolved_prefix}."
        if payload["model"].startswith(prefix):
            payload["model"] = payload["model"][len(prefix) :]
    if ":" not in payload["model"]:
        payload["model"] = f"{payload['model']}:latest"

    base_urls, cfgs = _get_ollama_user_config(connection_user)
    return await send_post_request(
        url=f"{url}/v1/completions",
        payload=json.dumps(payload),
        stream=payload.get("stream", False),
        key=get_api_key(chosen_idx, base_urls[chosen_idx] if chosen_idx < len(base_urls) else url, cfgs),
        user=user,
    )


@router.post("/v1/chat/completions")
@router.post("/v1/chat/completions/{url_idx}")
async def generate_openai_chat_completion(
    request: Request,
    form_data: dict,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
):
    metadata = form_data.pop("metadata", None)

    try:
        completion_form = OpenAIChatCompletionForm(**form_data)
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )

    payload = {**completion_form.model_dump(exclude_none=True, exclude=["metadata"])}
    if "metadata" in payload:
        del payload["metadata"]

    model_id = completion_form.model
    if ":" not in model_id and "/" not in model_id:
        model_id = f"{model_id}:latest"

    model_info = Models.get_model_by_id(model_id)
    if model_info:
        if model_info.base_model_id:
            payload["model"] = model_info.base_model_id

        params = model_info.params.model_dump()

        if params:
            payload = apply_model_params_to_body_openai(params, payload)
            payload = apply_model_system_prompt_to_body(params, payload, metadata, user)

        # Check if user has access to the model
        if user.role == "user":
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
    else:
        # Base models coming from a user's own Ollama connections may not have a DB row.
        # Allow and let upstream validate the model id.
        pass

    if ":" not in payload["model"]:
        payload["model"] = f"{payload['model']}:latest"

    connection_user = getattr(request.state, "connection_user", None) or user
    chosen_idx, url, api_config = _resolve_ollama_connection_by_model_id(
        connection_user, payload.get("model", ""), url_idx
    )
    if api_config.get("_resolved_model_id"):
        payload["model"] = api_config["_resolved_model_id"]

    resolved_prefix = (api_config.get("_resolved_prefix_id") or api_config.get("prefix_id") or "").strip() or None
    if resolved_prefix and isinstance(payload.get("model"), str):
        prefix = f"{resolved_prefix}."
        if payload["model"].startswith(prefix):
            payload["model"] = payload["model"][len(prefix) :]
    if ":" not in payload["model"]:
        payload["model"] = f"{payload['model']}:latest"

    base_urls, cfgs = _get_ollama_user_config(connection_user)
    return await send_post_request(
        url=f"{url}/v1/chat/completions",
        payload=json.dumps(payload),
        stream=payload.get("stream", False),
        key=get_api_key(chosen_idx, base_urls[chosen_idx] if chosen_idx < len(base_urls) else url, cfgs),
        user=user,
    )


@router.get("/v1/models")
@router.get("/v1/models/{url_idx}")
async def get_openai_models(
    request: Request,
    url_idx: Optional[int] = None,
    user=Depends(get_verified_user),
):

    models = []
    if url_idx is None:
        model_list = await get_all_models(request, user=user)
        models = [
            {
                "id": model["model"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "openai",
            }
            for model in model_list["models"]
        ]

    else:
        base_urls, cfgs = _get_ollama_user_config(user)
        if url_idx < 0 or url_idx >= len(base_urls):
            raise HTTPException(status_code=404, detail="Connection not found")
        url = base_urls[url_idx]
        key = get_api_key(url_idx, url, cfgs)
        try:
            r = requests.request(
                method="GET",
                url=f"{url.rstrip('/')}/api/tags",
                headers={**({"Authorization": f"Bearer {key}"} if key else {})},
            )
            r.raise_for_status()

            model_list = r.json()

            models = [
                {
                    "id": model["model"],
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "openai",
                }
                for model in (model_list.get("models", []) if isinstance(model_list, dict) else [])
                if isinstance(model, dict) and model.get("model")
            ]
        except Exception as e:
            log.exception(e)
            error_detail = _read_ollama_requests_error_detail(r, e)
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        error_detail = f"Ollama: {res['error']}"
                except Exception:
                    error_detail = f"Ollama: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=error_detail,
            )

    return {
        "data": models,
        "object": "list",
    }


class UrlForm(BaseModel):
    url: str


class UploadBlobForm(BaseModel):
    filename: str


def parse_huggingface_url(hf_url):
    try:
        # Parse the URL
        parsed_url = urlparse(hf_url)

        # Get the path and split it into components
        path_components = parsed_url.path.split("/")

        # Extract the desired output
        model_file = path_components[-1]

        return model_file
    except ValueError:
        return None


async def download_file_stream(
    ollama_url, file_url, file_path, file_name, chunk_size=1024 * 1024
):
    done = False

    if os.path.exists(file_path):
        current_size = os.path.getsize(file_path)
    else:
        current_size = 0

    headers = {"Range": f"bytes={current_size}-"} if current_size > 0 else {}

    timeout = aiohttp.ClientTimeout(total=600)  # Set the timeout

    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(file_url, headers=headers) as response:
            total_size = int(response.headers.get("content-length", 0)) + current_size

            with open(file_path, "ab+") as file:
                async for data in response.content.iter_chunked(chunk_size):
                    current_size += len(data)
                    file.write(data)

                    done = current_size == total_size
                    progress = round((current_size / total_size) * 100, 2)

                    yield f'data: {{"progress": {progress}, "completed": {current_size}, "total": {total_size}}}\n\n'

                if done:
                    file.seek(0)
                    hashed = calculate_sha256(file)
                    file.seek(0)

                    url = f"{ollama_url}/api/blobs/sha256:{hashed}"
                    response = requests.post(url, data=file)

                    if response.ok:
                        res = {
                            "done": done,
                            "blob": f"sha256:{hashed}",
                            "name": file_name,
                        }
                        os.remove(file_path)

                        yield f"data: {json.dumps(res)}\n\n"
                    else:
                        raise "Ollama: Could not create blob, Please try again."


# url = "https://huggingface.co/TheBloke/stablelm-zephyr-3b-GGUF/resolve/main/stablelm-zephyr-3b.Q2_K.gguf"
@router.post("/models/download")
@router.post("/models/download/{url_idx}")
async def download_model(
    request: Request,
    form_data: UrlForm,
    url_idx: Optional[int] = None,
    user=Depends(get_admin_user),
):
    allowed_hosts = ["https://huggingface.co/", "https://github.com/"]

    if not any(form_data.url.startswith(host) for host in allowed_hosts):
        raise HTTPException(
            status_code=400,
            detail="Invalid file_url. Only URLs from allowed hosts are permitted.",
        )

    if url_idx is None:
        url_idx = 0
    url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]

    file_name = parse_huggingface_url(form_data.url)

    if file_name:
        file_path = f"{UPLOAD_DIR}/{file_name}"

        return StreamingResponse(
            download_file_stream(url, form_data.url, file_path, file_name),
        )
    else:
        return None


# TODO: Progress bar does not reflect size & duration of upload.
@router.post("/models/upload")
@router.post("/models/upload/{url_idx}")
async def upload_model(
    request: Request,
    file: UploadFile = File(...),
    url_idx: Optional[int] = None,
    user=Depends(get_admin_user),
):
    if url_idx is None:
        url_idx = 0
    ollama_url = request.app.state.config.OLLAMA_BASE_URLS[url_idx]
    file_path = os.path.join(UPLOAD_DIR, file.filename)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # --- P1: save file locally ---
    chunk_size = 1024 * 1024 * 2  # 2 MB chunks
    with open(file_path, "wb") as out_f:
        while True:
            chunk = file.file.read(chunk_size)
            # log.info(f"Chunk: {str(chunk)}") # DEBUG
            if not chunk:
                break
            out_f.write(chunk)

    async def file_process_stream():
        nonlocal ollama_url
        total_size = os.path.getsize(file_path)
        log.info(f"Total Model Size: {str(total_size)}")  # DEBUG

        # --- P2: SSE progress + calculate sha256 hash ---
        file_hash = calculate_sha256(file_path, chunk_size)
        log.info(f"Model Hash: {str(file_hash)}")  # DEBUG
        try:
            with open(file_path, "rb") as f:
                bytes_read = 0
                while chunk := f.read(chunk_size):
                    bytes_read += len(chunk)
                    progress = round(bytes_read / total_size * 100, 2)
                    data_msg = {
                        "progress": progress,
                        "total": total_size,
                        "completed": bytes_read,
                    }
                    yield f"data: {json.dumps(data_msg)}\n\n"

            # --- P3: Upload to ollama /api/blobs ---
            with open(file_path, "rb") as f:
                url = f"{ollama_url}/api/blobs/sha256:{file_hash}"
                response = requests.post(url, data=f)

            if response.ok:
                log.info(f"Uploaded to /api/blobs")  # DEBUG
                # Remove local file
                os.remove(file_path)

                # Create model in ollama
                model_name, ext = os.path.splitext(file.filename)
                log.info(f"Created Model: {model_name}")  # DEBUG

                create_payload = {
                    "model": model_name,
                    # Reference the file by its original name => the uploaded blob's digest
                    "files": {file.filename: f"sha256:{file_hash}"},
                }
                log.info(f"Model Payload: {create_payload}")  # DEBUG

                # Call ollama /api/create
                # https://github.com/ollama/ollama/blob/main/docs/api.md#create-a-model
                create_resp = requests.post(
                    url=f"{ollama_url}/api/create",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(create_payload),
                )

                if create_resp.ok:
                    log.info(f"API SUCCESS!")  # DEBUG
                    done_msg = {
                        "done": True,
                        "blob": f"sha256:{file_hash}",
                        "name": file.filename,
                        "model_created": model_name,
                    }
                    yield f"data: {json.dumps(done_msg)}\n\n"
                else:
                    raise Exception(
                        f"Failed to create model in Ollama. {create_resp.text}"
                    )

            else:
                raise Exception("Ollama: Could not create blob, Please try again.")

        except Exception as e:
            res = {"error": str(e)}
            yield f"data: {json.dumps(res)}\n\n"

    return StreamingResponse(file_process_stream(), media_type="text/event-stream")
