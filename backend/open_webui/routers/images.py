import asyncio
import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
import httpx
import requests
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    REQUESTS_VERIFY,
    SRC_LOG_LEVELS,
)
from open_webui.models.files import Files
from open_webui.routers import gemini as gemini_router
from open_webui.routers import grok as grok_router
from open_webui.routers import openai as openai_router
from open_webui.routers.files import upload_file
from open_webui.utils.chat_image_refs import (
    extract_chat_image_file_id,
    resolve_chat_image_url_to_bytes,
)
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.access_control import has_permission
from open_webui.utils.error_handling import build_error_detail, read_requests_error_payload
from open_webui.utils.model_identity import (
    AMBIGUOUS_MODEL_CODE,
    AMBIGUOUS_MODEL_DETAIL,
    STALE_MODEL_REF_CODE,
    STALE_MODEL_REF_DETAIL,
    build_model_resolution_error,
    build_model_ref,
    build_selection_id,
    parse_selection_id,
    unique_strings,
)
from open_webui.utils.user_connections import get_user_connections
from open_webui.utils.images.comfyui import (
    ComfyUIGenerateImageForm,
    ComfyUIWorkflow,
    comfyui_generate_image,
)
from pydantic import BaseModel

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["IMAGES"])

IMAGE_CACHE_DIR = CACHE_DIR / "image" / "generations"
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


router = APIRouter()

COMFYUI_WORKFLOW_NODE_MAPPING_INVALID = (
    "ComfyUI workflow node mapping is invalid. Please update the workflow node IDs to match the current workflow."
)

IMAGE_MODEL_DISCOVERY_CACHE_TTL_SECONDS = 60.0
IMAGE_MODEL_DISCOVERY_CACHE_MAX_ENTRIES = 64
IMAGE_MODEL_DISCOVERY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
MARKDOWN_IMAGE_URL_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
DATA_IMAGE_URL_RE = re.compile(r"data:image\/[a-z0-9.+-]+;base64,[a-z0-9+/=\s]+", re.IGNORECASE)

OPENAI_IMAGE_DEFAULT_RESPONSE_FORMAT_PREFIXES = (
    "chatgpt-image-",
    "gpt-image-1-mini",
    "gpt-image-1.5",
    "gpt-image-1",
    "gpt-image-2",
)
OPENAI_DEDICATED_IMAGE_MODEL_RE = re.compile(
    "|".join(
        (
            r"dall-e(?:-[\w-]+)?",
            r"dalle(?:-[\w-]+)?",
            r"gpt-image(?:-[\w-]+)?",
            r"chatgpt-image(?:-[\w-]+)?",
            r"grok-2-image(?:-[\w-]+)?",
            r"imagen(?:-[\w-]+)?",
            r"flux(?:-[\w-]+)?",
            r"stable-?diffusion(?:-[\w-]+)?",
            r"stabilityai(?:-[\w-]+)?",
            r"sd-[\w-]+",
            r"sdxl(?:-[\w-]+)?",
            r"cogview(?:-[\w-]+)?",
            r"qwen-image(?:-[\w-]+)?",
            r"janus(?:-[\w-]+)?",
            r"midjourney(?:-[\w-]+)?",
            r"mj-[\w-]+",
            r"z-image(?:-[\w-]+)?",
            r"longcat-image(?:-[\w-]+)?",
            r"hunyuanimage(?:-[\w-]+)?",
            r"hunyuan-image(?:-[\w-]+)?",
            r"seedream(?:-[\w-]+)?",
            r"seededit(?:-[\w-]+)?",
            r"kandinsky(?:-[\w-]+)?",
        )
    ),
    re.IGNORECASE,
)
OPENAI_IMAGE_ALLOWED_SIZES = {"1024x1024", "1536x1024", "1024x1536"}
VOLCENGINE_IMAGES_ENDPOINT_HINTS = ("seedream", "seededit")
OPENAI_CHAT_IMAGE_HINTS = (
    "chatgpt-image",
    "image-preview",
    "create-preview",
    "flux",
    "stable-diffusion",
    "sdxl",
    "midjourney",
    "imagen",
    "ideogram",
    "recraft",
    "kolors",
    "wanx",
    "cogview",
    "playground",
    "nano-banana",
    "qwen-image",
    "glm-image",
    "hunyuan-image",
    "seedream",
    "agnes-image",
)
NEGATIVE_IMAGE_HINTS = (
    "embedding",
    "embed",
    "rerank",
    "reranker",
    "tts",
    "whisper",
    "transcribe",
    "transcription",
    "audio",
    "speech",
    "moderation",
    "omni-moderation",
    "safety",
    "guard",
    "ocr",
    "image-understanding",
    "image-analysis",
    "vision-only",
    "segmentation",
    "upscale",
)
NEGATIVE_TOKEN_RE = re.compile(r"(^|[\/._:-])(vision|vl|asr)([\/._:-]|$)", re.IGNORECASE)
VERSION_LIKE_BASE_URL_RE = re.compile(
    r"/(?:compatible-mode/)?v\d+(?:[a-z]+\d*)?$", re.IGNORECASE
)

IMAGE_SIZE_ASPECT_RATIO_OVERRIDES = {
    "512x512": "1:1",
    "1024x1024": "1:1",
    "1024x1536": "2:3",
    "1536x1024": "3:2",
}

GEMINI_IMAGE_SIZE_PIXELS = {
    "512": "512x512",
    "1K": "1024x1024",
    "2K": "2048x2048",
    "4K": "4096x4096",
}
GEMINI_IMAGE_SIZE_ORDER = ("512", "1K", "2K", "4K")
GEMINI_IMAGE_ASPECT_RATIOS = (
    "1:1",
    "2:3",
    "3:2",
    "3:4",
    "4:3",
    "4:5",
    "5:4",
    "9:16",
    "16:9",
    "21:9",
)
GROK_IMAGE_RESOLUTIONS = ("1k", "2k")
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


def _redact_upstream_headers(headers: dict[str, Any]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in (headers or {}).items():
        header_name = str(key)
        if header_name.lower() in {"authorization", "api-key", "x-api-key"}:
            redacted[header_name] = "<redacted>"
        else:
            redacted[header_name] = str(value)
    return redacted


def _filter_process_debug_env(env: dict[str, str]) -> dict[str, str]:
    allowed_keys = (
        "PATH",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "NODE_OPTIONS",
        "NODE_EXTRA_CA_CERTS",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    )
    return {
        key: str(env.get(key) or "")
        for key in allowed_keys
        if key in env
    }


def _evict_stale_image_models_cache(now: float) -> None:
    stale_keys = [
        cache_key
        for cache_key, (cached_at, _models) in IMAGE_MODEL_DISCOVERY_CACHE.items()
        if now - cached_at > IMAGE_MODEL_DISCOVERY_CACHE_TTL_SECONDS
    ]
    for cache_key in stale_keys:
        IMAGE_MODEL_DISCOVERY_CACHE.pop(cache_key, None)

    overflow = len(IMAGE_MODEL_DISCOVERY_CACHE) - IMAGE_MODEL_DISCOVERY_CACHE_MAX_ENTRIES
    if overflow <= 0:
        return

    oldest_keys = [
        cache_key
        for cache_key, _ in sorted(
            IMAGE_MODEL_DISCOVERY_CACHE.items(), key=lambda item: item[1][0]
        )[:overflow]
    ]
    for cache_key in oldest_keys:
        IMAGE_MODEL_DISCOVERY_CACHE.pop(cache_key, None)


def _can_use_image_generation(request: Request, user) -> bool:
    """Server-side permission gate for image generation (matches builtin_tools behavior)."""
    if getattr(user, "role", None) == "admin":
        return True
    try:
        return has_permission(
            user.id, "features.image_generation", request.app.state.config.USER_PERMISSIONS
        )
    except Exception:
        return False


def _normalize_engine(value: Optional[str]) -> str:
    engine = (value or "").strip().lower()
    # Historically, "" has been treated as Automatic1111 in this codebase.
    return engine or "automatic1111"


def _is_non_empty(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.strip() != ""


def _parse_comfyui_workflow_config(request: Request) -> dict:
    workflow = json.loads(request.app.state.config.COMFYUI_WORKFLOW or "{}")
    if not isinstance(workflow, dict):
        raise HTTPException(
            status_code=400, detail=COMFYUI_WORKFLOW_NODE_MAPPING_INVALID
        )
    return workflow


def _collect_missing_comfyui_node_refs(
    workflow: dict, workflow_nodes: list[dict], *, node_type: Optional[str] = None
) -> list[tuple[str, str]]:
    workflow_node_ids = {str(node_id) for node_id in workflow.keys()}
    missing_refs: list[tuple[str, str]] = []

    for node in workflow_nodes or []:
        current_type = str(node.get("type") or "")
        if node_type is not None and current_type != node_type:
            continue

        for raw_node_id in node.get("node_ids") or []:
            node_id = str(raw_node_id).strip()
            if node_id and node_id not in workflow_node_ids:
                missing_refs.append((current_type or "custom", node_id))

    return missing_refs


def _validate_comfyui_workflow_node_mapping(
    workflow: dict, workflow_nodes: list[dict], *, node_type: Optional[str] = None
) -> None:
    missing_refs = _collect_missing_comfyui_node_refs(
        workflow, workflow_nodes, node_type=node_type
    )
    if missing_refs:
        refs = ", ".join(f"{kind}({node_id})" for kind, node_id in missing_refs)
        log.warning(f"Invalid ComfyUI workflow node mapping detected: {refs}")
        raise HTTPException(
            status_code=400, detail=COMFYUI_WORKFLOW_NODE_MAPPING_INVALID
        )


def _normalize_comfyui_model_option(option) -> Optional[dict]:
    if option is None:
        return None

    if isinstance(option, (str, int, float)):
        value = str(option).strip()
        if not value:
            return None
        return {"id": value, "name": value}

    if isinstance(option, dict):
        raw_value = (
            option.get("key")
            or option.get("value")
            or option.get("id")
            or option.get("name")
            or option.get("label")
        )
        value = str(raw_value or "").strip()
        if not value:
            return None
        label = str(option.get("label") or option.get("name") or value).strip() or value
        return {"id": value, "name": label}

    return None


def _extract_comfyui_input_options(input_spec) -> list[dict]:
    if not isinstance(input_spec, list) or not input_spec:
        return []

    options = None
    if isinstance(input_spec[0], list):
        options = input_spec[0]
    elif len(input_spec) > 1 and isinstance(input_spec[1], dict):
        options = input_spec[1].get("options")

    if not isinstance(options, list):
        return []

    extracted = []
    seen = set()
    for option in options:
        normalized = _normalize_comfyui_model_option(option)
        if not normalized:
            continue
        option_id = normalized["id"]
        if option_id in seen:
            continue
        seen.add(option_id)
        extracted.append(normalized)

    return extracted


def _get_comfyui_models(info: dict, workflow: dict, workflow_nodes: list[dict]) -> list[dict]:
    model_node = next(
        (
            node
            for node in (workflow_nodes or [])
            if str(node.get("type") or "") == "model" and node.get("node_ids")
        ),
        None,
    )

    if not model_node:
        return _extract_comfyui_input_options(
            (
                info.get("CheckpointLoaderSimple", {})
                .get("input", {})
                .get("required", {})
                .get("ckpt_name")
            )
        )

    model_node_id = str((model_node.get("node_ids") or [""])[0]).strip()
    workflow_node = workflow.get(model_node_id, {}) if model_node_id else {}
    class_type = str(workflow_node.get("class_type") or "").strip()
    node_key = str(model_node.get("key") or "").strip()

    if not class_type:
        return []

    node_info = info.get(class_type, {})
    node_inputs = node_info.get("input", {}) if isinstance(node_info, dict) else {}

    candidate_specs = []

    for section_name in ("required", "optional"):
        section = node_inputs.get(section_name, {})
        if not isinstance(section, dict):
            continue
        if node_key and node_key in section:
            candidate_specs.append(section[node_key])

    if not candidate_specs:
        for section_name in ("required", "optional"):
            section = node_inputs.get(section_name, {})
            if not isinstance(section, dict):
                continue
            for key, spec in section.items():
                key_str = str(key or "").strip()
                if key_str == "model" or key_str.endswith("_name"):
                    candidate_specs.append(spec)

    models = []
    seen = set()
    for spec in candidate_specs:
        for option in _extract_comfyui_input_options(spec):
            option_id = option["id"]
            if option_id in seen:
                continue
            seen.add(option_id)
            models.append(option)

    return models


def _get_user_provider_urls_keys(user, provider: str) -> tuple[list[str], list[str]]:
    conns = get_user_connections(user)
    cfg = conns.get(provider) if isinstance(conns, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}

    if provider == "openai":
        urls_key = "OPENAI_API_BASE_URLS"
        keys_key = "OPENAI_API_KEYS"
    elif provider == "gemini":
        urls_key = "GEMINI_API_BASE_URLS"
        keys_key = "GEMINI_API_KEYS"
    elif provider == "grok":
        urls_key = "GROK_API_BASE_URLS"
        keys_key = "GROK_API_KEYS"
    else:
        return [], []

    base_urls = list(cfg.get(urls_key) or [])
    keys = list(cfg.get(keys_key) or [])

    # Keep list lengths aligned (do not mutate persisted settings here).
    if len(keys) != len(base_urls):
        if len(keys) > len(base_urls):
            keys = keys[: len(base_urls)]
        else:
            keys = keys + [""] * (len(base_urls) - len(keys))

    return base_urls, keys


def _pick_personal_connection(
    base_urls: list[str], keys: list[str], preferred_index: Optional[int] = None
) -> Optional[tuple[int, str, str]]:
    usable = _list_personal_connections(base_urls, keys)

    if not usable:
        return None

    if preferred_index is not None:
        for item in usable:
            if item[0] == preferred_index:
                return item

    return usable[0]


def _list_personal_connections(
    base_urls: list[str], keys: list[str]
) -> list[tuple[int, str, str]]:
    usable: list[tuple[int, str, str]] = []
    for idx, (url, key) in enumerate(zip(base_urls or [], keys or [])):
        u = str(url or "").strip()
        k = str(key or "").strip()
        if u and k:
            usable.append((idx, u, k))
    return usable


def _get_personal_connection_exact(
    base_urls: list[str], keys: list[str], index: Optional[int]
) -> Optional[tuple[int, str, str]]:
    if index is None:
        return None
    if index < 0:
        return None
    if index >= len(base_urls):
        return None
    u = str((base_urls[index] if index < len(base_urls) else "") or "").strip()
    k = str((keys[index] if index < len(keys) else "") or "").strip()
    if not u or not k:
        return None
    return index, u, k


def _shared_key_available(request: Request, engine: str) -> bool:
    cfg = request.app.state.config
    engine = _normalize_engine(engine)

    if engine == "openai":
        return _is_non_empty(getattr(cfg, "IMAGES_OPENAI_API_BASE_URL", "")) and _is_non_empty(
            getattr(cfg, "IMAGES_OPENAI_API_KEY", "")
        )
    if engine == "gemini":
        return _is_non_empty(getattr(cfg, "IMAGES_GEMINI_API_BASE_URL", "")) and _is_non_empty(
            getattr(cfg, "IMAGES_GEMINI_API_KEY", "")
        )
    if engine == "grok":
        return _is_non_empty(getattr(cfg, "IMAGES_GROK_API_BASE_URL", "")) and _is_non_empty(
            getattr(cfg, "IMAGES_GROK_API_KEY", "")
        )
    if engine == "comfyui":
        return _is_non_empty(getattr(cfg, "COMFYUI_BASE_URL", ""))
    if engine in ("automatic1111", ""):
        return _is_non_empty(getattr(cfg, "AUTOMATIC1111_BASE_URL", ""))

    return False


def _normalize_base_url(url: Optional[str]) -> str:
    return str(url or "").strip().rstrip("/")


def _normalize_image_provider_base_url(
    url: Optional[str],
    default_version_path: str,
    *,
    force_mode: bool = False,
) -> tuple[str, bool]:
    raw = str(url or "").strip()
    explicit_force_mode = raw.endswith("#")
    if explicit_force_mode:
        raw = raw[:-1]

    normalized = raw.rstrip("/")
    effective_force_mode = bool(force_mode or explicit_force_mode)

    if not normalized:
        return "", effective_force_mode

    if effective_force_mode:
        return normalized, True

    for suffix in (
        "/chat/completions",
        "/models",
        "/completions",
        "/images/generations",
        "/images/edits",
    ):
        if normalized.lower().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break

    if not normalized:
        return "", False

    if normalized.lower().endswith(default_version_path.lower()):
        return normalized, False

    if VERSION_LIKE_BASE_URL_RE.search(normalized):
        return normalized, False

    return f"{normalized}{default_version_path}", False


def _sync_image_provider_config_state(request: Request) -> None:
    cfg = request.app.state.config

    openai_base_url = str(getattr(cfg, "IMAGES_OPENAI_API_BASE_URL", "") or "")
    openai_force_mode = bool(getattr(cfg, "IMAGES_OPENAI_API_FORCE_MODE", False))
    normalized_openai_base_url, normalized_openai_force_mode = _normalize_image_provider_base_url(
        openai_base_url,
        "/v1",
        force_mode=openai_force_mode,
    )
    if normalized_openai_base_url != openai_base_url:
        request.app.state.config.IMAGES_OPENAI_API_BASE_URL = normalized_openai_base_url
    if normalized_openai_force_mode != openai_force_mode:
        request.app.state.config.IMAGES_OPENAI_API_FORCE_MODE = normalized_openai_force_mode

    gemini_base_url = str(getattr(cfg, "IMAGES_GEMINI_API_BASE_URL", "") or "")
    gemini_force_mode = bool(getattr(cfg, "IMAGES_GEMINI_API_FORCE_MODE", False))
    normalized_gemini_base_url, normalized_gemini_force_mode = _normalize_image_provider_base_url(
        gemini_base_url,
        "/v1beta",
        force_mode=gemini_force_mode,
    )
    if normalized_gemini_base_url != gemini_base_url:
        request.app.state.config.IMAGES_GEMINI_API_BASE_URL = normalized_gemini_base_url
    if normalized_gemini_force_mode != gemini_force_mode:
        request.app.state.config.IMAGES_GEMINI_API_FORCE_MODE = normalized_gemini_force_mode

    grok_base_url = str(getattr(cfg, "IMAGES_GROK_API_BASE_URL", "") or "")
    normalized_grok_base_url, _ = _normalize_image_provider_base_url(
        grok_base_url,
        "/v1",
        force_mode=False,
    )
    if normalized_grok_base_url != grok_base_url:
        request.app.state.config.IMAGES_GROK_API_BASE_URL = normalized_grok_base_url


def _normalize_context(value: Optional[str]) -> str:
    normalized = str(value or "runtime").strip().lower()
    return normalized if normalized in {"runtime", "settings"} else "runtime"


def _normalize_credential_source(value: Optional[str]) -> str:
    normalized = str(value or "auto").strip().lower()
    return normalized if normalized in {"auto", "personal", "shared"} else "auto"


def _model_id_basename(model_id: str) -> str:
    value = str(model_id or "").strip()
    if not value:
        return ""
    return value.split("/")[-1].strip()


def _strip_connection_model_prefix(model_id: str, api_config: Optional[dict]) -> str:
    normalized_model_id = str(model_id or "").strip()
    cfg = api_config if isinstance(api_config, dict) else {}
    prefix_id = str(cfg.get("_resolved_prefix_id") or cfg.get("prefix_id") or "").strip()
    if prefix_id:
        prefix = f"{prefix_id}."
        if normalized_model_id.startswith(prefix):
            return normalized_model_id[len(prefix) :]
    fallback_match = re.match(r"^[0-9a-f]{8}\.(.+)$", normalized_model_id, re.IGNORECASE)
    if fallback_match:
        return fallback_match.group(1)

    return normalized_model_id


def _normalize_config_model_ids(api_config: Optional[dict]) -> list[str]:
    cfg = api_config if isinstance(api_config, dict) else {}
    raw_model_ids = cfg.get("model_ids") or []
    if not isinstance(raw_model_ids, list):
        return []

    prefix_id = str(cfg.get("prefix_id") or "").strip()
    prefix = f"{prefix_id}." if prefix_id else ""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_model_id in raw_model_ids:
        model_id = str(raw_model_id or "").strip()
        if not model_id:
            continue
        if prefix and model_id.startswith(prefix):
            model_id = model_id[len(prefix) :]
        if model_id in seen:
            continue
        seen.add(model_id)
        normalized.append(model_id)

    return normalized


def _json_blob(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str).lower()
    except Exception:
        return str(value).lower()


def _collect_modality_tokens(value: Any) -> set[str]:
    tokens: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            token = node.strip().lower()
            if token:
                tokens.add(token)
        elif isinstance(node, dict):
            for key in ("type", "name", "value", "mode", "id", "kind"):
                if key in node:
                    _walk(node.get(key))
            for key in (
                "input",
                "output",
                "modalities",
                "input_modalities",
                "inputModalities",
                "output_modalities",
                "outputModalities",
                "supportedGenerationMethods",
                "supported_generation_methods",
            ):
                if key in node:
                    _walk(node.get(key))
        elif isinstance(node, (list, tuple, set)):
            for item in node:
                _walk(item)

    _walk(value)
    return tokens


def _extract_modalities(model: dict) -> tuple[set[str], set[str]]:
    input_modalities: set[str] = set()
    output_modalities: set[str] = set()

    for container in (
        model,
        model.get("architecture"),
        model.get("capabilities"),
    ):
        if not isinstance(container, dict):
            continue
        if "input_modalities" in container:
            input_modalities.update(_collect_modality_tokens(container.get("input_modalities")))
        if "inputModalities" in container:
            input_modalities.update(_collect_modality_tokens(container.get("inputModalities")))
        if "output_modalities" in container:
            output_modalities.update(
                _collect_modality_tokens(container.get("output_modalities"))
            )
        if "outputModalities" in container:
            output_modalities.update(
                _collect_modality_tokens(container.get("outputModalities"))
            )

    modalities = model.get("modalities")
    if isinstance(modalities, dict):
        input_modalities.update(_collect_modality_tokens(modalities.get("input")))
        output_modalities.update(_collect_modality_tokens(modalities.get("output")))
    elif modalities is not None:
        output_modalities.update(_collect_modality_tokens(modalities))

    return input_modalities, output_modalities


def _extract_supported_generation_methods(model: dict) -> set[str]:
    methods: set[str] = set()
    for key in ("supportedGenerationMethods", "supported_generation_methods"):
        methods.update(_collect_modality_tokens(model.get(key)))
    return methods


def _extract_endpoint_blob(model: dict) -> str:
    candidates = [
        model.get("endpoints"),
        model.get("supported_endpoints"),
        model.get("api"),
        model.get("architecture", {}).get("endpoints")
        if isinstance(model.get("architecture"), dict)
        else None,
    ]
    parts = [_json_blob(candidate) for candidate in candidates if candidate]
    return "\n".join(part for part in parts if part)


def _model_text_blob(model: dict) -> str:
    parts = [
        model.get("id"),
        model.get("name"),
        model.get("displayName"),
        model.get("display_name"),
        model.get("description"),
        model.get("type"),
        model.get("object"),
        _json_blob(model.get("architecture")),
        _json_blob(model.get("capabilities")),
        _json_blob(model.get("details")),
        _json_blob(model.get("metadata")),
    ]
    return "\n".join(str(part).lower() for part in parts if part)


def _is_volcengine_ark_connection(url: str) -> bool:
    try:
        hostname = (urlparse(_normalize_base_url(url)).hostname or "").strip().lower()
    except Exception:
        hostname = ""

    return hostname.endswith(".volces.com") or hostname.endswith(".volcengineapi.com")


def _is_official_xai_connection(url: str) -> bool:
    try:
        hostname = (urlparse(_normalize_base_url(url)).hostname or "").strip().lower()
    except Exception:
        hostname = ""

    return hostname == "api.x.ai"


def _has_image_modality(tokens: set[str]) -> bool:
    for token in tokens:
        if token == "image" or token.startswith("image/"):
            return True
        if token.endswith("_image") or token.endswith("-image"):
            return True
    return False


def _has_text_modality(tokens: set[str]) -> bool:
    return any(token == "text" or token.startswith("text/") for token in tokens)


def _is_openai_dedicated_image_model(*values: str) -> bool:
    return any(
        OPENAI_DEDICATED_IMAGE_MODEL_RE.search(str(value or "").lower())
        for value in values
        if str(value or "").strip()
    )


def _openai_image_model_has_default_response_format(model_id: str) -> bool:
    base_name = _model_id_basename(str(model_id or "")).lower()
    return any(
        base_name.startswith(prefix)
        for prefix in OPENAI_IMAGE_DEFAULT_RESPONSE_FORMAT_PREFIXES
    )


def _matches_image_positive_hint(text: str) -> bool:
    normalized = str(text or "").lower()
    if not normalized:
        return False

    base_name = _model_id_basename(normalized)
    if _is_openai_dedicated_image_model(base_name, normalized):
        return True

    if any(
        phrase in normalized
        for phrase in (
            "image generation",
            "image creator",
            "image creation",
            "text-to-image",
            "image-to-image",
            "图像生成",
            "图像创作",
            "文生图",
            "图生图",
        )
    ):
        return True

    return any(hint in normalized for hint in OPENAI_CHAT_IMAGE_HINTS)


def _matches_image_negative_hint(text: str) -> bool:
    normalized = str(text or "").lower()
    if not normalized:
        return False

    if any(hint in normalized for hint in NEGATIVE_IMAGE_HINTS):
        return True

    return NEGATIVE_TOKEN_RE.search(normalized) is not None


def _match_global_provider_connection(
    request: Request, provider: str, base_url: str
) -> tuple[Optional[int], str, dict]:
    normalized_target = _normalize_base_url(base_url)
    if not normalized_target:
        return None, "", {}

    cfg = request.app.state.config

    if provider == "openai":
        base_urls = list(getattr(cfg, "OPENAI_API_BASE_URLS", []) or [])
        keys = list(getattr(cfg, "OPENAI_API_KEYS", []) or [])
        configs = getattr(cfg, "OPENAI_API_CONFIGS", {}) or {}
    elif provider == "gemini":
        base_urls = list(getattr(cfg, "GEMINI_API_BASE_URLS", []) or [])
        keys = list(getattr(cfg, "GEMINI_API_KEYS", []) or [])
        configs = getattr(cfg, "GEMINI_API_CONFIGS", {}) or {}
    elif provider == "grok":
        base_urls = list(getattr(cfg, "GROK_API_BASE_URLS", []) or [])
        keys = list(getattr(cfg, "GROK_API_KEYS", []) or [])
        configs = getattr(cfg, "GROK_API_CONFIGS", {}) or {}
    else:
        return None, "", {}

    for idx, url in enumerate(base_urls):
        if _normalize_base_url(url) != normalized_target:
            continue

        api_key = keys[idx] if idx < len(keys) else ""
        api_config = configs.get(str(idx), configs.get(url, {})) or {}
        return idx, str(api_key or "").strip(), api_config

    return None, "", {}


def _get_connection_name(
    api_config: Optional[dict], base_url: str, *, fallback: str = ""
) -> str:
    config = api_config if isinstance(api_config, dict) else {}
    connection_name = str(config.get("remark") or "").strip()
    if connection_name:
        return connection_name

    try:
        hostname = urlparse(base_url).hostname or ""
    except Exception:
        hostname = ""

    return hostname or fallback


def _get_connection_icon(api_config: Optional[dict]) -> Optional[str]:
    config = api_config if isinstance(api_config, dict) else {}
    icon = str(config.get("icon") or "").strip()
    return icon or None


def _get_provider_user_connection_bundle(
    user, provider: str
) -> tuple[list[str], list[str], dict]:
    if provider == "openai":
        return openai_router._get_openai_user_config(user)
    if provider == "gemini":
        return gemini_router._get_gemini_user_config(user)
    if provider == "grok":
        return grok_router._get_grok_user_config(user)
    return [], [], {}


def _resolve_image_provider_source(
    request: Request,
    user,
    provider: str,
    *,
    context: str = "runtime",
    credential_source: Optional[str] = None,
    connection_index: Optional[int] = None,
    strict: bool = False,
) -> Optional[dict[str, Any]]:
    sources = _list_image_provider_sources(
        request,
        user,
        provider,
        context=context,
        credential_source=credential_source,
        connection_index=connection_index,
        strict=strict,
    )
    return sources[0] if sources else None


def _list_image_provider_sources(
    request: Request,
    user,
    provider: str,
    *,
    context: str = "runtime",
    credential_source: Optional[str] = None,
    connection_index: Optional[int] = None,
    strict: bool = False,
    prefer_shared: bool = False,
) -> list[dict[str, Any]]:
    _sync_image_provider_config_state(request)

    context = _normalize_context(context)
    credential_source = _normalize_credential_source(credential_source)
    cfg = request.app.state.config
    image_api_config: dict[str, Any] = {}

    if provider == "openai":
        shared_base_url, persisted_force_mode = _normalize_image_provider_base_url(
            getattr(cfg, "IMAGES_OPENAI_API_BASE_URL", ""),
            "/v1",
            force_mode=bool(getattr(cfg, "IMAGES_OPENAI_API_FORCE_MODE", False)),
        )
        shared_key = str(getattr(cfg, "IMAGES_OPENAI_API_KEY", "") or "").strip()
        if persisted_force_mode:
            image_api_config["force_mode"] = True
    elif provider == "gemini":
        shared_base_url, persisted_force_mode = _normalize_image_provider_base_url(
            getattr(cfg, "IMAGES_GEMINI_API_BASE_URL", ""),
            "/v1beta",
            force_mode=bool(getattr(cfg, "IMAGES_GEMINI_API_FORCE_MODE", False)),
        )
        shared_key = str(getattr(cfg, "IMAGES_GEMINI_API_KEY", "") or "").strip()
        if persisted_force_mode:
            image_api_config["force_mode"] = True
    elif provider == "grok":
        shared_base_url, _ = _normalize_image_provider_base_url(
            getattr(cfg, "IMAGES_GROK_API_BASE_URL", ""),
            "/v1",
            force_mode=False,
        )
        shared_key = str(getattr(cfg, "IMAGES_GROK_API_KEY", "") or "").strip()
    else:
        return []

    shared_global_index, shared_global_key, shared_api_config = _match_global_provider_connection(
        request, provider, shared_base_url
    )

    def _build_shared_source(
        *, for_settings: bool = False, fallback_to_global_key: bool = False
    ) -> Optional[dict[str, Any]]:
        if not shared_base_url:
            return None

        resolved_key = shared_key
        resolved_api_config = dict(image_api_config)
        if (for_settings or fallback_to_global_key) and not resolved_key and shared_global_key:
            resolved_key = shared_global_key
            if isinstance(shared_api_config, dict):
                resolved_api_config = {**shared_api_config, **resolved_api_config}
        elif not for_settings and isinstance(shared_api_config, dict):
            resolved_api_config = {**shared_api_config, **resolved_api_config}

        effective_source = "shared" if not for_settings else "settings"
        return {
            "provider": provider,
            "effective_source": effective_source,
            "base_url": shared_base_url,
            "key": resolved_key,
            "api_config": resolved_api_config,
            "connection_index": shared_global_index,
            "cache_scope": f"{provider}:{context}:shared",
            "connection_name": _get_connection_name(
                resolved_api_config,
                shared_base_url,
                fallback="Workspace Shared" if effective_source == "shared" else "",
            ),
            "connection_icon": _get_connection_icon(resolved_api_config),
        }

    if context == "settings":
        settings_source = _build_shared_source(for_settings=True, fallback_to_global_key=True)
        return [settings_source] if settings_source is not None else []

    personal_urls, personal_keys, personal_cfgs = _get_provider_user_connection_bundle(
        user, provider
    )
    shared_enabled = bool(getattr(cfg, "ENABLE_IMAGE_GENERATION_SHARED_KEY", False))
    shared_available = _shared_key_available(request, provider)

    def _resolve_personal_source(
        preferred_index: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        chosen = (
            _get_personal_connection_exact(personal_urls, personal_keys, preferred_index)
            if preferred_index is not None
            else _pick_personal_connection(personal_urls, personal_keys)
        )
        if chosen is None:
            return None

        idx, base_url, api_key = chosen
        api_config = personal_cfgs.get(str(idx), personal_cfgs.get(base_url, {})) or {}
        return {
            "provider": provider,
            "effective_source": "personal",
            "base_url": _normalize_base_url(base_url),
            "key": str(api_key or "").strip(),
            "api_config": api_config,
            "connection_index": idx,
            "cache_scope": f"{provider}:{context}:personal:{getattr(user, 'id', 'anon')}:{idx}",
            "connection_name": _get_connection_name(api_config, base_url),
            "connection_icon": _get_connection_icon(api_config),
        }

    def _list_personal_sources() -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for idx, _base_url, _api_key in _list_personal_connections(personal_urls, personal_keys):
            source = _resolve_personal_source(idx)
            if source is not None:
                sources.append(source)
        return sources

    if credential_source == "shared":
        shared_source = _build_shared_source(fallback_to_global_key=True)
        if shared_source is None or not str(shared_source.get("key") or "").strip():
            if strict:
                raise HTTPException(
                    status_code=400,
                    detail="Workspace shared key is not configured. Please contact your administrator.",
                )
            return []
        return [shared_source]

    if credential_source == "personal":
        personal_sources = (
            [_resolve_personal_source(connection_index)]
            if connection_index is not None
            else _list_personal_sources()
        )
        personal_sources = [source for source in personal_sources if source is not None]
        if personal_sources:
            return personal_sources

        if strict:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Selected personal connection is not configured. Go to Settings > Connections."
                    if connection_index is not None
                    else "No personal connection found. Go to Settings > Connections to add your key."
                ),
            )
        return []

    personal_sources = _list_personal_sources()
    if connection_index is not None:
        preferred = _resolve_personal_source(connection_index)
        if preferred is not None:
            personal_sources = [
                preferred,
                *[
                    source
                    for source in personal_sources
                    if source.get("connection_index") != preferred.get("connection_index")
                ],
            ]

    shared_source = _build_shared_source()
    sources: list[dict[str, Any]] = []
    if prefer_shared and shared_enabled and shared_available and shared_source is not None:
        sources.append(shared_source)
    sources.extend(personal_sources)
    if (
        not prefer_shared
        and shared_enabled
        and shared_available
        and shared_source is not None
    ):
        sources.append(shared_source)

    if sources:
        return sources

    if strict:
        if shared_enabled and not shared_available:
            raise HTTPException(
                status_code=400,
                detail="Shared key is enabled but not configured. Contact your administrator or set your own key in Settings > Connections.",
            )
        raise HTTPException(
            status_code=400,
            detail="No image generation connection configured. Go to Settings > Connections to add your key.",
        )

    return []


def _build_image_model_cache_key(engine: str, source: Optional[dict[str, Any]]) -> str:
    if not source:
        return f"{engine}:none"

    payload = {
        "engine": engine,
        "provider": source.get("provider"),
        "effective_source": source.get("effective_source"),
        "base_url": source.get("base_url"),
        "connection_index": source.get("connection_index"),
        "cache_scope": source.get("cache_scope"),
        "model_ids": _normalize_config_model_ids(source.get("api_config")),
        "force_mode": bool((source.get("api_config") or {}).get("force_mode")),
        "auth_type": (source.get("api_config") or {}).get("auth_type"),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _get_cached_image_models(cache_key: str) -> Optional[list[dict[str, Any]]]:
    now = time.monotonic()
    _evict_stale_image_models_cache(now)

    cached = IMAGE_MODEL_DISCOVERY_CACHE.get(cache_key)
    if not cached:
        return None

    _cached_at, models = cached

    return list(models)


def _set_cached_image_models(cache_key: str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.monotonic()
    IMAGE_MODEL_DISCOVERY_CACHE[cache_key] = (now, list(models))
    _evict_stale_image_models_cache(now)
    return models


def _build_image_model_selection_key(
    model_id: str, source: Optional[dict[str, Any]]
) -> str:
    normalized_model_id = str(model_id or "").strip()
    if not isinstance(source, dict):
        return normalized_model_id

    base_url = str(source.get("base_url") or "").strip()
    base_hash = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:12] if base_url else ""
    provider = str(source.get("provider") or "").strip()
    effective_source = str(source.get("effective_source") or "").strip()
    connection_index = source.get("connection_index")
    normalized_connection_index = (
        str(connection_index).strip() if connection_index is not None else ""
    )

    return "::".join(
        [
            provider,
            effective_source,
            normalized_connection_index,
            base_hash,
            normalized_model_id,
        ]
    )


def _split_image_model_selection_key(
    value: str,
) -> tuple[Optional[dict[str, Any]], str]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None, ""

    parts = raw_value.split("::", 4)
    if len(parts) != 5:
        return None, raw_value

    provider, effective_source, connection_index, base_hash, model_id = parts
    if not provider or not effective_source or not base_hash or not model_id:
        return None, raw_value

    return (
        {
            "provider": provider,
            "effective_source": effective_source,
            "connection_index": connection_index,
            "base_hash": base_hash,
        },
        model_id,
    )


def _image_source_matches_selection_hint(
    source: dict[str, Any], hint: Optional[dict[str, Any]]
) -> bool:
    if not hint:
        return False

    base_url = str(source.get("base_url") or "").strip()
    base_hash = hashlib.sha1(base_url.encode("utf-8")).hexdigest()[:12] if base_url else ""
    source_connection_index = source.get("connection_index")
    normalized_source_connection_index = (
        str(source_connection_index).strip()
        if source_connection_index is not None
        else ""
    )
    hint_connection_index = hint.get("connection_index")
    normalized_hint_connection_index = (
        str(hint_connection_index).strip()
        if hint_connection_index is not None
        else ""
    )

    return (
        str(source.get("provider") or "").strip() == str(hint.get("provider") or "").strip()
        and str(source.get("effective_source") or "").strip()
        == str(hint.get("effective_source") or "").strip()
        and normalized_source_connection_index
        == normalized_hint_connection_index
        and base_hash == str(hint.get("base_hash") or "").strip()
    )


def _image_source_matches_model_ref(
    source: dict[str, Any], model_ref: Optional[dict[str, Any]]
) -> bool:
    if not isinstance(source, dict) or not isinstance(model_ref, dict):
        return False

    if not _image_source_scope_matches_model_ref(source, model_ref):
        return False

    has_precise_ref = False
    connection_index = model_ref.get("connection_index")
    if connection_index is not None and str(connection_index).strip() != "":
        has_precise_ref = True
        source_connection_index = source.get("connection_index")
        normalized_source_connection_index = (
            str(source_connection_index).strip()
            if source_connection_index is not None
            else ""
        )
        if normalized_source_connection_index != str(connection_index).strip():
            return False

    connection_id = str(
        model_ref.get("connection_id") or model_ref.get("prefix_id") or ""
    ).strip()
    if connection_id:
        has_precise_ref = True
        api_config = source.get("api_config") if isinstance(source.get("api_config"), dict) else {}
        source_connection_id = str(
            source.get("connection_id")
            or api_config.get("_resolved_prefix_id")
            or api_config.get("prefix_id")
            or ""
        ).strip()
        if source_connection_id != connection_id:
            return False

    effective_source = str(
        model_ref.get("source") or model_ref.get("effective_source") or ""
    ).strip().lower()
    if effective_source in {"shared", "settings"}:
        return True

    return has_precise_ref


def _image_source_scope_matches_model_ref(
    source: dict[str, Any], model_ref: Optional[dict[str, Any]]
) -> bool:
    if not isinstance(source, dict) or not isinstance(model_ref, dict):
        return False

    provider = str(model_ref.get("provider") or "").strip().lower()
    if (
        provider in {"openai", "gemini", "grok"}
        and provider != str(source.get("provider") or "").strip().lower()
    ):
        return False

    effective_source = str(
        model_ref.get("source") or model_ref.get("effective_source") or ""
    ).strip().lower()
    if (
        effective_source
        and effective_source != str(source.get("effective_source") or "").strip().lower()
    ):
        return False

    return True


def _select_runtime_image_provider_source_from_ref(
    request: Request,
    user,
    engine: str,
    model_ref: Optional[dict[str, Any]],
    *,
    model_id: str = "",
    prefer_shared: bool = False,
) -> Optional[dict[str, Any]]:
    if not isinstance(model_ref, dict) or not model_ref:
        return None

    candidate_sources = _list_image_provider_sources(
        request,
        user,
        engine,
        context="runtime",
        credential_source="auto",
        strict=False,
        prefer_shared=prefer_shared,
    )
    connection_id = str(
        model_ref.get("connection_id") or model_ref.get("prefix_id") or ""
    ).strip()
    connection_index = model_ref.get("connection_index")
    if (
        not connection_id
        and connection_index is not None
        and str(connection_index).strip() != ""
    ):
        scoped_sources = [
            source
            for source in candidate_sources
            if _image_source_scope_matches_model_ref(source, model_ref)
        ]
        normalized_model_id = str(model_id or "").strip()
        if normalized_model_id:
            configured_matches = [
                source
                for source in scoped_sources
                if normalized_model_id
                in _normalize_config_model_ids(source.get("api_config"))
            ]
            if len(configured_matches) == 1:
                return configured_matches[0]
            if len(configured_matches) > 1:
                return None
        if len(scoped_sources) > 1:
            return None

    for source in candidate_sources:
        if _image_source_matches_model_ref(source, model_ref):
            return source

    return None


def _build_image_model_entry(
    *,
    model_id: str,
    name: Optional[str],
    generation_mode: str,
    detection_method: str,
    supports_background: bool,
    supports_batch: bool,
    size_mode: str,
    supports_image_size: bool = False,
    supports_resolution: bool = False,
    text_output_supported: bool,
    source: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    provider = source.get("provider") if isinstance(source, dict) else None
    effective_source = source.get("effective_source") if isinstance(source, dict) else None
    connection_index = source.get("connection_index") if isinstance(source, dict) else None
    api_config = source.get("api_config") if isinstance(source, dict) else {}
    connection_id = str(
        source.get("connection_id")
        or (api_config or {}).get("_resolved_prefix_id")
        or (api_config or {}).get("prefix_id")
        or ""
    ).strip() if isinstance(source, dict) else ""
    model_ref = (
        build_model_ref(
            provider=str(provider or ""),
            source=str(effective_source or ""),
            connection_id=connection_id or None,
            connection_index=connection_index,
        )
        if provider
        else {}
    )
    selection_id = (
        build_selection_id(
            provider=str(provider or ""),
            source=str(effective_source or ""),
            connection_id=connection_id or None,
            connection_index=connection_index,
            model_id=model_id,
        )
        if provider
        else model_id
    )
    return {
        "id": model_id,
        "model_id": model_id,
        "original_id": model_id,
        "name": name or model_id,
        "selection_id": selection_id,
        "selection_key": _build_image_model_selection_key(model_id, source),
        "model_ref": model_ref,
        "legacy_ids": unique_strings(
            [
                model_id,
                (
                    f"{connection_id}.{model_id}"
                    if connection_id
                    else ""
                ),
            ]
        ),
        "provider": source.get("provider") if isinstance(source, dict) else None,
        "generation_mode": generation_mode,
        "detection_method": detection_method,
        "supports_background": bool(supports_background),
        "supports_batch": bool(supports_batch),
        "size_mode": size_mode,
        "supports_image_size": bool(supports_image_size),
        "supports_resolution": bool(supports_resolution),
        "text_output_supported": bool(text_output_supported),
        "source": source.get("effective_source") if isinstance(source, dict) else None,
        "connection_index": source.get("connection_index") if isinstance(source, dict) else None,
        "connection_name": source.get("connection_name") if isinstance(source, dict) else None,
        "connection_icon": source.get("connection_icon") if isinstance(source, dict) else None,
    }


def _classify_openai_image_model(
    model: dict,
    *,
    base_url: str,
    api_config: Optional[dict] = None,
    source: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    model_id = str(model.get("id") or model.get("name") or "").strip()
    if not model_id:
        return None

    if _is_official_xai_connection(base_url):
        return _build_image_model_entry(
            model_id=model_id,
            name=str(
                model.get("name")
                or model.get("displayName")
                or model.get("display_name")
                or model_id
            ).strip(),
            generation_mode="xai_images",
            detection_method="metadata",
            supports_background=False,
            supports_batch=True,
            size_mode="aspect_ratio",
            supports_resolution=True,
            text_output_supported=False,
            source=source,
        )

    base_name = _model_id_basename(model_id).lower()
    text_blob = _model_text_blob(model)
    endpoint_blob = _extract_endpoint_blob(model)
    _input_modalities, output_modalities = _extract_modalities(model)
    output_has_image = _has_image_modality(output_modalities)
    output_has_text = _has_text_modality(output_modalities)

    negative_hint = _matches_image_negative_hint(base_name) or _matches_image_negative_hint(
        text_blob
    )
    positive_hint = _matches_image_positive_hint(base_name) or _matches_image_positive_hint(
        text_blob
    )
    images_endpoint_hint = (
        "images/generations" in endpoint_blob or "images.generate" in endpoint_blob
    )
    prefers_volcengine_images_endpoint = _is_volcengine_ark_connection(base_url) and any(
        hint in base_name or hint in text_blob for hint in VOLCENGINE_IMAGES_ENDPOINT_HINTS
    )

    if negative_hint and not (
        output_has_image
        or images_endpoint_hint
        or positive_hint
        or prefers_volcengine_images_endpoint
    ):
        return None

    if not (
        output_has_image
        or images_endpoint_hint
        or positive_hint
        or prefers_volcengine_images_endpoint
    ):
        return None

    is_dedicated_image_model = _is_openai_dedicated_image_model(base_name, text_blob)
    if images_endpoint_hint or is_dedicated_image_model or prefers_volcengine_images_endpoint:
        generation_mode = "openai_images"
        detection_method = "metadata" if images_endpoint_hint or output_has_image else "heuristic"
    else:
        generation_mode = "openai_chat_image"
        detection_method = "metadata" if output_has_image else "heuristic"

    return _build_image_model_entry(
        model_id=model_id,
        name=str(
            model.get("name")
            or model.get("displayName")
            or model.get("display_name")
            or model_id
        ).strip(),
        generation_mode=generation_mode,
        detection_method=detection_method,
        supports_background=(
            generation_mode == "openai_images" and base_name.startswith("gpt-image")
        ),
        supports_batch=generation_mode == "openai_images",
        size_mode="exact" if generation_mode == "openai_images" else "aspect_ratio",
        supports_resolution=False,
        text_output_supported=(
            output_has_text and not is_dedicated_image_model
        )
        or generation_mode == "openai_chat_image",
        source=source,
    )


def _classify_gemini_image_model(
    model: dict,
    *,
    source: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    model_id = str(model.get("id") or model.get("name") or "").replace("models/", "").strip()
    if not model_id:
        return None

    base_name = _model_id_basename(model_id).lower()
    text_blob = _model_text_blob(model)
    _input_modalities, output_modalities = _extract_modalities(model)
    methods = _extract_supported_generation_methods(model)
    output_has_image = _has_image_modality(output_modalities)
    output_has_text = _has_text_modality(output_modalities)

    negative_hint = _matches_image_negative_hint(base_name) or _matches_image_negative_hint(
        text_blob
    )
    positive_hint = _matches_image_positive_hint(base_name) or _matches_image_positive_hint(
        text_blob
    )
    has_predict = any("predict" == method or method.endswith(":predict") for method in methods)
    has_generate_content = any("generatecontent" in method for method in methods)
    looks_like_imagen = "imagen" in base_name
    supports_image_size = base_name.startswith("gemini-3")

    if negative_hint and not (positive_hint or has_predict or output_has_image):
        return None

    if (has_predict or (looks_like_imagen and not has_generate_content)) and (
        positive_hint or output_has_image or "image" in text_blob
    ):
        return _build_image_model_entry(
            model_id=model_id,
            name=str(model.get("displayName") or model.get("display_name") or model_id).strip(),
            generation_mode="gemini_predict",
            detection_method="metadata",
            supports_background=False,
            supports_batch=True,
            size_mode="unsupported",
            supports_image_size=False,
            text_output_supported=False,
            source=source,
        )

    if output_has_image:
        return _build_image_model_entry(
            model_id=model_id,
            name=str(model.get("displayName") or model.get("display_name") or model_id).strip(),
            generation_mode="gemini_generate_content_image",
            detection_method="metadata",
            supports_background=False,
            supports_batch=False,
            size_mode="aspect_ratio",
            supports_image_size=supports_image_size,
            text_output_supported=output_has_text,
            source=source,
        )

    if has_generate_content and (positive_hint or output_has_image):
        return _build_image_model_entry(
            model_id=model_id,
            name=str(model.get("displayName") or model.get("display_name") or model_id).strip(),
            generation_mode="gemini_generate_content_image",
            detection_method="metadata" if output_has_image else "heuristic",
            supports_background=False,
            supports_batch=False,
            size_mode="aspect_ratio",
            supports_image_size=supports_image_size,
            text_output_supported=True,
            source=source,
        )

    return None


def _classify_grok_image_model(
    model: dict,
    *,
    source: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    model_id = str(
        model.get("id")
        or model.get("name")
        or model.get("model")
        or model.get("slug")
        or ""
    ).strip()
    if not model_id:
        return None

    return _build_image_model_entry(
        model_id=model_id,
        name=str(
            model.get("display_name")
            or model.get("displayName")
            or model.get("name")
            or model_id
        ).strip(),
        generation_mode="xai_images",
        detection_method="metadata",
        supports_background=False,
        supports_batch=True,
        size_mode="aspect_ratio",
        supports_image_size=False,
        supports_resolution=True,
        text_output_supported=False,
        source=source,
    )


async def _read_aiohttp_body(response: aiohttp.ClientResponse) -> Any:
    try:
        return await response.json(content_type=None)
    except Exception:
        try:
            return await response.text()
        except Exception:
            return None


def _build_openai_image_headers(
    base_url: str,
    key: str,
    api_config: Optional[dict],
    user=None,
    *,
    accept: Optional[str] = "application/json",
    content_type: Optional[str] = "application/json",
) -> dict[str, str]:
    headers = openai_router._build_upstream_headers(
        base_url,
        key,
        api_config or {},
        user=user,
        accept=accept,
        content_type=content_type,
    )

    lower_to_actual = {header.lower(): header for header in headers}
    is_azure = bool((api_config or {}).get("azure")) or "openai.azure.com" in (
        base_url or ""
    )
    if is_azure and "api-key" not in lower_to_actual and key:
        auth_header = lower_to_actual.get("authorization")
        if auth_header and headers.get(auth_header, "").startswith("Bearer "):
            headers.pop(auth_header, None)
        headers["api-key"] = key

    return headers


async def _discover_openai_image_models(
    request: Request,
    user,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    model_ids = _normalize_config_model_ids(api_config)

    raw_models: list[dict[str, Any]] = []
    if model_ids:
        raw_models = [
            {"id": model_id, "name": model_id, "object": "model"} for model_id in model_ids
        ]
    elif _is_official_xai_connection(base_url):
        raw_models = await grok_router._fetch_grok_models(
            base_url,
            api_key,
            api_config,
            user=user,
        )
    else:
        models_url = openai_router._get_openai_models_url(base_url, api_config)
        headers = _build_openai_image_headers(base_url, api_key, api_config, user)

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
            trust_env=True,
        ) as session:
            async with session.get(
                models_url,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                body = await _read_aiohttp_body(response)
                if response.status != 200:
                    if openai_router._looks_like_models_listing_unsupported(
                        response.status, body
                    ):
                        return []
                    raise HTTPException(
                        status_code=400,
                        detail=build_error_detail(
                            body,
                            default=f"Failed to load image models from {models_url}",
                        ),
                    )

        normalized = openai_router._normalize_openai_models_response(body)
        if normalized is None:
            raise HTTPException(
                status_code=400,
                detail="Invalid response from upstream /models endpoint",
            )

        raw_models = normalized.get("data", []) or []

    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        classified = _classify_openai_image_model(
            raw_model,
            base_url=base_url,
            api_config=api_config,
            source=source,
        )
        if not classified:
            continue
        if classified["id"] in seen:
            continue
        seen.add(classified["id"])
        discovered.append(classified)

    return discovered


async def _discover_grok_image_models(
    request: Request,
    user,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    del request

    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    model_ids = _normalize_config_model_ids(api_config)

    raw_models: list[dict[str, Any]]
    if model_ids:
        raw_models = [{"id": model_id, "name": model_id} for model_id in model_ids]
    else:
        raw_models = await grok_router._fetch_grok_models(
            base_url,
            api_key,
            api_config,
            user=user,
        )

    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        classified = _classify_grok_image_model(raw_model, source=source)
        if not classified:
            continue
        if classified["id"] in seen:
            continue
        seen.add(classified["id"])
        discovered.append(classified)

    return discovered


async def _discover_gemini_image_models(
    request: Request,
    user,
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    del request, user

    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    model_ids = _normalize_config_model_ids(api_config)

    raw_models: list[dict[str, Any]] = []
    if model_ids:
        raw_models = [
            {
                "id": model_id,
                "name": f"models/{model_id}",
                "displayName": model_id,
                "supportedGenerationMethods": ["generateContent"],
            }
            for model_id in model_ids
        ]
    else:
        response = await gemini_router.send_get_request(f"{base_url}/models", api_key, api_config)
        if not response:
            return []
        if response.get("error"):
            raise HTTPException(
                status_code=400,
                detail=build_error_detail(
                    response,
                    default="Failed to load Gemini image models",
                ),
            )
        raw_models = response.get("models", []) or []

    discovered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        classified = _classify_gemini_image_model(raw_model, source=source)
        if not classified:
            continue
        if classified["id"] in seen:
            continue
        seen.add(classified["id"])
        discovered.append(classified)

    return discovered


def _discover_comfyui_image_models(request: Request) -> list[dict[str, Any]]:
    headers = None
    if request.app.state.config.COMFYUI_API_KEY:
        headers = {
            "Authorization": f"Bearer {request.app.state.config.COMFYUI_API_KEY}"
        }
    response = requests.get(
        url=f"{request.app.state.config.COMFYUI_BASE_URL}/object_info",
        headers=headers,
        timeout=15,
        verify=REQUESTS_VERIFY,
    )
    response.raise_for_status()
    info = response.json()

    workflow = _parse_comfyui_workflow_config(request)
    _validate_comfyui_workflow_node_mapping(
        workflow,
        request.app.state.config.COMFYUI_WORKFLOW_NODES,
        node_type="model",
    )
    models = _get_comfyui_models(
        info, workflow, request.app.state.config.COMFYUI_WORKFLOW_NODES
    )
    return [
        _build_image_model_entry(
            model_id=str(model.get("id") or "").strip(),
            name=str(model.get("name") or model.get("id") or "").strip(),
            generation_mode="comfyui",
            detection_method="metadata",
            supports_background=False,
            supports_batch=True,
            size_mode="exact",
            text_output_supported=False,
        )
        for model in models
        if str(model.get("id") or "").strip()
    ]


def _discover_automatic1111_image_models(request: Request) -> list[dict[str, Any]]:
    response = requests.get(
        url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/sd-models",
        headers={"authorization": get_automatic1111_api_auth(request)},
        timeout=15,
        verify=REQUESTS_VERIFY,
    )
    response.raise_for_status()
    raw_models = response.json()
    return [
        _build_image_model_entry(
            model_id=str(model.get("title") or "").strip(),
            name=str(model.get("model_name") or model.get("title") or "").strip(),
            generation_mode="automatic1111",
            detection_method="metadata",
            supports_background=False,
            supports_batch=True,
            size_mode="exact",
            text_output_supported=False,
        )
        for model in raw_models
        if str(model.get("title") or "").strip()
    ]


async def _discover_image_models_for_source(
    request: Request,
    user,
    engine: str,
    source: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    cache_key = _build_image_model_cache_key(engine, source)
    cached = _get_cached_image_models(cache_key)
    if cached is not None:
        return cached

    if engine == "openai":
        models = await _discover_openai_image_models(request, user, source or {})
    elif engine == "gemini":
        models = await _discover_gemini_image_models(request, user, source or {})
    elif engine == "grok":
        models = await _discover_grok_image_models(request, user, source or {})
    elif engine == "comfyui":
        models = _discover_comfyui_image_models(request)
    elif engine in ("automatic1111", ""):
        models = _discover_automatic1111_image_models(request)
    else:
        models = []

    return _set_cached_image_models(cache_key, models)


def _get_image_model_source_sort_priority(model: dict[str, Any]) -> int:
    source = str(model.get("source") or "").strip().lower()
    if source == "shared":
        return 0
    if source == "personal":
        return 1
    if source == "settings":
        return 2
    return 3


def _sort_discovered_image_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        models,
        key=lambda model: (
            str(model.get("name") or model.get("id") or "").lower(),
            _get_image_model_source_sort_priority(model),
            str(model.get("connection_name") or "").lower(),
            str(model.get("id") or "").lower(),
            str(model.get("selection_key") or "").lower(),
        ),
    )


async def _discover_image_models_from_sources(
    request: Request,
    user,
    engine: str,
    sources: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not sources:
        return []

    results = await asyncio.gather(
        *[
            _discover_image_models_for_source(request, user, engine, source)
            for source in sources
        ],
        return_exceptions=True,
    )

    aggregated_models: list[dict[str, Any]] = []
    first_error: Optional[Exception] = None

    for source, result in zip(sources, results):
        if isinstance(result, Exception):
            if first_error is None:
                first_error = result
            try:
                log.warning(
                    "Failed to discover image models for provider=%s source=%s connection_index=%s base_url=%s: %s",
                    engine,
                    source.get("effective_source"),
                    source.get("connection_index"),
                    source.get("base_url"),
                    result,
                )
            except Exception:
                pass
            continue

        aggregated_models.extend(result)

    if aggregated_models:
        return _sort_discovered_image_models(aggregated_models)

    if isinstance(first_error, HTTPException):
        raise first_error
    if first_error is not None:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(first_error))

    return []


async def _discover_image_models(
    request: Request,
    user,
    *,
    context: str = "runtime",
    credential_source: Optional[str] = None,
    connection_index: Optional[int] = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    engine = _normalize_engine(getattr(request.app.state.config, "IMAGE_GENERATION_ENGINE", ""))
    if engine not in {"openai", "gemini", "grok"}:
        return await _discover_image_models_for_source(request, user, engine, None)

    normalized_context = _normalize_context(context)
    normalized_credential_source = _normalize_credential_source(credential_source)

    if normalized_context != "runtime":
        source = _resolve_image_provider_source(
            request,
            user,
            engine,
            context=normalized_context,
            credential_source=normalized_credential_source,
            connection_index=connection_index,
            strict=strict,
        )
        if source is None:
            return []

        return await _discover_image_models_for_source(request, user, engine, source)

    sources = _list_image_provider_sources(
        request,
        user,
        engine,
        context=normalized_context,
        credential_source=normalized_credential_source,
        connection_index=connection_index,
        strict=strict,
    )
    if not sources:
        return []

    if len(sources) == 1:
        return _sort_discovered_image_models(
            await _discover_image_models_for_source(request, user, engine, sources[0])
        )

    return await _discover_image_models_from_sources(request, user, engine, sources)


async def _select_runtime_image_provider_source(
    request: Request,
    user,
    engine: str,
    *,
    selected_model: str = "",
    prefer_shared: bool = False,
) -> tuple[Optional[dict[str, Any]], Optional[list[dict[str, Any]]]]:
    candidate_sources = _list_image_provider_sources(
        request,
        user,
        engine,
        context="runtime",
        credential_source="auto",
        strict=False,
        prefer_shared=prefer_shared,
    )
    if not candidate_sources:
        return None, None

    selection_hint, normalized_model = _split_image_model_selection_key(selected_model)
    first_success: Optional[tuple[dict[str, Any], list[dict[str, Any]]]] = None
    first_error: Optional[HTTPException] = None
    matched_sources: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []

    for source in candidate_sources:
        try:
            discovered_models = await _discover_image_models_for_source(
                request, user, engine, source
            )
        except HTTPException as error:
            if first_error is None:
                first_error = error
            continue

        if first_success is None:
            first_success = (source, discovered_models)

        if normalized_model:
            has_model = any(
                str(model.get("id") or "").strip() == normalized_model
                for model in discovered_models
            )
            if not has_model:
                continue

            if selection_hint and (
                any(
                    str(model.get("id") or "").strip() == normalized_model
                    and str(model.get("selection_key") or "").strip()
                    == str(selected_model or "").strip()
                    for model in discovered_models
                )
                or _image_source_matches_selection_hint(source, selection_hint)
            ):
                return source, discovered_models

            if not selection_hint:
                matched_sources.append((source, discovered_models))
            continue

        if discovered_models:
            return source, discovered_models

    if normalized_model and not selection_hint:
        if len(matched_sources) > 1:
            raise HTTPException(
                status_code=400,
                detail=build_model_resolution_error(
                    code=AMBIGUOUS_MODEL_CODE,
                    detail=AMBIGUOUS_MODEL_DETAIL,
                    requested_model_id=selected_model,
                ),
            )
        if len(matched_sources) == 1:
            return matched_sources[0]
        if len(candidate_sources) > 1:
            raise HTTPException(
                status_code=400,
                detail=build_model_resolution_error(
                    code=AMBIGUOUS_MODEL_CODE,
                    detail=AMBIGUOUS_MODEL_DETAIL,
                    requested_model_id=selected_model,
                ),
            )

    if normalized_model and selection_hint:
        raise HTTPException(
            status_code=400,
            detail=build_model_resolution_error(
                code=STALE_MODEL_REF_CODE,
                detail=STALE_MODEL_REF_DETAIL,
                requested_model_id=selected_model,
            ),
        )

    if first_success is not None:
        return first_success

    if first_error is not None:
        raise first_error

    return candidate_sources[0], None


def _apply_image_model_regex_filter(request: Request, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regex = request.app.state.config.IMAGE_MODEL_FILTER_REGEX
    if not regex or not models:
        return models

    try:
        pattern = re.compile(regex)
        return [model for model in models if pattern.search(model.get("id", "") or "")]
    except re.error:
        log.warning(f"Invalid IMAGE_MODEL_FILTER_REGEX: {regex}")
        return models


@router.get("/config")
async def get_config(request: Request, user=Depends(get_admin_user)):
    return {
        "enabled": request.app.state.config.ENABLE_IMAGE_GENERATION,
        "engine": request.app.state.config.IMAGE_GENERATION_ENGINE,
        "prompt_generation": request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION,
        "shared_key_enabled": getattr(
            request.app.state.config, "ENABLE_IMAGE_GENERATION_SHARED_KEY", False
        ),
        "openai": {
            "OPENAI_API_BASE_URL": request.app.state.config.IMAGES_OPENAI_API_BASE_URL,
            "OPENAI_API_FORCE_MODE": getattr(
                request.app.state.config, "IMAGES_OPENAI_API_FORCE_MODE", False
            ),
            "OPENAI_API_KEY": request.app.state.config.IMAGES_OPENAI_API_KEY,
        },
        "automatic1111": {
            "AUTOMATIC1111_BASE_URL": request.app.state.config.AUTOMATIC1111_BASE_URL,
            "AUTOMATIC1111_API_AUTH": request.app.state.config.AUTOMATIC1111_API_AUTH,
            "AUTOMATIC1111_CFG_SCALE": request.app.state.config.AUTOMATIC1111_CFG_SCALE,
            "AUTOMATIC1111_SAMPLER": request.app.state.config.AUTOMATIC1111_SAMPLER,
            "AUTOMATIC1111_SCHEDULER": request.app.state.config.AUTOMATIC1111_SCHEDULER,
        },
        "comfyui": {
            "COMFYUI_BASE_URL": request.app.state.config.COMFYUI_BASE_URL,
            "COMFYUI_API_KEY": request.app.state.config.COMFYUI_API_KEY,
            "COMFYUI_WORKFLOW": request.app.state.config.COMFYUI_WORKFLOW,
            "COMFYUI_WORKFLOW_NODES": request.app.state.config.COMFYUI_WORKFLOW_NODES,
        },
        "gemini": {
            "GEMINI_API_BASE_URL": request.app.state.config.IMAGES_GEMINI_API_BASE_URL,
            "GEMINI_API_FORCE_MODE": getattr(
                request.app.state.config, "IMAGES_GEMINI_API_FORCE_MODE", False
            ),
            "GEMINI_API_KEY": request.app.state.config.IMAGES_GEMINI_API_KEY,
        },
        "grok": {
            "GROK_API_BASE_URL": request.app.state.config.IMAGES_GROK_API_BASE_URL,
            "GROK_API_KEY": request.app.state.config.IMAGES_GROK_API_KEY,
        },
    }


def _form_field_was_set(form_data: BaseModel, field_name: str) -> bool:
    fields_set = getattr(form_data, "model_fields_set", None)
    if fields_set is None:
        fields_set = getattr(form_data, "__fields_set__", set())
    return field_name in fields_set


class OpenAIConfigForm(BaseModel):
    OPENAI_API_BASE_URL: Optional[str] = None
    OPENAI_API_FORCE_MODE: Optional[bool] = None
    OPENAI_API_KEY: Optional[str] = None


class Automatic1111ConfigForm(BaseModel):
    AUTOMATIC1111_BASE_URL: Optional[str] = None
    AUTOMATIC1111_API_AUTH: Optional[str] = None
    AUTOMATIC1111_CFG_SCALE: Optional[str | float | int] = None
    AUTOMATIC1111_SAMPLER: Optional[str] = None
    AUTOMATIC1111_SCHEDULER: Optional[str] = None


class ComfyUIConfigForm(BaseModel):
    COMFYUI_BASE_URL: Optional[str] = None
    COMFYUI_API_KEY: Optional[str] = None
    COMFYUI_WORKFLOW: Optional[str] = None
    COMFYUI_WORKFLOW_NODES: Optional[list[dict]] = None


class GeminiConfigForm(BaseModel):
    GEMINI_API_BASE_URL: Optional[str] = None
    GEMINI_API_FORCE_MODE: Optional[bool] = None
    GEMINI_API_KEY: Optional[str] = None


class GrokConfigForm(BaseModel):
    GROK_API_BASE_URL: Optional[str] = None
    GROK_API_KEY: Optional[str] = None


class ConfigForm(BaseModel):
    enabled: Optional[bool] = None
    engine: Optional[str] = None
    prompt_generation: Optional[bool] = None
    shared_key_enabled: Optional[bool] = None
    openai: Optional[OpenAIConfigForm] = None
    automatic1111: Optional[Automatic1111ConfigForm] = None
    comfyui: Optional[ComfyUIConfigForm] = None
    gemini: Optional[GeminiConfigForm] = None
    grok: Optional[GrokConfigForm] = None


@router.post("/config/update")
async def update_config(
    request: Request, form_data: ConfigForm, user=Depends(get_admin_user)
):
    if _form_field_was_set(form_data, "engine") and form_data.engine is not None:
        request.app.state.config.IMAGE_GENERATION_ENGINE = form_data.engine
    if _form_field_was_set(form_data, "enabled") and form_data.enabled is not None:
        request.app.state.config.ENABLE_IMAGE_GENERATION = form_data.enabled
    if (
        _form_field_was_set(form_data, "shared_key_enabled")
        and form_data.shared_key_enabled is not None
    ):
        request.app.state.config.ENABLE_IMAGE_GENERATION_SHARED_KEY = (
            form_data.shared_key_enabled
        )
    if (
        _form_field_was_set(form_data, "prompt_generation")
        and form_data.prompt_generation is not None
    ):
        request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION = (
            form_data.prompt_generation
        )

    if form_data.openai is not None:
        openai_base_url_set = _form_field_was_set(
            form_data.openai, "OPENAI_API_BASE_URL"
        )
        openai_force_mode_set = _form_field_was_set(
            form_data.openai, "OPENAI_API_FORCE_MODE"
        )
        if openai_base_url_set or openai_force_mode_set:
            openai_base_url, openai_force_mode = _normalize_image_provider_base_url(
                form_data.openai.OPENAI_API_BASE_URL
                if openai_base_url_set
                else request.app.state.config.IMAGES_OPENAI_API_BASE_URL,
                "/v1",
                force_mode=(
                    form_data.openai.OPENAI_API_FORCE_MODE
                    if openai_force_mode_set
                    and form_data.openai.OPENAI_API_FORCE_MODE is not None
                    else getattr(
                        request.app.state.config,
                        "IMAGES_OPENAI_API_FORCE_MODE",
                        False,
                    )
                ),
            )
            request.app.state.config.IMAGES_OPENAI_API_BASE_URL = openai_base_url
            request.app.state.config.IMAGES_OPENAI_API_FORCE_MODE = openai_force_mode
        if _form_field_was_set(form_data.openai, "OPENAI_API_KEY"):
            request.app.state.config.IMAGES_OPENAI_API_KEY = (
                form_data.openai.OPENAI_API_KEY or ""
            )

    if form_data.gemini is not None:
        gemini_base_url_set = _form_field_was_set(
            form_data.gemini, "GEMINI_API_BASE_URL"
        )
        gemini_force_mode_set = _form_field_was_set(
            form_data.gemini, "GEMINI_API_FORCE_MODE"
        )
        if gemini_base_url_set or gemini_force_mode_set:
            gemini_base_url, gemini_force_mode = _normalize_image_provider_base_url(
                form_data.gemini.GEMINI_API_BASE_URL
                if gemini_base_url_set
                else request.app.state.config.IMAGES_GEMINI_API_BASE_URL,
                "/v1beta",
                force_mode=(
                    form_data.gemini.GEMINI_API_FORCE_MODE
                    if gemini_force_mode_set
                    and form_data.gemini.GEMINI_API_FORCE_MODE is not None
                    else getattr(
                        request.app.state.config,
                        "IMAGES_GEMINI_API_FORCE_MODE",
                        False,
                    )
                ),
            )
            request.app.state.config.IMAGES_GEMINI_API_BASE_URL = gemini_base_url
            request.app.state.config.IMAGES_GEMINI_API_FORCE_MODE = gemini_force_mode
        if _form_field_was_set(form_data.gemini, "GEMINI_API_KEY"):
            request.app.state.config.IMAGES_GEMINI_API_KEY = (
                form_data.gemini.GEMINI_API_KEY or ""
            )

    if form_data.grok is not None:
        if _form_field_was_set(form_data.grok, "GROK_API_BASE_URL"):
            grok_base_url, _ = _normalize_image_provider_base_url(
                form_data.grok.GROK_API_BASE_URL,
                "/v1",
                force_mode=False,
            )
            request.app.state.config.IMAGES_GROK_API_BASE_URL = grok_base_url
        if _form_field_was_set(form_data.grok, "GROK_API_KEY"):
            request.app.state.config.IMAGES_GROK_API_KEY = (
                form_data.grok.GROK_API_KEY or ""
            )

    if form_data.automatic1111 is not None:
        if _form_field_was_set(form_data.automatic1111, "AUTOMATIC1111_BASE_URL"):
            request.app.state.config.AUTOMATIC1111_BASE_URL = (
                form_data.automatic1111.AUTOMATIC1111_BASE_URL or ""
            )
        if _form_field_was_set(form_data.automatic1111, "AUTOMATIC1111_API_AUTH"):
            request.app.state.config.AUTOMATIC1111_API_AUTH = (
                form_data.automatic1111.AUTOMATIC1111_API_AUTH or ""
            )
        if _form_field_was_set(form_data.automatic1111, "AUTOMATIC1111_CFG_SCALE"):
            request.app.state.config.AUTOMATIC1111_CFG_SCALE = (
                float(form_data.automatic1111.AUTOMATIC1111_CFG_SCALE)
                if form_data.automatic1111.AUTOMATIC1111_CFG_SCALE
                else None
            )
        if _form_field_was_set(form_data.automatic1111, "AUTOMATIC1111_SAMPLER"):
            request.app.state.config.AUTOMATIC1111_SAMPLER = (
                form_data.automatic1111.AUTOMATIC1111_SAMPLER or None
            )
        if _form_field_was_set(form_data.automatic1111, "AUTOMATIC1111_SCHEDULER"):
            request.app.state.config.AUTOMATIC1111_SCHEDULER = (
                form_data.automatic1111.AUTOMATIC1111_SCHEDULER or None
            )

    if form_data.comfyui is not None:
        if _form_field_was_set(form_data.comfyui, "COMFYUI_BASE_URL"):
            request.app.state.config.COMFYUI_BASE_URL = (
                form_data.comfyui.COMFYUI_BASE_URL or ""
            ).strip("/")
        if _form_field_was_set(form_data.comfyui, "COMFYUI_API_KEY"):
            request.app.state.config.COMFYUI_API_KEY = (
                form_data.comfyui.COMFYUI_API_KEY or ""
            )
        if _form_field_was_set(form_data.comfyui, "COMFYUI_WORKFLOW"):
            request.app.state.config.COMFYUI_WORKFLOW = (
                form_data.comfyui.COMFYUI_WORKFLOW or ""
            )
        if _form_field_was_set(form_data.comfyui, "COMFYUI_WORKFLOW_NODES"):
            request.app.state.config.COMFYUI_WORKFLOW_NODES = (
                form_data.comfyui.COMFYUI_WORKFLOW_NODES or []
            )

    return {
        "enabled": request.app.state.config.ENABLE_IMAGE_GENERATION,
        "engine": request.app.state.config.IMAGE_GENERATION_ENGINE,
        "prompt_generation": request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION,
        "shared_key_enabled": request.app.state.config.ENABLE_IMAGE_GENERATION_SHARED_KEY,
        "openai": {
            "OPENAI_API_BASE_URL": request.app.state.config.IMAGES_OPENAI_API_BASE_URL,
            "OPENAI_API_FORCE_MODE": getattr(
                request.app.state.config, "IMAGES_OPENAI_API_FORCE_MODE", False
            ),
            "OPENAI_API_KEY": request.app.state.config.IMAGES_OPENAI_API_KEY,
        },
        "automatic1111": {
            "AUTOMATIC1111_BASE_URL": request.app.state.config.AUTOMATIC1111_BASE_URL,
            "AUTOMATIC1111_API_AUTH": request.app.state.config.AUTOMATIC1111_API_AUTH,
            "AUTOMATIC1111_CFG_SCALE": request.app.state.config.AUTOMATIC1111_CFG_SCALE,
            "AUTOMATIC1111_SAMPLER": request.app.state.config.AUTOMATIC1111_SAMPLER,
            "AUTOMATIC1111_SCHEDULER": request.app.state.config.AUTOMATIC1111_SCHEDULER,
        },
        "comfyui": {
            "COMFYUI_BASE_URL": request.app.state.config.COMFYUI_BASE_URL,
            "COMFYUI_API_KEY": request.app.state.config.COMFYUI_API_KEY,
            "COMFYUI_WORKFLOW": request.app.state.config.COMFYUI_WORKFLOW,
            "COMFYUI_WORKFLOW_NODES": request.app.state.config.COMFYUI_WORKFLOW_NODES,
        },
        "gemini": {
            "GEMINI_API_BASE_URL": request.app.state.config.IMAGES_GEMINI_API_BASE_URL,
            "GEMINI_API_FORCE_MODE": getattr(
                request.app.state.config, "IMAGES_GEMINI_API_FORCE_MODE", False
            ),
            "GEMINI_API_KEY": request.app.state.config.IMAGES_GEMINI_API_KEY,
        },
        "grok": {
            "GROK_API_BASE_URL": request.app.state.config.IMAGES_GROK_API_BASE_URL,
            "GROK_API_KEY": request.app.state.config.IMAGES_GROK_API_KEY,
        },
    }


def get_automatic1111_api_auth(request: Request):
    if request.app.state.config.AUTOMATIC1111_API_AUTH is None:
        return ""
    else:
        auth1111_byte_string = request.app.state.config.AUTOMATIC1111_API_AUTH.encode(
            "utf-8"
        )
        auth1111_base64_encoded_bytes = base64.b64encode(auth1111_byte_string)
        auth1111_base64_encoded_string = auth1111_base64_encoded_bytes.decode("utf-8")
        return f"Basic {auth1111_base64_encoded_string}"


@router.get("/config/url/verify")
async def verify_url(request: Request, user=Depends(get_admin_user)):
    if request.app.state.config.IMAGE_GENERATION_ENGINE == "automatic1111":
        try:
            r = requests.get(
                url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/options",
                headers={"authorization": get_automatic1111_api_auth(request)},
                verify=REQUESTS_VERIFY,
            )
            r.raise_for_status()
            return True
        except Exception:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES.INVALID_URL)
    elif request.app.state.config.IMAGE_GENERATION_ENGINE == "comfyui":

        headers = None
        if request.app.state.config.COMFYUI_API_KEY:
            headers = {
                "Authorization": f"Bearer {request.app.state.config.COMFYUI_API_KEY}"
            }

        try:
            r = requests.get(
                url=f"{request.app.state.config.COMFYUI_BASE_URL}/object_info",
                headers=headers,
                verify=REQUESTS_VERIFY,
            )
            r.raise_for_status()
            return True
        except Exception:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES.INVALID_URL)
    elif request.app.state.config.IMAGE_GENERATION_ENGINE == "grok":
        try:
            models = await grok_router._fetch_grok_models(
                request.app.state.config.IMAGES_GROK_API_BASE_URL,
                request.app.state.config.IMAGES_GROK_API_KEY,
                {},
                user=user,
            )
            if models is None:
                raise RuntimeError("No Grok models returned")
            return True
        except Exception:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES.INVALID_URL)
    else:
        return True


@router.get("/usage/config")
async def get_usage_config(request: Request, user=Depends(get_verified_user)):
    """
    Safe, non-admin config for the image generation UI (no keys).

    Used by the image generation page to:
    - decide whether the feature is enabled,
    - show engine + defaults,
    - determine if shared-key fallback is allowed/available.
    """
    if not _can_use_image_generation(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    cfg = request.app.state.config
    engine = _normalize_engine(getattr(cfg, "IMAGE_GENERATION_ENGINE", ""))
    shared_enabled = bool(getattr(cfg, "ENABLE_IMAGE_GENERATION_SHARED_KEY", False))
    shared_available = _shared_key_available(request, engine) if shared_enabled else False

    personal_supported = engine in ("openai", "gemini", "grok")
    provider = engine if personal_supported else None

    return {
        "enabled": bool(getattr(cfg, "ENABLE_IMAGE_GENERATION", False)),
        "engine": engine,
        "defaults": {
            "model": getattr(cfg, "IMAGE_GENERATION_MODEL", "") or "",
            "size": getattr(cfg, "IMAGE_SIZE", "") or "",
            "aspect_ratio": getattr(cfg, "IMAGE_ASPECT_RATIO", "") or "",
            "resolution": getattr(cfg, "IMAGE_RESOLUTION", "") or "",
            "steps": getattr(cfg, "IMAGE_STEPS", 0),
        },
        "shared_key": {"enabled": shared_enabled, "available": shared_available},
        "personal_key": {"supported": personal_supported, "provider": provider},
    }


def set_image_model(request: Request, model: str):
    log.info(f"Setting image model to {model}")
    request.app.state.config.IMAGE_GENERATION_MODEL = model
    if request.app.state.config.IMAGE_GENERATION_ENGINE in ["", "automatic1111"]:
        api_auth = get_automatic1111_api_auth(request)
        r = requests.get(
            url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/options",
            headers={"authorization": api_auth},
            verify=REQUESTS_VERIFY,
        )
        options = r.json()
        if model != options["sd_model_checkpoint"]:
            options["sd_model_checkpoint"] = model
            r = requests.post(
                url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/options",
                json=options,
                headers={"authorization": api_auth},
                verify=REQUESTS_VERIFY,
            )
    return request.app.state.config.IMAGE_GENERATION_MODEL


def get_image_model(request):
    if request.app.state.config.IMAGE_GENERATION_ENGINE == "openai":
        return (
            request.app.state.config.IMAGE_GENERATION_MODEL
            if request.app.state.config.IMAGE_GENERATION_MODEL
            else "dall-e-2"
        )
    elif request.app.state.config.IMAGE_GENERATION_ENGINE == "gemini":
        return (
            request.app.state.config.IMAGE_GENERATION_MODEL
            if request.app.state.config.IMAGE_GENERATION_MODEL
            else "imagen-3.0-generate-002"
        )
    elif request.app.state.config.IMAGE_GENERATION_ENGINE == "grok":
        return (
            request.app.state.config.IMAGE_GENERATION_MODEL
            if request.app.state.config.IMAGE_GENERATION_MODEL
            else "grok-imagine-image"
        )
    elif request.app.state.config.IMAGE_GENERATION_ENGINE == "comfyui":
        return (
            request.app.state.config.IMAGE_GENERATION_MODEL
            if request.app.state.config.IMAGE_GENERATION_MODEL
            else ""
        )
    elif (
        request.app.state.config.IMAGE_GENERATION_ENGINE == "automatic1111"
        or request.app.state.config.IMAGE_GENERATION_ENGINE == ""
    ):
        try:
            r = requests.get(
                url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/options",
                headers={"authorization": get_automatic1111_api_auth(request)},
                verify=REQUESTS_VERIFY,
            )
            options = r.json()
            return options["sd_model_checkpoint"]
        except Exception as e:
            raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(e))


class ImageConfigForm(BaseModel):
    MODEL: Optional[str] = None
    IMAGE_SIZE: Optional[str] = None
    IMAGE_ASPECT_RATIO: Optional[str] = None
    IMAGE_RESOLUTION: Optional[str] = None
    IMAGE_STEPS: Optional[int] = None
    IMAGE_MODEL_FILTER_REGEX: Optional[str] = None


@router.get("/image/config")
async def get_image_config(request: Request, user=Depends(get_admin_user)):
    return {
        "MODEL": request.app.state.config.IMAGE_GENERATION_MODEL,
        "IMAGE_SIZE": request.app.state.config.IMAGE_SIZE,
        "IMAGE_ASPECT_RATIO": getattr(request.app.state.config, "IMAGE_ASPECT_RATIO", "1:1"),
        "IMAGE_RESOLUTION": getattr(request.app.state.config, "IMAGE_RESOLUTION", "1k"),
        "IMAGE_STEPS": request.app.state.config.IMAGE_STEPS,
        "IMAGE_MODEL_FILTER_REGEX": request.app.state.config.IMAGE_MODEL_FILTER_REGEX,
    }


@router.post("/image/config/update")
async def update_image_config(
    request: Request, form_data: ImageConfigForm, user=Depends(get_admin_user)
):
    if _form_field_was_set(form_data, "MODEL"):
        model = str(form_data.MODEL or "").strip()
        if model:
            set_image_model(request, model)
        else:
            request.app.state.config.IMAGE_GENERATION_MODEL = ""

    if _form_field_was_set(form_data, "IMAGE_SIZE"):
        image_size = str(form_data.IMAGE_SIZE or "").strip().lower() or "auto"
        if image_size == "auto" or re.match(r"^\d+x\d+$", image_size):
            request.app.state.config.IMAGE_SIZE = image_size
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.INCORRECT_FORMAT("  (e.g., auto or 1024x1024)."),
            )

    if _form_field_was_set(form_data, "IMAGE_ASPECT_RATIO"):
        image_aspect_ratio = str(form_data.IMAGE_ASPECT_RATIO or "").strip() or "1:1"
        if image_aspect_ratio in GROK_IMAGE_ASPECT_RATIOS:
            request.app.state.config.IMAGE_ASPECT_RATIO = image_aspect_ratio
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.INCORRECT_FORMAT("  (invalid aspect ratio)."),
            )

    if _form_field_was_set(form_data, "IMAGE_RESOLUTION"):
        image_resolution = str(form_data.IMAGE_RESOLUTION or "").strip().lower() or "1k"
        if image_resolution in GROK_IMAGE_RESOLUTIONS:
            request.app.state.config.IMAGE_RESOLUTION = image_resolution
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.INCORRECT_FORMAT("  (invalid resolution)."),
            )

    if _form_field_was_set(form_data, "IMAGE_STEPS"):
        if form_data.IMAGE_STEPS is not None and form_data.IMAGE_STEPS >= 0:
            request.app.state.config.IMAGE_STEPS = form_data.IMAGE_STEPS
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.INCORRECT_FORMAT("  (e.g., 50)."),
            )

    if form_data.IMAGE_MODEL_FILTER_REGEX is not None:
        if form_data.IMAGE_MODEL_FILTER_REGEX:
            try:
                re.compile(form_data.IMAGE_MODEL_FILTER_REGEX)
            except re.error:
                raise HTTPException(
                    status_code=400,
                    detail=ERROR_MESSAGES.INCORRECT_FORMAT("  (invalid regex pattern)."),
                )
        request.app.state.config.IMAGE_MODEL_FILTER_REGEX = form_data.IMAGE_MODEL_FILTER_REGEX

    return {
        "MODEL": request.app.state.config.IMAGE_GENERATION_MODEL,
        "IMAGE_SIZE": request.app.state.config.IMAGE_SIZE,
        "IMAGE_ASPECT_RATIO": request.app.state.config.IMAGE_ASPECT_RATIO,
        "IMAGE_RESOLUTION": request.app.state.config.IMAGE_RESOLUTION,
        "IMAGE_STEPS": request.app.state.config.IMAGE_STEPS,
        "IMAGE_MODEL_FILTER_REGEX": request.app.state.config.IMAGE_MODEL_FILTER_REGEX,
    }


@router.get("/models")
async def get_models(
    request: Request,
    context: Optional[str] = "runtime",
    credential_source: Optional[str] = None,
    connection_index: Optional[int] = None,
    user=Depends(get_verified_user),
):
    if not _can_use_image_generation(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    try:
        models = await _discover_image_models(
            request,
            user,
            context=context,
            credential_source=credential_source,
            connection_index=connection_index,
            strict=False,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(e))

    return _apply_image_model_regex_filter(request, models)


class GenerateImageForm(BaseModel):
    model: Optional[str] = None
    model_ref: Optional[dict[str, Any]] = None
    prompt: str
    size: Optional[str] = None
    image_size: Optional[str] = None
    aspect_ratio: Optional[str] = None
    resolution: Optional[str] = None
    image_url: Optional[str] = None
    n: int = 1
    negative_prompt: Optional[str] = None
    credential_source: Optional[str] = None
    connection_index: Optional[int] = None
    steps: Optional[int] = None
    background: Optional[str] = None
    chat_generation: bool = False


def load_b64_image_data(b64_str):
    try:
        if "," in b64_str:
            header, encoded = b64_str.split(",", 1)
            mime_type = header.split(";", 1)[0]
            if mime_type.startswith("data:"):
                mime_type = mime_type[len("data:") :]
            img_data = base64.b64decode(encoded)
        else:
            mime_type = "image/png"
            img_data = base64.b64decode(b64_str)
        return img_data, mime_type
    except Exception as e:
        log.exception(f"Error loading image data: {e}")
        return None


def _normalize_url_origin(url: Optional[str]) -> Optional[tuple[str, str, Optional[int]]]:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(str(url or "").strip())
        scheme = str(parsed.scheme or "").lower()
        hostname = str(parsed.hostname or "").lower()
        if not scheme or not hostname:
            return None

        port = parsed.port
        if port is None:
            if scheme == "http":
                port = 80
            elif scheme == "https":
                port = 443

        return scheme, hostname, port
    except Exception:
        return None


def _is_allowed_internal_url(
    url: str, allowed_base_urls: Optional[list[str]] = None
) -> bool:
    target_origin = _normalize_url_origin(url)
    if not target_origin:
        return False

    for base_url in allowed_base_urls or []:
        base_origin = _normalize_url_origin(base_url)
        if base_origin and base_origin == target_origin:
            return True

    return False


def load_url_image_data(url, headers=None, allowed_base_urls: Optional[list[str]] = None):
    try:
        # Basic SSRF protection: reject private/internal URLs
        from urllib.parse import urlparse
        import ipaddress

        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        allow_internal = _is_allowed_internal_url(url, allowed_base_urls)

        if (
            not allow_internal
            and (
                hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
                or hostname.endswith(".local")
            )
        ):
            log.warning(f"Blocked SSRF attempt to internal URL: {hostname}")
            return None
        try:
            ip = ipaddress.ip_address(hostname)
            if not allow_internal and (
                ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            ):
                log.warning(f"Blocked SSRF attempt to private IP: {ip}")
                return None
        except ValueError:
            pass  # hostname is not an IP literal, OK

        if headers:
            r = requests.get(url, headers=headers, timeout=15)
        else:
            r = requests.get(url, timeout=15)

        r.raise_for_status()
        if r.headers["content-type"].split("/")[0] == "image":
            mime_type = r.headers["content-type"]
            return r.content, mime_type
        else:
            log.error("Url does not point to an image.")
            return None

    except Exception as e:
        log.exception(f"Error saving image: {e}")
        return None


def upload_image(request, image_metadata, image_data, content_type, user):
    image_format = mimetypes.guess_extension(content_type) or ".png"
    file = UploadFile(
        file=io.BytesIO(image_data),
        filename=f"generated-image{image_format}",  # will be converted to a unique ID on upload_file
        headers={
            "content-type": content_type,
        },
    )
    file_item = upload_file(request, file, user, file_metadata=image_metadata)
    url = request.app.url_path_for("get_file_content_by_id", id=file_item.id)
    return url


def _normalize_exact_image_size(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if not normalized or normalized == "auto":
        return None
    return normalized if re.match(r"^\d+x\d+$", normalized) else None


def _size_to_aspect_ratio(size: Optional[str]) -> Optional[str]:
    raw_size = _normalize_exact_image_size(size)
    if not raw_size:
        return None

    if raw_size in IMAGE_SIZE_ASPECT_RATIO_OVERRIDES:
        return IMAGE_SIZE_ASPECT_RATIO_OVERRIDES[raw_size]

    if not re.match(r"^\d+x\d+$", raw_size):
        return None

    width, height = tuple(map(int, raw_size.split("x")))
    if width <= 0 or height <= 0:
        return None

    import math

    divisor = math.gcd(width, height)
    if divisor <= 0:
        return None

    return f"{width // divisor}:{height // divisor}"


def _normalize_gemini_image_size(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    return normalized if normalized in GEMINI_IMAGE_SIZE_ORDER else None


def _normalize_gemini_aspect_ratio(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized if normalized in GEMINI_IMAGE_ASPECT_RATIOS else None


def _normalize_grok_aspect_ratio(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return normalized if normalized in GROK_IMAGE_ASPECT_RATIOS else None


def _normalize_grok_resolution(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return normalized if normalized in GROK_IMAGE_RESOLUTIONS else None


def _size_to_gemini_image_size(size: Optional[str]) -> Optional[str]:
    raw_size = _normalize_exact_image_size(size)
    if not raw_size:
        return None

    for gemini_size, pixel_size in GEMINI_IMAGE_SIZE_PIXELS.items():
        if raw_size == pixel_size.lower():
            return gemini_size

    if not re.match(r"^\d+x\d+$", raw_size):
        return None

    width, height = tuple(map(int, raw_size.split("x")))
    if width != height:
        return None

    square_map = {
        512: "512",
        1024: "1K",
        2048: "2K",
        4096: "4K",
    }
    return square_map.get(width)


def _looks_like_base64_image(value: str) -> bool:
    stripped = str(value or "").strip()
    if len(stripped) < 128:
        return False
    return re.fullmatch(r"[A-Za-z0-9+/=\s]+", stripped) is not None


def _is_valid_plain_base64(value: str) -> bool:
    stripped = re.sub(r"\s+", "", str(value or "").strip())
    if not stripped or len(stripped) % 4 != 0:
        return False
    if re.fullmatch(r"[A-Za-z0-9+/=]+", stripped) is None:
        return False
    try:
        base64.b64decode(stripped, validate=True)
        return True
    except Exception:
        return False


def _load_generated_image_from_value(
    value: Any,
    *,
    headers: Optional[dict[str, str]] = None,
    allowed_base_urls: Optional[list[str]] = None,
    mime_type_hint: Optional[str] = None,
    allow_plain_base64: bool = False,
) -> Optional[tuple[bytes, str]]:
    if not isinstance(value, str):
        return None

    raw_value = value.strip()
    if not raw_value:
        return None

    loaded = None
    if raw_value.startswith("data:"):
        loaded = load_b64_image_data(raw_value)
    elif raw_value.startswith(("http://", "https://")):
        loaded = load_url_image_data(
            raw_value, headers=headers, allowed_base_urls=allowed_base_urls
        )
    elif _looks_like_base64_image(raw_value) or (
        allow_plain_base64 and _is_valid_plain_base64(raw_value)
    ):
        loaded = load_b64_image_data(raw_value)

    if loaded and mime_type_hint:
        return loaded[0], str(mime_type_hint)

    return loaded


def _append_unique_generated_images(
    target: list[tuple[bytes, str]],
    new_images: list[tuple[bytes, str]],
    seen: set[tuple[str, str]],
) -> None:
    for image_bytes, mime_type in new_images:
        key = (hashlib.sha256(image_bytes).hexdigest(), str(mime_type or ""))
        if key in seen:
            continue
        seen.add(key)
        target.append((image_bytes, mime_type))


def _extract_generated_image_values_from_text(text: str) -> list[str]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return []

    values: list[str] = []
    seen: set[str] = set()

    def _append(value: Optional[str]) -> None:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        values.append(normalized)

    for match in MARKDOWN_IMAGE_URL_RE.findall(raw_text):
        _append(match)

    for match in DATA_IMAGE_URL_RE.findall(raw_text):
        _append(match)

    if raw_text.startswith(("http://", "https://", "data:image/")):
        _append(raw_text)

    return values


def _extract_generated_image_payload(
    item: Any,
    *,
    headers: Optional[dict[str, str]] = None,
    allowed_base_urls: Optional[list[str]] = None,
) -> Optional[tuple[bytes, str]]:
    if not isinstance(item, dict):
        return None

    mime_type_hint = (
        item.get("mime_type")
        or item.get("mimeType")
        or item.get("media_type")
        or item.get("content_type")
        or item.get("mediaType")
    )

    file_item = item.get("file")
    if isinstance(file_item, dict):
        file_mime = (
            file_item.get("mime_type")
            or file_item.get("mimeType")
            or file_item.get("media_type")
            or file_item.get("mediaType")
            or mime_type_hint
        )
        if file_mime and not str(file_mime).lower().startswith("image/"):
            return None
        for key in ("base64", "data", "url"):
            loaded = _load_generated_image_from_value(
                file_item.get(key),
                headers=headers,
                allowed_base_urls=allowed_base_urls,
                mime_type_hint=file_mime,
                allow_plain_base64=key in {"base64", "data"},
            )
            if loaded:
                return loaded

    for key in (
        "b64_json",
        "bytesBase64Encoded",
        "image_base64",
        "base64",
        "image",
        "result",
    ):
        value = item.get(key)
        if isinstance(value, dict):
            loaded = _extract_generated_image_payload(
                value,
                headers=headers,
                allowed_base_urls=allowed_base_urls,
            )
            if loaded:
                return loaded
        loaded = _load_generated_image_from_value(
            value,
            headers=headers,
            allowed_base_urls=allowed_base_urls,
            mime_type_hint=mime_type_hint,
            allow_plain_base64=True,
        )
        if loaded:
            return loaded

    image_url = item.get("image_url")
    if isinstance(image_url, dict):
        image_url = image_url.get("url") or image_url.get("image_url")
    loaded = _load_generated_image_from_value(
        image_url,
        headers=headers,
        allowed_base_urls=allowed_base_urls,
        mime_type_hint=mime_type_hint,
    )
    if loaded:
        return loaded

    loaded = _load_generated_image_from_value(
        item.get("url"),
        headers=headers,
        allowed_base_urls=allowed_base_urls,
        mime_type_hint=mime_type_hint,
    )
    if loaded:
        return loaded

    source = item.get("source")
    if isinstance(source, dict):
        source_value = source.get("data") or source.get("base64") or source.get("url")
        source_mime = source.get("media_type") or source.get("mime_type") or mime_type_hint
        loaded = _load_generated_image_from_value(
            source_value,
            headers=headers,
            allowed_base_urls=allowed_base_urls,
            mime_type_hint=source_mime,
            allow_plain_base64=True,
        )
        if loaded:
            return loaded

    generic_data = item.get("data")
    if isinstance(generic_data, str) and (
        str(item.get("type") or "").lower() in {"image", "output_image", "file"}
        or _looks_like_base64_image(generic_data)
    ):
        loaded = _load_generated_image_from_value(
            generic_data,
            headers=headers,
            allowed_base_urls=allowed_base_urls,
            mime_type_hint=mime_type_hint,
            allow_plain_base64=True,
        )
        if loaded:
            return loaded

    for text_key in ("text", "output_text", "content"):
        text_value = item.get(text_key)
        if not isinstance(text_value, str):
            continue
        for candidate in _extract_generated_image_values_from_text(text_value):
            loaded = _load_generated_image_from_value(
                candidate,
                headers=headers,
                allowed_base_urls=allowed_base_urls,
                mime_type_hint=mime_type_hint,
            )
            if loaded:
                return loaded

    return None


def _extract_generated_images_from_openai_response(
    body: Any,
    *,
    headers: Optional[dict[str, str]] = None,
    allowed_base_urls: Optional[list[str]] = None,
) -> list[tuple[bytes, str]]:
    items: list[Any] = []

    def add_message_like(value: Any) -> None:
        if not isinstance(value, dict):
            return
        if isinstance(value.get("images"), list):
            items.extend(value["images"])
        for image_key in ("image", "image_url", "file"):
            if image_key in value:
                items.append({image_key: value.get(image_key)})
        content = value.get("content")
        if isinstance(content, list):
            items.extend(content)
        elif isinstance(content, dict):
            items.append(content)
        elif isinstance(content, str):
            items.append({"text": content})

    if isinstance(body, dict):
        for key in ("data", "images", "output"):
            value = body.get(key)
            if isinstance(value, list):
                items.extend(value)
            elif isinstance(value, dict):
                items.append(value)

        for key in ("image", "image_url", "file"):
            value = body.get(key)
            if isinstance(value, dict):
                items.append(value)
            elif isinstance(value, str):
                items.append({key: value})

        for key in ("message", "delta"):
            add_message_like(body.get(key))

        for key in ("output_text", "text", "content"):
            value = body.get(key)
            if isinstance(value, str):
                items.append({"text": value})

        choices = body.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                add_message_like(choice.get("message"))
                add_message_like(choice.get("delta"))

    images: list[tuple[bytes, str]] = []
    for item in items:
        loaded = _extract_generated_image_payload(
            item, headers=headers, allowed_base_urls=allowed_base_urls
        )
        if loaded:
            images.append(loaded)

    return images


def _extract_generated_images_from_gemini_response(body: Any) -> list[tuple[bytes, str]]:
    images: list[tuple[bytes, str]] = []

    predictions = body.get("predictions", []) if isinstance(body, dict) else []
    if isinstance(predictions, list):
        for prediction in predictions:
            loaded = _extract_generated_image_payload(prediction)
            if loaded:
                images.append(loaded)

    candidates = body.get("candidates", []) if isinstance(body, dict) else []
    if isinstance(candidates, list):
        for candidate in candidates:
            try:
                _text, candidate_images, _grounding, _thinking, _tool_calls, _next_index = (
                    gemini_router._extract_content_segments(candidate)
                )
            except Exception:
                candidate_images = []

            for image in candidate_images:
                if not isinstance(image, dict):
                    continue
                data = image.get("data")
                if not data:
                    continue
                try:
                    image_bytes = base64.b64decode(data)
                except Exception:
                    continue
                images.append((image_bytes, image.get("mime_type", "image/png")))

    return images


def _get_openai_images_generation_url(base_url: str, api_config: Optional[dict]) -> str:
    normalized_url = _normalize_base_url(base_url)
    if openai_router._is_force_mode_connection(normalized_url, api_config):
        suffix = openai_router.OPENAI_CHAT_COMPLETIONS_SUFFIX
        if normalized_url.endswith(suffix):
            normalized_url = normalized_url[: -len(suffix)]

    is_azure = bool((api_config or {}).get("azure")) or "openai.azure.com" in normalized_url
    if is_azure:
        api_version = str((api_config or {}).get("api_version") or "2024-02-01")
        return f"{normalized_url}/images/generations?api-version={api_version}"
    return f"{normalized_url}/images/generations"


def _get_openai_responses_url(base_url: str) -> str:
    return f"{_normalize_base_url(base_url)}/responses"


def _post_json_with_attempts(
    attempts: list[tuple[str, dict[str, str]]],
    payload: dict[str, Any],
) -> tuple[requests.Response, str]:
    last_response: Optional[requests.Response] = None
    last_error: Optional[BaseException] = None

    for url, headers in attempts:
        try:
            response = requests.post(
                url=url,
                json=payload,
                headers=headers,
                verify=REQUESTS_VERIFY,
            )
        except Exception as error:
            last_error = error
            continue

        if response.status_code < 400:
            return response, url
        last_response = response

    if last_response is not None:
        last_response.raise_for_status()

    if last_error is not None:
        raise last_error

    raise RuntimeError("Failed to contact upstream image generation service")


def _parse_upstream_json_response(
    response: requests.Response, *, default_message: str
) -> Any:
    try:
        return response.json()
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=build_error_detail(
                getattr(response, "text", None),
                error,
                default=default_message,
            ),
        )


def _parse_openai_image_stream_response(stream_text: str) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    usage: Optional[dict[str, Any]] = None

    for raw_line in stream_text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue

        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue

        try:
            event = json.loads(data)
        except Exception:
            continue

        if not isinstance(event, dict):
            continue

        event_type = str(event.get("type") or "")
        if event_type in {"image_generation.completed", "image_edit.completed"}:
            b64_json = event.get("b64_json")
            if b64_json:
                loaded = load_b64_image_data(str(b64_json))
                if loaded:
                    image_bytes, mime_type = loaded
                    seen.add(
                        (
                            hashlib.sha256(image_bytes).hexdigest(),
                            str(mime_type or ""),
                        )
                    )
                images.append({"b64_json": b64_json})
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]

        for image_bytes, mime_type in _extract_generated_images_from_openai_response(
            event
        ):
            key = (hashlib.sha256(image_bytes).hexdigest(), str(mime_type or ""))
            if key in seen:
                continue
            seen.add(key)
            images.append(
                {"b64_json": base64.b64encode(image_bytes).decode("utf-8")}
            )

    body: dict[str, Any] = {"data": images}
    if usage is not None:
        body["usage"] = usage
    return body


def _build_openai_image_usage(body: Any, elapsed_ms: Optional[int]) -> Optional[dict[str, Any]]:
    upstream_usage = body.get("usage") if isinstance(body, dict) else None
    usage = dict(upstream_usage) if isinstance(upstream_usage, dict) else {}

    if isinstance(elapsed_ms, int) and elapsed_ms > 0:
        usage["total_duration"] = elapsed_ms * 1_000_000
        output_tokens = usage.get("output_tokens") or usage.get("completion_tokens")
        if isinstance(output_tokens, (int, float)) and output_tokens > 0:
            usage["response_token/s"] = float(output_tokens) / (elapsed_ms / 1000)
    return usage or None


async def _send_openai_image_request(
    *,
    url: str,
    headers: dict[str, str],
    request_kind: str,
    json_body: Optional[dict[str, Any]] = None,
    form_fields: Optional[dict[str, Any]] = None,
    files: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    request_headers = dict(headers)
    request_data: Optional[dict[str, str]] = None
    request_files: Optional[list[tuple[str, tuple[str, bytes, str]]]] = None
    request_json: Optional[dict[str, Any]] = None

    if request_kind == "json":
        request_json = json_body or {}
    elif request_kind == "multipart":
        request_headers = {
            key: value
            for key, value in request_headers.items()
            if key.lower() not in {"content-type", "content-length"}
        }
        request_data = {}
        for key, value in (form_fields or {}).items():
            if value is None:
                continue
            request_data[key] = str(value).lower() if isinstance(value, bool) else str(value)
        request_files = []
        for file_item in files or []:
            request_files.append(
                (
                    str(file_item.get("field_name") or "file"),
                    (
                        str(file_item.get("filename") or "file.bin"),
                        file_item.get("data") or b"",
                        str(file_item.get("mime") or "application/octet-stream"),
                    ),
                )
            )
    else:
        raise RuntimeError(f"Unsupported OpenAI image request kind: {request_kind}")

    started_at = time.monotonic()
    try:
        timeout = None if AIOHTTP_CLIENT_TIMEOUT is None else float(AIOHTTP_CLIENT_TIMEOUT)
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=True,
            verify=REQUESTS_VERIFY,
            follow_redirects=True,
        ) as client:
            expects_stream = bool(
                (request_json or {}).get("stream")
                or str((request_data or {}).get("stream") or "").lower() == "true"
            )
            if expects_stream:
                async with client.stream(
                    "POST",
                    url,
                    headers=request_headers,
                    json=request_json,
                    data=request_data,
                    files=request_files,
                ) as response:
                    response_text = "\n".join([line async for line in response.aiter_lines()])
            else:
                response = await client.post(
                    url,
                    headers=request_headers,
                    json=request_json,
                    data=request_data,
                    files=request_files,
                )
                response_text = response.text

            if expects_stream and response.status_code < 400:
                response_body = json.dumps(
                    _parse_openai_image_stream_response(response_text),
                    ensure_ascii=False,
                )
            else:
                response_body = response_text

            return {
                "status": response.status_code,
                "headers": dict(response.headers),
                "response_body": response_body,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            }
    except Exception as error:
        log.warning(
            "openai_image_request_failed request_kind=%s url=%s error_type=%s error_message=%s elapsed_ms=%s",
            request_kind,
            url,
            error.__class__.__name__,
            str(error),
            int((time.monotonic() - started_at) * 1000),
        )
        raise RuntimeError(
            build_error_detail(
                str(error),
                default="Failed to contact upstream image generation service",
            )
        ) from error


def _parse_openai_image_response_json(
    result: dict[str, Any], *, default_message: str
) -> Any:
    try:
        return json.loads(result.get("response_body") or "")
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail=build_error_detail(
                result.get("response_body"),
                error,
                default=default_message,
            ),
        )


def _raise_openai_image_request_error(
    result: dict[str, Any], *, default_message: str
) -> None:
    raise RuntimeError(
        build_error_detail(
            result.get("response_body"),
            default=default_message,
        )
    )


def _get_openai_images_edit_url(base_url: str, api_config: Optional[dict]) -> str:
    normalized_url = _normalize_base_url(base_url)
    if openai_router._is_force_mode_connection(normalized_url, api_config):
        suffix = openai_router.OPENAI_CHAT_COMPLETIONS_SUFFIX
        if normalized_url.endswith(suffix):
            normalized_url = normalized_url[: -len(suffix)]

    is_azure = bool((api_config or {}).get("azure")) or "openai.azure.com" in normalized_url
    if is_azure:
        api_version = str((api_config or {}).get("api_version") or "2024-02-01")
        return f"{normalized_url}/images/edits?api-version={api_version}"
    return f"{normalized_url}/images/edits"


def _resolve_image_edit_input(
    request: Request, user, image_url: Optional[str]
) -> Optional[tuple[str, bytes]]:
    if not image_url or not str(image_url).strip():
        return None

    resolved = resolve_chat_image_url_to_bytes(
        image_url,
        user_id=getattr(user, "id", None),
        is_admin=getattr(user, "role", "") == "admin",
    )
    if resolved:
        mime_type, data = resolved
        return mime_type, data

    token_obj = getattr(getattr(request, "state", None), "token", None)
    session_token = getattr(token_obj, "credentials", None) if token_obj else None
    auth_headers = (
        {"Authorization": f"Bearer {session_token}"} if session_token else None
    )
    loaded = load_url_image_data(
        str(image_url).strip(),
        auth_headers,
        allowed_base_urls=[str(request.base_url).rstrip("/")],
    )
    if not loaded:
        return None
    return loaded[1], loaded[0]


def _resolve_image_input_as_data_url(
    request: Request, user, image_url: Optional[str]
) -> Optional[tuple[str, bytes, str]]:
    resolved_image = _resolve_image_edit_input(request, user, image_url)
    if not resolved_image:
        return None

    image_mime, image_bytes = resolved_image
    mime_type = image_mime or "image/png"
    encoded = base64.b64encode(image_bytes).decode("utf-8")
    return mime_type, image_bytes, f"data:{mime_type};base64,{encoded}"


async def _generate_via_openai_image_edits_endpoint(
    request: Request,
    user,
    *,
    model_id: str,
    prompt: str,
    image_url: str,
    n: int,
    size: Optional[str],
    background: Optional[str],
    source: dict[str, Any],
) -> list[dict[str, str]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    headers = _build_openai_image_headers(
        base_url,
        api_key,
        api_config,
        user,
        content_type=None,
    )
    content_type_header = next(
        (header for header in list(headers.keys()) if header.lower() == "content-type"),
        None,
    )
    if content_type_header:
        headers.pop(content_type_header, None)
    upstream_model_id = _strip_connection_model_prefix(model_id, api_config)
    base_name = _model_id_basename(upstream_model_id).lower()

    resolved_image = _resolve_image_edit_input(request, user, image_url)
    if not resolved_image:
        raise HTTPException(
            status_code=400,
            detail="Failed to resolve image input for edit request.",
        )

    image_mime, image_bytes = resolved_image
    payload: dict[str, Any] = {
        "model": upstream_model_id,
        "prompt": prompt,
        "n": n,
    }
    if size:
        payload["size"] = size
    if background:
        payload["background"] = background
    if not _openai_image_model_has_default_response_format(base_name):
        payload["response_format"] = "b64_json"

    image_extension = mimetypes.guess_extension(image_mime or "image/png") or ".png"
    image_filename = f"image{image_extension}"
    image_field_name = "image"
    source_file_id = extract_chat_image_file_id(image_url)
    source_file = Files.get_file_by_id(source_file_id) if source_file_id else None
    source_file_meta = (
        source_file.meta
        if source_file and isinstance(getattr(source_file, "meta", None), dict)
        else {}
    )
    try:
        log.info(
            "openai_image_edit_input model=%s upstream_model=%s source_image_url=%s source_file_id=%s source_file_name=%s source_file_size=%s source_file_sha256=%s resolved_mime=%s resolved_bytes=%s prompt_len=%s prompt_sha256=%s payload_keys=%s multipart_file_fields=%s request_url=%s request_headers=%s server_pid=%s server_cwd=%s server_env=%s",
            model_id,
            upstream_model_id,
            str(image_url or "").strip(),
            source_file_id or "",
            source_file_meta.get("name") or getattr(source_file, "filename", None) or "",
            source_file_meta.get("size") or "",
            hashlib.sha256(image_bytes).hexdigest(),
            image_mime or "",
            len(image_bytes),
            len(str(prompt or "")),
            hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest(),
            sorted(payload.keys()),
            [image_field_name],
            _get_openai_images_edit_url(base_url, api_config),
            json.dumps(_redact_upstream_headers(headers), ensure_ascii=False, sort_keys=True),
            os.getpid(),
            os.getcwd(),
            json.dumps(_filter_process_debug_env(os.environ), ensure_ascii=False, sort_keys=True),
        )
    except Exception:
        pass
    generation_url = _get_openai_images_edit_url(base_url, api_config)
    result = await _send_openai_image_request(
        url=generation_url,
        headers=headers,
        request_kind="multipart",
        form_fields=payload,
        files=[
            {
                "field_name": image_field_name,
                "filename": image_filename,
                "mime": image_mime or "image/png",
                "data": image_bytes,
            }
        ],
    )
    try:
        log.info(
            "openai_image_request_result status=%s elapsed_ms=%s response_headers=%s",
            result.get("status"),
            result.get("elapsed_ms"),
            json.dumps(
                _redact_upstream_headers(result.get("headers") or {}),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    except Exception:
        pass

    response_status = result.get("status")
    response_body_text = str(result.get("response_body") or "")
    if not isinstance(response_status, int):
        _raise_openai_image_request_error(
            result,
            default_message="OpenAI image request did not return an HTTP status",
        )
    if response_status >= 400:
        raise HTTPException(
            status_code=response_status,
            detail=build_error_detail(
                response_body_text,
                default="Failed to edit image via upstream /images/edits",
            ),
        )

    response_body = _parse_openai_image_response_json(
        result,
        default_message="Invalid JSON response from upstream /images/edits",
    )
    usage = _build_openai_image_usage(response_body, result.get("elapsed_ms"))
    images = _extract_generated_images_from_openai_response(
        response_body,
        headers=headers,
        allowed_base_urls=[base_url],
    )
    if not images:
        raise HTTPException(
            status_code=400,
            detail="Upstream image edit completed but returned no images.",
        )

    return [
        {
            "url": upload_image(request, payload, output_image_bytes, mime_type, user),
            **({"usage": usage} if usage else {}),
        }
        for output_image_bytes, mime_type in images
    ]


async def _generate_via_openai_images_endpoint(
    request: Request,
    user,
    *,
    model_id: str,
    prompt: str,
    n: int,
    size: Optional[str],
    background: Optional[str],
    source: dict[str, Any],
) -> list[dict[str, str]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    headers = _build_openai_image_headers(base_url, api_key, api_config, user)
    upstream_model_id = _strip_connection_model_prefix(model_id, api_config)
    base_name = _model_id_basename(upstream_model_id).lower()

    payload: dict[str, Any] = {
        "model": upstream_model_id,
        "prompt": prompt,
        "n": n,
    }
    if size:
        payload["size"] = size
    if not _openai_image_model_has_default_response_format(base_name):
        payload["response_format"] = "b64_json"

    if background:
        payload["background"] = background

    generation_url = _get_openai_images_generation_url(base_url, api_config)
    result = await _send_openai_image_request(
        url=generation_url,
        headers=headers,
        request_kind="json",
        json_body=payload,
    )

    response_status = result.get("status")
    response_body_text = str(result.get("response_body") or "")
    if not isinstance(response_status, int):
        _raise_openai_image_request_error(
            result,
            default_message="OpenAI image request did not return an HTTP status",
        )
    if response_status >= 400:
        raise HTTPException(
            status_code=response_status,
            detail=build_error_detail(
                response_body_text,
                default="Failed to generate image via upstream /images/generations",
            ),
        )

    response_body = _parse_openai_image_response_json(
        result,
        default_message="Invalid JSON response from upstream /images/generations",
    )
    usage = _build_openai_image_usage(response_body, result.get("elapsed_ms"))
    images = _extract_generated_images_from_openai_response(
        response_body,
        headers=headers,
        allowed_base_urls=[base_url],
    )
    if not images:
        raise HTTPException(
            status_code=400,
            detail="Upstream image generation completed but returned no images.",
        )

    return [
        {
            "url": upload_image(request, payload, image_bytes, mime_type, user),
            **({"usage": usage} if usage else {}),
        }
        for image_bytes, mime_type in images
    ]


async def _generate_via_xai_images(
    request: Request,
    user,
    *,
    model_id: str,
    prompt: str,
    n: int,
    source: dict[str, Any],
    aspect_ratio: Optional[str] = None,
    resolution: Optional[str] = None,
    fallback_size: Optional[str] = None,
) -> list[dict[str, str]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    headers = _build_openai_image_headers(base_url, api_key, api_config, user)
    upstream_model_id = _strip_connection_model_prefix(model_id, api_config)

    effective_aspect_ratio = _normalize_grok_aspect_ratio(aspect_ratio)
    if not effective_aspect_ratio:
        effective_aspect_ratio = _normalize_grok_aspect_ratio(
            _size_to_aspect_ratio(fallback_size)
        )

    effective_resolution = _normalize_grok_resolution(resolution)

    payload: dict[str, Any] = {
        "model": upstream_model_id,
        "prompt": prompt,
        "n": max(1, int(n or 1)),
        "response_format": "b64_json",
    }
    if effective_aspect_ratio:
        payload["aspect_ratio"] = effective_aspect_ratio
    if effective_resolution:
        payload["resolution"] = effective_resolution

    generation_url = _get_openai_images_generation_url(base_url, api_config)
    response = await asyncio.to_thread(
        requests.post,
        generation_url,
        json=payload,
        headers=headers,
        verify=REQUESTS_VERIFY,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=build_error_detail(
                read_requests_error_payload(response),
                default="Failed to generate image via xAI /images/generations",
            ),
        )

    response_body = _parse_upstream_json_response(
        response,
        default_message="Invalid JSON response from xAI /images/generations",
    )
    images = _extract_generated_images_from_openai_response(
        response_body,
        headers=headers,
        allowed_base_urls=[base_url],
    )
    if not images:
        raise HTTPException(
            status_code=400,
            detail="xAI image generation completed but returned no images.",
        )

    return [
        {
            "url": upload_image(request, payload, image_bytes, mime_type, user),
        }
        for image_bytes, mime_type in images
    ]


async def _generate_via_openai_chat_image(
    request: Request,
    user,
    *,
    model_id: str,
    prompt: str,
    source: dict[str, Any],
    image_url: Optional[str] = None,
) -> list[dict[str, str]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    headers = _build_openai_image_headers(base_url, api_key, api_config, user)
    chat_url = openai_router._get_openai_chat_completions_url(base_url, api_config)
    upstream_model_id = _strip_connection_model_prefix(model_id, api_config)
    use_responses_api = openai_router._should_use_responses_api(
        base_url,
        api_config,
        upstream_model_id,
        native_web_search=False,
    )

    image_input: Optional[tuple[str, bytes, str]] = None
    if image_url:
        image_input = _resolve_image_input_as_data_url(request, user, image_url)
        if not image_input:
            raise HTTPException(
                status_code=400,
                detail="Failed to resolve image input for chat image request.",
            )

    chat_content: Any = str(prompt or "")
    responses_content: list[dict[str, Any]] = []
    prompt_text = str(prompt or "")
    if prompt_text.strip():
        responses_content.append({"type": "input_text", "text": prompt_text})
    if image_input:
        _image_mime, _image_bytes, data_url = image_input
        content_parts: list[dict[str, Any]] = []
        if prompt_text.strip():
            content_parts.append({"type": "text", "text": prompt_text})
        content_parts.append({"type": "image_url", "image_url": {"url": data_url}})
        chat_content = content_parts
        responses_content.append({"type": "input_image", "image_url": data_url})
    if not responses_content:
        responses_content.append({"type": "input_text", "text": prompt_text})

    if use_responses_api:
        request_url = _get_openai_responses_url(base_url)
        payload: dict[str, Any] = {
            "model": upstream_model_id,
            "input": [{"role": "user", "content": responses_content}],
            "tools": [{"type": "image_generation"}],
            "stream": False,
        }
    else:
        request_url = chat_url
        payload = {
            "model": upstream_model_id,
            "messages": [{"role": "user", "content": chat_content}],
            "stream": True,
        }
    upload_metadata: dict[str, Any] = {
        "model": upstream_model_id,
        "prompt": str(prompt or ""),
        "endpoint": "responses" if use_responses_api else "chat/completions",
        "stream": bool(payload.get("stream")),
        "input_image": bool(image_input),
        **({"input_image_mime": image_input[0]} if image_input else {}),
    }

    started_at = time.monotonic()
    images: list[tuple[bytes, str]] = []
    seen_images: set[tuple[str, str]] = set()
    usage: Optional[dict[str, Any]] = None

    try:
        image_mime = image_input[0] if image_input else ""
        image_bytes_len = len(image_input[1]) if image_input else 0
        log.info(
            "openai_chat_image_request model=%s upstream_model=%s input_image=%s input_image_mime=%s input_image_bytes=%s prompt_len=%s responses=%s request_url=%s",
            model_id,
            upstream_model_id,
            "yes" if image_input else "no",
            image_mime,
            image_bytes_len,
            len(str(prompt or "")),
            "yes" if use_responses_api else "no",
            request_url,
        )
    except Exception:
        pass

    try:
        timeout = None if AIOHTTP_CLIENT_TIMEOUT is None else float(AIOHTTP_CLIENT_TIMEOUT)
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=True,
            verify=REQUESTS_VERIFY,
            follow_redirects=True,
        ) as client:
            if use_responses_api:
                response = await client.post(
                    request_url,
                    headers=headers,
                    json=payload,
                )
                if response.status_code >= 400:
                    error_body = response.text
                    raise HTTPException(
                        status_code=response.status_code,
                        detail=build_error_detail(
                            error_body,
                            default="Failed to generate image via upstream /responses",
                        ),
                    )

                try:
                    response_body = response.json()
                except Exception as error:
                    raise HTTPException(
                        status_code=502,
                        detail=build_error_detail(
                            response.text,
                            error,
                            default="Invalid JSON response from upstream /responses",
                        ),
                    )

                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                usage = _build_openai_image_usage(response_body, elapsed_ms)
                _append_unique_generated_images(
                    images,
                    _extract_generated_images_from_openai_response(
                        response_body,
                        headers=headers,
                        allowed_base_urls=[base_url],
                    ),
                    seen_images,
                )

                try:
                    log.info(
                        "openai_chat_image_result status=%s elapsed_ms=%s image_count=%s responses=%s response_headers=%s",
                        response.status_code,
                        elapsed_ms,
                        len(images),
                        "yes",
                        json.dumps(
                            _redact_upstream_headers(dict(response.headers)),
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                    )
                except Exception:
                    pass
            else:
                async with client.stream(
                    "POST",
                    request_url,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        error_body = (await response.aread()).decode("utf-8", errors="replace")
                        raise HTTPException(
                            status_code=response.status_code,
                            detail=build_error_detail(
                                error_body,
                                default="Failed to generate image via upstream chat/completions",
                            ),
                        )

                    async for raw_line in response.aiter_lines():
                        line = str(raw_line or "").strip()
                        if not line or line.startswith(":") or line.startswith("event:"):
                            continue
                        data = line[len("data:") :].strip() if line.startswith("data:") else line
                        if not data or data == "[DONE]":
                            continue
                        try:
                            event = json.loads(data)
                        except Exception:
                            continue
                        if isinstance(event, dict) and isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        _append_unique_generated_images(
                            images,
                            _extract_generated_images_from_openai_response(
                                event,
                                headers=headers,
                                allowed_base_urls=[base_url],
                            ),
                            seen_images,
                        )

                    try:
                        log.info(
                            "openai_chat_image_result status=%s elapsed_ms=%s image_count=%s responses=%s response_headers=%s",
                            response.status_code,
                            int((time.monotonic() - started_at) * 1000),
                            len(images),
                            "no",
                            json.dumps(
                                _redact_upstream_headers(dict(response.headers)),
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        )
                    except Exception:
                        pass
    except HTTPException:
        raise
    except Exception as error:
        raise RuntimeError(
            build_error_detail(
                str(error),
                default=(
                    "Failed to contact upstream /responses image service"
                    if use_responses_api
                    else "Failed to contact upstream chat/completions image service"
                ),
            )
        ) from error

    if not images:
        raise HTTPException(
            status_code=400,
            detail="Upstream image generation completed but returned no images.",
        )

    return [
        {
            "url": upload_image(request, upload_metadata, image_bytes, mime_type, user),
            **({"usage": usage} if usage else {}),
        }
        for image_bytes, mime_type in images
    ]


async def _generate_via_gemini_predict(
    request: Request,
    user,
    *,
    model_id: str,
    prompt: str,
    n: int,
    source: dict[str, Any],
) -> list[dict[str, str]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    upstream_model_id = _strip_connection_model_prefix(model_id, api_config)
    attempts = gemini_router._auth_attempts(
        f"{base_url}/models/{upstream_model_id}:predict", api_key, api_config
    )
    payload = {
        "instances": {"prompt": prompt},
        "parameters": {
            "sampleCount": max(1, min(int(n or 1), 4)),
            "outputOptions": {"mimeType": "image/png"},
        },
    }

    try:
        response, _used_url = await asyncio.to_thread(
            _post_json_with_attempts,
            attempts,
            payload,
        )
    except requests.HTTPError as error:
        response = error.response
        raise HTTPException(
            status_code=response.status_code if response is not None else 500,
            detail=build_error_detail(
                read_requests_error_payload(response),
                error,
                default="Failed to generate image via Gemini predict",
            ),
        )

    response_body = _parse_upstream_json_response(
        response,
        default_message="Invalid JSON response from Gemini predict",
    )
    images = _extract_generated_images_from_gemini_response(response_body)
    if not images:
        raise HTTPException(
            status_code=400,
            detail="Gemini image generation completed but returned no images.",
        )

    return [
        {
            "url": upload_image(request, payload, image_bytes, mime_type, user),
        }
        for image_bytes, mime_type in images
    ]


async def _generate_via_gemini_generate_content(
    request: Request,
    user,
    *,
    model_id: str,
    prompt: str,
    image_size: Optional[str],
    aspect_ratio: Optional[str],
    source: dict[str, Any],
) -> list[dict[str, str]]:
    base_url = source.get("base_url") or ""
    api_key = source.get("key") or ""
    api_config = source.get("api_config") or {}
    upstream_model_id = _strip_connection_model_prefix(model_id, api_config)
    attempts = gemini_router._auth_attempts(
        f"{base_url}/models/{upstream_model_id}:generateContent", api_key, api_config
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    image_config: dict[str, Any] = {}
    normalized_aspect_ratio = _normalize_gemini_aspect_ratio(aspect_ratio)
    if normalized_aspect_ratio:
        image_config["aspectRatio"] = normalized_aspect_ratio
    normalized_image_size = _normalize_gemini_image_size(image_size)
    if normalized_image_size:
        image_config["imageSize"] = normalized_image_size
    if image_config:
        payload["generationConfig"]["imageConfig"] = image_config

    try:
        response, _used_url = await asyncio.to_thread(
            _post_json_with_attempts,
            attempts,
            payload,
        )
    except requests.HTTPError as error:
        response = error.response
        raise HTTPException(
            status_code=response.status_code if response is not None else 500,
            detail=build_error_detail(
                read_requests_error_payload(response),
                error,
                default="Failed to generate image via Gemini generateContent",
            ),
        )

    response_body = _parse_upstream_json_response(
        response,
        default_message="Invalid JSON response from Gemini generateContent",
    )
    images = _extract_generated_images_from_gemini_response(response_body)
    if not images:
        raise HTTPException(
            status_code=400,
            detail="Gemini image generation completed but returned no images.",
        )

    return [
        {
            "url": upload_image(request, payload, image_bytes, mime_type, user),
        }
        for image_bytes, mime_type in images
    ]


@router.post("/generations")
async def image_generations(
    request: Request,
    form_data: GenerateImageForm,
    user=Depends(get_verified_user),
):
    if not _can_use_image_generation(request, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if not request.app.state.config.ENABLE_IMAGE_GENERATION:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Image generation is disabled by the administrator.",
        )

    configured_size_value = (
        "auto" if form_data.chat_generation else request.app.state.config.IMAGE_SIZE
    )
    configured_size = str(configured_size_value or "auto").strip().lower() or "auto"
    effective_size = _normalize_exact_image_size(configured_size)
    if not form_data.chat_generation and form_data.size is not None:
        requested_size = str(form_data.size or "").strip().lower() or "auto"
        if requested_size == "auto":
            effective_size = None
        elif re.match(r"^\d+x\d+$", requested_size):
            effective_size = requested_size
        else:
            raise HTTPException(
                status_code=400,
                detail=ERROR_MESSAGES.INCORRECT_FORMAT("  (e.g., auto or 1024x1024)."),
            )
    requested_aspect_ratio = _normalize_gemini_aspect_ratio(form_data.aspect_ratio)
    requested_grok_aspect_ratio = _normalize_grok_aspect_ratio(
        form_data.aspect_ratio
        or getattr(request.app.state.config, "IMAGE_ASPECT_RATIO", None)
    )
    requested_image_size = _normalize_gemini_image_size(form_data.image_size)
    requested_grok_resolution = _normalize_grok_resolution(
        form_data.resolution or getattr(request.app.state.config, "IMAGE_RESOLUTION", None)
    )
    if not requested_aspect_ratio:
        requested_aspect_ratio = _size_to_aspect_ratio(effective_size)
    if not requested_grok_aspect_ratio:
        requested_grok_aspect_ratio = _normalize_grok_aspect_ratio(
            _size_to_aspect_ratio(effective_size)
        )
    if not requested_image_size:
        requested_image_size = _size_to_gemini_image_size(effective_size)

    raster_size = effective_size or "512x512"
    width, height = tuple(map(int, raster_size.split("x")))
    selected_model_value = str(
        form_data.model or request.app.state.config.IMAGE_GENERATION_MODEL or ""
    ).strip()
    _selection_hint, selected_model = _split_image_model_selection_key(selected_model_value)
    model_ref = dict(form_data.model_ref) if isinstance(form_data.model_ref, dict) else {}
    parsed_selection_id = parse_selection_id(selected_model_value)
    if parsed_selection_id:
        selected_model = parsed_selection_id["model_id"]
        model_ref = {**model_ref, **parsed_selection_id["model_ref"]}
    if not _selection_hint:
        legacy_prefix_match = re.match(
            r"^([0-9a-f]{8})\.(.+)$", selected_model, re.IGNORECASE
        )
        if legacy_prefix_match:
            model_ref.setdefault("connection_id", legacy_prefix_match.group(1))
            selected_model = legacy_prefix_match.group(2).strip()

    selected_engine = _normalize_engine(
        getattr(request.app.state.config, "IMAGE_GENERATION_ENGINE", "")
    )
    model_ref_provider = str(model_ref.get("provider") or "").strip().lower()
    if model_ref_provider in {"openai", "gemini", "grok"}:
        selected_engine = model_ref_provider
    connection_user = getattr(getattr(request, "state", None), "connection_user", None) or user

    r = None
    try:
        if selected_engine == "openai":
            credential_source = _normalize_credential_source(form_data.credential_source)
            source: Optional[dict[str, Any]] = None
            discovered_models: Optional[list[dict[str, Any]]] = None
            if credential_source == "auto" and form_data.connection_index is None:
                source = _select_runtime_image_provider_source_from_ref(
                    request,
                    connection_user,
                    "openai",
                    model_ref,
                    model_id=selected_model,
                    prefer_shared=not bool(form_data.model) and bool(selected_model),
                )
                if source is None and model_ref:
                    raise HTTPException(
                        status_code=400,
                        detail=build_model_resolution_error(
                            code=STALE_MODEL_REF_CODE,
                            detail=STALE_MODEL_REF_DETAIL,
                            requested_model_id=selected_model_value,
                        ),
                    )
                if source is None:
                    source, discovered_models = await _select_runtime_image_provider_source(
                        request,
                        connection_user,
                        "openai",
                        selected_model=selected_model_value,
                        prefer_shared=not bool(form_data.model) and bool(selected_model),
                    )

            if source is None:
                source = _resolve_image_provider_source(
                    request,
                    connection_user,
                    "openai",
                    context="runtime",
                    credential_source=credential_source,
                    connection_index=form_data.connection_index,
                    strict=True,
                )
                assert source is not None

            if discovered_models is None:
                try:
                    discovered_models = await _discover_image_models_for_source(
                        request, connection_user, "openai", source
                    )
                except HTTPException:
                    if selected_model:
                        discovered_models = []
                    else:
                        raise

            filtered_models = _apply_image_model_regex_filter(request, discovered_models)
            selected_model_meta = next(
                (model for model in filtered_models if model.get("id") == selected_model),
                None,
            ) or next(
                (model for model in discovered_models if model.get("id") == selected_model),
                None,
            )

            if not selected_model:
                if filtered_models:
                    selected_model_meta = filtered_models[0]
                elif discovered_models:
                    selected_model_meta = discovered_models[0]
                selected_model = (
                    str((selected_model_meta or {}).get("id") or "").strip()
                    or get_image_model(request)
                )

            if not selected_model_meta and selected_model:
                selected_model_meta = _classify_openai_image_model(
                    {"id": selected_model, "name": selected_model},
                    base_url=source.get("base_url") or "",
                    api_config=source.get("api_config") or {},
                    source=source,
                )

            generation_mode = (
                (selected_model_meta or {}).get("generation_mode") or "openai_images"
            )
            requested_n = (
                max(1, min(int(form_data.n or 1), 4))
                if (selected_model_meta or {}).get("supports_batch", True)
                else 1
            )
            background = (
                form_data.background
                if (selected_model_meta or {}).get("supports_background")
                else None
            )
            openai_request_size: Optional[str] = effective_size

            try:
                log.info(
                    f"image_generation user_id={user.id} engine=openai credential_source={source.get('effective_source') or credential_source} connection_index={source.get('connection_index') if source.get('connection_index') is not None else ''} model={selected_model or ''} generation_mode={generation_mode} chat_generation={'yes' if form_data.chat_generation else 'no'} edit_input={'yes' if form_data.image_url else 'no'} size={(openai_request_size or 'auto')} n={requested_n} steps={(form_data.steps if form_data.steps is not None else '')}"
                )
            except Exception:
                pass

            if generation_mode == "xai_images":
                return await _generate_via_xai_images(
                    request,
                    user,
                    model_id=selected_model,
                    prompt=form_data.prompt,
                    n=requested_n,
                    source=source,
                    aspect_ratio=requested_grok_aspect_ratio,
                    resolution=requested_grok_resolution,
                    fallback_size=effective_size,
                )

            if generation_mode == "openai_chat_image":
                return await _generate_via_openai_chat_image(
                    request,
                    user,
                    model_id=selected_model,
                    prompt=form_data.prompt,
                    source=source,
                    image_url=form_data.image_url,
                )

            if form_data.image_url:
                return await _generate_via_openai_image_edits_endpoint(
                    request,
                    user,
                    model_id=selected_model,
                    prompt=form_data.prompt,
                    image_url=form_data.image_url,
                    n=requested_n,
                    size=openai_request_size,
                    background=background,
                    source=source,
                )

            return await _generate_via_openai_images_endpoint(
                request,
                user,
                model_id=selected_model,
                prompt=form_data.prompt,
                n=requested_n,
                size=openai_request_size,
                background=background,
                source=source,
            )

        elif selected_engine == "gemini":
            credential_source = _normalize_credential_source(form_data.credential_source)
            source: Optional[dict[str, Any]] = None
            discovered_models: Optional[list[dict[str, Any]]] = None
            if credential_source == "auto" and form_data.connection_index is None:
                source = _select_runtime_image_provider_source_from_ref(
                    request,
                    connection_user,
                    "gemini",
                    model_ref,
                    model_id=selected_model,
                    prefer_shared=not bool(form_data.model) and bool(selected_model),
                )
                if source is None and model_ref:
                    raise HTTPException(
                        status_code=400,
                        detail=build_model_resolution_error(
                            code=STALE_MODEL_REF_CODE,
                            detail=STALE_MODEL_REF_DETAIL,
                            requested_model_id=selected_model_value,
                        ),
                    )
                if source is None:
                    source, discovered_models = await _select_runtime_image_provider_source(
                        request,
                        connection_user,
                        "gemini",
                        selected_model=selected_model_value,
                        prefer_shared=not bool(form_data.model) and bool(selected_model),
                    )
            if source is None:
                source = _resolve_image_provider_source(
                    request,
                    connection_user,
                    "gemini",
                    context="runtime",
                    credential_source=credential_source,
                    connection_index=form_data.connection_index,
                    strict=True,
                )
                assert source is not None

            if discovered_models is None:
                try:
                    discovered_models = await _discover_image_models_for_source(
                        request, connection_user, "gemini", source
                    )
                except HTTPException:
                    if selected_model:
                        discovered_models = []
                    else:
                        raise

            filtered_models = _apply_image_model_regex_filter(request, discovered_models)
            selected_model_meta = next(
                (model for model in filtered_models if model.get("id") == selected_model),
                None,
            ) or next(
                (model for model in discovered_models if model.get("id") == selected_model),
                None,
            )

            if not selected_model:
                if filtered_models:
                    selected_model_meta = filtered_models[0]
                elif discovered_models:
                    selected_model_meta = discovered_models[0]
                selected_model = (
                    str((selected_model_meta or {}).get("id") or "").strip()
                    or get_image_model(request)
                )

            if not selected_model_meta and selected_model:
                selected_model_meta = _classify_gemini_image_model(
                    {
                        "id": selected_model,
                        "name": f"models/{selected_model}",
                        "displayName": selected_model,
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    source=source,
                )

            generation_mode = (
                (selected_model_meta or {}).get("generation_mode") or "gemini_predict"
            )
            requested_n = (
                max(1, min(int(form_data.n or 1), 4))
                if (selected_model_meta or {}).get("supports_batch", True)
                else 1
            )

            try:
                log.info(
                    f"image_generation user_id={user.id} engine=gemini credential_source={source.get('effective_source') or credential_source} connection_index={source.get('connection_index') if source.get('connection_index') is not None else ''} model={selected_model or ''} generation_mode={generation_mode} size={effective_size} n={requested_n} steps={(form_data.steps if form_data.steps is not None else '')}"
                )
            except Exception:
                pass

            if generation_mode == "gemini_generate_content_image":
                return await _generate_via_gemini_generate_content(
                    request,
                    user,
                    model_id=selected_model,
                    prompt=form_data.prompt,
                    image_size=(
                        requested_image_size
                        if (selected_model_meta or {}).get("supports_image_size")
                        else None
                    ),
                    aspect_ratio=(
                        requested_aspect_ratio
                        if (selected_model_meta or {}).get("size_mode") == "aspect_ratio"
                        else None
                    ),
                    source=source,
                )

            return await _generate_via_gemini_predict(
                request,
                user,
                model_id=selected_model,
                prompt=form_data.prompt,
                n=requested_n,
                source=source,
            )

        elif selected_engine == "grok":
            credential_source = _normalize_credential_source(form_data.credential_source)
            source: Optional[dict[str, Any]] = None
            discovered_models: Optional[list[dict[str, Any]]] = None
            if credential_source == "auto" and form_data.connection_index is None:
                source = _select_runtime_image_provider_source_from_ref(
                    request,
                    connection_user,
                    "grok",
                    model_ref,
                    model_id=selected_model,
                    prefer_shared=not bool(form_data.model) and bool(selected_model),
                )
                if source is None and model_ref:
                    raise HTTPException(
                        status_code=400,
                        detail=build_model_resolution_error(
                            code=STALE_MODEL_REF_CODE,
                            detail=STALE_MODEL_REF_DETAIL,
                            requested_model_id=selected_model_value,
                        ),
                    )
                if source is None:
                    source, discovered_models = await _select_runtime_image_provider_source(
                        request,
                        connection_user,
                        "grok",
                        selected_model=selected_model_value,
                        prefer_shared=not bool(form_data.model) and bool(selected_model),
                    )
            if source is None:
                source = _resolve_image_provider_source(
                    request,
                    connection_user,
                    "grok",
                    context="runtime",
                    credential_source=credential_source,
                    connection_index=form_data.connection_index,
                    strict=True,
                )
                assert source is not None

            if discovered_models is None:
                try:
                    discovered_models = await _discover_image_models_for_source(
                        request, connection_user, "grok", source
                    )
                except HTTPException:
                    if selected_model:
                        discovered_models = []
                    else:
                        raise

            filtered_models = _apply_image_model_regex_filter(request, discovered_models)
            selected_model_meta = next(
                (model for model in filtered_models if model.get("id") == selected_model),
                None,
            ) or next(
                (model for model in discovered_models if model.get("id") == selected_model),
                None,
            )

            if not selected_model:
                if filtered_models:
                    selected_model_meta = filtered_models[0]
                elif discovered_models:
                    selected_model_meta = discovered_models[0]
                selected_model = (
                    str((selected_model_meta or {}).get("id") or "").strip()
                    or get_image_model(request)
                )

            if not selected_model_meta and selected_model:
                selected_model_meta = _classify_grok_image_model(
                    {"id": selected_model, "name": selected_model},
                    source=source,
                )

            requested_n = (
                max(1, min(int(form_data.n or 1), 4))
                if (selected_model_meta or {}).get("supports_batch", True)
                else 1
            )

            try:
                log.info(
                    f"image_generation user_id={user.id} engine=grok credential_source={source.get('effective_source') or credential_source} connection_index={source.get('connection_index') if source.get('connection_index') is not None else ''} model={selected_model or ''} aspect_ratio={requested_grok_aspect_ratio or ''} resolution={requested_grok_resolution or ''} n={requested_n}"
                )
            except Exception:
                pass

            return await _generate_via_xai_images(
                request,
                user,
                model_id=selected_model,
                prompt=form_data.prompt,
                n=requested_n,
                source=source,
                aspect_ratio=requested_grok_aspect_ratio,
                resolution=requested_grok_resolution,
                fallback_size=effective_size,
            )

        elif selected_engine == "comfyui":
            workflow = _parse_comfyui_workflow_config(request)
            _validate_comfyui_workflow_node_mapping(
                workflow, request.app.state.config.COMFYUI_WORKFLOW_NODES
            )
            data = {
                "prompt": form_data.prompt,
                "width": width,
                "height": height,
                "n": form_data.n,
            }

            if request.app.state.config.IMAGE_STEPS is not None:
                data["steps"] = request.app.state.config.IMAGE_STEPS

            # Per-request steps override
            if form_data.steps is not None:
                data["steps"] = form_data.steps

            if form_data.negative_prompt is not None:
                data["negative_prompt"] = form_data.negative_prompt

            form_data = ComfyUIGenerateImageForm(
                **{
                    "workflow": ComfyUIWorkflow(
                        **{
                            "workflow": json.dumps(workflow),
                            "nodes": request.app.state.config.COMFYUI_WORKFLOW_NODES,
                        }
                    ),
                    **data,
                }
            )
            res = await comfyui_generate_image(
                selected_model,
                form_data,
                user.id,
                request.app.state.config.COMFYUI_BASE_URL,
                request.app.state.config.COMFYUI_API_KEY,
            )
            log.debug(f"res: {res}")

            images = []

            for image in res["data"]:
                headers = None
                if request.app.state.config.COMFYUI_API_KEY:
                    headers = {
                        "Authorization": f"Bearer {request.app.state.config.COMFYUI_API_KEY}"
                    }

                loaded_image = load_url_image_data(
                    image["url"],
                    headers,
                    allowed_base_urls=[request.app.state.config.COMFYUI_BASE_URL],
                )
                if not loaded_image:
                    raise HTTPException(
                        status_code=400,
                        detail="Failed to load generated image from ComfyUI output URL.",
                    )
                image_data, content_type = loaded_image
                url = upload_image(
                    request,
                    form_data.model_dump(exclude_none=True),
                    image_data,
                    content_type,
                    user,
                )
                images.append({"url": url})
            return images
        elif selected_engine == "automatic1111" or selected_engine == "":
            if form_data.model:
                set_image_model(request, form_data.model)

            data = {
                "prompt": form_data.prompt,
                "batch_size": form_data.n,
                "width": width,
                "height": height,
            }

            if request.app.state.config.IMAGE_STEPS is not None:
                data["steps"] = request.app.state.config.IMAGE_STEPS

            # Per-request steps override
            if form_data.steps is not None:
                data["steps"] = form_data.steps

            if form_data.negative_prompt is not None:
                data["negative_prompt"] = form_data.negative_prompt

            if request.app.state.config.AUTOMATIC1111_CFG_SCALE:
                data["cfg_scale"] = request.app.state.config.AUTOMATIC1111_CFG_SCALE

            if request.app.state.config.AUTOMATIC1111_SAMPLER:
                data["sampler_name"] = request.app.state.config.AUTOMATIC1111_SAMPLER

            if request.app.state.config.AUTOMATIC1111_SCHEDULER:
                data["scheduler"] = request.app.state.config.AUTOMATIC1111_SCHEDULER

            # Use asyncio.to_thread for the requests.post call
            r = await asyncio.to_thread(
                requests.post,
                url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/txt2img",
                json=data,
                headers={"authorization": get_automatic1111_api_auth(request)},
                verify=REQUESTS_VERIFY,
            )

            res = r.json()
            log.debug(f"res: {res}")

            images = []

            for image in res["images"]:
                image_data, content_type = load_b64_image_data(image)
                url = upload_image(
                    request,
                    {**data, "info": res["info"]},
                    image_data,
                    content_type,
                    user,
                )
                images.append({"url": url})
            return images
    except HTTPException:
        raise
    except Exception as e:
        error = (
            build_error_detail(read_requests_error_payload(r), e)
            if r is not None
            else build_error_detail(e)
        )
        raise HTTPException(status_code=400, detail=ERROR_MESSAGES.DEFAULT(error))
