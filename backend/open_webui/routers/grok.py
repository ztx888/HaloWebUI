import logging
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, SRC_LOG_LEVELS
from open_webui.routers import openai as openai_router
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.error_handling import build_error_detail
from open_webui.utils.user_connections import get_user_connections

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()

DEFAULT_GROK_API_BASE_URL = "https://api.x.ai/v1"
VERSION_LIKE_BASE_URL_RE = re.compile(r"/v\d+(?:[a-z]+\d*)?$", re.IGNORECASE)

GROK_IMAGE_ASPECT_RATIOS = (
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "2:1",
    "1:2",
    "19.5:9",
    "9:19.5",
    "20:9",
    "9:20",
    "auto",
)
GROK_IMAGE_RESOLUTIONS = ("1k", "2k")


def _normalize_grok_base_url(url: Optional[str]) -> str:
    normalized = str(url or "").strip().rstrip("/")
    if not normalized:
        return ""

    for suffix in (
        "/image-generation-models",
        "/models",
        "/images/generations",
        "/images/edits",
    ):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break

    if not normalized:
        return ""

    if normalized.lower().endswith("/v1") or VERSION_LIKE_BASE_URL_RE.search(normalized):
        return normalized

    return f"{normalized}/v1"


def _get_grok_models_url(base_url: str) -> str:
    return f"{_normalize_grok_base_url(base_url)}/image-generation-models"


def _get_grok_generation_url(base_url: str) -> str:
    return f"{_normalize_grok_base_url(base_url)}/images/generations"


def _build_grok_headers(
    base_url: str,
    key: str,
    api_config: Optional[dict] = None,
    user=None,
) -> dict[str, str]:
    return openai_router._build_upstream_headers(
        _normalize_grok_base_url(base_url),
        key,
        api_config or {},
        user=user,
        accept="application/json",
        content_type="application/json",
    )


def _extract_grok_models(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        items = (
            body.get("data")
            or body.get("models")
            or body.get("items")
            or body.get("image_generation_models")
            or []
        )
    else:
        items = []

    models: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        model_id = str(
            item.get("id")
            or item.get("name")
            or item.get("model")
            or item.get("slug")
            or ""
        ).strip()
        if not model_id or model_id in seen:
            continue

        seen.add(model_id)
        models.append(
            {
                "id": model_id,
                "name": str(
                    item.get("display_name")
                    or item.get("displayName")
                    or item.get("name")
                    or model_id
                ).strip(),
                **item,
            }
        )

    return models


async def _read_response_body(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        try:
            return await response.text()
        except Exception:
            return None


async def _fetch_grok_models(
    url: str,
    key: str,
    api_config: Optional[dict] = None,
    *,
    user=None,
) -> list[dict[str, Any]]:
    models_url = _get_grok_models_url(url)
    headers = _build_grok_headers(url, key, api_config or {}, user=user)

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.get(
            models_url,
            headers=headers,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as response:
            body = await _read_response_body(response)
            if response.status >= 400:
                raise HTTPException(
                    status_code=response.status,
                    detail=build_error_detail(
                        body,
                        default=f"Failed to load Grok image models from {models_url}",
                    ),
                )

    return _extract_grok_models(body)


def _get_grok_user_config(connection_user) -> tuple[list[str], list[str], dict]:
    conns = get_user_connections(connection_user)
    cfg = conns.get("grok") if isinstance(conns, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}

    base_urls = list(cfg.get("GROK_API_BASE_URLS") or [])
    keys = list(cfg.get("GROK_API_KEYS") or [])
    configs = cfg.get("GROK_API_CONFIGS") or {}
    configs = configs if isinstance(configs, dict) else {}

    if len(keys) != len(base_urls):
        if len(keys) > len(base_urls):
            keys = keys[: len(base_urls)]
        else:
            keys = keys + [""] * (len(base_urls) - len(keys))

    return base_urls, keys, configs


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_GROK_API": request.app.state.config.ENABLE_GROK_API,
        "GROK_API_BASE_URLS": request.app.state.config.GROK_API_BASE_URLS,
        "GROK_API_KEYS": request.app.state.config.GROK_API_KEYS,
        "GROK_API_CONFIGS": request.app.state.config.GROK_API_CONFIGS,
    }


class GrokConfigForm(BaseModel):
    ENABLE_GROK_API: Optional[bool] = None
    GROK_API_BASE_URLS: list[str]
    GROK_API_KEYS: list[str]
    GROK_API_CONFIGS: dict


@router.post("/config/update")
async def update_config(
    request: Request, form_data: GrokConfigForm, user=Depends(get_admin_user)
):
    prev_urls = list(getattr(request.app.state.config, "GROK_API_BASE_URLS", []) or [])
    prev_cfgs = getattr(request.app.state.config, "GROK_API_CONFIGS", {}) or {}
    prev_prefix_by_url: dict[str, str] = {}
    prev_empty_urls: set[str] = set()

    for idx, prev_url in enumerate(prev_urls):
        url_key = _normalize_grok_base_url(prev_url)
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

    request.app.state.config.ENABLE_GROK_API = form_data.ENABLE_GROK_API
    request.app.state.config.GROK_API_BASE_URLS = [
        _normalize_grok_base_url(url) or DEFAULT_GROK_API_BASE_URL
        for url in form_data.GROK_API_BASE_URLS
    ]
    request.app.state.config.GROK_API_KEYS = form_data.GROK_API_KEYS

    if len(request.app.state.config.GROK_API_KEYS) != len(
        request.app.state.config.GROK_API_BASE_URLS
    ):
        if len(request.app.state.config.GROK_API_KEYS) > len(
            request.app.state.config.GROK_API_BASE_URLS
        ):
            request.app.state.config.GROK_API_KEYS = (
                request.app.state.config.GROK_API_KEYS[
                    : len(request.app.state.config.GROK_API_BASE_URLS)
                ]
            )
        else:
            request.app.state.config.GROK_API_KEYS += [""] * (
                len(request.app.state.config.GROK_API_BASE_URLS)
                - len(request.app.state.config.GROK_API_KEYS)
            )

    request.app.state.config.GROK_API_CONFIGS = form_data.GROK_API_CONFIGS

    keys = list(map(str, range(len(request.app.state.config.GROK_API_BASE_URLS))))
    request.app.state.config.GROK_API_CONFIGS = {
        key: value
        for key, value in request.app.state.config.GROK_API_CONFIGS.items()
        if key in keys
    }

    used_prefix_ids = set()
    normalized_configs = {}

    preserved_empty_idx = None
    if len(keys) >= 1:
        for idx_str in keys:
            idx = int(idx_str)
            url = request.app.state.config.GROK_API_BASE_URLS[idx]
            url_key = _normalize_grok_base_url(url)
            if url_key and url_key in prev_empty_urls:
                preserved_empty_idx = idx
                break

    for idx_str in keys:
        idx = int(idx_str)
        url = request.app.state.config.GROK_API_BASE_URLS[idx]
        url_key = _normalize_grok_base_url(url)
        cfg = request.app.state.config.GROK_API_CONFIGS.get(idx_str, {}) or {}

        name = str(cfg.get("name") or "").strip()
        if not name:
            try:
                name = urlparse(url).hostname or f"Connection {idx + 1}"
            except Exception:
                name = f"Connection {idx + 1}"

        prev_prefix = prev_prefix_by_url.get(url_key) if url_key else None
        prefix_id = str(prev_prefix or (cfg.get("prefix_id") or "")).strip()
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
            normalized_cfg["prefix_id"] = ""
        else:
            normalized_cfg.pop("prefix_id", None)

        normalized_configs[idx_str] = normalized_cfg

    request.app.state.config.GROK_API_CONFIGS = normalized_configs
    request.app.state.GROK_MODELS = {}

    return {
        "ENABLE_GROK_API": request.app.state.config.ENABLE_GROK_API,
        "GROK_API_BASE_URLS": request.app.state.config.GROK_API_BASE_URLS,
        "GROK_API_KEYS": request.app.state.config.GROK_API_KEYS,
        "GROK_API_CONFIGS": request.app.state.config.GROK_API_CONFIGS,
    }


@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    models = {"data": []}

    base_urls, keys, cfgs = _get_grok_user_config(user)

    if url_idx is None:
        seen: set[str] = set()
        for idx, url in enumerate(base_urls):
            api_key = keys[idx] if idx < len(keys) else ""
            api_config = cfgs.get(str(idx), {}) if isinstance(cfgs, dict) else {}
            prefix_id = str(api_config.get("prefix_id") or "").strip()
            connection_name = str(api_config.get("remark") or api_config.get("name") or "").strip()
            if not url or not api_key:
                continue

            try:
                payload = await _fetch_grok_models(url, api_key, api_config, user=user)
            except HTTPException:
                continue

            for model in payload:
                model_id = str(model.get("id") or "").strip()
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                models["data"].append(
                    {
                        "id": model_id,
                        "original_id": model_id,
                        "name": str(model.get("name") or model_id).strip(),
                        "owned_by": "openai",
                        "source": "personal",
                        "connection_index": idx,
                        **({"connection_id": prefix_id} if prefix_id else {}),
                        **({"connection_name": connection_name} if connection_name else {}),
                    }
                )
    else:
        if url_idx < 0 or url_idx >= len(base_urls):
            raise HTTPException(status_code=404, detail="Connection not found")

        url = base_urls[url_idx]
        api_key = keys[url_idx] if url_idx < len(keys) else ""
        api_config = cfgs.get(str(url_idx), {}) if isinstance(cfgs, dict) else {}
        prefix_id = str(api_config.get("prefix_id") or "").strip()
        connection_name = str(api_config.get("remark") or api_config.get("name") or "").strip()
        payload = await _fetch_grok_models(url, api_key, api_config, user=user)
        models = {
            "data": [
                {
                    "id": str(model.get("id") or "").strip(),
                    "original_id": str(model.get("id") or "").strip(),
                    "name": str(model.get("name") or model.get("id") or "").strip(),
                    "owned_by": "openai",
                    "source": "personal",
                    "connection_index": url_idx,
                    **({"connection_id": prefix_id} if prefix_id else {}),
                    **({"connection_name": connection_name} if connection_name else {}),
                }
                for model in payload
                if str(model.get("id") or "").strip()
            ]
        }

    return models


class ConnectionVerificationForm(BaseModel):
    url: str
    key: str
    config: Optional[dict] = None


class HealthCheckForm(BaseModel):
    url: str
    key: str = ""
    config: Optional[dict] = None
    model: Optional[str] = None


@router.post("/verify")
async def verify_connection(
    request: Request,
    form_data: ConnectionVerificationForm,
    user=Depends(get_verified_user),
):
    del request
    url = _normalize_grok_base_url(form_data.url)
    if not url:
        raise HTTPException(status_code=400, detail="Grok: URL is required")

    models = await _fetch_grok_models(url, form_data.key or "", form_data.config or {}, user=user)
    return {"models": models}


@router.post("/health_check")
async def health_check_connection(
    form_data: HealthCheckForm,
    user=Depends(get_verified_user),
):
    url = _normalize_grok_base_url(form_data.url)
    if not url:
        raise HTTPException(status_code=400, detail="Grok: URL is required")

    key = form_data.key or ""
    config = form_data.config or {}
    chosen_model = str(form_data.model or "").strip()
    if not chosen_model:
        models = await _fetch_grok_models(url, key, config, user=user)
        chosen_model = str((models[0] if models else {}).get("id") or "").strip()

    if not chosen_model:
        raise HTTPException(status_code=400, detail="Grok: No compatible model found")

    request_url = _get_grok_generation_url(url)
    payload = {
        "model": chosen_model,
        "prompt": "health check",
        "aspect_ratio": GROK_IMAGE_ASPECT_RATIOS[0],
        "resolution": GROK_IMAGE_RESOLUTIONS[0],
        "response_format": "b64_json",
        "n": 1,
    }
    headers = _build_grok_headers(url, key, config, user=user)

    timeout = aiohttp.ClientTimeout(total=20)
    started_at = time.monotonic()
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(
                request_url,
                headers=headers,
                json=payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                body = await _read_response_body(response)
                if response.status < 400:
                    return {
                        "ok": True,
                        "model": chosen_model,
                        "response_time_ms": max(
                            1, int((time.monotonic() - started_at) * 1000)
                        ),
                    }
                raise HTTPException(
                    status_code=response.status,
                    detail=build_error_detail(
                        body,
                        default="Failed to generate image via Grok /images/generations",
                    ),
                )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Unexpected Grok health check error: %s", e)
        raise HTTPException(
            status_code=500,
            detail=build_error_detail(e, prefix="Grok"),
        )
