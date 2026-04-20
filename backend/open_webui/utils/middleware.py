import time
import logging
import sys
import os
import base64

import asyncio
from aiocache import cached
from typing import Any, Optional
import random
import json
import html
import inspect
import re
import ast
import hashlib
from difflib import get_close_matches
from urllib.parse import urlparse

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from starlette.responses import Response, StreamingResponse


from open_webui.models.chats import Chats
from open_webui.models.files import Files
from open_webui.models.users import Users
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
    get_active_status_by_user_id,
)
from open_webui.routers.tasks import (
    generate_queries,
    generate_title,
    generate_image_prompt,
    generate_chat_tags,
    generate_follow_ups,
)
from open_webui.routers.retrieval import (
    ProcessFileForm,
    SearchForm,
    process_file,
    process_web_search,
)
from open_webui.routers.images import image_generations, GenerateImageForm
from open_webui.routers.openai import (
    NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG,
    NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
    NATIVE_FILE_INPUT_STATUS_SUPPORTED,
    NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED,
    NATIVE_FILE_INPUT_STATUS_UPSTREAM_REJECTED,
    _get_cached_openai_file_id,
    _get_native_file_input_capability,
    _get_openai_file_cache_key,
    _get_openai_user_config,
    _probe_responses_support_for_native_file_inputs,
    _set_cached_openai_file_id,
    _resolve_openai_connection_by_model_id,
    _should_use_responses_api,
    _upload_file_to_openai,
)
from open_webui.routers.gemini import (
    _get_gemini_user_config,
    _resolve_gemini_connection_by_model_id,
)

from open_webui.utils.webhook import post_webhook


from open_webui.models.users import UserModel
from open_webui.models.functions import Functions
from open_webui.models.models import Models

from open_webui.retrieval.utils import get_sources_from_files
from open_webui.retrieval.runtime import get_safe_reranking_runtime
from open_webui.retrieval.document_processing import (
    DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    FILE_PROCESSING_MODE_FULL_CONTEXT,
    FILE_PROCESSING_MODE_NATIVE_FILE,
    FILE_PROCESSING_MODE_RETRIEVAL,
    get_file_effective_processing_mode,
    get_requested_processing_mode_for_file_item,
    normalize_document_provider,
    normalize_file_processing_mode,
)
from open_webui.storage.provider import Storage


from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.native_web_search import (
    build_native_web_search_support,
    resolve_effective_native_web_search_support,
    strip_model_prefix,
)
from open_webui.utils.payload import merge_additive_payload_fields
from open_webui.utils.skill_runtime import (
    build_skill_system_prompt,
    build_skill_tool_context,
    get_selected_skill_context,
)
from open_webui.utils.task import (
    get_task_model_id,
    prompt_template,
    rag_template,
    tools_function_calling_generation_template,
)
from open_webui.utils.misc import (
    deep_update,
    get_message_list,
    add_or_update_system_message,
    add_or_update_user_message,
    get_last_user_message,
    get_last_assistant_message,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
)
from open_webui.utils.tools import (
    get_tools,
    get_tool_servers_data,
    validate_tool_ids_access,
)
from open_webui.utils.mcp import (
    extract_selected_mcp_indices,
    get_mcp_servers_cached_data,
    get_mcp_servers_data,
)
from open_webui.utils.builtin_tools import get_builtin_tools
from open_webui.utils.user_tools import (
    MAX_TOOL_CALL_ROUNDS_DEFAULT,
    get_user_mcp_server_connections,
    get_user_native_tools_config,
    get_user_tool_server_connections,
    MAX_TOOL_CALL_ROUNDS_KEY,
    normalize_max_tool_call_rounds,
)
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    get_sorted_filters,
    process_filter_functions,
)
from open_webui.utils.shared_tool_runtime import (
    ensure_selected_shared_tool_runtime_loaded,
)
from open_webui.utils.code_interpreter import execute_code_jupyter

from open_webui.tasks import create_task, set_current_task_blocks_completion

from open_webui.config import (
    CACHE_DIR,
    DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
    DEFAULT_CODE_INTERPRETER_PROMPT,
)
from open_webui.env import (
    SRC_LOG_LEVELS,
    GLOBAL_LOG_LEVEL,
    BYPASS_MODEL_ACCESS_CONTROL,
    ENABLE_REALTIME_CHAT_SAVE,
)
from open_webui.constants import TASKS


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

# Regex to strip <details type="reasoning"> blocks from stored message content.
# This prevents thinking/reasoning tokens from leaking into the model's context
# (KV cache protection for reasoning models).
_REASONING_DETAILS_RE = re.compile(
    r'<details\s+type="reasoning"[^>]*>.*?</details>', re.DOTALL | re.IGNORECASE
)


def strip_reasoning_details(content: str) -> str:
    """Remove <details type="reasoning"> blocks from message content."""
    if not content or not isinstance(content, str):
        return content or ""
    return _REASONING_DETAILS_RE.sub("", content).strip()


def _summarize_form_data_for_debug(form_data: Any) -> dict[str, Any]:
    if not isinstance(form_data, dict):
        return {"type": type(form_data).__name__}

    messages = form_data.get("messages")
    files = form_data.get("files")
    features = form_data.get("features")

    return {
        "model": form_data.get("model"),
        "message_count": len(messages) if isinstance(messages, list) else 0,
        "file_count": len(files) if isinstance(files, list) else 0,
        "feature_keys": sorted(features.keys()) if isinstance(features, dict) else [],
    }


def _truncate_text(value: Any, max_chars: int) -> str:
    try:
        text = value if isinstance(value, str) else ("" if value is None else str(value))
    except Exception:
        text = ""
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


_DATA_IMAGE_MARKDOWN_RE = re.compile(
    r"!\[[^\]]*\]\((data:image/[^)]+)\)", re.IGNORECASE
)


def _build_image_data_url(mime_type: Any, data: Any) -> str:
    normalized_mime_type = str(mime_type or "").strip() or "image/png"
    normalized_data = str(data or "").strip()
    if not normalized_data:
        return ""
    return f"data:{normalized_mime_type};base64,{normalized_data}"


def _normalize_message_files(files: Any) -> list[dict]:
    normalized: list[dict] = []
    seen: set[str] = set()

    def add_file(file_item: Any):
        item: Optional[dict] = None

        if isinstance(file_item, str):
            raw_value = file_item.strip()
            if raw_value.startswith("data:image/"):
                item = {"type": "image", "url": raw_value}
        elif isinstance(file_item, dict):
            candidate = json.loads(json.dumps(file_item, ensure_ascii=False, default=str))
            file_type = str(candidate.get("type") or "").strip().lower()
            file_url = str(candidate.get("url") or "").strip()
            if file_url or file_type or candidate.get("name") or candidate.get("id"):
                item = candidate

        if not item:
            return

        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            return
        seen.add(key)
        normalized.append(item)

    def walk(value: Any):
        if isinstance(value, list):
            for item in value:
                walk(item)
            return

        if isinstance(value, str):
            text, extracted_files = _extract_image_files_from_text(value)
            for extracted in extracted_files:
                add_file(extracted)
            if text.startswith("data:image/"):
                add_file(text)
            return

        if isinstance(value, dict):
            value_type = str(value.get("type") or "").strip().lower()
            image_url = value.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url") or image_url.get("image_url")
            elif not isinstance(image_url, str):
                image_url = None

            if value_type in {"image", "image_url", "input_image", "output_image"}:
                url = str(image_url or value.get("url") or "").strip()
                if not url:
                    url = _build_image_data_url(
                        value.get("mime_type") or value.get("mimeType"),
                        value.get("data") or value.get("b64_json"),
                    )
                if url:
                    add_file({"type": "image", "url": url})
                return

            add_file(value)
            return

        add_file(value)

    walk(files)
    return normalized


def _merge_message_files(existing: Any, incoming: Any) -> list[dict]:
    return _normalize_message_files([*(existing or []), *(incoming or [])])


def normalize_message_files(files: Any) -> list[dict]:
    # Backward-compatible alias for call sites introduced before helpers were
    # promoted to module scope with private-prefixed names.
    return _normalize_message_files(files)


def merge_message_files(existing: Any, incoming: Any) -> list[dict]:
    # Keep the legacy helper name available so older orchestration paths do not
    # fail with NameError during tool result post-processing.
    return _merge_message_files(existing, incoming)


def _extract_image_files_from_text(text: Any) -> tuple[str, list[dict]]:
    if not isinstance(text, str) or not text:
        return ("" if text is None else str(text or "")), []

    if "![" not in text or "data:image/" not in text:
        return text, []

    files: list[dict] = []

    def replace(match: re.Match) -> str:
        url = str(match.group(1) or "").strip()
        if url:
            files.append({"type": "image", "url": url})
        return ""

    cleaned = _DATA_IMAGE_MARKDOWN_RE.sub(replace, text)
    return cleaned.strip(), _merge_message_files(None, files)


def _filter_response_image_files(
    files: Any, *, allow_base64_image_url_conversion: bool = True
) -> list[dict]:
    normalized = _normalize_message_files(files)
    if allow_base64_image_url_conversion:
        return normalized

    filtered: list[dict] = []
    for file_item in normalized:
        if not isinstance(file_item, dict):
            continue

        url = str(file_item.get("url") or "").strip()
        if url.startswith("data:image/"):
            continue
        filtered.append(file_item)

    return filtered


def _extract_top_level_response_image_files(
    value: Any, *, allow_base64_image_url_conversion: bool = True
) -> list[dict]:
    if not isinstance(value, dict):
        return []

    image_payloads: list[Any] = []

    if "image_url" in value:
        image_payloads.append({"type": "image_url", "image_url": value.get("image_url")})

    raw_images = value.get("images")
    if isinstance(raw_images, dict):
        raw_images = [raw_images]
    if isinstance(raw_images, list):
        for image_item in raw_images:
            if isinstance(image_item, dict):
                image_type = str(image_item.get("type") or "").strip().lower()
                if image_type in {"image", "image_url", "input_image", "output_image"}:
                    image_payloads.append(image_item)
                elif "image_url" in image_item:
                    image_payloads.append(
                        {"type": "image_url", "image_url": image_item.get("image_url")}
                    )
                elif image_item.get("url"):
                    image_payloads.append({"type": "image", "url": image_item.get("url")})
            elif isinstance(image_item, str):
                image_payloads.append({"type": "image", "url": image_item})

    return _filter_response_image_files(
        image_payloads,
        allow_base64_image_url_conversion=allow_base64_image_url_conversion,
    )


def _extract_stream_content_and_files(
    value: Any, *, allow_base64_image_url_conversion: bool = True
) -> tuple[str, list[dict]]:
    if isinstance(value, list):
        parts: list[str] = []
        files: list[dict] = []

        for item in value:
            text_part, file_part = _extract_stream_content_and_files(
                item,
                allow_base64_image_url_conversion=allow_base64_image_url_conversion,
            )
            if text_part:
                parts.append(text_part)
            if file_part:
                files = _merge_message_files(files, file_part)

        return "".join(parts), files

    if isinstance(value, dict):
        item_type = str(value.get("type") or "").strip().lower()
        if item_type in {"image", "image_url", "input_image", "output_image"}:
            files = _filter_response_image_files(
                value,
                allow_base64_image_url_conversion=allow_base64_image_url_conversion,
            )
            return "", files

        top_level_files = _extract_top_level_response_image_files(
            value,
            allow_base64_image_url_conversion=allow_base64_image_url_conversion,
        )

        nested_content = value.get("content")
        if isinstance(nested_content, (list, dict)):
            text_part, nested_files = _extract_stream_content_and_files(
                nested_content,
                allow_base64_image_url_conversion=allow_base64_image_url_conversion,
            )
            return text_part, _merge_message_files(top_level_files, nested_files)

        text_value = (
            value.get("text")
            or value.get("content")
            or value.get("value")
            or ""
        )
        text_part, nested_files = _extract_stream_content_and_files(
            text_value,
            allow_base64_image_url_conversion=allow_base64_image_url_conversion,
        )
        return text_part, _merge_message_files(top_level_files, nested_files)

    if isinstance(value, str):
        return _extract_image_files_from_text(value)

    return "", []


def _has_nonempty_text_content(content_blocks: Any) -> bool:
    if not isinstance(content_blocks, list):
        return False

    for block in content_blocks:
        if not isinstance(block, dict):
            continue

        block_type = str(block.get("type") or "").strip().lower()
        if block_type in {"text", "reasoning"} and str(block.get("content") or "").strip():
            return True

    return False


def _has_visible_message_files(message_files: Any) -> bool:
    if not isinstance(message_files, list):
        return False

    for file_item in message_files:
        if not isinstance(file_item, dict):
            continue

        if str(file_item.get("type") or "").strip().lower() != "image":
            continue

        if any(str(file_item.get(key) or "").strip() for key in ("url", "id", "name")):
            return True

    return False


def _has_visible_assistant_output(content_blocks: Any, message_files: Any) -> bool:
    return _has_nonempty_text_content(content_blocks) or _has_visible_message_files(
        message_files
    )


def _consume_stream_image_delta(
    pending_images: dict[str, dict[str, Any]], image_delta: Any
) -> Optional[dict]:
    if not isinstance(image_delta, dict):
        return None

    image_id = str(image_delta.get("id") or "").strip()
    if not image_id:
        return None

    pending = pending_images.get(image_id) or {
        "mime_type": "image/png",
        "parts": [],
    }

    mime_type = str(image_delta.get("mime_type") or "").strip() or pending.get(
        "mime_type", "image/png"
    )
    data = str(image_delta.get("data") or "")
    final = image_delta.get("final") is True

    pending["mime_type"] = mime_type
    if data:
        pending.setdefault("parts", []).append(data)
    pending_images[image_id] = pending

    if not final:
        return None

    pending_images.pop(image_id, None)

    url = _build_image_data_url(
        mime_type,
        "".join(pending.get("parts") or []),
    )
    if not url:
        return None

    return {"type": "image", "url": url}


# ── API error payload builder ────────────────────────────────────────
_REQUEST_INCOMPATIBLE_PATTERNS = (
    "unknown parameter",
    "unsupported parameter",
    "unsupported value",
    "unsupported type",
    "invalid value",
    "invalid_request_error",
    "schema",
    "not supported",
    "not support",
    "unexpected field",
    "extra fields",
    "tool_choice",
    "stream_options",
    "response_format",
    "input_image",
    "input_file",
    "messages[",
    "tools[",
)

_API_ERROR_FAMILY_CONFIG: dict[str, dict[str, Any]] = {
    "request_incompatible": {
        "reasons": [
            "api_request_interface_mismatch",
            "api_request_model_mismatch",
            "api_request_feature_not_supported",
            "api_proxy_schema_mismatch",
        ],
        "suggestion": "check_request_compatibility",
    },
    "auth_error": {
        "reasons": ["api_auth_error"],
        "suggestion": "check_api_key",
    },
    "model_not_found": {
        "reasons": ["api_model_not_found"],
        "suggestion": "retry_or_switch",
    },
    "rate_limited": {
        "reasons": ["api_rate_limit", "api_quota_exceeded"],
        "suggestion": "wait_retry",
    },
    "timeout": {
        "reasons": ["api_request_timeout"],
        "suggestion": "wait_retry",
    },
    "upstream_service_error": {
        "reasons": ["api_upstream_error", "proxy_error"],
        "suggestion": "wait_retry",
    },
}

_API_ERROR_STATUS_RE = (
    re.compile(r"Responses API upstream error \((\d{3})\)", re.IGNORECASE),
    re.compile(r"\bHTTP\s*(\d{3})\b", re.IGNORECASE),
    re.compile(r"\bstatus\s*[:=]\s*(\d{3})\b", re.IGNORECASE),
)

_API_ERROR_HOST_RE = re.compile(
    r"\bfrom\s+([a-z0-9.-]+\.[a-z]{2,})(?::\d+)?\b", re.IGNORECASE
)

_API_ERROR_PARAM_RES = (
    re.compile(r"Unknown parameter:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"['\"]param['\"]\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"\bparameter\s*[:=]?\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
)

_API_ERROR_CODE_RES = (
    re.compile(r"['\"]code['\"]\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE),
    re.compile(r"\berror code\s*[:=]?\s*([a-z0-9_.-]+)\b", re.IGNORECASE),
)


def _parse_error_message(raw: str) -> str:
    """Extract a human-readable message from a raw error string.

    Upstream errors are often JSON like:
      '{"error":{"type":"...","message":"the real message"},"type":"error"}'
    This helper extracts the inner message field when possible.
    """
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                return err.get("message") or err.get("msg") or raw
            return parsed.get("message") or parsed.get("msg") or raw
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


def _extract_api_error_status_from_code(code: Any) -> int | None:
    if not isinstance(code, str):
        return None
    if not code.startswith("http_"):
        return None
    try:
        status = int(code[5:])
    except ValueError:
        return None
    return status if status >= 400 else None


def _extract_api_error_status_from_text(raw: str) -> int | None:
    if not raw:
        return None

    for pattern in _API_ERROR_STATUS_RE:
        match = pattern.search(raw)
        if not match:
            continue
        try:
            status = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if status >= 400:
            return status
    return None


def _resolve_api_error_status(
    error_payload: dict,
    *,
    raw_message: str,
    status_override: int | None = None,
) -> int | None:
    raw_status = error_payload.get("status")
    if isinstance(raw_status, int) and raw_status >= 400:
        return raw_status

    code_status = _extract_api_error_status_from_code(error_payload.get("code"))
    if code_status is not None:
        return code_status

    text_status = _extract_api_error_status_from_text(raw_message)
    if text_status is not None:
        return text_status

    if isinstance(status_override, int) and status_override >= 400:
        return status_override

    return None


def _classify_api_error_family(*, status: int | None, raw_message: str) -> str:
    text = (raw_message or "").lower()

    if status in {401, 403}:
        return "auth_error"
    if status == 404:
        return "model_not_found"
    if status == 429:
        return "rate_limited"
    if status in {408, 504}:
        return "timeout"
    if status == 400:
        return "request_incompatible"
    if status is not None and 500 <= status <= 599:
        return "upstream_service_error"
    if any(pattern in text for pattern in _REQUEST_INCOMPATIBLE_PATTERNS):
        return "request_incompatible"
    return "upstream_service_error"


def _extract_api_error_interface_hint(raw_message: str) -> str | None:
    text = (raw_message or "").lower()
    if "responses api" in text or "/responses" in text:
        return "Responses API"
    if "chat completions" in text or "/chat/completions" in text:
        return "Chat Completions API"
    if "embeddings" in text or "/embeddings" in text:
        return "Embeddings API"
    return None


def _extract_api_error_host_hint(raw_message: str) -> str | None:
    if not raw_message:
        return None
    match = _API_ERROR_HOST_RE.search(raw_message)
    return match.group(1) if match else None


def _extract_api_error_param_hint(raw_message: str) -> str | None:
    if not raw_message:
        return None
    for pattern in _API_ERROR_PARAM_RES:
        match = pattern.search(raw_message)
        if match:
            return match.group(1)
    return None


def _extract_api_error_code_hint(raw_message: str) -> str | None:
    if not raw_message:
        return None
    for pattern in _API_ERROR_CODE_RES:
        match = pattern.search(raw_message)
        if match:
            return match.group(1)
    return None


def _build_api_error_title(*, family: str, status: int | None) -> str:
    if family == "request_incompatible":
        return (
            f"携带参数与当前接口不兼容，请求被拒绝（HTTP {status}）"
            if status
            else "携带参数与当前接口不兼容，请求被拒绝"
        )
    if family == "auth_error":
        return (
            f"上游服务鉴权失败，请求被拒绝（HTTP {status}）"
            if status
            else "上游服务鉴权失败，请求被拒绝"
        )
    if family == "model_not_found":
        return (
            f"上游模型或接口不存在，请求失败（HTTP {status}）"
            if status
            else "上游模型或接口不存在，请求失败"
        )
    if family == "rate_limited":
        return (
            f"请求过于频繁，已被上游限流（HTTP {status}）"
            if status
            else "请求过于频繁，已被上游限流"
        )
    if family == "timeout":
        return (
            f"上游服务响应超时，请求失败（HTTP {status}）"
            if status
            else "上游服务响应超时，请求失败"
        )
    return (
        f"上游服务返回错误，请求失败（HTTP {status}）"
        if status
        else "上游服务返回错误，请求失败"
    )


def _build_api_error_body(*, family: str, evidence: dict[str, str]) -> str:
    summaries = {
        "request_incompatible": "上游接口已拒绝这次请求。检测到这更像是请求字段、参数或调用方式与当前接口不兼容。",
        "auth_error": "请求已发送到上游，但鉴权未通过。请检查 API Key、权限或连接配置。",
        "model_not_found": "上游没有找到当前模型或目标接口。请检查模型名、连接配置，或确认该模型在上游可用。",
        "rate_limited": "上游在短时间内拒绝了更多请求。通常与速率限制、额度或并发控制有关。",
        "timeout": "请求已发出，但上游在限定时间内没有完成响应。",
        "upstream_service_error": "上游服务返回了异常响应。问题可能来自上游服务本身、代理转发，或当前请求与上游能力不完全匹配。",
    }

    detail_lines: list[str] = []
    interface_hint = evidence.get("interface")
    parameter_hint = evidence.get("parameter")
    host_hint = evidence.get("host")
    code_hint = evidence.get("code")

    if interface_hint:
        detail_lines.append(f"接口线索：{interface_hint}")
    if parameter_hint:
        detail_lines.append(f"参数线索：{parameter_hint}")
    elif code_hint:
        detail_lines.append(f"错误代码：{code_hint}")
    if host_hint and len(detail_lines) < 2:
        detail_lines.append(f"上游服务：{host_hint}")

    summary = summaries.get(family, summaries["upstream_service_error"])
    return "\n".join([summary, *detail_lines]) if detail_lines else summary


def _build_api_error_reasons_and_suggestion(
    *, family: str, status: int | None
) -> tuple[list[str], str]:
    config = _API_ERROR_FAMILY_CONFIG.get(
        family, _API_ERROR_FAMILY_CONFIG["upstream_service_error"]
    )
    reasons = list(config.get("reasons", []))
    suggestion = str(config.get("suggestion", "retry_or_switch"))

    if family == "upstream_service_error":
        if status == 500:
            reasons = ["api_server_error", "proxy_error"]
            suggestion = "retry_or_switch"
        elif status in {502, 503, 504}:
            reasons = ["api_upstream_error", "proxy_error"]
            suggestion = "wait_retry"

    return reasons, suggestion


def _normalize_api_error(error: Any, status_override: int | None = None) -> dict:
    candidate = error
    if isinstance(candidate, str):
        candidate = _safe_json_loads(candidate)

    if isinstance(candidate, dict):
        nested = candidate.get("error")
        normalized = dict(nested) if isinstance(nested, dict) else dict(candidate)
    else:
        normalized = {}

    if not normalized:
        message = _truncate_text(error, 4000).strip()
        normalized = {"message": message} if message else {}

    if not normalized.get("message"):
        for key in ("detail", "msg", "error_description"):
            value = normalized.get(key)
            if isinstance(value, str) and value.strip():
                normalized["message"] = value.strip()
                break

    if status_override is not None:
        if status_override >= 400:
            normalized.setdefault("status", status_override)
            code_str = str(normalized.get("code", "") or "")
            if not code_str.startswith("http_"):
                if code_str:
                    normalized.setdefault("upstream_code", code_str)
                normalized["code"] = f"http_{status_override}"
        else:
            normalized.setdefault("transport_status", status_override)

    return normalized


WEB_SEARCH_MODE_OFF = "off"
WEB_SEARCH_MODE_HALO = "halo"
WEB_SEARCH_MODE_NATIVE = "native"
WEB_SEARCH_MODE_AUTO = "auto"
WEB_SEARCH_RUNTIME_MODES = {
    WEB_SEARCH_MODE_HALO,
    WEB_SEARCH_MODE_NATIVE,
    WEB_SEARCH_MODE_AUTO,
}

_NATIVE_WEB_SEARCH_RETRY_PATTERNS = (
    "responses api",
    "/responses",
    "responses_api_error",
    "web_search",
    "web search",
    "browser_search",
    "google search",
    "googlesearch",
    "tools[",
    "tool type",
    "tool_choice",
    "unsupported",
    "not support",
    "unknown parameter",
    "invalid value",
    "schema",
)

_NATIVE_FILE_INPUT_RETRY_PATTERNS = (
    "native file inputs request failed",
    "native file inputs",
    "input_file",
    "file_id",
    "files api",
    "responses api",
    "/responses",
    "invalid file",
    "unsupported file",
    "unsupported document",
    "file upload",
    "purpose",
    "user_data",
)

_ANTHROPIC_NATIVE_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "text/plain",
}
_ANTHROPIC_NATIVE_IMAGE_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}


def _get_builtin_web_tools_to_suppress(effective_mode: Any) -> set[str]:
    mode = _normalize_web_search_mode(effective_mode, WEB_SEARCH_MODE_OFF)

    if mode == WEB_SEARCH_MODE_HALO:
        return {"search_web"}

    if mode == WEB_SEARCH_MODE_NATIVE:
        return {"search_web", "fetch_url", "fetch_url_rendered"}

    return set()


def _parse_generation_response_payload(response: Any) -> Optional[dict]:
    if isinstance(response, dict):
        return response

    if isinstance(response, Response):
        body = getattr(response, "body", None)
        if body is None:
            return None

        if isinstance(body, memoryview):
            body = body.tobytes()

        if isinstance(body, bytes):
            raw = body.decode("utf-8", errors="replace")
        else:
            raw = str(body)

        if not raw:
            return None

        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return {"detail": raw}

    return None


def _get_generation_response_error(payload: Optional[dict]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None

    detail = payload.get("detail")
    if isinstance(detail, str) and detail.strip():
        return detail.strip()

    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message") or error.get("detail")
        if isinstance(message, str) and message.strip():
            return message.strip()

    return None


def _get_generation_response_content(response: Any) -> Optional[str]:
    payload = _parse_generation_response_payload(response)
    if not isinstance(payload, dict):
        return None

    choices = payload.get("choices")
    if (
        isinstance(choices, list)
        and choices
        and isinstance(choices[0], dict)
        and isinstance(choices[0].get("message"), dict)
    ):
        content = choices[0]["message"].get("content")
        return content if isinstance(content, str) else None

    error_detail = _get_generation_response_error(payload)
    if error_detail:
        raise ValueError(error_detail)

    return None


def _normalize_web_search_mode(
    value: Any, fallback: str = WEB_SEARCH_MODE_OFF
) -> str:
    if value is True or value == "always":
        return WEB_SEARCH_MODE_HALO

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {
            WEB_SEARCH_MODE_OFF,
            WEB_SEARCH_MODE_HALO,
            WEB_SEARCH_MODE_NATIVE,
            WEB_SEARCH_MODE_AUTO,
        }:
            return normalized

    return fallback


def _resolve_native_web_search_support(
    request: Request, user: UserModel, model: dict, model_id: str
) -> dict:
    provider = str(model.get("owned_by") or "").lower().strip()
    connection_user = (
        getattr(getattr(request, "state", None), "connection_user", None) or user
    )

    if provider == "openai":
        base_urls, keys, cfgs = _get_openai_user_config(connection_user)
        if not base_urls:
            return {
                "provider": provider,
                "status": "unsupported",
                "reason": "connection_not_found",
                "source": "provider_resolution",
                "supported": False,
                "can_attempt": False,
            }

        _idx, url, _key, api_config = _resolve_openai_connection_by_model_id(
            model_id, base_urls, keys, cfgs
        )
        if not url:
            return {
                "provider": provider,
                "status": "unsupported",
                "reason": "connection_not_found",
                "source": "provider_resolution",
                "supported": False,
                "can_attempt": False,
            }

        connection_support = build_native_web_search_support(
            "openai",
            url=url,
            api_config=api_config,
            connection_name=model.get("connection_name"),
        )
        support = resolve_effective_native_web_search_support(
            connection_support,
            provider="openai",
            model_id=model.get("original_id") or strip_model_prefix(
                model_id, api_config.get("_resolved_prefix_id")
            ),
            model_name=model.get("name") or "",
        )
        support["url"] = url
        support["api_config"] = api_config
        return support

    if provider in {"google", "gemini"} or model.get("gemini") is not None:
        base_urls, keys, cfgs = _get_gemini_user_config(connection_user)
        if not base_urls:
            return {
                "provider": "gemini",
                "status": "unsupported",
                "reason": "connection_not_found",
                "source": "provider_resolution",
                "supported": False,
                "can_attempt": False,
            }

        _idx, url, _key, api_config = _resolve_gemini_connection_by_model_id(
            model_id, base_urls, keys, cfgs
        )
        if not url:
            return {
                "provider": "gemini",
                "status": "unsupported",
                "reason": "connection_not_found",
                "source": "provider_resolution",
                "supported": False,
                "can_attempt": False,
            }

        connection_support = build_native_web_search_support(
            "gemini",
            url=url,
            api_config=api_config,
            connection_name=model.get("connection_name"),
        )
        support = resolve_effective_native_web_search_support(
            connection_support,
            provider="gemini",
            model_id=model.get("original_id") or strip_model_prefix(
                model_id, api_config.get("_resolved_prefix_id")
            ),
            model_name=model.get("name") or "",
        )
        support["url"] = url
        support["api_config"] = api_config
        return support

    return resolve_effective_native_web_search_support(
        build_native_web_search_support(provider or "unknown"),
        provider=provider or "unknown",
        model_id=model.get("original_id") or model_id,
        model_name=model.get("name") or "",
    )


def _resolve_web_search_strategy(
    request: Request, user: UserModel, model: dict, model_id: str, features: dict
) -> dict:
    features = features if isinstance(features, dict) else {}
    requested_mode = _normalize_web_search_mode(
        features.get("web_search_mode"),
        WEB_SEARCH_MODE_HALO if features.get("web_search") else WEB_SEARCH_MODE_OFF,
    )

    halo_enabled = bool(request.app.state.config.ENABLE_WEB_SEARCH)
    native_enabled = bool(
        getattr(request.app.state.config, "ENABLE_NATIVE_WEB_SEARCH", False)
    )

    native_support = _resolve_native_web_search_support(request, user, model, model_id)
    effective_mode = WEB_SEARCH_MODE_OFF
    notification = None

    if requested_mode == WEB_SEARCH_MODE_HALO:
        effective_mode = WEB_SEARCH_MODE_HALO if halo_enabled else WEB_SEARCH_MODE_OFF
    elif requested_mode == WEB_SEARCH_MODE_NATIVE:
        if native_enabled and (
            native_support.get("supported") or native_support.get("can_attempt")
        ):
            effective_mode = WEB_SEARCH_MODE_NATIVE
        elif halo_enabled:
            effective_mode = WEB_SEARCH_MODE_HALO
            notification = (
                "Current model or connection does not support native web search. "
                "Falling back to HaloWebUI web search."
            )
    elif requested_mode == WEB_SEARCH_MODE_AUTO:
        if native_enabled and native_support.get("supported"):
            effective_mode = WEB_SEARCH_MODE_NATIVE
        elif halo_enabled:
            effective_mode = WEB_SEARCH_MODE_HALO

    return {
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "native_support": native_support,
        "allow_halo_retry": effective_mode == WEB_SEARCH_MODE_NATIVE and halo_enabled,
        "notification": notification,
    }


def should_retry_native_web_search_with_halo(metadata: dict, error: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    if not metadata.get("allow_native_web_search_halo_fallback"):
        return False

    text = str(error or "").strip().lower()
    if not text:
        return False

    return any(pattern in text for pattern in _NATIVE_WEB_SEARCH_RETRY_PATTERNS)


def should_retry_native_file_inputs_with_rag(metadata: dict, error: Any) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("disable_native_file_inputs"):
        return False
    if not metadata.get("native_file_input_file_ids"):
        return False

    text = str(error or "").strip().lower()
    if not text:
        return False

    return any(pattern in text for pattern in _NATIVE_FILE_INPUT_RETRY_PATTERNS)


def _build_api_error_payload(
    error: dict | str,
    model_id: str,
    *,
    status_override: int | None = None,
) -> dict:
    """Build a structured error payload from a stream API error chunk.

    *error* is the dict captured from the SSE error event, e.g.:
      {"message": "...", "type": "api_error", "code": "http_500"}
    """
    error_payload = _normalize_api_error(error, status_override=status_override)

    raw_message = str(error_payload.get("message", ""))
    raw_message = _truncate_text(raw_message, 4000).strip()
    parsed_message = _parse_error_message(raw_message)
    status = _resolve_api_error_status(
        error_payload, raw_message=raw_message, status_override=status_override
    )
    family = _classify_api_error_family(status=status, raw_message=raw_message)
    evidence = {
        key: value
        for key, value in {
            "interface": _extract_api_error_interface_hint(raw_message),
            "host": _extract_api_error_host_hint(raw_message),
            "parameter": _extract_api_error_param_hint(raw_message),
            "code": _extract_api_error_code_hint(raw_message),
        }.items()
        if value
    }
    title = _build_api_error_title(family=family, status=status)
    body = _build_api_error_body(family=family, evidence=evidence)
    reasons, suggestion = _build_api_error_reasons_and_suggestion(
        family=family, status=status
    )

    return {
        "type": "api_error",
        "model_id": model_id,
        "family": family,
        "status": status,
        "title": title,
        "body": body,
        "content": title,
        "reasons": list(reasons),
        "suggestion": suggestion,
        "raw_message": raw_message or parsed_message,
        "evidence": evidence,
    }


def _extract_files_from_messages(messages: list[dict]) -> list[dict]:
    """Extract file references from chat messages for tool access.

    Scans messages for file attachments (images, documents) and resolves
    them via the Files model to provide {id, filename, content_type, size}.
    """
    file_ids_seen: set[str] = set()
    result: list[dict] = []

    for msg in messages:
        # Files can be attached at message level as "files" key
        msg_files = msg.get("files") or []
        if not isinstance(msg_files, list):
            continue
        for f in msg_files:
            if not isinstance(f, dict):
                continue
            file_id = f.get("id") or f.get("file_id") or ""
            if not file_id or file_id in file_ids_seen:
                continue
            file_ids_seen.add(file_id)

            # Try to resolve full file info from the database
            try:
                file_obj = Files.get_file_by_id(file_id)
                if file_obj:
                    meta = file_obj.meta or {}
                    result.append({
                        "id": file_id,
                        "filename": file_obj.filename,
                        "content_type": meta.get("content_type", ""),
                        "size": meta.get("size", 0),
                        "processing_mode": f.get("processing_mode") or meta.get("processing_mode"),
                        "context": f.get("context"),
                    })
                else:
                    # File not in DB -- use whatever info we have from the message
                    result.append({
                        "id": file_id,
                        "filename": f.get("name") or f.get("filename") or "",
                        "content_type": f.get("content_type") or f.get("type") or "",
                        "size": f.get("size") or 0,
                        "processing_mode": f.get("processing_mode"),
                        "context": f.get("context"),
                    })
            except Exception:
                result.append({
                    "id": file_id,
                    "filename": f.get("name") or f.get("filename") or "",
                    "content_type": f.get("content_type") or f.get("type") or "",
                    "size": f.get("size") or 0,
                    "processing_mode": f.get("processing_mode"),
                    "context": f.get("context"),
                })

    return result


def _get_attachment_file_id(file_item: Any) -> str:
    if not isinstance(file_item, dict):
        return ""
    file_id = file_item.get("id")
    if not file_id and isinstance(file_item.get("file"), dict):
        file_id = file_item["file"].get("id")
    return str(file_id or "").strip()


def _get_file_item_processing_mode(file_item: Any) -> str:
    file_id = _get_attachment_file_id(file_item)
    file_obj = Files.get_file_by_id(file_id) if file_id else None
    return get_requested_processing_mode_for_file_item(
        file_item,
        file_obj=file_obj,
        default_mode=FILE_PROCESSING_MODE_RETRIEVAL,
    )


def _is_native_file_input_candidate(file_item: Any) -> bool:
    if not isinstance(file_item, dict):
        return False
    if file_item.get("type") != "file":
        return False
    if file_item.get("source") == "knowledge":
        return False
    if _get_file_item_processing_mode(file_item) != FILE_PROCESSING_MODE_NATIVE_FILE:
        return False
    return bool(_get_attachment_file_id(file_item))


def _filter_rag_files_for_native_file_inputs(
    files: list[dict], native_file_input_ids: set[str]
) -> list[dict]:
    if not files or not native_file_input_ids:
        return files
    return [
        file_item
        for file_item in files
        if _get_attachment_file_id(file_item) not in native_file_input_ids
    ]


def _merge_message_content_with_native_file_inputs(
    content: Any, file_parts: list[dict]
) -> Any:
    if not file_parts:
        return content

    if isinstance(content, list):
        return [*content, *file_parts]

    parts: list[dict] = []
    if isinstance(content, str):
        if content:
            parts.append({"type": "text", "text": content})
    elif content is not None:
        text = str(content)
        if text:
            parts.append({"type": "text", "text": text})

    parts.extend(file_parts)
    return parts


def _get_native_file_input_diagnostics(metadata: dict) -> dict[str, dict[str, Any]]:
    diagnostics = metadata.get("native_file_input_diagnostics")
    if isinstance(diagnostics, dict):
        return diagnostics

    diagnostics = {}
    metadata["native_file_input_diagnostics"] = diagnostics
    return diagnostics


def _build_native_file_input_diagnostic(
    status: str,
    *,
    reason: str,
    message: str,
    detail: Optional[str] = None,
    source: str = "",
) -> dict[str, Any]:
    diagnostic = {
        "status": status,
        "reason": reason,
        "message": message,
    }
    if detail:
        diagnostic["detail"] = detail
    if source:
        diagnostic["source"] = source
    return diagnostic


def _set_native_file_input_diagnostic(
    metadata: dict,
    *,
    file_id: str,
    file_name: str,
    diagnostic: dict[str, Any],
) -> None:
    diagnostics = _get_native_file_input_diagnostics(metadata)
    diagnostics[file_id] = {
        "file_id": file_id,
        "file_name": file_name,
        **diagnostic,
    }


def _get_native_file_input_diagnostic(
    metadata: dict,
    file_id: str,
) -> Optional[dict[str, Any]]:
    diagnostics = metadata.get("native_file_input_diagnostics")
    if not isinstance(diagnostics, dict):
        return None
    value = diagnostics.get(file_id)
    return value if isinstance(value, dict) else None


def _native_file_provider_display_name(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized == DOCUMENT_PROVIDER_LOCAL_DEFAULT:
        return "local document parsing"
    if not normalized:
        return "document parsing"
    return normalized


def _join_processing_notices(*parts: Optional[str]) -> Optional[str]:
    normalized_parts: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized_parts.append(text)
    return " ".join(normalized_parts) if normalized_parts else None


def _build_native_file_input_fallback_message(
    diagnostic: dict[str, Any],
    *,
    file_name: Optional[str] = None,
    fallback_provider: Optional[str] = None,
    used_cached_context: bool = False,
) -> str:
    base_message = str(
        diagnostic.get("message") or "Native file inputs could not be used."
    ).strip()
    display_name = str(file_name or diagnostic.get("file_name") or "").strip()
    if display_name:
        base_message = f"{base_message} ({display_name})"

    if used_cached_context:
        return f"{base_message} Using the existing full document context instead."

    provider_name = _native_file_provider_display_name(str(fallback_provider or ""))
    if provider_name == "local document parsing":
        return f"{base_message} Falling back to local document parsing for this request."

    return (
        f"{base_message} Local document parsing failed, so this request used "
        f"{provider_name} instead."
    )


def _persist_native_file_fallback_notice(
    file_id: str,
    *,
    diagnostic: dict[str, Any],
    fallback_provider: str,
    fallback_reason: Optional[str] = None,
) -> None:
    file_obj = Files.get_file_by_id(file_id)
    if not file_obj:
        return

    notice = _build_native_file_input_fallback_message(
        diagnostic,
        file_name=file_obj.filename,
        fallback_provider=fallback_provider,
    )
    Files.update_file_metadata_by_id(
        file_id,
        {
            "processing_notice": _join_processing_notices(
                notice,
                (file_obj.meta or {}).get("processing_notice"),
            ),
            "fallback_provider": fallback_provider,
            "fallback_reason": fallback_reason
            or diagnostic.get("detail")
            or diagnostic.get("reason")
            or diagnostic.get("message"),
            "native_file_input_status": diagnostic.get("status"),
            "native_file_input_reason": diagnostic.get("reason"),
            "native_file_input_message": diagnostic.get("message"),
        },
    )


def _classify_native_file_input_retry_error(error: Any) -> dict[str, Any]:
    message = str(error or "").strip()
    lowered = message.lower()

    if (
        "/responses" in lowered
        and any(token in lowered for token in ("not found", "unknown", "unsupported", "405", "404"))
    ):
        return _build_native_file_input_diagnostic(
            NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
            reason="responses_endpoint_unsupported",
            message=(
                "The current connection does not support the OpenAI Responses "
                "endpoint required for native file inputs."
            ),
            detail=message,
            source="responses_request",
        )

    return _build_native_file_input_diagnostic(
        NATIVE_FILE_INPUT_STATUS_UPSTREAM_REJECTED,
        reason="upstream_rejected",
        message=(
            "The upstream rejected native file inputs for this request."
        ),
        detail=message,
        source="responses_request",
    )


def build_native_file_input_retry_notification(metadata: dict, error: Any) -> str:
    diagnostic = _classify_native_file_input_retry_error(error)
    metadata["native_file_input_request_error"] = diagnostic
    return _build_native_file_input_fallback_message(
        diagnostic,
        fallback_provider=DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    )


async def _process_file_with_native_fallback_chain(
    request: Request,
    user: UserModel,
    *,
    file_id: str,
    diagnostic: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    local_error: Optional[Exception] = None
    requested_provider = normalize_document_provider(
        getattr(request.app.state.config, "DOCUMENT_PROVIDER", None),
        DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    )

    try:
        result = await run_in_threadpool(
            process_file,
            request,
            ProcessFileForm(
                file_id=file_id,
                processing_mode=FILE_PROCESSING_MODE_FULL_CONTEXT,
                document_provider=DOCUMENT_PROVIDER_LOCAL_DEFAULT,
                allow_provider_local_fallback=False,
            ),
            user,
        )
        _persist_native_file_fallback_notice(
            file_id,
            diagnostic=diagnostic,
            fallback_provider=DOCUMENT_PROVIDER_LOCAL_DEFAULT,
        )
        return result, DOCUMENT_PROVIDER_LOCAL_DEFAULT
    except Exception as exc:
        local_error = exc

    if requested_provider != DOCUMENT_PROVIDER_LOCAL_DEFAULT:
        result = await run_in_threadpool(
            process_file,
            request,
            ProcessFileForm(
                file_id=file_id,
                processing_mode=FILE_PROCESSING_MODE_FULL_CONTEXT,
                document_provider=requested_provider,
                allow_provider_local_fallback=False,
            ),
            user,
        )
        _persist_native_file_fallback_notice(
            file_id,
            diagnostic=diagnostic,
            fallback_provider=requested_provider,
            fallback_reason=str(local_error),
        )
        return result, requested_provider

    if local_error is not None:
        raise local_error
    raise RuntimeError("Native file fallback processing failed.")


def _anthropic_model_supports_native_file_mode(model: dict) -> bool:
    if not isinstance(model, dict):
        return False
    return (model.get("owned_by") == "anthropic") or isinstance(
        model.get("anthropic"), dict
    )


def _file_supports_anthropic_native_mode(file_obj: Any) -> bool:
    if not file_obj or not getattr(file_obj, "meta", None):
        return False
    content_type = str((file_obj.meta or {}).get("content_type") or "").lower()
    return content_type in _ANTHROPIC_NATIVE_DOCUMENT_MIME_TYPES or content_type in _ANTHROPIC_NATIVE_IMAGE_MIME_TYPES


def _extend_native_file_inputs_for_anthropic(metadata: dict, model: dict) -> None:
    if not _anthropic_model_supports_native_file_mode(model):
        return

    native_ids = {
        str(file_id).strip()
        for file_id in (metadata.get("native_file_input_file_ids") or [])
        if str(file_id).strip()
    }
    for file_item in metadata.get("files") or []:
        if _get_file_item_processing_mode(file_item) != FILE_PROCESSING_MODE_NATIVE_FILE:
            continue
        file_id = _get_attachment_file_id(file_item)
        if not file_id:
            continue
        file_obj = Files.get_file_by_id(file_id)
        if _file_supports_anthropic_native_mode(file_obj):
            native_ids.add(file_id)

    metadata["native_file_input_file_ids"] = list(native_ids)


async def _ensure_requested_chat_file_modes(
    request: Request,
    metadata: dict,
    user: UserModel,
    model: dict,
    event_emitter,
) -> None:
    regular_files = metadata.get("files") or []
    if not isinstance(regular_files, list) or not regular_files:
        return

    _extend_native_file_inputs_for_anthropic(metadata, model)
    native_ids = {
        str(file_id).strip()
        for file_id in (metadata.get("native_file_input_file_ids") or [])
        if str(file_id).strip()
    }
    default_mode = normalize_file_processing_mode(
        request.app.state.config.FILE_PROCESSING_DEFAULT_MODE,
        FILE_PROCESSING_MODE_RETRIEVAL,
    )
    fallback_messages: list[str] = []

    for file_item in regular_files:
        if not isinstance(file_item, dict) or file_item.get("type") != "file":
            continue

        file_id = _get_attachment_file_id(file_item)
        if not file_id:
            continue

        file_obj = Files.get_file_by_id(file_id)
        if not file_obj:
            continue

        desired_mode = get_requested_processing_mode_for_file_item(
            file_item,
            file_obj=file_obj,
            default_mode=default_mode,
        )
        native_fallback_diagnostic = None

        if desired_mode == FILE_PROCESSING_MODE_NATIVE_FILE:
            if file_id not in native_ids:
                native_fallback_diagnostic = _get_native_file_input_diagnostic(
                    metadata, file_id
                ) or _build_native_file_input_diagnostic(
                    NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
                    reason="native_file_not_prepared",
                    message=(
                        "Native file inputs were not prepared for this request."
                    ),
                    source="chat_preparation",
                )
                desired_mode = FILE_PROCESSING_MODE_FULL_CONTEXT
                file_item["processing_mode"] = FILE_PROCESSING_MODE_FULL_CONTEXT
                file_item["context"] = "full"
            else:
                file_item["processing_mode"] = FILE_PROCESSING_MODE_NATIVE_FILE
                file_item.pop("context", None)
        elif desired_mode == FILE_PROCESSING_MODE_FULL_CONTEXT:
            file_item["processing_mode"] = FILE_PROCESSING_MODE_FULL_CONTEXT
            file_item["context"] = "full"
        else:
            file_item["processing_mode"] = FILE_PROCESSING_MODE_RETRIEVAL
            file_item.pop("context", None)

        current_mode = get_file_effective_processing_mode(
            file_obj, default_mode=default_mode
        )
        needs_processing = current_mode != desired_mode
        if desired_mode == FILE_PROCESSING_MODE_RETRIEVAL and not (
            (file_obj.meta or {}).get("collection_name")
        ):
            needs_processing = True
        if desired_mode == FILE_PROCESSING_MODE_FULL_CONTEXT and not (
            (file_obj.data or {}).get("content")
        ):
            needs_processing = True

        if needs_processing:
            if native_fallback_diagnostic is not None:
                _result, fallback_provider = await _process_file_with_native_fallback_chain(
                    request,
                    user,
                    file_id=file_id,
                    diagnostic=native_fallback_diagnostic,
                )
                fallback_messages.append(
                    _build_native_file_input_fallback_message(
                        native_fallback_diagnostic,
                        file_name=file_obj.filename,
                        fallback_provider=fallback_provider,
                    )
                )
            else:
                await run_in_threadpool(
                    process_file,
                    request,
                    ProcessFileForm(file_id=file_id, processing_mode=desired_mode),
                    user,
                )
            refreshed = Files.get_file_by_id(file_id)
            if refreshed:
                file_item["file"] = refreshed.model_dump()
                if refreshed.meta and refreshed.meta.get("collection_name"):
                    file_item["collection_name"] = refreshed.meta.get("collection_name")
        elif native_fallback_diagnostic is not None:
            fallback_messages.append(
                _build_native_file_input_fallback_message(
                    native_fallback_diagnostic,
                    file_name=file_obj.filename,
                    used_cached_context=True,
                )
            )

    metadata["files"] = regular_files

    if fallback_messages and event_emitter:
        await event_emitter(
            {
                "type": "notification",
                "data": {
                    "type": "info",
                    "content": "\n".join(fallback_messages),
                },
            }
        )


async def _prepare_openai_native_file_inputs(
    request: Request, form_data: dict, metadata: dict, user: UserModel, model: dict
) -> None:
    if metadata.get("disable_native_file_inputs"):
        return
    if not (
        (model or {}).get("owned_by") == "openai" or isinstance((model or {}).get("openai"), dict)
    ):
        return

    messages = form_data.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    all_regular_files = metadata.get("files") or []
    candidate_files = [
        file_item for file_item in all_regular_files if _is_native_file_input_candidate(file_item)
    ]
    if not candidate_files:
        return

    connection_user = getattr(getattr(request, "state", None), "connection_user", None) or user
    base_urls, keys, cfgs = _get_openai_user_config(connection_user)
    if not base_urls:
        return

    model_id = form_data.get("model") or (model or {}).get("id") or ""
    url_idx, url, key, api_config = _resolve_openai_connection_by_model_id(
        model_id, base_urls, keys, cfgs
    )
    if not url:
        return

    file_ids_by_message_idx: dict[int, list[str]] = {}
    message_level_files_found = False
    for idx, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        message_files = message.get("files") or []
        if not isinstance(message_files, list) or not message_files:
            continue
        eligible_ids = [
            _get_attachment_file_id(file_item)
            for file_item in message_files
            if _is_native_file_input_candidate(file_item)
        ]
        eligible_ids = [file_id for file_id in eligible_ids if file_id]
        if eligible_ids:
            message_level_files_found = True
            file_ids_by_message_idx[idx] = eligible_ids

    if not message_level_files_found:
        last_user_idx = None
        for idx in range(len(messages) - 1, -1, -1):
            if isinstance(messages[idx], dict) and messages[idx].get("role") == "user":
                last_user_idx = idx
                break
        if last_user_idx is None:
            return
        file_ids_by_message_idx[last_user_idx] = [
            _get_attachment_file_id(file_item) for file_item in candidate_files
        ]

    unique_file_ids: list[str] = []
    for file_ids in file_ids_by_message_idx.values():
        for file_id in file_ids:
            if file_id and file_id not in unique_file_ids:
                unique_file_ids.append(file_id)
    if not unique_file_ids:
        return

    capability = _get_native_file_input_capability(url, api_config)
    connection_name = (
        (model or {}).get("connection_name")
        or api_config.get("remark")
        or (urlparse(url).hostname or "")
    )
    metadata["native_file_input_connection"] = {
        "model_id": model_id,
        "connection_name": connection_name,
        "url": url,
        "prefix_id": api_config.get("_resolved_prefix_id") or api_config.get("prefix_id") or "",
        "responses_configured": capability.get("responses_configured"),
        "native_file_inputs_enabled": api_config.get("native_file_inputs_enabled"),
        "official_openai": capability.get("official"),
        "status": capability.get("status"),
        "reason": capability.get("reason"),
    }
    log.info(
        "[OPENAI NATIVE FILE INPUTS] model=%s connection=%s status=%s reason=%s responses_configured=%s enabled=%s official=%s",
        model_id,
        connection_name or url,
        capability.get("status"),
        capability.get("reason"),
        capability.get("responses_configured"),
        api_config.get("native_file_inputs_enabled"),
        capability.get("official"),
    )

    if capability.get("status") != NATIVE_FILE_INPUT_STATUS_SUPPORTED:
        for file_id in unique_file_ids:
            file_obj = Files.get_file_by_id(file_id)
            _set_native_file_input_diagnostic(
                metadata,
                file_id=file_id,
                file_name=(file_obj.filename if file_obj else file_id),
                diagnostic=_build_native_file_input_diagnostic(
                    str(capability.get("status") or NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG),
                    reason=str(capability.get("reason") or "connection_not_supported"),
                    message=str(
                        capability.get("message")
                        or "Native file inputs are not enabled for this connection."
                    ),
                    source="connection_policy",
                ),
            )
        return

    responses_configured = _should_use_responses_api(
        url, api_config, model_id, native_web_search=False
    )
    if not responses_configured:
        probe = await _probe_responses_support_for_native_file_inputs(
            url=url,
            key=key or "",
            api_config=api_config,
            user=user,
            model_id=model_id,
        )
        metadata["native_file_input_responses_probe"] = probe
        log.info(
            "[OPENAI NATIVE FILE INPUTS] responses_probe model=%s connection=%s supported=%s reason=%s status=%s",
            model_id,
            connection_name or url,
            probe.get("supported"),
            probe.get("reason"),
            probe.get("http_status"),
        )
        if probe.get("supported") is False:
            for file_id in unique_file_ids:
                file_obj = Files.get_file_by_id(file_id)
                _set_native_file_input_diagnostic(
                    metadata,
                    file_id=file_id,
                    file_name=(file_obj.filename if file_obj else file_id),
                    diagnostic=_build_native_file_input_diagnostic(
                        NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
                        reason=str(probe.get("reason") or "responses_endpoint_unsupported"),
                        message=str(
                            probe.get("message")
                            or "The current connection does not support the Responses endpoint required for native file inputs."
                        ),
                        detail=str(probe.get("body_preview") or ""),
                        source="responses_probe",
                    ),
                )
            return

    conn_key = _get_openai_file_cache_key(api_config, url_idx)
    remote_ids_by_local_id: dict[str, str] = {}

    for file_id in unique_file_ids:
        file_obj = Files.get_file_by_id(file_id)
        if not file_obj or not file_obj.meta or not file_obj.path:
            _set_native_file_input_diagnostic(
                metadata,
                file_id=file_id,
                file_name=(file_obj.filename if file_obj else file_id),
                diagnostic=_build_native_file_input_diagnostic(
                    NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED,
                    reason="file_not_available",
                    message=(
                        "The uploaded file is no longer available for native file transfer."
                    ),
                    source="local_file_lookup",
                ),
            )
            continue

        content_type = (file_obj.meta or {}).get("content_type") or "application/octet-stream"
        if str(content_type).startswith("image/"):
            _set_native_file_input_diagnostic(
                metadata,
                file_id=file_id,
                file_name=file_obj.filename or file_id,
                diagnostic=_build_native_file_input_diagnostic(
                    NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
                    reason="image_attachment_not_uploaded_via_files_api",
                    message=(
                        "This attachment was not sent through the OpenAI Files API path for native file inputs."
                    ),
                    source="files_api_preparation",
                ),
            )
            continue

        remote_file_id = _get_cached_openai_file_id(file_obj.meta, conn_key)
        if not remote_file_id:
            try:
                local_path = Storage.get_file(file_obj.path)
                remote_file_id = await _upload_file_to_openai(
                    base_url=url.rstrip("/"),
                    key=key or "",
                    api_config=api_config,
                    local_path=local_path,
                    filename=file_obj.filename or "file",
                    content_type=content_type,
                    user=user,
                )
                _set_cached_openai_file_id(
                    file_id, file_obj.meta, conn_key, remote_file_id
                )
            except Exception as exc:
                detail = str(exc)
                log.warning(
                    "[OPENAI NATIVE FILE INPUTS] files_api_upload_failed model=%s connection=%s file_id=%s filename=%s detail=%s",
                    model_id,
                    connection_name or url,
                    file_id,
                    file_obj.filename,
                    detail,
                )
                _set_native_file_input_diagnostic(
                    metadata,
                    file_id=file_id,
                    file_name=file_obj.filename or file_id,
                    diagnostic=_build_native_file_input_diagnostic(
                        NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED,
                        reason="files_api_upload_failed",
                        message=(
                            "Uploading this file to the upstream Files API failed."
                        ),
                        detail=detail,
                        source="files_api_upload",
                    ),
                )
                continue

        if remote_file_id:
            remote_ids_by_local_id[file_id] = remote_file_id

    if not remote_ids_by_local_id:
        return

    parts_by_message_idx: dict[str, list[dict]] = {}
    native_file_input_ids: list[str] = []
    for idx, file_ids in file_ids_by_message_idx.items():
        parts: list[dict] = []
        for file_id in file_ids:
            remote_file_id = remote_ids_by_local_id.get(file_id)
            if not remote_file_id:
                continue
            parts.append({"type": "input_file", "file_id": remote_file_id})
            if file_id not in native_file_input_ids:
                native_file_input_ids.append(file_id)
        if parts:
            parts_by_message_idx[str(idx)] = parts

    if not parts_by_message_idx:
        return

    metadata["native_file_input_file_ids"] = native_file_input_ids
    metadata["native_file_input_parts_by_message"] = parts_by_message_idx
    metadata["native_file_inputs_force_responses_api"] = True
    log.info(
        "[OPENAI NATIVE FILE INPUTS] prepared model=%s connection=%s files=%s force_responses=%s",
        model_id,
        connection_name or url,
        native_file_input_ids,
        True,
    )


def _apply_prepared_openai_native_file_inputs(
    form_data: dict, metadata: dict
) -> None:
    messages = form_data.get("messages")
    if not isinstance(messages, list) or not messages:
        return

    parts_by_message = metadata.get("native_file_input_parts_by_message") or {}
    if not isinstance(parts_by_message, dict) or not parts_by_message:
        return

    for idx_str, file_parts in parts_by_message.items():
        try:
            idx = int(idx_str)
        except Exception:
            continue
        if idx < 0 or idx >= len(messages):
            continue
        if not isinstance(file_parts, list) or not file_parts:
            continue
        message = messages[idx]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        message["content"] = _merge_message_content_with_native_file_inputs(
            message.get("content"), file_parts
        )


def get_citation_sources_from_tool_result(
    tool_name: str,
    tool_params: dict,
    tool_result: Any,
    *,
    tool_id: str = "",
    max_unique_sources: int = 10,
    max_doc_chars: int = 2000,
) -> list[dict]:
    """
    Convert tool results into the "sources" format expected by the UI citations components.

    This is UI-only: callers should avoid feeding these sources back into the RAG context unless
    they explicitly want to.
    """
    try:
        name = str(tool_name or "")
    except Exception:
        name = ""

    if not name:
        return []

    result = _safe_json_loads(tool_result)
    params = tool_params if isinstance(tool_params, dict) else {}

    if name == "search_web":
        if isinstance(result, dict) and "result" in result:
            result = result.get("result")
        if isinstance(result, str):
            result = _safe_json_loads(result)
        if not isinstance(result, list):
            return []

        out: list[dict] = []
        seen: set[str] = set()
        for item in result:
            if not isinstance(item, dict):
                continue
            title = _truncate_text(item.get("title", "") or "", 300).strip()
            link = str(item.get("link") or item.get("url") or "").strip()
            snippet = _truncate_text(item.get("snippet", "") or "", 700).strip()
            if not (title or link or snippet):
                continue

            source_key = link or title
            if source_key:
                if source_key in seen:
                    continue
                seen.add(source_key)

            md_source = f"web:{link}" if link else (title or f"search_web:{len(out)+1}")
            md: dict = {"source": md_source}
            if title:
                md["name"] = title
            if link:
                md["url"] = link

            out.append(
                {
                    "source": {
                        "id": link or md_source,
                        "name": title or link or "search_web",
                        "type": "url" if link else "tool",
                        **({"url": link} if link else {}),
                    },
                    "document": [_truncate_text(f"{title}\n{snippet}".strip(), max_doc_chars)],
                    "metadata": [md],
                }
            )

            if len(seen) >= max_unique_sources:
                break

        return out

    if name in {"fetch_url", "fetch_url_rendered"}:
        if isinstance(result, str):
            parsed_result = _safe_json_loads(result)
            if isinstance(parsed_result, dict):
                result = parsed_result
        url = ""
        content = ""
        title = ""
        if isinstance(result, dict):
            url = str(result.get("url") or "").strip()
            content = str(result.get("content") or "").strip()
            title = str(result.get("title") or "").strip()
        elif isinstance(result, str):
            content = result.strip()
        if not url:
            url = str(params.get("url") or "").strip()
        if not url:
            return []
        if not title:
            title = url

        return [
            {
                "source": {"id": url, "name": title, "type": "url", "url": url},
                "document": [_truncate_text(content, max_doc_chars)],
                "metadata": [{"source": f"web:{url}", "name": title, "url": url}],
            }
        ]

    if name == "view_knowledge_file":
        if isinstance(result, str):
            result = _safe_json_loads(result)
        if not isinstance(result, dict):
            return []

        file_id = str(result.get("file_id") or result.get("id") or "").strip()
        filename = str(result.get("name") or result.get("filename") or "").strip()
        content = str(result.get("content") or "").strip()
        if not (file_id or filename):
            return []

        src_id = file_id or filename
        src_name = filename or file_id
        md = {"source": filename or file_id or src_id, "name": src_name}
        if file_id:
            md["file_id"] = file_id
        return [
            {
                "source": {"id": src_id, "name": src_name, "type": "file"},
                "document": [_truncate_text(content, max_doc_chars)],
                "metadata": [md],
            }
        ]

    if name == "query_knowledge_bases":
        if isinstance(result, str):
            result = _safe_json_loads(result)
        if not isinstance(result, dict):
            return []

        documents_nested = result.get("documents") or []
        metadatas_nested = result.get("metadatas") or []
        distances_nested = result.get("distances") or []
        if not isinstance(documents_nested, list) or not documents_nested:
            return []

        docs0 = documents_nested[0] if isinstance(documents_nested[0], list) else []
        metas0 = (
            metadatas_nested[0]
            if isinstance(metadatas_nested, list)
            and metadatas_nested
            and isinstance(metadatas_nested[0], list)
            else []
        )
        dists0 = (
            distances_nested[0]
            if isinstance(distances_nested, list)
            and distances_nested
            and isinstance(distances_nested[0], list)
            else []
        )

        groups: dict[str, dict] = {}
        order: list[str] = []

        for i, doc in enumerate(docs0):
            meta = metas0[i] if i < len(metas0) and isinstance(metas0[i], dict) else {}
            dist = dists0[i] if i < len(dists0) else None

            source_key = (
                str(meta.get("source") or meta.get("url") or meta.get("file_id") or meta.get("name") or "")
                .strip()
            )
            if not source_key:
                source_key = f"kb:{i+1}"

            if source_key not in groups:
                groups[source_key] = {
                    "source": {
                        "id": meta.get("file_id") or meta.get("note_id") or source_key,
                        "name": meta.get("name") or source_key,
                        "type": meta.get("type") or "file",
                    },
                    "document": [],
                    "metadata": [],
                    "distances": [],
                }
                order.append(source_key)
                if len(order) > max_unique_sources:
                    break

            groups[source_key]["document"].append(_truncate_text(doc, max_doc_chars))
            md = dict(meta) if isinstance(meta, dict) else {}
            md.setdefault("source", source_key)
            md.setdefault("name", groups[source_key]["source"].get("name") or source_key)
            groups[source_key]["metadata"].append(md)
            if dist is not None:
                groups[source_key]["distances"].append(dist)

        out: list[dict] = []
        for key in order[:max_unique_sources]:
            entry = groups.get(key)
            if not entry:
                continue
            # Only include distances when present (UI uses it for relevance display).
            if not entry.get("distances"):
                entry.pop("distances", None)
            out.append(entry)
        return out

    return []


async def chat_completion_tools_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel, models, tools
) -> tuple[dict, dict]:
    async def get_content_from_response(response) -> Optional[str]:
        content = None
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                data = json.loads(chunk.decode("utf-8"))
                content = data["choices"][0]["message"]["content"]

            # Cleanup any remaining background tasks if necessary
            if response.background is not None:
                await response.background()
        else:
            content = response["choices"][0]["message"]["content"]
        return content

    def get_tools_function_calling_payload(messages, task_model_id, content):
        user_message = get_last_user_message(messages)
        history = "\n".join(
            f"{message['role'].upper()}: \"\"\"{strip_reasoning_details(message['content'])}\"\"\""
            for message in messages[::-1][:4]
        )

        prompt = f"History:\n{history}\nQuery: {user_message}"

        return {
            "model": task_model_id,
            "messages": [
                {"role": "system", "content": content},
                {"role": "user", "content": f"Query: {prompt}"},
            ],
            "stream": False,
            "metadata": {"task": str(TASKS.FUNCTION_CALLING)},
        }

    event_caller = extra_params["__event_call__"]
    metadata = extra_params["__metadata__"]

    task_model_id = get_task_model_id(
        body["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    skip_files = False
    sources = []
    ui_sources: list[dict] = []
    ui_sources_seen: set[str] = set()

    def _add_ui_sources(new_sources: list[dict]):
        nonlocal ui_sources, ui_sources_seen
        if not isinstance(new_sources, list) or not new_sources:
            return
        for s in new_sources:
            if not isinstance(s, dict):
                continue
            key = ""
            try:
                meta = s.get("metadata") or []
                if isinstance(meta, list) and meta and isinstance(meta[0], dict):
                    key = str(meta[0].get("source") or meta[0].get("url") or "")
                if not key:
                    src = s.get("source") or {}
                    if isinstance(src, dict):
                        key = str(src.get("id") or src.get("url") or src.get("name") or "")
            except Exception:
                key = ""
            if key and key in ui_sources_seen:
                continue
            if key:
                ui_sources_seen.add(key)
            ui_sources.append(s)
            if len(ui_sources_seen) >= 10:
                break

    specs = [tool["spec"] for tool in tools.values()]
    tools_specs = json.dumps(specs)

    if request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE != "":
        template = request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    tools_function_calling_prompt = tools_function_calling_generation_template(
        template, tools_specs
    )
    payload = get_tools_function_calling_payload(
        body["messages"], task_model_id, tools_function_calling_prompt
    )

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "structured_output_generation response_type=%s",
                type(response).__name__,
            )
        content = await get_content_from_response(response)
        if log.isEnabledFor(logging.DEBUG):
            log.debug(
                "structured_output_generation content_len=%s",
                len(content) if isinstance(content, str) else None,
            )

        if not content:
            return body, {}

        try:
            content = content[content.find("{") : content.rfind("}") + 1]
            if not content:
                raise Exception("No JSON object found in the response")

            result = json.loads(content)

            async def tool_call_handler(tool_call):
                nonlocal skip_files

                log.debug(f"{tool_call=}")

                tool_function_name = tool_call.get("name", None)
                if tool_function_name not in tools:
                    return body, {}

                tool_function_params = tool_call.get("parameters", {})

                try:
                    tool = tools[tool_function_name]

                    spec = tool.get("spec", {})
                    allowed_params = (
                        spec.get("parameters", {}).get("properties", {}).keys()
                    )
                    tool_function_params = {
                        k: v
                        for k, v in tool_function_params.items()
                        if k in allowed_params
                    }

                    log.info(f"[TOOL CALL] {tool_function_name} params={json.dumps(tool_function_params, ensure_ascii=False)}")

                    if tool.get("direct", False):
                        tool_result = await event_caller(
                            {
                                "type": "execute:tool",
                                "data": {
                                    "id": str(uuid4()),
                                    "name": tool_function_name,
                                    "params": tool_function_params,
                                    "server": tool.get("server", {}),
                                    "session_id": metadata.get("session_id", None),
                                },
                            }
                        )
                    else:
                        tool_function = tool["callable"]
                        tool_result = await tool_function(**tool_function_params)

                    log.info(f"[TOOL RESULT] {tool_function_name} result={str(tool_result)[:500]}")

                except Exception as e:
                    log.error(f"[TOOL ERROR] {tool_function_name} error={e}")
                    tool_result = str(e)

                # UI-only citation sources from builtin tools / knowledge tools (do not feed into RAG context).
                try:
                    tool_id = tool.get("tool_id", "") if isinstance(tool, dict) else ""
                    _add_ui_sources(
                        get_citation_sources_from_tool_result(
                            tool_function_name,
                            tool_function_params if isinstance(tool_function_params, dict) else {},
                            tool_result,
                            tool_id=tool_id,
                        )
                    )
                except Exception:
                    # Never block tool execution due to citation extraction.
                    pass

                tool_result_files = []
                if isinstance(tool_result, list):
                    for item in tool_result:
                        # check if string
                        if isinstance(item, str) and item.startswith("data:"):
                            tool_result_files.append(item)
                            tool_result.remove(item)

                if isinstance(tool_result, list):
                    tool_result = {"results": tool_result}

                if isinstance(tool_result, dict) or isinstance(tool_result, list):
                    tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)

                if isinstance(tool_result, str):
                    tool = tools[tool_function_name]
                    tool_id = tool.get("tool_id", "")

                    tool_name = (
                        f"{tool_id}/{tool_function_name}"
                        if tool_id
                        else f"{tool_function_name}"
                    )
                    if tool.get("metadata", {}).get("citation", False) or tool.get(
                        "direct", False
                    ):
                        # Citation is enabled for this tool
                        sources.append(
                            {
                                "source": {
                                    "name": (f"TOOL:{tool_name}"),
                                },
                                "document": [tool_result],
                                "metadata": [{"source": (f"TOOL:{tool_name}")}],
                            }
                        )
                    else:
                        # Citation is not enabled for this tool
                        body["messages"] = add_or_update_user_message(
                            f"\nTool `{tool_name}` Output: {tool_result}",
                            body["messages"],
                        )

                    if (
                        tools[tool_function_name]
                        .get("metadata", {})
                        .get("file_handler", False)
                    ):
                        skip_files = True

            # check if "tool_calls" in result
            if result.get("tool_calls"):
                for tool_call in result.get("tool_calls"):
                    await tool_call_handler(tool_call)
            else:
                await tool_call_handler(result)

        except Exception as e:
            log.debug(f"Error: {e}")
            content = None
    except Exception as e:
        log.debug(f"Error: {e}")
        content = None

    if log.isEnabledFor(logging.DEBUG):
        log.debug("tool_contexts sources_count=%d", len(sources))

    if skip_files and "files" in body.get("metadata", {}):
        del body["metadata"]["files"]

    return body, {"sources": sources, "ui_sources": ui_sources}


async def chat_web_search_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    def build_tavily_loader_fallback_notification(notice: dict) -> tuple[str, str] | None:
        if not isinstance(notice, dict):
            return None
        if notice.get("type") != "tavily_loader_auth_fallback":
            return None

        is_admin = str(getattr(user, "role", "") or "").strip().lower() == "admin"
        fallback_succeeded = bool(notice.get("fallback_succeeded"))
        used_direct_docs = bool(notice.get("used_direct_docs"))

        if fallback_succeeded:
            if is_admin:
                return (
                    "warning",
                    "Tavily 网页加载器鉴权失败，已临时切换备用方式。请到管理后台的联网搜索设置检查 Tavily API Key 和 Extract Base URL。",
                )
            return (
                "warning",
                "联网资料抓取服务暂时不可用，已自动切换备用方式继续回答，结果可能较少。",
            )

        if used_direct_docs:
            if is_admin:
                return (
                    "warning",
                    "Tavily 网页加载器鉴权失败，已尝试备用方式但仍无法抓取网页正文。本次将改用搜索摘要继续回答，请检查 Tavily API Key 和 Extract Base URL。",
                )
            return (
                "warning",
                "网页内容抓取失败，已尝试备用方式仍未成功。本次将改用搜索摘要继续回答，结果可能较少。",
            )

        if is_admin:
            return (
                "error",
                "Tavily 网页加载器鉴权失败，已尝试备用方式仍未成功，本次无法获取网页正文。请到管理后台的联网搜索设置检查 Tavily API Key 和 Extract Base URL。",
            )
        return (
            "error",
            "网页内容抓取失败，已尝试备用方式仍未成功。",
        )

    event_emitter = extra_params["__event_emitter__"]
    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search",
                "description": "Generating search query",
                "done": False,
            },
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)

    queries = []
    try:
        res = await generate_queries(
            request,
            {
                "model": form_data["model"],
                "messages": messages,
                "prompt": user_message,
                "type": "web_search",
            },
            user,
        )
        response = _get_generation_response_content(res)
        if not response:
            raise ValueError("Query generation returned no content")

        try:
            bracket_start = response.find("{")
            bracket_end = response.rfind("}") + 1

            if bracket_start == -1 or bracket_end == -1:
                raise Exception("No JSON object found in the response")

            json_str = response[bracket_start:bracket_end]
            queries = json.loads(json_str)
            queries = queries.get("queries", [])
        except Exception as e:
            queries = [user_message]

    except Exception as e:
        log.exception(e)
        queries = [user_message]

    if len(queries) == 0:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search query generated",
                    "done": True,
                },
            }
        )
        return form_data

    all_results = []
    emitted_loader_runtime_notices = set()

    for searchQuery in queries:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": 'Searching "{{searchQuery}}"',
                    "query": searchQuery,
                    "done": False,
                },
            }
        )

        try:
            results = await process_web_search(
                request,
                SearchForm(
                    **{
                        "query": searchQuery,
                    }
                ),
                user=user,
            )

            if results:
                runtime_notice = results.get("loader_runtime_notice")
                notification = build_tavily_loader_fallback_notification(runtime_notice)
                if notification is not None:
                    notice_key = json.dumps(runtime_notice, sort_keys=True, ensure_ascii=False)
                    if notice_key not in emitted_loader_runtime_notices:
                        emitted_loader_runtime_notices.add(notice_key)
                        level, content = notification
                        await event_emitter(
                            {
                                "type": "notification",
                                "data": {
                                    "type": level,
                                    "content": content,
                                },
                            }
                        )

                all_results.append(results)
                files = form_data.get("files", [])

                if results.get("collection_names"):
                    for col_idx, collection_name in enumerate(
                        results.get("collection_names")
                    ):
                        files.append(
                            {
                                "collection_name": collection_name,
                                "name": searchQuery,
                                "type": "web_search",
                                "urls": [results["filenames"][col_idx]],
                            }
                        )
                elif results.get("docs"):
                    # Invoked when bypass embedding and retrieval is set to True
                    docs = results["docs"]

                    if len(docs) == len(results["filenames"]):
                        # the number of docs and filenames (urls) should be the same
                        for doc_idx, doc in enumerate(docs):
                            files.append(
                                {
                                    "docs": [doc],
                                    "name": searchQuery,
                                    "type": "web_search",
                                    "urls": [results["filenames"][doc_idx]],
                                }
                            )
                    else:
                        # edge case when the number of docs and filenames (urls) are not the same
                        # this should not happen, but if it does, we will just append the docs
                        files.append(
                            {
                                "docs": results.get("docs", []),
                                "name": searchQuery,
                                "type": "web_search",
                                "urls": results["filenames"],
                            }
                        )

                form_data["files"] = files
        except Exception as e:
            log.exception(e)
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": 'Error searching "{{searchQuery}}"',
                        "query": searchQuery,
                        "done": True,
                        "error": True,
                    },
                }
            )

    if all_results:
        urls = []
        total_failed = 0
        for results in all_results:
            if "filenames" in results:
                urls.extend(results["filenames"])
            total_failed += results.get("failed_count", 0)

        if total_failed > 0:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "Searched {{count}} sites, {{failed}} failed to index",
                        "urls": urls,
                        "failed": total_failed,
                        "done": True,
                    },
                }
            )
        else:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "Searched {{count}} sites",
                        "urls": urls,
                        "done": True,
                    },
                }
            )
    else:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search results found",
                    "done": True,
                    "error": True,
                },
            }
        )

    return form_data


async def chat_image_generation_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    __event_emitter__ = extra_params["__event_emitter__"]
    await __event_emitter__(
        {
            "type": "status",
            "data": {"description": "Generating an image", "done": False},
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)

    prompt = user_message
    negative_prompt = ""
    image_generation_options = (
        extra_params.get("__metadata__", {})
        .get("image_generation_options", {})
    )
    image_generation_options = (
        image_generation_options if isinstance(image_generation_options, dict) else {}
    )

    if request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION:
        try:
            res = await generate_image_prompt(
                request,
                {
                    "model": form_data["model"],
                    "messages": messages,
                },
                user,
            )
            response = _get_generation_response_content(res)
            if not response:
                raise ValueError("Image prompt generation returned no content")

            try:
                bracket_start = response.find("{")
                bracket_end = response.rfind("}") + 1

                if bracket_start == -1 or bracket_end == -1:
                    raise Exception("No JSON object found in the response")

                json_str = response[bracket_start:bracket_end]
                parsed = json.loads(json_str)
                prompt = parsed.get("prompt", [])
            except Exception as e:
                prompt = user_message

        except Exception as e:
            log.exception(e)
            prompt = user_message

    system_message_content = ""

    try:
        images = await image_generations(
            request=request,
            form_data=GenerateImageForm(
                **{
                    "prompt": prompt,
                    **{
                        key: image_generation_options.get(key)
                        for key in (
                            "model",
                            "size",
                            "image_size",
                            "aspect_ratio",
                            "n",
                            "negative_prompt",
                            "credential_source",
                            "connection_index",
                            "steps",
                            "background",
                        )
                        if image_generation_options.get(key) is not None
                    },
                }
            ),
            user=user,
        )

        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": "Generated an image", "done": True},
            }
        )

        await __event_emitter__(
            {
                "type": "files",
                "data": {
                    "files": [
                        {
                            "type": "image",
                            "url": image["url"],
                        }
                        for image in images
                    ]
                },
            }
        )

        system_message_content = "<context>User is shown the generated image, tell the user that the image has been generated</context>"
    except Exception as e:
        log.exception(e)
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "description": f"An error occurred while generating an image",
                    "done": True,
                },
            }
        )

        system_message_content = "<context>Unable to generate an image, tell the user that an error occurred</context>"

    if system_message_content:
        form_data["messages"] = add_or_update_system_message(
            system_message_content, form_data["messages"]
        )

    return form_data


async def chat_completion_files_handler(
    request: Request, body: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    sources = []

    # J-7-16: Merge knowledge files back in for RAG processing.
    _regular_files = body.get("metadata", {}).get("files", None) or []
    native_file_input_ids = {
        str(file_id).strip()
        for file_id in (body.get("metadata", {}).get("native_file_input_file_ids") or [])
        if str(file_id).strip()
    }
    _regular_files = _filter_rag_files_for_native_file_inputs(
        _regular_files, native_file_input_ids
    )
    _knowledge_files = body.get("metadata", {}).get("knowledge_files", [])
    files = _regular_files + _knowledge_files if (_regular_files or _knowledge_files) else None

    if files:
        queries = []
        try:
            queries_response = await generate_queries(
                request,
                {
                    "model": body["model"],
                    "messages": body["messages"],
                    "type": "retrieval",
                },
                user,
            )
            queries_response = _get_generation_response_content(queries_response)
            if not queries_response:
                raise ValueError("Retrieval query generation returned no content")

            try:
                bracket_start = queries_response.find("{")
                bracket_end = queries_response.rfind("}") + 1

                if bracket_start == -1 or bracket_end == -1:
                    raise Exception("No JSON object found in the response")

                queries_response = queries_response[bracket_start:bracket_end]
                queries_response = json.loads(queries_response)
            except Exception as e:
                queries_response = {"queries": [queries_response]}

            queries = queries_response.get("queries", [])
        except:
            pass

        if len(queries) == 0:
            queries = [get_last_user_message(body["messages"])]

        try:
            # Offload get_sources_from_files to a separate thread
            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor() as executor:
                sources = await loop.run_in_executor(
                    executor,
                    lambda: get_sources_from_files(
                        request=request,
                        files=files,
                        queries=queries,
                        embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                            query, prefix=prefix, user=user
                        ),
                        k=request.app.state.config.TOP_K,
                        reranking_function=get_safe_reranking_runtime(request.app)
                        if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH
                        else None,
                        k_reranker=request.app.state.config.TOP_K_RERANKER,
                        r=request.app.state.config.RELEVANCE_THRESHOLD,
                        hybrid_search=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
                        full_context=request.app.state.config.RAG_FULL_CONTEXT,
                        bm25_weight=request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT,
                        enable_enriched_texts=getattr(
                            request.app.state.config,
                            "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS",
                            False,
                        ),
                    ),
                )
        except Exception as e:
            log.exception(e)

        if log.isEnabledFor(logging.DEBUG):
            log.debug("rag_contexts sources_count=%d", len(sources))

    return body, {"sources": sources}


def apply_params_to_form_data(form_data, model):
    params = dict(form_data.pop("params", {}) or {})
    custom_params = params.pop("custom_params", None)

    if model.get("ollama"):
        form_data["options"] = merge_additive_payload_fields(params, custom_params)

        if "format" in params:
            form_data["format"] = params["format"]

        if "keep_alive" in params:
            form_data["keep_alive"] = params["keep_alive"]
    else:
        if "seed" in params and params["seed"] is not None:
            form_data["seed"] = params["seed"]

        if "stop" in params and params["stop"] is not None:
            form_data["stop"] = params["stop"]

        if "temperature" in params and params["temperature"] is not None:
            form_data["temperature"] = params["temperature"]

        if "max_tokens" in params and params["max_tokens"] is not None:
            form_data["max_tokens"] = params["max_tokens"]

        if "top_p" in params and params["top_p"] is not None:
            form_data["top_p"] = params["top_p"]

        if "frequency_penalty" in params and params["frequency_penalty"] is not None:
            form_data["frequency_penalty"] = params["frequency_penalty"]

        if "reasoning_effort" in params and params["reasoning_effort"] is not None:
            form_data["reasoning_effort"] = params["reasoning_effort"]

        if "thinking" in params and params["thinking"] is not None:
            form_data["thinking"] = params["thinking"]

        if "logit_bias" in params and params["logit_bias"] is not None:
            try:
                form_data["logit_bias"] = json.loads(
                    convert_logit_bias_input_to_json(params["logit_bias"])
                )
            except Exception as e:
                print(f"Error parsing logit_bias: {e}")

        if isinstance(custom_params, dict) and custom_params:
            form_data["custom_params"] = custom_params

    return form_data


async def process_chat_payload(request, form_data, user, metadata, model):

    form_data = apply_params_to_form_data(form_data, model)
    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "process_chat_payload summary=%s",
            _summarize_form_data_for_debug(form_data),
        )

    event_emitter = get_event_emitter(metadata)
    event_call = get_event_call(metadata)

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_call,
        "__user__": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
        },
        "__metadata__": metadata,
        "__request__": request,
        "__model__": model,
    }

    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        models = {
            request.state.model["id"]: request.state.model,
        }
    else:
        models = getattr(request.state, "MODELS", None) or request.app.state.MODELS

    task_model_id = get_task_model_id(
        form_data["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    events = []
    sources = []
    ui_sources = []

    user_message = get_last_user_message(form_data["messages"])
    model_knowledge = model.get("info", {}).get("meta", {}).get("knowledge", False)

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": False,
                },
            }
        )

        knowledge_files = []
        for item in model_knowledge:
            if item.get("collection_name"):
                knowledge_files.append(
                    {
                        "id": item.get("collection_name"),
                        "name": item.get("name"),
                        "legacy": True,
                        "source": "knowledge",
                    }
                )
            elif item.get("collection_names"):
                knowledge_files.append(
                    {
                        "name": item.get("name"),
                        "type": "collection",
                        "collection_names": item.get("collection_names"),
                        "legacy": True,
                        "source": "knowledge",
                    }
                )
            else:
                knowledge_files.append({**item, "source": "knowledge"})

        files = form_data.get("files", [])
        files.extend(knowledge_files)
        form_data["files"] = files

    variables = form_data.pop("variables", None)

    try:
        # J-3-04: Use get_sorted_filters to batch-load filter functions
        # instead of N+1 pattern: get_sorted_filter_ids + get_function_by_id per id
        filter_functions = get_sorted_filters(model)

        form_data, flags = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type="inlet",
            form_data=form_data,
            extra_params=extra_params,
        )
    except Exception as e:
        raise Exception(f"Error: {e}")

    features = form_data.pop("features", None)
    if features:
        if isinstance(features.get("image_generation_options"), dict):
            metadata["image_generation_options"] = features["image_generation_options"]
        web_search_strategy = _resolve_web_search_strategy(
            request, user, model, form_data.get("model", ""), features
        )
        metadata["requested_web_search_mode"] = web_search_strategy["requested_mode"]
        metadata["effective_web_search_mode"] = web_search_strategy["effective_mode"]
        metadata["allow_native_web_search_halo_fallback"] = web_search_strategy[
            "allow_halo_retry"
        ]

        if web_search_strategy.get("notification"):
            await event_emitter(
                {
                    "type": "notification",
                    "data": {
                        "type": "info",
                        "content": web_search_strategy["notification"],
                    },
                }
            )

        if web_search_strategy["effective_mode"] == WEB_SEARCH_MODE_HALO:
            form_data = await chat_web_search_handler(
                request, form_data, extra_params, user
            )
        elif web_search_strategy["effective_mode"] == WEB_SEARCH_MODE_NATIVE:
            form_data["native_web_search"] = True

        # In native tool-calling mode, image generation should happen through the
        # `generate_image` tool only. Otherwise the request gets duplicated:
        # 1) direct pre-generation here, and
        # 2) a second generation when the model later calls `generate_image`.
        if (
            "image_generation" in features
            and features["image_generation"]
            and metadata.get("function_calling") != "native"
        ):
            form_data = await chat_image_generation_handler(
                request, form_data, extra_params, user
            )

        if "code_interpreter" in features and features["code_interpreter"]:
            form_data["messages"] = add_or_update_user_message(
                (
                    request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                    if request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE != ""
                    else DEFAULT_CODE_INTERPRETER_PROMPT
                ),
                form_data["messages"],
            )

    skill_ids = form_data.pop("skill_ids", None)
    skill_selection_touched = bool(form_data.pop("skill_selection_touched", False))
    model_skill_ids = (
        (model.get("info", {}) or {}).get("meta", {}) or {}
    ).get("skillIds", [])
    skill_context = get_selected_skill_context(
        user,
        skill_ids,
        [] if skill_selection_touched else model_skill_ids,
    )
    skill_system_prompt = build_skill_system_prompt(
        skill_context["prompt_skills"],
        requested_skill_ids=skill_context["requested_ids"],
    )
    if skill_system_prompt:
        form_data["messages"] = add_or_update_system_message(
            skill_system_prompt,
            form_data.get("messages", []),
        )

    tool_ids = form_data.pop("tool_ids", None)
    files = form_data.pop("files", None)

    # Remove files duplicates
    if files:
        files = list({json.dumps(f, sort_keys=True): f for f in files}.values())

    metadata = {
        **metadata,
        "selected_skill_ids": skill_context["resolved_ids"],
        "selected_prompt_skills": [
            {
                "id": skill.id,
                "name": skill.name,
            }
            for skill in skill_context["prompt_skills"]
        ],
        "selected_runnable_skills": build_skill_tool_context(
            skill_context["runnable_skills"]
        ),
        "skill_selection_touched": skill_selection_touched,
        "skill_ids": skill_context["resolved_ids"],
        "tool_ids": tool_ids,
        "files": files,
    }

    # J-2-02: Extract file references from chat messages and enrich metadata["files"]
    # so tools can access uploaded files via __metadata__["files"].
    try:
        message_files = _extract_files_from_messages(form_data.get("messages", []))
        if message_files:
            existing = metadata.get("files") or []
            existing_ids = {f.get("id") for f in existing if f.get("id")}
            for mf in message_files:
                if mf["id"] not in existing_ids:
                    existing.append(mf)
                    existing_ids.add(mf["id"])
            metadata["files"] = existing
    except Exception as e:
        log.debug(f"Error extracting files from messages: {e}")

    form_data["metadata"] = metadata

    # J-7-16: Separate knowledge files from regular attachments so that
    # native tool calls only see user-uploaded files, while RAG still
    # processes everything (knowledge + uploads).
    all_files = metadata.get("files") or []
    knowledge_files = [f for f in all_files if f.get("source") == "knowledge"]
    if knowledge_files:
        metadata["files"] = [f for f in all_files if f.get("source") != "knowledge"]
        metadata["knowledge_files"] = knowledge_files

    try:
        await _prepare_openai_native_file_inputs(
            request, form_data, metadata, user, model
        )
    except Exception as e:
        log.exception(e)

    try:
        await _ensure_requested_chat_file_modes(
            request, metadata, user, model, event_emitter
        )
    except Exception as e:
        log.exception(e)

    # Server side tools
    tool_ids = metadata.get("tool_ids", None)
    # Client side tools
    tool_servers = metadata.get("tool_servers", None)

    log.debug(f"{tool_ids=}")
    log.debug(f"{tool_servers=}")

    tools_dict = {}
    tool_calling_disabled = metadata.get("tool_calling_mode") == "off"

    if tool_ids and not tool_calling_disabled:
        validate_tool_ids_access(tool_ids, user, request)

        # Ensure server-side toolkits are loaded before resolving tool_ids into callable specs.
        # This keeps /api/chat/completions robust even if /api/tools hasn't been called yet.
        if any(str(tid).startswith("server:") for tid in tool_ids) and not getattr(
            request.state, "TOOL_SERVERS", None
        ):
            request.state.TOOL_SERVER_CONNECTIONS = get_user_tool_server_connections(
                request, user
            )
            request.state.TOOL_SERVERS = await get_tool_servers_data(
                request.state.TOOL_SERVER_CONNECTIONS,
                session_token=request.state.token.credentials,
            )

        if any(str(tid).startswith("mcp:") for tid in tool_ids) and not getattr(
            request.state, "MCP_SERVERS", None
        ):
            selected_mcp_indices = extract_selected_mcp_indices(tool_ids)
            request.state.MCP_SERVER_CONNECTIONS = get_user_mcp_server_connections(
                request, user
            )
            try:
                request.state.MCP_SERVERS = await get_mcp_servers_data(
                    request.state.MCP_SERVER_CONNECTIONS,
                    session_token=request.state.token.credentials,
                    selected_indices=selected_mcp_indices,
                    strict_selected=True,
                    user_id=user.id,
                )
            except RuntimeError as exc:
                log.warning(
                    "Falling back to cached MCP tool snapshot for selected servers %s: %s",
                    sorted(selected_mcp_indices),
                    exc,
                )
                request.state.MCP_SERVERS = get_mcp_servers_cached_data(
                    request.state.MCP_SERVER_CONNECTIONS,
                    selected_indices=selected_mcp_indices,
                    strict_selected=True,
                )

        await ensure_selected_shared_tool_runtime_loaded(request, user, tool_ids)

        tools_dict = get_tools(
            request,
            tool_ids,
            user,
            {
                **extra_params,
                "__model__": models[task_model_id],
                "__messages__": form_data["messages"],
                "__files__": metadata.get("files", []),
            },
        )

    if tool_servers and not tool_calling_disabled:
        for tool_server in tool_servers:
            tool_specs = tool_server.pop("specs", [])

            for tool in tool_specs:
                tools_dict[tool["name"]] = {
                    "spec": tool,
                    "direct": True,
                    "server": tool_server,
                }

    # Built-in tools are exposed when tool calling is enabled (native or compatibility/default),
    # and are still gated by admin config + permissions.
    if (
        not tool_calling_disabled
        and (
            metadata.get("function_calling") == "native"
            or tool_ids
            or tool_servers
            or metadata.get("preview_tool_compat")
            or (metadata.get("selected_runnable_skills") or {}).get("skill_ids")
        )
    ):
        builtin_tools = get_builtin_tools(request, user, metadata)
        suppressed_web_tools = _get_builtin_web_tools_to_suppress(
            metadata.get("effective_web_search_mode")
        )
        if suppressed_web_tools:
            # Keep search responsibilities in a single runtime path:
            # - HALO mode performs server-side web search up front, so `search_web`
            #   should not be re-exposed to the model in the same request.
            # - NATIVE mode should rely on the upstream provider's web search path
            #   instead of Halo fallback tools.
            for tool_name in suppressed_web_tools:
                builtin_tools.pop(tool_name, None)
        if builtin_tools:
            log.info(f"[BUILTIN TOOLS] Injecting: {list(builtin_tools.keys())}")
        for tool_name, tool in builtin_tools.items():
            if tool_name in tools_dict:
                log.warning(f"Tool {tool_name} already exists; skipping builtin version")
                continue
            tools_dict[tool_name] = tool

    if tools_dict:
        log.info(f"[TOOLS] Available tools for this request: {list(tools_dict.keys())}")
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "tool_loading",
                    "description": "Loaded {{count}} tools",
                    "count": len(tools_dict),
                    "done": True,
                },
            }
        )
        if metadata.get("function_calling") == "native":
            # If the function calling is native, then call the tools function calling handler
            metadata["tools"] = tools_dict
            form_data["tools"] = [
                {"type": "function", "function": tool.get("spec", {})}
                for tool in tools_dict.values()
            ]

            # Native tool calling relies on the upstream model/provider to return valid tool_calls.
            # Some upstreams/models occasionally hallucinate or concatenate tool names (e.g. `a+b` -> `ab`),
            # which then fails validation on the provider side with opaque 5xx errors. Add a strict rules
            # block to reduce these failures.
            try:
                tool_names = sorted(list(tools_dict.keys()))
                memory_note_guidance = ""
                if "add_memory" in tool_names and "add_note" in tool_names:
                    memory_note_guidance = (
                        "MEMORY_VS_NOTE_GUIDANCE:\n"
                        "- Use add_memory/search_memories for stable user preferences, personal facts, likes/dislikes, and recurring context.\n"
                        "- Use add_note/search_notes for note-taking, longer saved content, or when the user explicitly asks to save a note.\n"
                        "\n"
                    )
                # Keep it short; the full schemas are already passed via `tools`.
                native_tool_rules = (
                    '<native_tool_rules v="1">\n'
                    "You may call tools.\n"
                    "RULES:\n"
                    "- Only call tools from the allowed list below. Tool names must match EXACTLY.\n"
                    "- NEVER invent tools. NEVER concatenate multiple tool names into one.\n"
                    "- If multiple tools are needed, return multiple tool_calls.\n"
                    "- For tools with no parameters, use an empty JSON object: {}.\n"
                    "- For tools with parameters, arguments must be a JSON object matching the tool schema.\n"
                    "\n"
                    + memory_note_guidance
                    + "ALLOWED_TOOL_NAMES:\n"
                    + "\n".join([f"- {n}" for n in tool_names])
                    + "\n</native_tool_rules>"
                )

                existing_system = (
                    form_data.get("messages")
                    and isinstance(form_data["messages"], list)
                    and form_data["messages"][0].get("role") == "system"
                    and isinstance(form_data["messages"][0].get("content"), str)
                    and 'native_tool_rules v="1"' in form_data["messages"][0].get("content")
                )
                if not existing_system:
                    form_data["messages"] = add_or_update_system_message(
                        native_tool_rules, form_data["messages"]
                    )
            except Exception:
                # Never block the request due to prompt injection failures.
                pass
        elif not tool_calling_disabled:
            # If the function calling is not native, then call the tools function calling handler
            try:
                form_data, flags = await chat_completion_tools_handler(
                    request, form_data, extra_params, user, models, tools_dict
                )
                sources.extend(flags.get("sources", []))
                ui_sources.extend(flags.get("ui_sources", []))

            except Exception as e:
                log.exception(e)

    try:
        form_data, flags = await chat_completion_files_handler(request, form_data, user)
        sources.extend(flags.get("sources", []))
    except Exception as e:
        log.exception(e)

    # If context is not empty, insert it into the messages
    if len(sources) > 0:
        context_string = ""
        citated_file_idx = {}
        for _, source in enumerate(sources, 1):
            if "document" in source:
                for doc_context, doc_meta in zip(
                    source["document"], source["metadata"]
                ):
                    file_id = doc_meta.get("file_id")
                    if file_id not in citated_file_idx:
                        citated_file_idx[file_id] = len(citated_file_idx) + 1
                    context_string += f'<source id="{citated_file_idx[file_id]}">{doc_context}</source>\n'

        context_string = context_string.strip()
        prompt = get_last_user_message(form_data["messages"])

        if prompt is None:
            raise Exception("No user message found")
        if (
            request.app.state.config.RELEVANCE_THRESHOLD == 0
            and context_string.strip() == ""
        ):
            log.debug(
                f"With a 0 relevancy threshold for RAG, the context cannot be empty"
            )

        # Build RAG content with optional static system context prefix
        rag_content = rag_template(
            request.app.state.config.RAG_TEMPLATE, context_string, prompt
        )
        system_context = request.app.state.config.RAG_SYSTEM_CONTEXT
        if system_context and system_context.strip():
            rag_content = system_context.strip() + "\n\n" + rag_content

        # Workaround for Ollama 2.0+ system prompt issue
        # TODO: replace with add_or_update_system_message
        if model.get("owned_by") == "ollama":
            form_data["messages"] = prepend_to_first_user_message_content(
                rag_content,
                form_data["messages"],
            )
        else:
            form_data["messages"] = add_or_update_system_message(
                rag_content,
                form_data["messages"],
            )

    _apply_prepared_openai_native_file_inputs(form_data, metadata)
    if metadata.get("native_file_input_file_ids"):
        form_data["native_file_inputs"] = True

    # If there are citations, add them to the data_items
    sources = [source for source in sources if source.get("source", {}).get("name", "")]
    ui_sources = [source for source in ui_sources if source.get("source", {}).get("name", "")]

    sources_for_ui = []
    if sources or ui_sources:
        seen = set()
        for s in [*sources, *ui_sources]:
            try:
                key = None
                meta = (s or {}).get("metadata") or []
                if isinstance(meta, list) and meta and isinstance(meta[0], dict):
                    key = meta[0].get("source") or meta[0].get("url")
                if not key:
                    src = (s or {}).get("source") or {}
                    if isinstance(src, dict):
                        key = src.get("id") or src.get("url") or src.get("name")
                key = str(key or "")
                if key and key in seen:
                    continue
                if key:
                    seen.add(key)
            except Exception:
                pass
            sources_for_ui.append(s)

        if sources_for_ui:
            events.append({"sources": sources_for_ui})

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": True,
                    "hidden": True,
                },
            }
        )

    return form_data, metadata, events


async def process_chat_response(
    request, response, form_data, user, metadata, model, events, tasks
):
    async def background_tasks_handler():
        message_map = Chats.get_messages_by_chat_id(metadata["chat_id"])
        message = message_map.get(metadata["message_id"]) if message_map else None

        if message:
            messages = get_message_list(message_map, message.get("id"))

            # Strip reasoning/thinking HTML blocks from message content to avoid
            # wasting tokens on title/tags/follow-up generation (H-12 KV cache protection).
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("content"):
                    msg["content"] = strip_reasoning_details(msg["content"])

            if tasks and messages:
                if TASKS.TITLE_GENERATION in tasks:
                    if tasks[TASKS.TITLE_GENERATION]:
                        res = await generate_title(
                            request,
                            {
                                "model": message["model"],
                                "messages": messages,
                                "chat_id": metadata["chat_id"],
                            },
                            user,
                        )

                        if res and isinstance(res, dict):
                            if len(res.get("choices", [])) == 1:
                                title_string = (
                                    res.get("choices", [])[0]
                                    .get("message", {})
                                    .get("content")
                                ) or message.get("content") or "New Chat"
                            else:
                                title_string = ""

                            title_string = title_string[
                                title_string.find("{") : title_string.rfind("}") + 1
                            ]

                            try:
                                title = json.loads(title_string).get(
                                    "title", "New Chat"
                                )
                            except Exception as e:
                                title = ""

                            if not title:
                                title = messages[0].get("content", "New Chat")

                            Chats.update_chat_title_by_id(metadata["chat_id"], title)

                            await event_emitter(
                                {
                                    "type": "chat:title",
                                    "data": title,
                                }
                            )
                    elif len(messages) == 2:
                        title = messages[0].get("content", "New Chat")

                        Chats.update_chat_title_by_id(metadata["chat_id"], title)

                        await event_emitter(
                            {
                                "type": "chat:title",
                                "data": message.get("content", "New Chat"),
                            }
                        )

                if TASKS.TAGS_GENERATION in tasks and tasks[TASKS.TAGS_GENERATION]:
                    res = await generate_chat_tags(
                        request,
                        {
                            "model": message["model"],
                            "messages": messages,
                            "chat_id": metadata["chat_id"],
                        },
                        user,
                    )

                    if res and isinstance(res, dict):
                        if len(res.get("choices", [])) == 1:
                            tags_string = (
                                res.get("choices", [])[0]
                                .get("message", {})
                                .get("content", "")
                            )
                        else:
                            tags_string = ""

                        tags_string = tags_string[
                            tags_string.find("{") : tags_string.rfind("}") + 1
                        ]

                        try:
                            tags = json.loads(tags_string).get("tags", [])
                            Chats.update_chat_tags_by_id(
                                metadata["chat_id"], tags, user
                            )

                            await event_emitter(
                                {
                                    "type": "chat:tags",
                                    "data": tags,
                                }
                            )
                        except Exception as e:
                            pass

                if (
                    TASKS.FOLLOW_UP_GENERATION in tasks
                    and tasks[TASKS.FOLLOW_UP_GENERATION]
                ):
                    try:
                        res = await generate_follow_ups(
                            request,
                            {
                                "model": message["model"],
                                "messages": messages,
                                "chat_id": metadata["chat_id"],
                            },
                            user,
                        )

                        if res and isinstance(res, dict):
                            if len(res.get("choices", [])) == 1:
                                fu_string = (
                                    res.get("choices", [])[0]
                                    .get("message", {})
                                    .get("content", "")
                                )
                            else:
                                fu_string = ""

                            fu_string = fu_string[
                                fu_string.find("{") : fu_string.rfind("}") + 1
                            ]

                            follow_ups = json.loads(fu_string).get(
                                "follow_ups", []
                            )

                            if follow_ups and isinstance(follow_ups, list):
                                await event_emitter(
                                    {
                                        "type": "chat:message:follow_ups",
                                        "data": {
                                            "follow_ups": follow_ups[:3],
                                        },
                                    }
                                )
                    except Exception as e:
                        log.debug(f"Error generating follow-ups: {e}")

    event_emitter = None
    event_caller = None
    if (
        "session_id" in metadata
        and metadata["session_id"]
        and "chat_id" in metadata
        and metadata["chat_id"]
        and "message_id" in metadata
        and metadata["message_id"]
    ):
        event_emitter = get_event_emitter(metadata)
        event_caller = get_event_call(metadata)

    # Non-streaming response
    if not isinstance(response, StreamingResponse):
        if event_emitter:
            allow_base64_image_url_conversion = bool(
                getattr(
                    request.app.state.config,
                    "ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION",
                    False,
                )
            )
            if "error" in response:
                error = response["error"].get("detail", response["error"])
                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "error": {"content": error},
                    },
                )

            choices = response.get("choices", [])
            if choices and isinstance(choices[0], dict):
                message_payload = choices[0].get("message", {}) or {}
                content, message_files = _extract_stream_content_and_files(
                    message_payload,
                    allow_base64_image_url_conversion=allow_base64_image_url_conversion,
                )
                response_message = response["choices"][0].setdefault("message", {})

                if message_files:
                    await event_emitter(
                        {
                            "type": "files",
                            "data": {"files": message_files},
                        }
                    )

                    response_message["files"] = message_files

                if isinstance(response_message, dict):
                    response_message["content"] = content

                if content or message_files:
                    completed_at = int(time.time())

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": response,
                        }
                    )

                    title = Chats.get_chat_title_by_id(metadata["chat_id"])

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "done": True,
                                "content": content,
                                "title": title,
                                "completedAt": completed_at,
                                **({"files": message_files} if message_files else {}),
                            },
                        }
                    )

                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": content,
                            "done": True,
                            "completedAt": completed_at,
                            **({"files": message_files} if message_files else {}),
                        },
                    )

                    # Send a webhook notification if the user is not active
                    if get_active_status_by_user_id(user.id) is None:
                        webhook_url = Users.get_user_webhook_url_by_id(user.id)
                        if webhook_url:
                            post_webhook(
                                request.app.state.WEBUI_NAME,
                                webhook_url,
                                f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                                {
                                    "action": "chat",
                                    "message": content,
                                    "title": title,
                                    "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                                },
                            )

                    await background_tasks_handler()

            return response
        else:
            return response

    # Non standard response
    if not any(
        content_type in response.headers["Content-Type"]
        for content_type in ["text/event-stream", "application/x-ndjson"]
    ):
        return response

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
        },
        "__metadata__": metadata,
        "__request__": request,
        "__model__": model,
    }
    # J-3-04: Batch-load filter functions (eliminates N+1 get_function_by_id calls)
    filter_functions = get_sorted_filters(model)

    # Streaming response
    if event_emitter and event_caller:
        task_id = str(uuid4())  # Create a unique task ID.
        model_id = form_data.get("model", "")

        Chats.upsert_message_to_chat_by_id_and_message_id(
            metadata["chat_id"],
            metadata["message_id"],
            {
                "model": model_id,
            },
        )

        def split_content_and_whitespace(content):
            content_stripped = content.rstrip()
            original_whitespace = (
                content[len(content_stripped) :]
                if len(content) > len(content_stripped)
                else ""
            )
            return content_stripped, original_whitespace

        def is_opening_code_block(content):
            backtick_segments = content.split("```")
            # Even number of segments means the last backticks are opening a new block
            return len(backtick_segments) > 1 and len(backtick_segments) % 2 == 0

        # Handle as a background task
        async def post_response_handler(response, events):
            def serialize_content_blocks(content_blocks, raw=False):
                content = ""

                for block in content_blocks:
                    if block["type"] == "text":
                        content = f"{content}{block['content'].strip()}\n"
                    elif block["type"] == "tool_calls":
                        attributes = block.get("attributes", {})

                        tool_calls = block.get("content", [])
                        results = block.get("results", [])

                        if results:

                            tool_calls_display_content = ""
                            for tool_call in tool_calls:

                                tool_call_id = tool_call.get("id", "")
                                tool_name = tool_call.get("function", {}).get(
                                    "name", ""
                                )
                                tool_arguments = tool_call.get("function", {}).get(
                                    "arguments", ""
                                )

                                tool_result = None
                                tool_result_files = None
                                for result in results:
                                    if tool_call_id == result.get("tool_call_id", ""):
                                        tool_result = result.get("content", None)
                                        tool_result_files = result.get("files", None)
                                        break

                                if tool_result:
                                    tool_calls_display_content = f'{tool_calls_display_content}\n<details type="tool_calls" done="true" id="{tool_call_id}" name="{tool_name}" arguments="{html.escape(json.dumps(tool_arguments))}" result="{html.escape(json.dumps(tool_result))}" files="{html.escape(json.dumps(tool_result_files)) if tool_result_files else ""}">\n<summary>Tool Executed</summary>\n</details>\n'
                                else:
                                    tool_calls_display_content = f'{tool_calls_display_content}\n<details type="tool_calls" done="false" id="{tool_call_id}" name="{tool_name}" arguments="{html.escape(json.dumps(tool_arguments))}">\n<summary>Executing...</summary>\n</details>'

                            if not raw:
                                content = f"{content}\n{tool_calls_display_content}\n\n"
                        else:
                            tool_calls_display_content = ""

                            for tool_call in tool_calls:
                                tool_call_id = tool_call.get("id", "")
                                tool_name = tool_call.get("function", {}).get(
                                    "name", ""
                                )
                                tool_arguments = tool_call.get("function", {}).get(
                                    "arguments", ""
                                )

                                tool_calls_display_content = f'{tool_calls_display_content}\n<details type="tool_calls" done="false" id="{tool_call_id}" name="{tool_name}" arguments="{html.escape(json.dumps(tool_arguments))}">\n<summary>Executing...</summary>\n</details>'

                            if not raw:
                                content = f"{content}\n{tool_calls_display_content}\n\n"

                    elif block["type"] == "reasoning":
                        reasoning_display_content = "\n".join(
                            (f"> {line}" if not line.startswith(">") else line)
                            for line in block["content"].splitlines()
                        )

                        reasoning_duration = block.get("duration", None)

                        if reasoning_duration is not None:
                            if raw:
                                content = f'{content}\n<{block["start_tag"]}>{block["content"]}<{block["end_tag"]}>\n'
                            else:
                                content = f'{content}\n<details type="reasoning" done="true" duration="{reasoning_duration}">\n<summary>Thought for {reasoning_duration} seconds</summary>\n{reasoning_display_content}\n</details>\n'
                        else:
                            if raw:
                                content = f'{content}\n<{block["start_tag"]}>{block["content"]}<{block["end_tag"]}>\n'
                            else:
                                content = f'{content}\n<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n{reasoning_display_content}\n</details>\n'

                    elif block["type"] == "code_interpreter":
                        attributes = block.get("attributes", {})
                        output = block.get("output", None)
                        lang = attributes.get("lang", "")

                        content_stripped, original_whitespace = (
                            split_content_and_whitespace(content)
                        )
                        if is_opening_code_block(content_stripped):
                            # Remove trailing backticks that would open a new block
                            content = (
                                content_stripped.rstrip("`").rstrip()
                                + original_whitespace
                            )
                        else:
                            # Keep content as is - either closing backticks or no backticks
                            content = content_stripped + original_whitespace

                        if output:
                            output = html.escape(json.dumps(output))

                            if raw:
                                content = f'{content}\n<code_interpreter type="code" lang="{lang}">\n{block["content"]}\n</code_interpreter>\n```output\n{output}\n```\n'
                            else:
                                content = f'{content}\n<details type="code_interpreter" done="true" output="{output}">\n<summary>Analyzed</summary>\n```{lang}\n{block["content"]}\n```\n</details>\n'
                        else:
                            if raw:
                                content = f'{content}\n<code_interpreter type="code" lang="{lang}">\n{block["content"]}\n</code_interpreter>\n'
                            else:
                                content = f'{content}\n<details type="code_interpreter" done="false">\n<summary>Analyzing...</summary>\n```{lang}\n{block["content"]}\n```\n</details>\n'

                    else:
                        block_content = str(block["content"]).strip()
                        content = f"{content}{block['type']}: {block_content}\n"

                return content.strip()

            def convert_content_blocks_to_messages(content_blocks):
                messages = []

                temp_blocks = []
                for idx, block in enumerate(content_blocks):
                    if block["type"] == "tool_calls":
                        serialized = serialize_content_blocks(temp_blocks)
                        messages.append(
                            {
                                "role": "assistant",
                                "content": serialized if serialized else None,
                                "tool_calls": block.get("content"),
                            }
                        )

                        results = block.get("results", [])

                        for result in results:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": result["tool_call_id"],
                                    "content": result["content"],
                                }
                            )
                        temp_blocks = []
                    else:
                        temp_blocks.append(block)

                if temp_blocks:
                    content = serialize_content_blocks(temp_blocks)
                    if content:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": content,
                            }
                        )

                return messages

            def trim_content_blocks_for_finalize(blocks, max_result_chars: int = 500):
                trimmed_blocks = []
                for block in blocks if isinstance(blocks, list) else []:
                    if not isinstance(block, dict):
                        continue

                    if block.get("type") != "tool_calls":
                        trimmed_blocks.append(block)
                        continue

                    new_block = dict(block)

                    raw_calls = block.get("content")
                    if isinstance(raw_calls, list):
                        new_block["content"] = raw_calls[:6]

                    raw_results = block.get("results")
                    trimmed_results = []
                    if isinstance(raw_results, list):
                        for result in raw_results[:6]:
                            if not isinstance(result, dict):
                                continue
                            new_result = dict(result)
                            new_result["content"] = _truncate_text(
                                str(new_result.get("content") or ""),
                                max_result_chars,
                            )
                            trimmed_results.append(new_result)
                    new_block["results"] = trimmed_results

                    trimmed_blocks.append(new_block)

                return trimmed_blocks

            def collect_recent_tool_names(blocks, limit: int = 6) -> list[str]:
                names: list[str] = []
                seen: set[str] = set()
                for block in reversed(blocks if isinstance(blocks, list) else []):
                    if not isinstance(block, dict) or block.get("type") != "tool_calls":
                        continue
                    calls = block.get("content")
                    if not isinstance(calls, list):
                        continue
                    for tc in reversed(calls):
                        if not isinstance(tc, dict):
                            continue
                        tool_name = str(((tc.get("function") or {}).get("name") or "")).strip()
                        if not tool_name or tool_name in seen:
                            continue
                        seen.add(tool_name)
                        names.append(tool_name)
                        if len(names) >= limit:
                            return names
                return names

            async def emit_tool_orchestration_status(stage: str, done: bool = False, **extra):
                data = {
                    "action": "tool_orchestration",
                    "stage": stage,
                    "done": done,
                    "hidden": True,
                }
                data.update(extra)
                try:
                    await event_emitter(
                        {
                            "type": "status",
                            "data": data,
                        }
                    )
                except Exception:
                    pass

            def stringify_stream_content(value) -> str:
                if isinstance(value, list):
                    parts = []
                    for item in value:
                        if isinstance(item, dict):
                            item_type = item.get("type")
                            if item_type in (
                                "text",
                                "output_text",
                                "input_text",
                                "assistant_text",
                            ):
                                parts.append(item.get("text") or item.get("content") or "")
                            elif item_type in (None, ""):
                                parts.append(item.get("text") or item.get("content") or "")
                        elif isinstance(item, str):
                            parts.append(item)
                    return "".join(parts)

                if isinstance(value, dict):
                    return (
                        value.get("text")
                        or value.get("content")
                        or value.get("value")
                        or ""
                    )

                if isinstance(value, str):
                    return value

                return ""

            def extract_reasoning_content(message_or_delta: dict) -> str:
                if not isinstance(message_or_delta, dict):
                    return ""

                value = (
                    message_or_delta.get("reasoning_content")
                    or message_or_delta.get("reasoning")
                    or message_or_delta.get("thinking")
                    or message_or_delta.get("thinking_content")
                    or message_or_delta.get("thought")
                    or message_or_delta.get("thought_content")
                )
                return stringify_stream_content(value)

            def tag_content_handler(content_type, tags, content, content_blocks):
                end_flag = False

                def extract_attributes(tag_content):
                    """Extract attributes from a tag if they exist."""
                    attributes = {}
                    if not tag_content:  # Ensure tag_content is not None
                        return attributes
                    # Match attributes in the format: key="value" (ignores single quotes for simplicity)
                    matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
                    for key, value in matches:
                        attributes[key] = value
                    return attributes

                if content_blocks[-1]["type"] == "text":
                    for start_tag, end_tag in tags:
                        # Match start tag e.g., <tag> or <tag attr="value">
                        start_tag_pattern = rf"<{re.escape(start_tag)}(\s.*?)?>"
                        match = re.search(start_tag_pattern, content)
                        if match:
                            attr_content = (
                                match.group(1) if match.group(1) else ""
                            )  # Ensure it's not None
                            attributes = extract_attributes(
                                attr_content
                            )  # Extract attributes safely

                            # Capture everything before and after the matched tag
                            before_tag = content[
                                : match.start()
                            ]  # Content before opening tag
                            after_tag = content[
                                match.end() :
                            ]  # Content after opening tag

                            # Remove the start tag and after from the currently handling text block
                            content_blocks[-1]["content"] = content_blocks[-1][
                                "content"
                            ].replace(match.group(0) + after_tag, "")

                            if before_tag:
                                content_blocks[-1]["content"] = before_tag

                            if not content_blocks[-1]["content"]:
                                content_blocks.pop()

                            # Append the new block
                            content_blocks.append(
                                {
                                    "type": content_type,
                                    "start_tag": start_tag,
                                    "end_tag": end_tag,
                                    "attributes": attributes,
                                    "content": "",
                                    "started_at": time.time(),
                                }
                            )

                            if after_tag:
                                content_blocks[-1]["content"] = after_tag

                            break
                elif content_blocks[-1]["type"] == content_type:
                    start_tag = content_blocks[-1]["start_tag"]
                    end_tag = content_blocks[-1]["end_tag"]
                    # Match end tag e.g., </tag>
                    end_tag_pattern = rf"<{re.escape(end_tag)}>"

                    # Check if the content has the end tag
                    if re.search(end_tag_pattern, content):
                        end_flag = True

                        block_content = content_blocks[-1]["content"]
                        # Strip start and end tags from the content
                        start_tag_pattern = rf"<{re.escape(start_tag)}(.*?)>"
                        block_content = re.sub(
                            start_tag_pattern, "", block_content
                        ).strip()

                        end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                        split_content = end_tag_regex.split(block_content, maxsplit=1)

                        # Content inside the tag
                        block_content = (
                            split_content[0].strip() if split_content else ""
                        )

                        # Leftover content (everything after `</tag>`)
                        leftover_content = (
                            split_content[1].strip() if len(split_content) > 1 else ""
                        )

                        if block_content:
                            content_blocks[-1]["content"] = block_content
                            content_blocks[-1]["ended_at"] = time.time()
                            content_blocks[-1]["duration"] = round(
                                content_blocks[-1]["ended_at"]
                                - content_blocks[-1]["started_at"], 1
                            )

                            # Reset the content_blocks by appending a new text block
                            if content_type != "code_interpreter":
                                if leftover_content:

                                    content_blocks.append(
                                        {
                                            "type": "text",
                                            "content": leftover_content,
                                        }
                                    )
                                else:
                                    content_blocks.append(
                                        {
                                            "type": "text",
                                            "content": "",
                                        }
                                    )

                        else:
                            # Remove the block if content is empty
                            content_blocks.pop()

                            if leftover_content:
                                content_blocks.append(
                                    {
                                        "type": "text",
                                        "content": leftover_content,
                                    }
                                )
                            else:
                                content_blocks.append(
                                    {
                                        "type": "text",
                                        "content": "",
                                    }
                                )

                        # Clean processed content
                        content = re.sub(
                            rf"<{re.escape(start_tag)}(.*?)>(.|\n)*?<{re.escape(end_tag)}>",
                            "",
                            content,
                            flags=re.DOTALL,
                        )

                return content, content_blocks, end_flag

            message = Chats.get_message_by_id_and_message_id(
                metadata["chat_id"], metadata["message_id"]
            )

            tool_calls = []
            tool_call_batch_repairs = []

            last_assistant_message = None
            try:
                if form_data["messages"][-1]["role"] == "assistant":
                    last_assistant_message = get_last_assistant_message(
                        form_data["messages"]
                    )
            except Exception as e:
                pass

            content = (
                message.get("content", "")
                if message
                else last_assistant_message if last_assistant_message else ""
            )
            message_files = _merge_message_files(
                message.get("files") if message else None, None
            )
            pending_stream_images: dict[str, dict[str, Any]] = {}

            content_blocks = [
                {
                    "type": "text",
                    "content": content,
                }
            ]

            # Accumulate usage (token counts) across all LLM rounds so the
            # frontend info button can display them even after tool orchestration.
            accumulated_usage: dict = {}

            def _merge_usage(incoming: dict) -> None:
                """Merge *incoming* usage dict into accumulated_usage (in-place)."""
                for k, v in incoming.items():
                    if isinstance(v, (int, float)):
                        accumulated_usage[k] = accumulated_usage.get(k, 0) + v
                    else:
                        accumulated_usage[k] = v

            # We might want to disable this by default
            DETECT_REASONING = True
            DETECT_SOLUTION = True
            DETECT_CODE_INTERPRETER = metadata.get("features", {}).get(
                "code_interpreter", False
            )

            # Use custom reasoning tags from config if available, else defaults
            custom_tags_str = getattr(
                getattr(request.app.state, "config", None),
                "CUSTOM_REASONING_TAGS",
                "",
            )
            if custom_tags_str:
                import json as _json

                try:
                    custom_list = _json.loads(custom_tags_str)
                    reasoning_tags = [
                        (pair[0], pair[1]) for pair in custom_list if len(pair) == 2
                    ]
                except Exception:
                    reasoning_tags = []
            else:
                reasoning_tags = [
                    ("think", "/think"),
                    ("thinking", "/thinking"),
                    ("reason", "/reason"),
                    ("reasoning", "/reasoning"),
                    ("thought", "/thought"),
                    ("Thought", "/Thought"),
                    ("|begin_of_thought|", "|end_of_thought|"),
                ]

            code_interpreter_tags = [("code_interpreter", "/code_interpreter")]

            solution_tags = [("|begin_of_solution|", "|end_of_solution|")]

            def _merge_stream_tool_field(existing: str, incoming: str) -> str:
                if not isinstance(existing, str):
                    existing = str(existing or "")
                if not isinstance(incoming, str):
                    incoming = str(incoming or "")

                if not incoming:
                    return existing
                if not existing:
                    return incoming

                # Exact duplicate chunks (common with some upstream retransmissions).
                # NOTE: do NOT use "incoming in existing" — short streaming deltas
                # (e.g. 'y"') are common substrings of the accumulated buffer
                # (e.g. 'query"') and get silently dropped, producing malformed JSON.
                if incoming == existing:
                    return existing

                # Upstream sends the full value again; prefer the fuller incoming value.
                if existing in incoming:
                    repeat_count = len(incoming) // len(existing) if existing else 0
                    if repeat_count >= 2 and existing * repeat_count == incoming:
                        return existing
                    return incoming

                # Duplicate head/tail fragment.
                if existing.startswith(incoming) or existing.endswith(incoming):
                    return existing

                # Normal streaming append with overlap detection.
                max_overlap = min(len(existing), len(incoming))
                for overlap in range(max_overlap, 0, -1):
                    if existing.endswith(incoming[:overlap]):
                        return existing + incoming[overlap:]

                return existing + incoming

            try:
                for event in events:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": event,
                        }
                    )

                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            **event,
                        },
                    )

                _stream_api_error = None
                _stream_response_status = None
                _stream_non_sse_error_lines: list[str] = []

                async def stream_body_handler(response):
                    nonlocal content
                    nonlocal content_blocks
                    nonlocal message_files
                    nonlocal _stream_api_error
                    nonlocal _stream_response_status
                    nonlocal _stream_non_sse_error_lines

                    response_tool_calls = []
                    tool_call_lookup: dict[tuple[str, str], int] = {}
                    stream_tool_repairs: list[dict] = []
                    recovered_missing_index_keys: set[str] = set()
                    emitted_url_citations: set[str] = set()

                    async def emit_new_message_files(new_files: list[dict]) -> None:
                        nonlocal message_files

                        if not new_files:
                            return

                        previous_keys = {
                            json.dumps(file, ensure_ascii=False, sort_keys=True)
                            for file in _merge_message_files(None, message_files)
                        }
                        message_files = _merge_message_files(message_files, new_files)
                        delta_files = [
                            file
                            for file in message_files
                            if json.dumps(file, ensure_ascii=False, sort_keys=True)
                            not in previous_keys
                        ]

                        if not delta_files:
                            return

                        await event_emitter(
                            {
                                "type": "files",
                                "data": {"files": delta_files},
                            }
                        )

                    allow_base64_image_url_conversion = bool(
                        getattr(
                            request.app.state.config,
                            "ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION",
                            False,
                        )
                    )

                    _stream_line_count = 0
                    _stream_data_count = 0
                    _stream_skip_count = 0
                    _stream_finish_reason = None
                    _stream_exit_reason = "normal"  # normal | break_code_interpreter | exception
                    try:
                        _stream_response_status = int(
                            getattr(response, "status_code", None) or 0
                        )
                    except Exception:
                        _stream_response_status = None

                    async for line in response.body_iterator:
                        line = line.decode("utf-8") if isinstance(line, bytes) else line
                        data = line
                        _stream_line_count += 1

                        # Skip empty lines
                        if not data.strip():
                            continue

                        # "data:" is the prefix for each event
                        if not data.startswith("data:"):
                            _stream_skip_count += 1
                            stripped = data.strip()
                            if (
                                stripped
                                and _stream_response_status
                                and _stream_response_status >= 400
                            ):
                                _stream_non_sse_error_lines.append(
                                    _truncate_text(stripped, 4000)
                                )
                                if _stream_api_error is None:
                                    candidate_error = _normalize_api_error(
                                        stripped,
                                        status_override=_stream_response_status,
                                    )
                                    if candidate_error.get("message"):
                                        _stream_api_error = candidate_error
                            if _stream_skip_count <= 3:
                                log.info("[STREAM] skipped non-data line: %s", data.strip()[:200])
                            continue

                        _stream_data_count += 1

                        # Remove the prefix
                        data = data[len("data:") :].strip()

                        try:
                            data = json.loads(data)

                            data, _ = await process_filter_functions(
                                request=request,
                                filter_functions=filter_functions,
                                filter_type="stream",
                                form_data=data,
                                extra_params=extra_params,
                            )

                            if data:
                                if "event" in data:
                                    await event_emitter(data.get("event", {}))

                                # Capture usage before any data reassignment.
                                _raw_usage = data.get("usage") if isinstance(data, dict) else None
                                if isinstance(_raw_usage, dict) and _raw_usage:
                                    _merge_usage(_raw_usage)

                                choices = data.get("choices", [])

                                # ── Per-event diagnostic logging ──
                                _evt_finish_reason = None
                                if choices and isinstance(choices[0], dict):
                                    _evt_finish_reason = choices[0].get("finish_reason")
                                    if _evt_finish_reason is not None:
                                        _stream_finish_reason = _evt_finish_reason
                                _evt_delta = (choices[0].get("delta", {}) or {}) if choices and isinstance(choices[0], dict) else {}
                                _evt_has_content = bool(_evt_delta.get("content"))
                                _evt_has_reasoning = bool(extract_reasoning_content(_evt_delta)) if _evt_delta else False
                                _evt_has_tool_calls = bool(_evt_delta.get("tool_calls"))
                                _evt_content_snippet = str(_evt_delta.get("content", ""))[:80] if _evt_has_content else ""

                                if _stream_data_count <= 5 or _stream_data_count % 20 == 0 or _evt_finish_reason:
                                    log.info(
                                        "[STREAM EVT #%d] finish_reason=%s has_content=%s has_reasoning=%s "
                                        "has_tools=%s has_usage=%s accumulated_len=%d snippet=%s",
                                        _stream_data_count, _evt_finish_reason,
                                        _evt_has_content, _evt_has_reasoning,
                                        _evt_has_tool_calls, bool(_raw_usage),
                                        len(content), repr(_evt_content_snippet),
                                    )

                                if not choices:
                                    error = data.get("error", {})
                                    if error:
                                        _stream_api_error = error
                                        await event_emitter(
                                            {
                                                "type": "chat:completion",
                                                "data": {
                                                    "error": error,
                                                },
                                            }
                                        )
                                    if _raw_usage:
                                        await event_emitter(
                                            {
                                                "type": "chat:completion",
                                                "data": {
                                                    "usage": accumulated_usage,
                                                },
                                            }
                                        )
                                    continue

                                choice = choices[0] if isinstance(choices[0], dict) else {}
                                delta = choice.get("delta", {})

                                message = choice.get("message")
                                if (
                                    (not delta or (isinstance(delta, dict) and not delta))
                                    and isinstance(message, dict)
                                ):
                                    message_content = message.get("content")
                                    message_reasoning = extract_reasoning_content(message)
                                    message_tool_calls = message.get("tool_calls")

                                    delta = {}
                                    if message_content is not None:
                                        delta["content"] = message_content
                                    if message_reasoning:
                                        delta["reasoning_content"] = message_reasoning
                                    if message_tool_calls:
                                        delta["tool_calls"] = message_tool_calls

                                delta_tool_calls = delta.get("tool_calls", None)

                                # URL citations (OpenAI-style delta annotations).
                                annotations = delta.get("annotations")
                                if isinstance(annotations, list) and annotations:
                                    for annotation in annotations:
                                        if not isinstance(annotation, dict):
                                            continue
                                        if (
                                            annotation.get("type") == "url_citation"
                                            and isinstance(annotation.get("url_citation"), dict)
                                        ):
                                            url_citation = annotation.get("url_citation") or {}
                                            url = str(url_citation.get("url") or "").strip()
                                            if not url:
                                                continue
                                            title = str(url_citation.get("title") or url).strip()
                                            dedupe_key = url
                                            if dedupe_key in emitted_url_citations:
                                                continue
                                            emitted_url_citations.add(dedupe_key)
                                            source = {
                                                "source": {
                                                    "id": url,
                                                    "name": title or url,
                                                    "type": "url",
                                                    "url": url,
                                                },
                                                "document": [title or url],
                                                "metadata": [
                                                    {
                                                        "source": f"web:{url}",
                                                        "name": title or url,
                                                        "url": url,
                                                    }
                                                ],
                                            }
                                            await event_emitter({"type": "source", "data": source})

                                if delta_tool_calls:
                                    for pos, delta_tool_call in enumerate(
                                        delta_tool_calls
                                    ):
                                        if not isinstance(delta_tool_call, dict):
                                            continue

                                        incoming_index = delta_tool_call.get("index")
                                        incoming_id = delta_tool_call.get("id")

                                        key_candidates: list[tuple[str, str]] = []
                                        if incoming_index is not None:
                                            key_candidates.append(
                                                ("index", str(incoming_index))
                                            )
                                        if incoming_id:
                                            key_candidates.append(("id", str(incoming_id)))
                                        if not key_candidates:
                                            key_candidates.append(("pos", str(pos)))

                                        resolved_idx = None
                                        for key in key_candidates:
                                            if key in tool_call_lookup:
                                                resolved_idx = tool_call_lookup[key]
                                                break

                                        if resolved_idx is None:
                                            new_tool_call = dict(delta_tool_call)
                                            new_tool_call["function"] = dict(
                                                delta_tool_call.get("function", {}) or {}
                                            )
                                            new_tool_call["function"].setdefault(
                                                "name", ""
                                            )
                                            new_tool_call["function"].setdefault(
                                                "arguments", ""
                                            )

                                            if new_tool_call.get("index") is None:
                                                new_tool_call["index"] = len(
                                                    response_tool_calls
                                                )
                                                marker = (
                                                    f"id:{incoming_id}"
                                                    if incoming_id
                                                    else f"pos:{pos}"
                                                )
                                                if (
                                                    marker
                                                    not in recovered_missing_index_keys
                                                ):
                                                    recovered_missing_index_keys.add(
                                                        marker
                                                    )
                                                    stream_tool_repairs.append(
                                                        {
                                                            "action": "recover_missing_index",
                                                            "from": {
                                                                "id": incoming_id
                                                                or "",
                                                                "index": "(missing)",
                                                            },
                                                            "to": {
                                                                "index": new_tool_call[
                                                                    "index"
                                                                ]
                                                            },
                                                        }
                                                    )

                                            response_tool_calls.append(new_tool_call)
                                            resolved_idx = len(response_tool_calls) - 1
                                        else:
                                            current_response_tool_call = (
                                                response_tool_calls[resolved_idx]
                                            )
                                            current_response_tool_call.setdefault(
                                                "function", {}
                                            )
                                            current_response_tool_call[
                                                "function"
                                            ].setdefault("name", "")
                                            current_response_tool_call[
                                                "function"
                                            ].setdefault("arguments", "")

                                            if incoming_id and not current_response_tool_call.get(
                                                "id"
                                            ):
                                                current_response_tool_call[
                                                    "id"
                                                ] = incoming_id

                                            if (
                                                incoming_index is not None
                                                and current_response_tool_call.get(
                                                    "index"
                                                )
                                                is None
                                            ):
                                                current_response_tool_call[
                                                    "index"
                                                ] = incoming_index

                                            delta_name = (
                                                delta_tool_call.get("function", {})
                                                or {}
                                            ).get("name")
                                            delta_arguments = (
                                                delta_tool_call.get("function", {})
                                                or {}
                                            ).get("arguments")

                                            if delta_name:
                                                # Only accept the function name from
                                                # the first delta; subsequent deltas
                                                # for the same tool call that repeat
                                                # the name should be ignored to avoid
                                                # concatenation (e.g. "search_websearch_web").
                                                existing_name = current_response_tool_call[
                                                    "function"
                                                ].get("name", "")
                                                if not existing_name:
                                                    current_response_tool_call[
                                                        "function"
                                                    ]["name"] = delta_name

                                            if delta_arguments:
                                                current_response_tool_call[
                                                    "function"
                                                ][
                                                    "arguments"
                                                ] = _merge_stream_tool_field(
                                                    current_response_tool_call[
                                                        "function"
                                                    ].get("arguments", ""),
                                                    delta_arguments,
                                                )

                                        current_tool_call = response_tool_calls[
                                            resolved_idx
                                        ]
                                        current_tool_call.setdefault("function", {})
                                        current_tool_call["function"].setdefault(
                                            "name", ""
                                        )
                                        current_tool_call["function"].setdefault(
                                            "arguments", ""
                                        )

                                        if current_tool_call.get("index") is not None:
                                            tool_call_lookup[
                                                (
                                                    "index",
                                                    str(current_tool_call.get("index")),
                                                )
                                            ] = resolved_idx
                                        if current_tool_call.get("id"):
                                            tool_call_lookup[
                                                ("id", str(current_tool_call.get("id")))
                                            ] = resolved_idx

                                        for key in key_candidates:
                                            tool_call_lookup[key] = resolved_idx

                                value, streamed_files = _extract_stream_content_and_files(
                                    delta,
                                    allow_base64_image_url_conversion=allow_base64_image_url_conversion,
                                )
                                streamed_image = _consume_stream_image_delta(
                                    pending_stream_images,
                                    delta.get("image"),
                                )
                                if streamed_image:
                                    streamed_files = _merge_message_files(
                                        streamed_files, [streamed_image]
                                    )
                                if streamed_files:
                                    await emit_new_message_files(streamed_files)

                                reasoning_content = extract_reasoning_content(delta)
                                if not reasoning_content and isinstance(choice, dict):
                                    reasoning_content = extract_reasoning_content(choice)
                                if reasoning_content:
                                    if (
                                        not content_blocks
                                        or content_blocks[-1]["type"] != "reasoning"
                                    ):
                                        reasoning_block = {
                                            "type": "reasoning",
                                            "start_tag": "think",
                                            "end_tag": "/think",
                                            "attributes": {"type": "reasoning_content"},
                                            "content": "",
                                            "started_at": time.time(),
                                        }
                                        content_blocks.append(reasoning_block)
                                    else:
                                        reasoning_block = content_blocks[-1]

                                    reasoning_block["content"] += reasoning_content

                                    data = {
                                        "content": serialize_content_blocks(content_blocks),
                                        **(
                                            {"files": message_files}
                                            if message_files
                                            else {}
                                        ),
                                    }

                                if (
                                    streamed_files
                                    and not value
                                    and not reasoning_content
                                    and not delta_tool_calls
                                    and not annotations
                                    and not _raw_usage
                                ):
                                    continue

                                if value:
                                        if (
                                            content_blocks
                                            and content_blocks[-1]["type"]
                                            == "reasoning"
                                            and content_blocks[-1]
                                            .get("attributes", {})
                                            .get("type")
                                            == "reasoning_content"
                                        ):
                                            reasoning_block = content_blocks[-1]
                                            reasoning_block["ended_at"] = time.time()
                                            reasoning_block["duration"] = round(
                                                reasoning_block["ended_at"]
                                                - reasoning_block["started_at"], 1
                                            )

                                            content_blocks.append(
                                                {
                                                    "type": "text",
                                                    "content": "",
                                                }
                                            )

                                        content = f"{content}{value}"
                                        if not content_blocks:
                                            content_blocks.append(
                                                {
                                                    "type": "text",
                                                    "content": "",
                                                }
                                            )

                                        content_blocks[-1]["content"] = (
                                            content_blocks[-1]["content"] + value
                                        )

                                        if DETECT_REASONING:
                                            content, content_blocks, _ = (
                                                tag_content_handler(
                                                    "reasoning",
                                                    reasoning_tags,
                                                    content,
                                                    content_blocks,
                                                )
                                            )

                                        if DETECT_CODE_INTERPRETER:
                                            content, content_blocks, end = (
                                                tag_content_handler(
                                                    "code_interpreter",
                                                    code_interpreter_tags,
                                                    content,
                                                    content_blocks,
                                                )
                                            )

                                            if end:
                                                _stream_exit_reason = "break_code_interpreter"
                                                log.warning(
                                                    "[STREAM BREAK] code_interpreter tag detected at data_event #%d, "
                                                    "breaking stream loop. content_len=%d",
                                                    _stream_data_count, len(content),
                                                )
                                                break

                                        if DETECT_SOLUTION:
                                            content, content_blocks, _ = (
                                                tag_content_handler(
                                                    "solution",
                                                    solution_tags,
                                                    content,
                                                    content_blocks,
                                                )
                                            )

                                        if ENABLE_REALTIME_CHAT_SAVE:
                                            # Save message in the database
                                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata["chat_id"],
                                                metadata["message_id"],
                                                {
                                                    "content": serialize_content_blocks(
                                                        content_blocks
                                                    ),
                                                },
                                            )
                                        else:
                                            data = {
                                                "content": serialize_content_blocks(
                                                    content_blocks
                                                ),
                                                **(
                                                    {"files": message_files}
                                                    if message_files
                                                    else {}
                                                ),
                                            }

                                await event_emitter(
                                    {
                                        "type": "chat:completion",
                                        "data": data,
                                    }
                                )
                        except Exception as e:
                            done = "data: [DONE]" in line
                            if done:
                                log.info(
                                    "[STREAM] [DONE] marker at line #%d, data_events=%d content_len=%d",
                                    _stream_line_count, _stream_data_count, len(content),
                                )
                            else:
                                if (
                                    _stream_response_status
                                    and _stream_response_status >= 400
                                ):
                                    _stream_non_sse_error_lines.append(
                                        _truncate_text(data, 4000)
                                    )
                                    if _stream_api_error is None:
                                        candidate_error = _normalize_api_error(
                                            data,
                                            status_override=_stream_response_status,
                                        )
                                        if candidate_error.get("message"):
                                            _stream_api_error = candidate_error
                                log.warning(
                                    "[STREAM PARSE ERROR] line #%d data_event #%d | error=%s | line=%s",
                                    _stream_line_count, _stream_data_count,
                                    str(e)[:200], line[:300] if isinstance(line, str) else str(line)[:300],
                                )
                                continue

                    if pending_stream_images:
                        log.warning(
                            "[STREAM IMAGE DROP] discarding %d incomplete streamed image(s)",
                            len(pending_stream_images),
                        )
                        pending_stream_images.clear()

                    if (
                        _stream_api_error is None
                        and _stream_response_status
                        and _stream_response_status >= 400
                        and _stream_non_sse_error_lines
                    ):
                        _stream_api_error = _normalize_api_error(
                            "\n".join(_stream_non_sse_error_lines[-10:]),
                            status_override=_stream_response_status,
                        )

                    # ── Stream summary log ──
                    log.info(
                        "[STREAM SUMMARY] lines=%d data_events=%d skipped=%d "
                        "content_blocks=%d content_len=%d finish_reason=%s exit_reason=%s "
                        "tool_calls=%d",
                        _stream_line_count,
                        _stream_data_count,
                        _stream_skip_count,
                        len(content_blocks),
                        len(content),
                        _stream_finish_reason,
                        _stream_exit_reason,
                        len(response_tool_calls),
                    )
                    # Log content tail for debugging truncation
                    if content:
                        _tail = content[-100:] if len(content) > 100 else content
                        log.info("[STREAM CONTENT TAIL] ...%s", repr(_tail))

                    if _stream_finish_reason and _stream_finish_reason not in ("stop", "tool_calls"):
                        log.warning(
                            "[STREAM ABNORMAL FINISH] finish_reason=%s — "
                            "response likely truncated! content_len=%d exit_reason=%s",
                            _stream_finish_reason, len(content), _stream_exit_reason,
                        )

                    if _stream_data_count == 0:
                        log.warning(
                            "[STREAM EMPTY] Received %d lines but 0 valid SSE data events. "
                            "The upstream may have returned an error in non-SSE format or an empty body.",
                            _stream_line_count,
                        )

                    if content_blocks:
                        for block in content_blocks:
                            if (
                                block["type"] == "reasoning"
                                and block.get("duration") is None
                                and "started_at" in block
                            ):
                                block["ended_at"] = time.time()
                                block["duration"] = round(
                                    block["ended_at"] - block["started_at"], 1
                                )

                        # Clean up the last text block
                        if content_blocks[-1]["type"] == "text":
                            content_blocks[-1]["content"] = content_blocks[-1][
                                "content"
                            ].strip()

                            if not content_blocks[-1]["content"]:
                                content_blocks.pop()

                                if not content_blocks:
                                    content_blocks.append(
                                        {
                                            "type": "text",
                                            "content": "",
                                        }
                                    )

                    if response_tool_calls:
                        tool_calls.append(response_tool_calls)
                        tool_call_batch_repairs.append(stream_tool_repairs)

                    if response.background:
                        await response.background()

                await stream_body_handler(response)

                native_cfg = get_user_native_tools_config(request, user)
                max_rounds_cfg = normalize_max_tool_call_rounds(
                    (native_cfg or {}).get(MAX_TOOL_CALL_ROUNDS_KEY),
                    default=MAX_TOOL_CALL_ROUNDS_DEFAULT,
                )

                MAX_TOOL_CALL_RETRIES = max_rounds_cfg
                tool_call_retries = 0

                MAX_TOTAL_TOOL_CALLS = 24
                MAX_FETCH_CALLS = 4
                MAX_PER_TOOL_CALLS = 8
                NETWORK_PARALLELISM = 2

                tool_exec_total = 0
                tool_exec_fetch = 0
                per_tool_counts: dict[str, int] = {}
                fetch_domain_failures: set[str] = set()
                seen_tool_signatures: set[str] = set()
                seen_search_query_fingerprints: list[set[str]] = []
                fetch_attempt_count = 0
                successful_fetch_evidence_count = 0
                low_gain_rounds = 0
                consecutive_no_text_rounds = 0
                adaptive_pressure_level = 0

                def _get_active_governor_limits() -> tuple[int, int, int]:
                    total_limit = MAX_TOTAL_TOOL_CALLS
                    fetch_limit = MAX_FETCH_CALLS
                    per_tool_limit = MAX_PER_TOOL_CALLS

                    if adaptive_pressure_level >= 1:
                        total_limit = min(total_limit, 20)
                        fetch_limit = min(fetch_limit, 3)
                        per_tool_limit = min(per_tool_limit, 6)
                    if adaptive_pressure_level >= 2:
                        total_limit = min(total_limit, 14)
                        fetch_limit = min(fetch_limit, 2)
                        per_tool_limit = min(per_tool_limit, 4)
                    if adaptive_pressure_level >= 3:
                        total_limit = min(total_limit, 10)
                        fetch_limit = min(fetch_limit, 1)
                        per_tool_limit = min(per_tool_limit, 3)

                    return total_limit, fetch_limit, per_tool_limit

                def _tokenize_query_for_similarity(query: str) -> set[str]:
                    if not isinstance(query, str):
                        return set()
                    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", query.lower())
                    parts = [p.strip() for p in normalized.split() if p and len(p.strip()) >= 2]
                    return set(parts)

                def _search_query_is_semantically_redundant(query: str) -> bool:
                    token_set = _tokenize_query_for_similarity(query)
                    if not token_set:
                        return False

                    for existing in seen_search_query_fingerprints:
                        if not existing:
                            continue
                        inter = len(token_set & existing)
                        union = len(token_set | existing)
                        if union <= 0:
                            continue
                        similarity = inter / union
                        if similarity >= 0.72:
                            return True

                    seen_search_query_fingerprints.append(token_set)
                    return False

                def _is_effectively_empty_tool_result(tool_name: str, raw_result: Any) -> bool:
                    if isinstance(raw_result, str) and raw_result.startswith("skipped:"):
                        return True

                    result = raw_result
                    if isinstance(result, str):
                        parsed = _safe_json_loads(result)
                        if parsed is not result:
                            result = parsed

                    if tool_name == "list_knowledge_bases":
                        return isinstance(result, list) and len(result) == 0

                    if tool_name in {"search_notes", "search_knowledge_bases", "search_knowledge_files", "query_knowledge_bases"}:
                        if isinstance(result, list):
                            return len(result) == 0
                        if isinstance(result, dict):
                            docs = result.get("documents")
                            return isinstance(docs, list) and all((not x) for x in docs)
                        return False

                    if tool_name == "search_chats":
                        return isinstance(result, list) and len(result) == 0

                    if tool_name == "search_web":
                        if isinstance(result, list):
                            return len(result) == 0
                        return False

                    if tool_name in {"fetch_url", "fetch_url_rendered"}:
                        if isinstance(result, dict):
                            status = str(result.get("status") or "").lower()
                            content_len = int(result.get("content_length") or 0)
                            return status in {"blocked", "thin", "error"} or content_len < 120
                        return False

                    return False

                log.info(
                    "[TOOL ORCH] rounds_limit=%s initial_batches=%s",
                    MAX_TOOL_CALL_RETRIES,
                    len(tool_calls),
                )
                await emit_tool_orchestration_status(
                    "rounds_start",
                    done=False,
                    rounds_limit=MAX_TOOL_CALL_RETRIES,
                    initial_batches=len(tool_calls),
                )

                def _get_tool_param_rules(tools: dict) -> dict:
                    rules = {}
                    for name, tool in (tools or {}).items():
                        spec = (tool or {}).get("spec", {}) or {}
                        params = (spec.get("parameters") or {}) if isinstance(spec, dict) else {}
                        props = params.get("properties") or {}
                        required = params.get("required") or []
                        if not isinstance(props, dict):
                            props = {}
                        if not isinstance(required, list):
                            required = []
                        rules[name] = {
                            "allowed": set(props.keys()),
                            "required": set([str(x) for x in required if x is not None]),
                        }
                    return rules

                def _segment_tool_name(
                    name: str, known_names_sorted: list[str], known_set: set[str], max_parts: int = 3
                ) -> list[str] | None:
                    if not name:
                        return None
                    if name in known_set:
                        return [name]
                    if max_parts <= 1:
                        return None
                    for prefix in known_names_sorted:
                        if name.startswith(prefix):
                            rest = name[len(prefix) :]
                            seg = _segment_tool_name(
                                rest, known_names_sorted, known_set, max_parts=max_parts - 1
                            )
                            if seg:
                                return [prefix, *seg]
                    return None

                def _split_concatenated_json(s: str) -> list[dict]:
                    """Split concatenated JSON objects like '{"a":1}{"b":2}' into [{"a":1},{"b":2}]."""
                    results: list[dict] = []
                    s = s.strip()
                    if not s:
                        return results
                    decoder = json.JSONDecoder()
                    pos = 0
                    while pos < len(s):
                        # Skip whitespace between objects.
                        while pos < len(s) and s[pos] in " \t\r\n":
                            pos += 1
                        if pos >= len(s):
                            break
                        try:
                            obj, end = decoder.raw_decode(s, pos)
                            if isinstance(obj, dict):
                                results.append(obj)
                            pos = end
                        except json.JSONDecodeError:
                            break
                    return results

                def _fix_invalid_json_escapes(s: str) -> str:
                    """Fix invalid JSON escape sequences that models emit (e.g. \\$ from bash-style).

                    Valid JSON escapes: \\" \\\\ \\/ \\b \\f \\n \\r \\t \\uXXXX
                    Invalid ones like \\$ are stripped of the backslash → $.
                    Valid sequences (including \\\\) are preserved intact.
                    """
                    return re.sub(
                        r'\\(["\\\/bfnrt]|u[0-9a-fA-F]{4})|\\(.)',
                        lambda m: m.group(0) if m.group(1) is not None else m.group(2),
                        s,
                    )

                def _parse_tool_args(tool_call: dict) -> dict:
                    # Prefer JSON; fall back to escape-fix / literal_eval for models that emit invalid JSON.
                    args = tool_call.get("function", {}).get("arguments", "{}")
                    if isinstance(args, dict):
                        return args
                    if not isinstance(args, str):
                        return {}
                    try:
                        return json.loads(args) if args.strip() else {}
                    except Exception as e1:
                        # Fix invalid JSON escape sequences (e.g. \$ from powershell/bash commands).
                        try:
                            fixed = _fix_invalid_json_escapes(args)
                            if fixed != args:
                                try:
                                    result = json.loads(fixed) if fixed.strip() else {}
                                    if isinstance(result, dict):
                                        return result
                                except Exception as e2:
                                    log.debug(
                                        "[TOOL ARGS] escape-fix json.loads still failed: %s | raw_len=%d fixed_diff=%d",
                                        str(e2)[:200], len(args), len(fixed) - len(args),
                                    )
                        except Exception:
                            pass
                        # Handle concatenated JSON objects, e.g. '{"query":"x"}{"url":"y"}'.
                        parts = _split_concatenated_json(args)
                        if len(parts) > 1:
                            merged: dict = {}
                            for part in parts:
                                merged.update(part)
                            return merged
                        try:
                            return ast.literal_eval(args) if args.strip() else {}
                        except Exception:
                            return {}

                def _normalize_tool_calls(
                    response_tool_calls: list[dict], tools: dict
                ) -> tuple[list[dict], list[dict], list[dict]]:
                    """
                    Normalize/repair malformed tool_calls from some upstreams/models.

                    Returns: (normalized_calls, repaired_info, invalid_calls)
                    """
                    if not response_tool_calls:
                        return [], [], []

                    tool_rules = _get_tool_param_rules(tools)
                    known_names_sorted = sorted(list(tool_rules.keys()), key=len, reverse=True)
                    known_set = set(tool_rules.keys())

                    normalized: list[dict] = []
                    repaired: list[dict] = []
                    invalid: list[dict] = []

                    def _get_tool_name(tc: dict) -> str:
                        fn = tc.get("function") or {}
                        return str((fn.get("name") or "")).strip()

                    def _get_raw_arguments(tc: dict) -> str:
                        args = (tc.get("function") or {}).get("arguments", "")
                        if isinstance(args, dict):
                            return json.dumps(args, ensure_ascii=False)
                        return str(args or "")

                    def _is_empty_arguments(tc: dict) -> bool:
                        args_dict = _parse_tool_args(tc)
                        if isinstance(args_dict, dict) and args_dict:
                            return False

                        raw = _get_raw_arguments(tc).strip()
                        return raw in ["", "{}", "null", "None"]

                    def _parse_tool_args_relaxed(tc: dict) -> dict:
                        args_dict = _parse_tool_args(tc)
                        if isinstance(args_dict, dict) and args_dict:
                            return args_dict

                        raw = _get_raw_arguments(tc).strip()
                        if raw in ["", "{}", "null", "None"]:
                            return {}

                        recovered: dict = {}
                        for match in re.finditer(
                            r'"([^"\\]+)"\s*:\s*("(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?|true|false|null)',
                            raw,
                        ):
                            key = match.group(1)
                            value_raw = match.group(2)
                            try:
                                recovered[key] = json.loads(value_raw)
                            except Exception:
                                # json.loads failed (e.g. invalid escape \$).
                                # Try fixing escapes then json.loads again.
                                try:
                                    fixed_val = _fix_invalid_json_escapes(value_raw)
                                    recovered[key] = json.loads(fixed_val)
                                except Exception:
                                    # Last resort: strip quotes and manually unescape.
                                    inner = value_raw[1:-1] if len(value_raw) >= 2 and value_raw[0] == '"' and value_raw[-1] == '"' else value_raw
                                    # Unescape common JSON escapes manually.
                                    inner = inner.replace('\\"', '"').replace('\\\\', '\\').replace('\\n', '\n').replace('\\t', '\t').replace('\\r', '\r')
                                    recovered[key] = inner

                        return recovered

                    def _infer_tool_name_for_args(args_dict: dict) -> str | None:
                        if not isinstance(args_dict, dict) or not args_dict:
                            return None

                        arg_keys = set(args_dict.keys())

                        candidates: list[tuple[tuple[int, int, int, int], str]] = []
                        priority_order = [
                            "search_web",
                            "fetch_url",
                            "fetch_url_rendered",
                            "query_knowledge_bases",
                            "search_knowledge_files",
                            "search_knowledge_bases",
                            "search_chats",
                            "search_notes",
                            "search_memories",
                        ]
                        priority_weight = {
                            name: (len(priority_order) - idx)
                            for idx, name in enumerate(priority_order)
                        }

                        for tool_name, rules in tool_rules.items():
                            allowed = rules["allowed"]
                            required = rules["required"]

                            if required and not required.issubset(arg_keys):
                                continue

                            unknown_keys = arg_keys - allowed
                            if unknown_keys:
                                continue

                            overlap = len(arg_keys & allowed)
                            if overlap <= 0:
                                continue

                            score = (
                                len(required),
                                overlap,
                                -len(allowed),
                                priority_weight.get(tool_name, 0),
                            )
                            candidates.append((score, tool_name))

                        if not candidates:
                            return None

                        candidates.sort(key=lambda x: x[0], reverse=True)

                        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
                            return None

                        return candidates[0][1]

                    def _apply_param_alias_mapping(
                        args_dict: dict, allowed: set[str]
                    ) -> tuple[dict, list[dict]]:
                        if not isinstance(args_dict, dict) or not args_dict:
                            return {}, []

                        mapped: dict = {}
                        repairs: list[dict] = []

                        for key, value in args_dict.items():
                            if key in allowed:
                                mapped[key] = value
                                continue

                            singular_candidates = []
                            if key.endswith("ies") and len(key) > 3:
                                singular_candidates.append(f"{key[:-3]}y")
                            if key.endswith("s") and len(key) > 1:
                                singular_candidates.append(key[:-1])

                            matched_singular = next(
                                (
                                    candidate
                                    for candidate in singular_candidates
                                    if candidate in allowed
                                ),
                                None,
                            )
                            if matched_singular:
                                mapped[matched_singular] = (
                                    value[0]
                                    if isinstance(value, list) and len(value) == 1
                                    else value
                                )
                                repairs.append(
                                    {
                                        "action": "param_alias_mapping",
                                        "from": key,
                                        "to": matched_singular,
                                    }
                                )
                                continue

                            plural_candidates = [f"{key}s"]
                            if key.endswith("y") and len(key) > 1:
                                plural_candidates.insert(0, f"{key[:-1]}ies")

                            matched_plural = next(
                                (
                                    candidate
                                    for candidate in plural_candidates
                                    if candidate in allowed
                                ),
                                None,
                            )
                            if matched_plural:
                                mapped[matched_plural] = (
                                    value if isinstance(value, list) else [value]
                                )
                                repairs.append(
                                    {
                                        "action": "param_alias_mapping",
                                        "from": key,
                                        "to": matched_plural,
                                    }
                                )
                                continue

                            # Fuzzy match: catch typos like "powershel_script" → "powershell_script"
                            fuzzy = get_close_matches(key, list(allowed), n=1, cutoff=0.85)
                            if fuzzy:
                                mapped[fuzzy[0]] = value
                                repairs.append(
                                    {
                                        "action": "param_fuzzy_match",
                                        "from": key,
                                        "to": fuzzy[0],
                                    }
                                )

                        return mapped, repairs

                    # 1) Drop pure placeholders: empty tool name + empty arguments.
                    filtered_calls: list[dict] = []
                    for tc in response_tool_calls:
                        if (not _get_tool_name(tc)) and _is_empty_arguments(tc):
                            repaired.append(
                                {
                                    "from": "(empty_placeholder)",
                                    "to": [],
                                    "action": "drop_placeholder",
                                }
                            )
                            continue
                        filtered_calls.append(tc)

                    # 2) Merge split pattern: <tool_name with empty args> + <empty name with args>.
                    compact_calls: list[dict] = []
                    idx = 0
                    while idx < len(filtered_calls):
                        tc = filtered_calls[idx]
                        name = _get_tool_name(tc)
                        args_dict = _parse_tool_args(tc)
                        has_args = isinstance(args_dict, dict) and bool(args_dict)

                        if name and not has_args:
                            merge_idx = None
                            merge_args = None

                            look_ahead = idx + 1
                            while look_ahead < len(filtered_calls):
                                next_tc = filtered_calls[look_ahead]
                                next_name = _get_tool_name(next_tc)

                                if next_name:
                                    break

                                next_args = _parse_tool_args_relaxed(next_tc)
                                if isinstance(next_args, dict) and next_args:
                                    merge_idx = look_ahead
                                    merge_args = next_args
                                    break

                                if _is_empty_arguments(next_tc):
                                    look_ahead += 1
                                    continue

                                break

                            if merge_idx is not None and isinstance(merge_args, dict):
                                merged_tc = dict(tc)
                                merged_fn = dict(tc.get("function") or {})
                                merged_fn["arguments"] = json.dumps(
                                    merge_args, ensure_ascii=False
                                )
                                merged_tc["function"] = merged_fn
                                compact_calls.append(merged_tc)
                                repaired.append(
                                    {
                                        "from": f"{name}+<empty_name_args>",
                                        "to": [name],
                                        "action": "merge_split_call",
                                    }
                                )
                                idx = merge_idx + 1
                                continue

                        compact_calls.append(tc)
                        idx += 1

                    # 3) Repair empty tool name when arguments are usable and map safely to one tool.
                    resolved_calls: list[dict] = []
                    for tc in compact_calls:
                        name = _get_tool_name(tc)
                        if name:
                            resolved_calls.append(tc)
                            continue

                        args_dict = _parse_tool_args_relaxed(tc)
                        inferred_name = _infer_tool_name_for_args(args_dict)
                        if not inferred_name:
                            resolved_calls.append(tc)
                            continue

                        inferred_tc = dict(tc)
                        inferred_fn = dict(tc.get("function") or {})
                        inferred_fn["name"] = inferred_name
                        inferred_fn["arguments"] = json.dumps(args_dict or {}, ensure_ascii=False)
                        inferred_tc["function"] = inferred_fn

                        resolved_calls.append(inferred_tc)
                        repaired.append(
                            {
                                "from": "(empty_name)",
                                "to": [inferred_name],
                                "action": "infer_empty_name",
                                "args_keys": sorted(list(args_dict.keys())),
                            }
                        )

                    def _normalize_known_tool_call(tc: dict, fn: dict, name: str):
                        args_dict = _parse_tool_args(tc)
                        if not isinstance(args_dict, dict):
                            args_dict = {}

                        # Fallback: if strict parsing returned empty but raw args
                        # are non-trivial, try relaxed regex extraction.
                        if not args_dict:
                            raw = _get_raw_arguments(tc).strip()
                            if raw and raw not in ("{}", "null", "None"):
                                relaxed = _parse_tool_args_relaxed(tc)
                                if isinstance(relaxed, dict) and relaxed:
                                    args_dict = relaxed
                                    repaired.append(
                                        {
                                            "action": "relaxed_parse_fallback",
                                            "tool": name,
                                            "recovered_keys": sorted(
                                                list(relaxed.keys())
                                            ),
                                        }
                                    )

                        rules = tool_rules.get(name) or {
                            "allowed": set(),
                            "required": set(),
                        }
                        allowed = rules["allowed"]
                        required = rules["required"]

                        mapped_args, alias_repairs = _apply_param_alias_mapping(
                            args_dict, allowed
                        )
                        if alias_repairs:
                            repaired.extend(alias_repairs)

                        mapped_args = {
                            k: v for k, v in mapped_args.items() if k in allowed
                        }

                        if required and not required.issubset(set(mapped_args.keys())):
                            invalid.append(
                                {
                                    "id": tc.get("id", ""),
                                    "name": name or "(empty)",
                                    "arguments": fn.get("arguments", ""),
                                    "arg_keys": sorted(list(args_dict.keys())),
                                }
                            )
                            return None

                        normalized_tc = dict(tc)
                        normalized_fn = dict(fn)
                        normalized_fn["arguments"] = json.dumps(
                            mapped_args or {}, ensure_ascii=False
                        )
                        normalized_tc["function"] = normalized_fn
                        return normalized_tc

                    for tc in resolved_calls:
                        fn = tc.get("function") or {}
                        name = fn.get("name") or ""

                        if name in known_set:
                            normalized_tc = _normalize_known_tool_call(tc, fn, name)
                            if normalized_tc is not None:
                                normalized.append(normalized_tc)
                            continue

                        # Try to split concatenated tool names, e.g. `get_current_datesearch_web`
                        seg = _segment_tool_name(name, known_names_sorted, known_set, max_parts=3)

                        # Repeated same tool name, e.g. `search_websearch_web`.
                        if seg and len(seg) > 1 and len(set(seg)) == 1:
                            dedup_name = seg[0]
                            dedup_tc = dict(tc)
                            dedup_fn = dict(fn)
                            dedup_fn["name"] = dedup_name
                            dedup_tc["function"] = dedup_fn
                            repaired.append(
                                {
                                    "from": name,
                                    "to": [dedup_name],
                                    "action": "dedupe_repeated_name",
                                }
                            )
                            normalized_tc = _normalize_known_tool_call(
                                dedup_tc, dedup_fn, dedup_name
                            )
                            if normalized_tc is not None:
                                normalized.append(normalized_tc)
                            continue

                        if not seg or len(seg) <= 1:
                            parsed_args = _parse_tool_args_relaxed(tc)
                            invalid.append(
                                {
                                    "id": tc.get("id", ""),
                                    "name": name or "(empty)",
                                    "arguments": fn.get("arguments", ""),
                                    "arg_keys": (
                                        sorted(list(parsed_args.keys()))
                                        if isinstance(parsed_args, dict)
                                        else []
                                    ),
                                }
                            )
                            continue

                        args_dict = _parse_tool_args(tc)
                        if not isinstance(args_dict, dict):
                            args_dict = {}

                        remaining = dict(args_dict)
                        new_calls: list[dict] = []
                        ok = True
                        for i, part_name in enumerate(seg):
                            rules = tool_rules.get(part_name) or {"allowed": set(), "required": set()}
                            allowed = rules["allowed"]
                            required = rules["required"]

                            part_args = {k: remaining[k] for k in list(remaining.keys()) if k in allowed}
                            for k in part_args.keys():
                                remaining.pop(k, None)

                            # If the tool has required args, ensure they're present in this segment.
                            if required and not required.issubset(set(part_args.keys())):
                                ok = False
                                break

                            new_tc = dict(tc)
                            new_tc["id"] = tc.get("id") if i == 0 else str(uuid4())
                            new_fn = dict(fn)
                            new_fn["name"] = part_name
                            new_fn["arguments"] = json.dumps(part_args or {}, ensure_ascii=False)
                            new_tc["function"] = new_fn
                            new_calls.append(new_tc)

                        if not ok or remaining:
                            parsed_args = _parse_tool_args_relaxed(tc)
                            invalid.append(
                                {
                                    "id": tc.get("id", ""),
                                    "name": name or "(empty)",
                                    "arguments": fn.get("arguments", ""),
                                    "arg_keys": (
                                        sorted(list(parsed_args.keys()))
                                        if isinstance(parsed_args, dict)
                                        else []
                                    ),
                                }
                            )
                            continue

                        repaired.append({"from": name, "to": seg})
                        normalized.extend(new_calls)

                    return normalized, repaired, invalid

                force_final_round = False

                while len(tool_calls) > 0 and tool_call_retries < MAX_TOOL_CALL_RETRIES:
                    tool_call_retries += 1

                    response_tool_calls = tool_calls.pop(0)
                    current_total_limit, current_fetch_limit, current_per_tool_limit = (
                        _get_active_governor_limits()
                    )

                    log.info(
                        "[TOOL ORCH] round_start round=%s tool_calls_in_batch=%s remaining_batches=%s pressure=%s limits(total=%s,fetch=%s,per_tool=%s)",
                        tool_call_retries,
                        len(response_tool_calls) if isinstance(response_tool_calls, list) else 0,
                        len(tool_calls),
                        adaptive_pressure_level,
                        current_total_limit,
                        current_fetch_limit,
                        current_per_tool_limit,
                    )
                    await emit_tool_orchestration_status(
                        "round_start",
                        done=False,
                        round=tool_call_retries,
                        tool_calls_in_batch=(
                            len(response_tool_calls)
                            if isinstance(response_tool_calls, list)
                            else 0
                        ),
                        remaining_batches=len(tool_calls),
                        pressure=adaptive_pressure_level,
                        total_limit=current_total_limit,
                        fetch_limit=current_fetch_limit,
                        per_tool_limit=current_per_tool_limit,
                    )

                    tools = metadata.get("tools", {})
                    stream_repair_info = (
                        tool_call_batch_repairs.pop(0)
                        if tool_call_batch_repairs
                        else []
                    )

                    # Debug: log raw tool calls before normalization for diagnosing fallback triggers
                    if response_tool_calls:
                        _debug_tcs = []
                        for _tc in (response_tool_calls if isinstance(response_tool_calls, list) else []):
                            _fn = _tc.get("function", {}) if isinstance(_tc, dict) else {}
                            _debug_tcs.append({
                                "name": _fn.get("name", ""),
                                "args_len": len(str(_fn.get("arguments", ""))),
                                "args_preview": str(_fn.get("arguments", ""))[:200],
                                "id": str(_tc.get("id", ""))[:20] if isinstance(_tc, dict) else "",
                            })
                        log.info(
                            "[TOOL ORCH] pre_normalize round=%s raw_tool_calls=%s known_tools=%s",
                            tool_call_retries,
                            json.dumps(_debug_tcs, ensure_ascii=False),
                            sorted(list((tools or {}).keys()))[:10],
                        )

                    response_tool_calls, repaired_tool_calls, invalid_tool_calls = (
                        _normalize_tool_calls(response_tool_calls, tools)
                    )

                    if stream_repair_info:
                        repaired_tool_calls = stream_repair_info + repaired_tool_calls

                    if repaired_tool_calls:
                        log.warning(
                            f"[TOOL CALL] Normalized malformed tool_calls: {json.dumps(repaired_tool_calls, ensure_ascii=False)}"
                        )

                    if invalid_tool_calls:
                        log.warning(
                            "[TOOL ORCH] invalid_tool_calls detected round=%s invalid=%s normalized=%s",
                            tool_call_retries,
                            json.dumps(invalid_tool_calls, ensure_ascii=False)[:500],
                            len(response_tool_calls),
                        )
                        # One-shot fallback: if native tool_calls are malformed, switch to compatibility/default
                        # tool calling for this request. Must be explicit to the user (status + message).
                        can_fallback = (
                            metadata.get("function_calling") == "native"
                            and not metadata.get("native_tool_fallback_used", False)
                            and event_caller is not None
                        )
                        if can_fallback:
                            metadata["native_tool_fallback_used"] = True
                            try:
                                await event_emitter(
                                    {
                                        "type": "status",
                                        "data": {
                                            "action": "native_tool_fallback",
                                            "description": "检测到原生工具调用异常，已切换兼容模式重试一次",
                                            "done": True,
                                        },
                                    }
                                )
                            except Exception:
                                pass

                            warn_summary = "已自动切换兼容模式重试一次"
                            warn_body = (
                                "**说明**：本次请求的原生工具调用返回格式异常（空工具名/缺参/拆分等），为避免卡住，系统已自动切换到兼容模式重试一次。\n\n"
                                "**你可以做什么**：\n"
                                "- 若问题频繁出现：在对话高级设置中将函数工具调用模式切换为“默认/兼容”。\n"
                                "- 或更换更稳定支持工具调用的模型/上游代理。\n\n"
                                "（该自动回退只会执行一次，不会循环。）"
                            )
                            warn_details = (
                                '<details type="warning" done="true">\n'
                                f"<summary>{warn_summary}</summary>\n"
                                f"{warn_body}\n"
                                "</details>"
                            )
                            content_blocks.append(
                                {"type": "text", "content": warn_details}
                            )
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": {
                                        "content": serialize_content_blocks(
                                            content_blocks
                                        ),
                                    },
                                }
                            )

                            try:
                                models_fb = (
                                    getattr(request.state, "MODELS", None)
                                    or request.app.state.MODELS
                                )
                                fallback_form_data = dict(form_data)
                                if isinstance(form_data.get("messages"), list):
                                    fallback_form_data["messages"] = list(
                                        form_data["messages"]
                                    )

                                # Compatibility tool calling should not ask the upstream for tool_calls.
                                fallback_form_data.pop("tools", None)
                                fallback_form_data.pop("tool_choice", None)
                                fallback_form_data["stream"] = True

                                fb_extra_params = {
                                    "__event_call__": event_caller,
                                    "__metadata__": metadata,
                                }
                                fallback_form_data, fb_flags = (
                                    await chat_completion_tools_handler(
                                        request,
                                        fallback_form_data,
                                        fb_extra_params,
                                        user,
                                        models_fb,
                                        tools,
                                    )
                                )

                                # Emit UI sources (citations) as dedicated events.
                                for src in (fb_flags or {}).get("ui_sources", []) or []:
                                    await event_emitter(
                                        {"type": "source", "data": src}
                                    )
                                for src in (fb_flags or {}).get("sources", []) or []:
                                    await event_emitter(
                                        {"type": "source", "data": src}
                                    )

                                res = await generate_chat_completion(
                                    request, fallback_form_data, user
                                )
                                if isinstance(res, StreamingResponse):
                                    await stream_body_handler(res)
                                else:
                                    # Best-effort: provider may ignore stream and return JSON once.
                                    res_data = res
                                    try:
                                        if hasattr(res, "body") and isinstance(
                                            getattr(res, "body", None), (bytes, bytearray)
                                        ):
                                            res_data = json.loads(
                                                res.body.decode("utf-8", "replace")
                                            )
                                    except Exception:
                                        res_data = res
                                    if isinstance(res_data, dict):
                                        await event_emitter(
                                            {
                                                "type": "chat:completion",
                                                "data": res_data,
                                            }
                                        )
                                break
                            except Exception as e:
                                log.exception(e)

                        provided = sorted(list((tools or {}).keys()))
                        summary = "模型调用失败"
                        body = (
                            "**原因**：模型使用或返回的工具格式不规范。\n\n"
                            "**建议**：\n"
                            "- 在对话高级设置中将函数工具调用设置为默认兼容模式。\n"
                            "- 更换高级模型或支持工具调用的模型。\n"
                            "- 在内置工具设置中关闭不支持的工具或切换为兼容模式。\n\n"
                            f"**未识别的工具调用**：\n```json\n{json.dumps(invalid_tool_calls, ensure_ascii=False, indent=2)[:2000]}\n```\n"
                            f"**已提供的工具列表（节选）**：\n```text\n{', '.join(provided)[:2000]}\n```"
                        )
                        friendly = (
                            '<details type="error" done="true">\n'
                            f"<summary>{summary}</summary>\n"
                            f"{body}\n"
                            "</details>"
                        )

                        content_blocks.append({"type": "text", "content": friendly})
                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_content_blocks(content_blocks),
                                },
                            }
                        )
                        break

                    # -- force_final_round: skip execution, inject fake results --
                    if force_final_round:
                        fake_results = []
                        for tc in (response_tool_calls if isinstance(response_tool_calls, list) else []):
                            fake_results.append({
                                "tool_call_id": tc.get("id", f"skip_{tool_call_retries}"),
                                "content": (
                                    "Tool call limit reached. "
                                    "Please provide your answer based on the tool results you already have. "
                                    "Do not call any more tools."
                                ),
                            })
                        content_blocks.append({
                            "type": "tool_calls",
                            "content": response_tool_calls,
                        })
                        content_blocks[-1]["results"] = fake_results
                        content_blocks.append({"type": "text", "content": ""})
                        log.warning(
                            "[TOOL ORCH] fake_results_injected round=%s tool_count=%s",
                            tool_call_retries,
                            len(fake_results),
                        )
                        # Clear queue so loop exits after the follow-up call
                        tool_calls.clear()
                        tool_call_batch_repairs.clear()

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_content_blocks(content_blocks),
                                },
                            }
                        )

                        try:
                            followup_messages = [
                                *form_data["messages"],
                                *convert_content_blocks_to_messages(content_blocks),
                            ]
                            payload = {
                                "model": model_id,
                                "stream": True,
                                "messages": followup_messages,
                            }
                            if form_data.get("tools"):
                                payload["tools"] = form_data["tools"]

                            res = await generate_chat_completion(request, payload, user)

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res)
                            else:
                                res_data = res
                                try:
                                    from starlette.responses import JSONResponse as StarletteJSONResponse
                                    if isinstance(res, StarletteJSONResponse):
                                        res_data = json.loads(res.body.decode("utf-8", "replace"))
                                except Exception:
                                    res_data = res
                                if isinstance(res_data, dict):
                                    msg = (
                                        res_data.get("choices", [{}])[0].get("message", {})
                                        if isinstance(res_data.get("choices"), list)
                                        else {}
                                    )
                                    msg_content = msg.get("content")
                                    if isinstance(msg_content, str) and msg_content:
                                        if content_blocks and content_blocks[-1].get("type") == "text":
                                            content_blocks[-1]["content"] += msg_content
                                        else:
                                            content_blocks.append({"type": "text", "content": msg_content})
                        except Exception as e:
                            log.exception("[TOOL ORCH] fake_results_followup_error: %s", e)

                        break  # Exit tool loop

                    content_blocks.append(
                        {
                            "type": "tool_calls",
                            "content": response_tool_calls,
                        }
                    )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_content_blocks(content_blocks),
                            },
                        }
                    )

                    results = []
                    emitted_tool_sources_seen: set[str] = set()
                    governor_lock = asyncio.Lock()

                    async def build_skipped_tool_result(
                        tool_call_id: str,
                        tool_name: str,
                        tool_params: dict,
                        tool: dict,
                        reason: str,
                    ) -> dict:
                        msg = f"skipped:{reason}"
                        log.info(
                            "[TOOL ORCH] tool_skip round=%s tool=%s reason=%s params=%s",
                            tool_call_retries,
                            tool_name,
                            reason,
                            json.dumps(tool_params, ensure_ascii=False),
                        )
                        await emit_tool_orchestration_status(
                            "tool_skip",
                            done=True,
                            round=tool_call_retries,
                            tool=tool_name,
                            reason=reason,
                        )
                        return {
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "params": tool_params,
                            "raw_result": msg,
                            "result": msg,
                            "files": [],
                            "tool_id": tool.get("tool_id", "") or "",
                        }

                    async def run_single_tool_call(tool_call: dict) -> dict:
                        nonlocal tool_exec_total, tool_exec_fetch, fetch_attempt_count

                        tool_call_id = tool_call.get("id", "")
                        tool_name = tool_call.get("function", {}).get("name", "")

                        tool_function_params = {}
                        # Ensure arguments is always a valid string; upstream
                        # may return null/None for parameterless tool calls.
                        raw_arguments = tool_call.get("function", {}).get("arguments") or "{}"
                        if isinstance(raw_arguments, dict):
                            tool_function_params = raw_arguments
                        else:
                            if not isinstance(raw_arguments, str):
                                raw_arguments = str(raw_arguments)
                            raw_arguments = raw_arguments.strip() or "{}"
                            try:
                                tool_function_params = ast.literal_eval(raw_arguments)
                            except Exception:
                                try:
                                    tool_function_params = json.loads(raw_arguments)
                                except Exception:
                                    tool_function_params = {}

                        if not isinstance(tool_function_params, dict):
                            tool_function_params = {}

                        if tool_name not in tools:
                            return {
                                "tool_call_id": tool_call_id,
                                "tool_name": tool_name,
                                "params": tool_function_params,
                                "raw_result": f"tool_not_found:{tool_name}",
                                "result": f"tool_not_found:{tool_name}",
                                "files": [],
                                "tool_id": "",
                            }

                        tool = tools[tool_name]
                        spec = tool.get("spec", {})
                        allowed_params = (
                            spec.get("parameters", {}).get("properties", {}).keys()
                        )
                        tool_function_params = {
                            k: v for k, v in tool_function_params.items() if k in allowed_params
                        }

                        signature_src = json.dumps(
                            {
                                "name": tool_name,
                                "params": tool_function_params,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        fetch_domain = ""
                        if tool_name in {"fetch_url", "fetch_url_rendered"}:
                            fetch_url_value = str(tool_function_params.get("url") or "").strip()
                            parsed_fetch = urlparse(fetch_url_value)
                            fetch_domain = parsed_fetch.netloc.lower()

                        signature = hashlib.sha256(signature_src.encode("utf-8")).hexdigest()

                        if tool_name == "search_web":
                            search_query = str(tool_function_params.get("query") or "").strip()
                            if search_query and _search_query_is_semantically_redundant(search_query):
                                return await build_skipped_tool_result(
                                    tool_call_id,
                                    tool_name,
                                    tool_function_params,
                                    tool,
                                    "duplicate_semantic_query",
                                )

                        active_total_limit, active_fetch_limit, active_per_tool_limit = (
                            _get_active_governor_limits()
                        )

                        async with governor_lock:
                            if signature in seen_tool_signatures:
                                return await build_skipped_tool_result(
                                    tool_call_id,
                                    tool_name,
                                    tool_function_params,
                                    tool,
                                    "duplicate_tool_call",
                                )
                            seen_tool_signatures.add(signature)

                            if tool_exec_total >= active_total_limit:
                                return await build_skipped_tool_result(
                                    tool_call_id,
                                    tool_name,
                                    tool_function_params,
                                    tool,
                                    f"tool_budget_exceeded total={tool_exec_total} limit={active_total_limit}",
                                )

                            current_tool_count = per_tool_counts.get(tool_name, 0)
                            if current_tool_count >= active_per_tool_limit:
                                return await build_skipped_tool_result(
                                    tool_call_id,
                                    tool_name,
                                    tool_function_params,
                                    tool,
                                    f"tool_limit_exceeded name={tool_name} count={current_tool_count} limit={active_per_tool_limit}",
                                )

                            if tool_name in {"fetch_url", "fetch_url_rendered"}:
                                if fetch_attempt_count >= active_fetch_limit + 2:
                                    return await build_skipped_tool_result(
                                        tool_call_id,
                                        tool_name,
                                        tool_function_params,
                                        tool,
                                        f"fetch_budget_exceeded attempts={fetch_attempt_count} limit={active_fetch_limit + 2}",
                                    )

                                # 域名封锁只对 fetch_url 生效；fetch_url_rendered 使用 Jina 渲染，
                                # 可能成功抓取 fetch_url 失败的域名，不应被连带封锁。
                                if tool_name == "fetch_url" and fetch_domain and fetch_domain in fetch_domain_failures:
                                    return await build_skipped_tool_result(
                                        tool_call_id,
                                        tool_name,
                                        tool_function_params,
                                        tool,
                                        f"fetch_domain_blocked domain={fetch_domain}",
                                    )

                            tool_exec_total += 1
                            per_tool_counts[tool_name] = current_tool_count + 1
                            if tool_name in {"fetch_url", "fetch_url_rendered"}:
                                fetch_attempt_count += 1

                        tool_result = None
                        try:
                            log.info(
                                f"[TOOL CALL] {tool_name} params={json.dumps(tool_function_params, ensure_ascii=False)}"
                            )

                            if tool.get("direct", False):
                                tool_result = await event_caller(
                                    {
                                        "type": "execute:tool",
                                        "data": {
                                            "id": str(uuid4()),
                                            "name": tool_name,
                                            "params": tool_function_params,
                                            "server": tool.get("server", {}),
                                            "session_id": metadata.get("session_id", None),
                                        },
                                    }
                                )
                            else:
                                tool_function = tool["callable"]
                                tool_result = await tool_function(**tool_function_params)

                            log.info(f"[TOOL RESULT] {tool_name} result={str(tool_result)[:500]}")
                        except Exception as e:
                            log.error(f"[TOOL ERROR] {tool_name} error={e}")
                            tool_result = str(e)

                        if tool_name in {"fetch_url", "fetch_url_rendered"}:
                            parsed_fetch_result = tool_result
                            if isinstance(parsed_fetch_result, str):
                                try:
                                    parsed_fetch_result = json.loads(parsed_fetch_result)
                                except Exception:
                                    parsed_fetch_result = None

                            if isinstance(parsed_fetch_result, dict):
                                status = str(parsed_fetch_result.get("status") or "").strip().lower()
                                signals = parsed_fetch_result.get("signals") or []
                                should_block_domain = status in {"blocked"} or (
                                    isinstance(signals, list)
                                    and "anti_bot_or_challenge" in signals
                                )
                                if should_block_domain and fetch_domain:
                                    async with governor_lock:
                                        fetch_domain_failures.add(fetch_domain)
                                    log.info(
                                        "[TOOL ORCH] fetch_domain_marked_blocked round=%s domain=%s status=%s signals=%s",
                                        tool_call_retries,
                                        fetch_domain,
                                        status,
                                        signals,
                                    )

                                fetch_is_success_evidence = (
                                    status == "ok"
                                    and float(parsed_fetch_result.get("quality_score") or 0.0) >= 0.55
                                    and not (
                                        isinstance(signals, list)
                                        and "anti_bot_or_challenge" in signals
                                    )
                                )
                                if fetch_is_success_evidence:
                                    async with governor_lock:
                                        tool_exec_fetch += 1
                            elif isinstance(tool_result, str) and len(tool_result.strip()) >= 240:
                                async with governor_lock:
                                    tool_exec_fetch += 1

                        tool_result_files = _normalize_message_files(tool_result)
                        if isinstance(tool_result, list):
                            kept_items = []
                            for item in tool_result:
                                if isinstance(item, str) and item.startswith("data:"):
                                    continue
                                else:
                                    kept_items.append(item)
                            tool_result = kept_items

                        rendered_result = tool_result
                        if isinstance(rendered_result, list):
                            rendered_result = {"results": rendered_result}

                        if isinstance(rendered_result, (dict, list)):
                            rendered_result = json.dumps(
                                rendered_result, indent=2, ensure_ascii=False
                            )

                        return {
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "params": tool_function_params,
                            "raw_result": tool_result,
                            "result": rendered_result,
                            "files": tool_result_files,
                            "tool_id": tool.get("tool_id", "") or "",
                        }

                    async def execute_tool_calls_with_governor(calls: list[dict]) -> list[dict]:
                        if not isinstance(calls, list) or not calls:
                            return []

                        indexed_network_calls: list[tuple[int, dict]] = []
                        indexed_serial_calls: list[tuple[int, dict]] = []
                        for idx, tc in enumerate(calls):
                            t_name = (tc.get("function") or {}).get("name")
                            if t_name in {"search_web", "fetch_url", "fetch_url_rendered"}:
                                indexed_network_calls.append((idx, tc))
                            else:
                                indexed_serial_calls.append((idx, tc))

                        executed_by_index: dict[int, dict] = {}

                        for idx, tc in indexed_serial_calls:
                            executed_by_index[idx] = await run_single_tool_call(tc)

                        if indexed_network_calls:
                            semaphore = asyncio.Semaphore(max(1, NETWORK_PARALLELISM))

                            async def run_network(idx: int, tc: dict) -> tuple[int, dict]:
                                async with semaphore:
                                    result = await run_single_tool_call(tc)
                                    return idx, result

                            network_results = await asyncio.gather(
                                *[run_network(idx, tc) for idx, tc in indexed_network_calls],
                                return_exceptions=False,
                            )
                            for idx, result in network_results:
                                executed_by_index[idx] = result

                        executed: list[dict] = []
                        for idx in range(len(calls)):
                            if idx in executed_by_index:
                                executed.append(executed_by_index[idx])
                        return executed

                    executed_results = await execute_tool_calls_with_governor(response_tool_calls)
                    round_pressure_bump = 0

                    round_message_files: list[dict] = []

                    for exec_item in executed_results:
                        tool_name = exec_item.get("tool_name", "")
                        tool_function_params = exec_item.get("params", {})
                        raw_tool_result = exec_item.get("raw_result")

                        # Governor 跳过的调用（去重/预算超限等）视为负面信号
                        if isinstance(raw_tool_result, str) and raw_tool_result.startswith("skipped:"):
                            round_pressure_bump = max(round_pressure_bump, 1)

                        if tool_name in {"fetch_url", "fetch_url_rendered"}:
                            fetch_obj = raw_tool_result
                            if isinstance(fetch_obj, str):
                                fetch_obj = _safe_json_loads(fetch_obj)
                            if isinstance(fetch_obj, dict):
                                status = str(fetch_obj.get("status") or "").strip().lower()
                                signals = fetch_obj.get("signals") or []
                                quality_score = float(fetch_obj.get("quality_score") or 0.0)

                                has_anti_bot = (
                                    isinstance(signals, list)
                                    and "anti_bot_or_challenge" in signals
                                )

                                if (
                                    status == "ok"
                                    and quality_score >= 0.55
                                    and not has_anti_bot
                                ):
                                    successful_fetch_evidence_count += 1
                                elif status in {"blocked", "thin", "error"}:
                                    round_pressure_bump = max(round_pressure_bump, 1)

                                if has_anti_bot or (
                                    isinstance(signals, list)
                                    and any(s in signals for s in ("jina_error", "fetch_error"))
                                ):
                                    round_pressure_bump = max(round_pressure_bump, 1)

                        if tool_name in {
                            "search_notes",
                            "search_chats",
                            "search_knowledge_bases",
                            "search_knowledge_files",
                            "query_knowledge_bases",
                            "list_knowledge_bases",
                        } and _is_effectively_empty_tool_result(tool_name, raw_tool_result):
                            round_pressure_bump = max(round_pressure_bump, 1)

                        try:
                            tool_id = exec_item.get("tool_id", "") or ""
                            citation_sources = get_citation_sources_from_tool_result(
                                tool_name,
                                tool_function_params if isinstance(tool_function_params, dict) else {},
                                raw_tool_result,
                                tool_id=tool_id,
                            )
                            for src in citation_sources:
                                if not isinstance(src, dict):
                                    continue
                                key = ""
                                meta = src.get("metadata") or []
                                if isinstance(meta, list) and meta and isinstance(meta[0], dict):
                                    key = str(meta[0].get("source") or meta[0].get("url") or "")
                                if not key:
                                    ssrc = src.get("source") or {}
                                    if isinstance(ssrc, dict):
                                        key = str(ssrc.get("id") or ssrc.get("url") or ssrc.get("name") or "")
                                if key and key in emitted_tool_sources_seen:
                                    continue
                                if key:
                                    emitted_tool_sources_seen.add(key)
                                await event_emitter({"type": "source", "data": src})
                                if len(emitted_tool_sources_seen) >= 10:
                                    break
                        except Exception:
                            pass

                        exec_item_files = _normalize_message_files(exec_item.get("files"))
                        if exec_item_files:
                            round_message_files = _merge_message_files(
                                round_message_files, exec_item_files
                            )

                        results.append(
                            {
                                "tool_call_id": exec_item.get("tool_call_id", ""),
                                "content": exec_item.get("result", ""),
                                **(
                                    {"files": exec_item_files}
                                    if exec_item_files
                                    else {}
                                ),
                            }
                        )

                    if round_message_files:
                        try:
                            existing_message = Chats.get_message_by_id_and_message_id(
                                metadata["chat_id"], metadata["message_id"]
                            ) or {}
                            merged_message_files = _merge_message_files(
                                existing_message.get("files"), round_message_files
                            )
                            if merged_message_files:
                                await event_emitter(
                                    {
                                        "type": "files",
                                        "data": {"files": merged_message_files},
                                    }
                                )
                                Chats.upsert_message_to_chat_by_id_and_message_id(
                                    metadata["chat_id"],
                                    metadata["message_id"],
                                    {"files": merged_message_files},
                                )
                        except Exception:
                            pass

                    if round_pressure_bump > 0:
                        low_gain_rounds += 1
                    else:
                        low_gain_rounds = max(0, low_gain_rounds - 1)

                    if low_gain_rounds >= 2:
                        round_pressure_bump = max(round_pressure_bump, 1)

                    if round_pressure_bump > 0:
                        adaptive_pressure_level = min(3, adaptive_pressure_level + round_pressure_bump)

                    log.info(
                        "[TOOL ORCH] adaptive_state round=%s pressure=%s low_gain_rounds=%s",
                        tool_call_retries,
                        adaptive_pressure_level,
                        low_gain_rounds,
                    )
                    await emit_tool_orchestration_status(
                        "adaptive_state",
                        done=True,
                        round=tool_call_retries,
                        pressure=adaptive_pressure_level,
                        low_gain_rounds=low_gain_rounds,
                    )

                    if _has_nonempty_text_content(content_blocks):
                        consecutive_no_text_rounds = 0
                    else:
                        consecutive_no_text_rounds += 1

                    if (
                        (
                            adaptive_pressure_level >= 3
                            and low_gain_rounds >= 2
                        )
                        or consecutive_no_text_rounds >= 5
                    ) and (not force_final_round):
                        force_final_round = True
                        log.warning(
                            "[TOOL ORCH] force_final_round round=%s pressure=%s low_gain_rounds=%s no_text_rounds=%s",
                            tool_call_retries,
                            adaptive_pressure_level,
                            low_gain_rounds,
                            consecutive_no_text_rounds,
                        )
                        await emit_tool_orchestration_status(
                            "force_final_round",
                            done=False,
                            round=tool_call_retries,
                            pressure=adaptive_pressure_level,
                            low_gain_rounds=low_gain_rounds,
                            consecutive_no_text_rounds=consecutive_no_text_rounds,
                        )

                    content_blocks[-1]["results"] = results

                    content_blocks.append(
                        {
                            "type": "text",
                            "content": "",
                        }
                    )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_content_blocks(content_blocks),
                            },
                        }
                    )

                    try:
                        followup_messages = [
                            *form_data["messages"],
                            *convert_content_blocks_to_messages(content_blocks),
                        ]

                        payload = {
                            "model": model_id,
                            "stream": True,
                            "messages": followup_messages,
                        }

                        if form_data.get("tools"):
                            payload["tools"] = form_data["tools"]

                        pre_followup_tool_queue_len = len(tool_calls)
                        pre_followup_repair_queue_len = len(tool_call_batch_repairs)

                        res = await generate_chat_completion(request, payload, user)

                        if isinstance(res, StreamingResponse):
                            await stream_body_handler(res)
                            # NOTE: when force_final_round is True, do NOT delete
                            # new tool_calls from the queue. Let them stay so the
                            # next iteration injects fake results and the model
                            # can synthesize a text response.
                        else:
                            # Provider may ignore stream flag and return JSON once tool_calls are handled.
                            res_data = res
                            try:
                                from starlette.responses import JSONResponse as StarletteJSONResponse

                                if isinstance(res, StarletteJSONResponse):
                                    res_data = json.loads(
                                        res.body.decode("utf-8", "replace")
                                    )
                            except Exception:
                                res_data = res

                            if isinstance(res_data, dict):
                                # Capture usage from non-streaming response
                                _usage = res_data.get("usage")
                                if isinstance(_usage, dict) and _usage:
                                    _merge_usage(_usage)

                                msg = (
                                    res_data.get("choices", [{}])[0].get("message", {})
                                    if isinstance(res_data.get("choices"), list)
                                    else {}
                                )
                                msg_content = msg.get("content")
                                msg_tool_calls = msg.get("tool_calls")

                                if not msg_content and not msg_tool_calls:
                                    log.warning(
                                        "[TOOL ORCH] followup_empty_message round=%s message_keys=%s",
                                        tool_call_retries,
                                        list(msg.keys()) if isinstance(msg, dict) else [],
                                    )

                                if isinstance(msg_tool_calls, list) and msg_tool_calls:
                                    # Always enqueue — when force_final_round is True,
                                    # the next iteration will inject fake results.
                                    tool_calls.append(msg_tool_calls)
                                    tool_call_batch_repairs.append([])

                                if isinstance(msg_content, str) and msg_content:
                                    if (
                                        content_blocks
                                        and content_blocks[-1].get("type") == "text"
                                    ):
                                        content_blocks[-1]["content"] += msg_content
                                    else:
                                        content_blocks.append(
                                            {"type": "text", "content": msg_content}
                                        )

                                await event_emitter(
                                    {
                                        "type": "chat:completion",
                                        "data": {
                                            "content": serialize_content_blocks(
                                                content_blocks
                                            ),
                                        },
                                    }
                                )

                                # Persist best-effort so refresh doesn't lose the final text.
                                try:
                                    Chats.upsert_message_to_chat_by_id_and_message_id(
                                        metadata["chat_id"],
                                        metadata["message_id"],
                                        {
                                            "content": serialize_content_blocks(
                                                content_blocks
                                            ),
                                        },
                                    )
                                except Exception:
                                    pass

                                # Continue only if we received another tool round.
                                if (
                                    isinstance(msg_tool_calls, list)
                                    and msg_tool_calls
                                ):
                                    continue

                            log.info(
                                "[TOOL ORCH] round_stop round=%s has_text=%s pending_batches=%s",
                                tool_call_retries,
                                _has_nonempty_text_content(content_blocks),
                                len(tool_calls),
                            )
                            await emit_tool_orchestration_status(
                                "round_stop",
                                done=True,
                                round=tool_call_retries,
                                has_text=_has_nonempty_text_content(content_blocks),
                                pending_batches=len(tool_calls),
                            )

                            break

                    except Exception as e:
                        # If the follow-up model turn fails, surface an error message to the UI
                        # so the client doesn't look "stuck" after tools have finished.
                        def _format_model_error(err: Exception) -> str:
                            # Prefer structured info for common HTTP client errors.
                            try:
                                from fastapi import HTTPException as FastAPIHTTPException

                                if isinstance(err, FastAPIHTTPException):
                                    detail = getattr(err, "detail", "") or ""
                                    return f"HTTP {err.status_code}: {detail}"
                            except Exception:
                                pass

                            try:
                                import aiohttp

                                if isinstance(err, aiohttp.ClientResponseError):
                                    status_code = getattr(err, "status", None)
                                    message = getattr(err, "message", "") or ""
                                    req = getattr(err, "request_info", None)
                                    url = ""
                                    if req is not None:
                                        url = str(
                                            getattr(req, "real_url", None)
                                            or getattr(req, "url", None)
                                            or ""
                                        )
                                    return f"HTTP {status_code} {message}".strip() + (
                                        f" ({url})" if url else ""
                                    )
                            except Exception:
                                pass

                            return str(err) or "Unknown error"

                        reason = _format_model_error(e)
                        # Keep it short to avoid dumping provider internals / HTML.
                        if len(reason) > 800:
                            reason = reason[:800] + "..."

                        # Best-effort extraction for a clearer, user-facing diagnosis.
                        upstream_url = ""
                        http_status = None
                        http_message = ""
                        cls = e.__class__
                        raw_english = (
                            f"{getattr(cls, '__module__', '')}.{getattr(cls, '__name__', type(e).__name__)}: {str(e)}"
                        ).strip()
                        try:
                            import aiohttp

                            if isinstance(e, aiohttp.ClientResponseError):
                                http_status = getattr(e, "status", None)
                                http_message = getattr(e, "message", "") or ""
                                req = getattr(e, "request_info", None)
                                if req is not None:
                                    upstream_url = str(
                                        getattr(req, "real_url", None)
                                        or getattr(req, "url", None)
                                        or ""
                                    )
                        except Exception:
                            pass

                        classification = "上游模型服务/代理请求失败"
                        if http_status is not None:
                            if 500 <= int(http_status) <= 599:
                                classification = "上游模型服务/代理返回 5xx（服务端错误）"
                            elif 400 <= int(http_status) <= 499:
                                classification = "上游模型服务/代理返回 4xx（请求/鉴权/配额类错误）"

                        likely_causes = [
                            "上游服务短时故障/过载（常见于 500/502/503/504）",
                            "代理/网关配置异常（转发到错误的后端、后端崩溃）",
                            "模型/参数不兼容导致上游内部异常（例如某些代理对参数校验不完善）",
                            "鉴权/额度/限流问题（更常见于 401/403/429，但部分代理也会错误地返回 500）",
                            "网络/超时/连接被重置（可能表现为 5xx 或客户端异常）",
                        ]

                        summary = "工具已执行完成，但生成回复失败"
                        if http_status is not None:
                            if http_message:
                                summary = (
                                    f"工具已执行完成，但上游模型请求失败（HTTP {http_status} {http_message}）"
                                )
                            else:
                                summary = f"工具已执行完成，但上游模型请求失败（HTTP {http_status}）"

                        safe_model_id = str(model_id).replace('"', "")
                        safe_http_message = str(http_message).replace('"', "") if http_message else ""

                        details_attrs = [
                            'type="error"',
                            'done="true"',
                            f'model="{safe_model_id}"',
                        ]
                        if http_status is not None:
                            details_attrs.append(f'status="{http_status}"')
                        if safe_http_message:
                            details_attrs.append(f'message="{safe_http_message}"')
                        if upstream_url:
                            # Keep attributes reasonably small; content body includes the full URL too.
                            safe_url = upstream_url
                            if len(safe_url) > 300:
                                safe_url = safe_url[:300] + "..."
                            safe_url_attr = str(safe_url).replace('"', "")
                            details_attrs.append(f'url="{safe_url_attr}"')

                        friendly_body = (
                            f"**分类**：{classification}\n\n"
                            f"**情况**：工具调用已成功执行完成，但在生成最终回复时，请求上游模型服务失败，因此无法输出最终回答。\n\n"
                            f"**模型**：`{model_id}`\n\n"
                            + (f"**上游地址**：`{upstream_url}`\n\n" if upstream_url else "")
                            + (
                                f"**HTTP**：`{http_status}` message=`{http_message}`\n\n"
                                if http_status is not None
                                else ""
                            )
                            + f"**摘要**：{reason}\n\n"
                            "### 建议操作\n"
                            "- 先重试一次（上游 5xx 多为短时波动）。\n"
                            "- 检查上游地址/API Key/额度/限流（尤其是 401/403/429）。\n"
                            "- 检查模型名是否存在、参数是否兼容（部分代理对参数校验不完善会回 500）。\n"
                            "- 如果你启用了“Native/原生”工具调用，可尝试切换为“Default/兼容”再试。\n\n"
                            "### 可能原因（供排查）\n"
                            + "".join([f"- {c}\n" for c in likely_causes])
                            + "\n### 原始英文报错（Raw error）\n"
                            + "```text\n"
                            + (raw_english[:2000] + ("..." if len(raw_english) > 2000 else ""))
                            + "\n```"
                        )

                        friendly = (
                            f"<details {' '.join(details_attrs)}>\n"
                            f"<summary>{summary}</summary>\n"
                            f"{friendly_body}\n"
                            f"</details>"
                        )

                        if content_blocks and content_blocks[-1]["type"] == "text":
                            # Keep the error clearly separated from normal output.
                            content_blocks[-1]["content"] = friendly

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_content_blocks(content_blocks),
                                },
                            }
                        )

                        log.debug(e)
                        break

                if len(tool_calls) > 0 and tool_call_retries >= MAX_TOOL_CALL_RETRIES:
                    log.warning(
                        "[TOOL ORCH] rounds_exhausted tool_rounds=%s pending_batches=%s pressure=%s low_gain_rounds=%s",
                        tool_call_retries,
                        len(tool_calls),
                        adaptive_pressure_level,
                        low_gain_rounds,
                    )
                    await emit_tool_orchestration_status(
                        "rounds_exhausted",
                        done=True,
                        tool_rounds=tool_call_retries,
                        pending_batches=len(tool_calls),
                        pressure=adaptive_pressure_level,
                        low_gain_rounds=low_gain_rounds,
                    )

                finalize_error_payload = None

                if tool_call_retries > 0 and not _has_visible_assistant_output(
                    content_blocks, message_files
                ):
                    log.warning(
                        "[TOOL ORCH] finalize_trigger reason=empty_output_after_tools tool_rounds=%s pending_batches=%s",
                        tool_call_retries,
                        len(tool_calls),
                    )
                    await emit_tool_orchestration_status(
                        "finalize_start",
                        done=False,
                        reason="empty_output_after_tools",
                        tool_rounds=tool_call_retries,
                        pending_batches=len(tool_calls),
                    )
                    try:
                        finalize_blocks = trim_content_blocks_for_finalize(content_blocks)
                        fallback_payload = {
                            "model": model_id,
                            "stream": True,
                            "messages": [
                                *form_data["messages"],
                                *convert_content_blocks_to_messages(finalize_blocks),
                            ],
                        }
                        if form_data.get("tools"):
                            fallback_payload["tools"] = form_data["tools"]

                        res = await generate_chat_completion(request, fallback_payload, user)

                        if isinstance(res, StreamingResponse):
                            await stream_body_handler(res)
                            log.info(
                                "[TOOL ORCH] finalize_stream_done has_visible_output=%s",
                                _has_visible_assistant_output(
                                    content_blocks, message_files
                                ),
                            )
                        else:
                            res_data = res
                            try:
                                from starlette.responses import JSONResponse as StarletteJSONResponse

                                if isinstance(res, StarletteJSONResponse):
                                    res_data = json.loads(
                                        res.body.decode("utf-8", "replace")
                                    )
                            except Exception:
                                res_data = res

                            if isinstance(res_data, dict):
                                # Capture usage from non-streaming response
                                _usage = res_data.get("usage")
                                if isinstance(_usage, dict) and _usage:
                                    _merge_usage(_usage)

                                msg = (
                                    res_data.get("choices", [{}])[0].get("message", {})
                                    if isinstance(res_data.get("choices"), list)
                                    else {}
                                )

                                msg_content = msg.get("content")
                                if not msg_content:
                                    msg_content = msg.get("reasoning_content")

                                if isinstance(msg_content, str) and msg_content:
                                    if (
                                        content_blocks
                                        and content_blocks[-1].get("type") == "text"
                                    ):
                                        content_blocks[-1]["content"] += msg_content
                                    else:
                                        content_blocks.append(
                                            {"type": "text", "content": msg_content}
                                        )

                                await event_emitter(
                                    {
                                        "type": "chat:completion",
                                        "data": {
                                            "content": serialize_content_blocks(content_blocks),
                                        },
                                    }
                                )

                                try:
                                    Chats.upsert_message_to_chat_by_id_and_message_id(
                                        metadata["chat_id"],
                                        metadata["message_id"],
                                        {
                                            "content": serialize_content_blocks(content_blocks),
                                        },
                                    )
                                except Exception:
                                    pass
                    except Exception as e:
                        log.warning(f"[TOOL CALL] Final synthesis retry failed: {e}")

                    final_has_visible_output = _has_visible_assistant_output(
                        content_blocks, message_files
                    )
                    if not final_has_visible_output:
                        recent_tool_names = collect_recent_tool_names(content_blocks)
                        tool_names_text = (
                            "、".join(recent_tool_names)
                            if recent_tool_names
                            else "（未识别到工具名）"
                        )
                        fallback_text = (
                            "工具调用已完成，但未生成可显示的最终回答。\n\n"
                            "已尝试的工具："
                            f"{tool_names_text}。\n\n"
                            "建议：请重试一次，或缩小问题范围后再试；"
                            "若多次失败，请切换其他可用模型。"
                        )
                        finalize_error_payload = {
                            "type": "tool_no_output",
                            "tools": tool_names_text,
                            "content": fallback_text,
                            "reasons": [
                                "tool_no_final_answer",
                            ],
                            "suggestion": "retry_narrow_or_switch",
                        }

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_content_blocks(content_blocks),
                                    "error": finalize_error_payload,
                                },
                            }
                        )

                        try:
                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata["chat_id"],
                                metadata["message_id"],
                                {
                                    "content": serialize_content_blocks(content_blocks),
                                    "error": finalize_error_payload,
                                },
                            )
                        except Exception:
                            pass

                        log.warning(
                            "[TOOL ORCH] finalize_empty_fallback_inserted tool_rounds=%s pending_batches=%s tools=%s",
                            tool_call_retries,
                            len(tool_calls),
                            recent_tool_names,
                        )
                        await emit_tool_orchestration_status(
                            "finalize_empty_fallback_inserted",
                            done=True,
                            tool_rounds=tool_call_retries,
                            pending_batches=len(tool_calls),
                            tools=recent_tool_names,
                        )
                        final_has_text = True

                    log.info(
                        "[TOOL ORCH] finalize_done has_text=%s tool_rounds=%s pending_batches=%s",
                        final_has_text,
                        tool_call_retries,
                        len(tool_calls),
                    )
                    await emit_tool_orchestration_status(
                        "finalize_done",
                        done=True,
                        has_text=final_has_text,
                        tool_rounds=tool_call_retries,
                        pending_batches=len(tool_calls),
                    )

                if DETECT_CODE_INTERPRETER:
                    MAX_RETRIES = 5
                    retries = 0

                    while (
                        content_blocks[-1]["type"] == "code_interpreter"
                        and retries < MAX_RETRIES
                    ):
                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_content_blocks(content_blocks),
                                },
                            }
                        )

                        retries += 1
                        log.debug(f"Attempt count: {retries}")

                        output = ""
                        try:
                            if content_blocks[-1]["attributes"].get("type") == "code":
                                code = content_blocks[-1]["content"]

                                if (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "pyodide"
                                ):
                                    output = await event_caller(
                                        {
                                            "type": "execute:python",
                                            "data": {
                                                "id": str(uuid4()),
                                                "code": code,
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )
                                elif (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "jupyter"
                                ):
                                    output = await execute_code_jupyter(
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
                                        code,
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "token"
                                            else None
                                        ),
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "password"
                                            else None
                                        ),
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
                                    )
                                else:
                                    output = {
                                        "stdout": "Code interpreter engine not configured."
                                    }

                                log.debug(f"Code interpreter output: {output}")

                                if isinstance(output, dict):
                                    stdout = output.get("stdout", "")

                                    if isinstance(stdout, str):
                                        stdoutLines = stdout.split("\n")
                                        for idx, line in enumerate(stdoutLines):
                                            if "data:image/png;base64" in line:
                                                id = str(uuid4())

                                                # ensure the path exists
                                                os.makedirs(
                                                    os.path.join(CACHE_DIR, "images"),
                                                    exist_ok=True,
                                                )

                                                image_path = os.path.join(
                                                    CACHE_DIR,
                                                    f"images/{id}.png",
                                                )

                                                with open(image_path, "wb") as f:
                                                    f.write(
                                                        base64.b64decode(
                                                            line.split(",")[1]
                                                        )
                                                    )

                                                stdoutLines[idx] = (
                                                    f"![Output Image {idx}](/cache/images/{id}.png)"
                                                )

                                        output["stdout"] = "\n".join(stdoutLines)

                                    result = output.get("result", "")

                                    if isinstance(result, str):
                                        resultLines = result.split("\n")
                                        for idx, line in enumerate(resultLines):
                                            if "data:image/png;base64" in line:
                                                id = str(uuid4())

                                                # ensure the path exists
                                                os.makedirs(
                                                    os.path.join(CACHE_DIR, "images"),
                                                    exist_ok=True,
                                                )

                                                image_path = os.path.join(
                                                    CACHE_DIR,
                                                    f"images/{id}.png",
                                                )

                                                with open(image_path, "wb") as f:
                                                    f.write(
                                                        base64.b64decode(
                                                            line.split(",")[1]
                                                        )
                                                    )

                                                resultLines[idx] = (
                                                    f"![Output Image {idx}](/cache/images/{id}.png)"
                                                )

                                        output["result"] = "\n".join(resultLines)
                        except Exception as e:
                            output = str(e)

                        content_blocks[-1]["output"] = output

                        content_blocks.append(
                            {
                                "type": "text",
                                "content": "",
                            }
                        )

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_content_blocks(content_blocks),
                                },
                            }
                        )

                        try:
                            res = await generate_chat_completion(
                                request,
                                {
                                    "model": model_id,
                                    "stream": True,
                                    "messages": [
                                        *form_data["messages"],
                                        {
                                            "role": "assistant",
                                            "content": serialize_content_blocks(
                                                content_blocks, raw=True
                                            ),
                                        },
                                    ],
                                },
                                user,
                            )

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res)
                            else:
                                break
                        except Exception as e:
                            log.debug(e)
                            break

                title = Chats.get_chat_title_by_id(metadata["chat_id"])

                # Detect empty response (model returned 200 but no content).
                # Common with reverse proxies / relay services that swallow errors.
                if not finalize_error_payload and not _has_visible_assistant_output(
                    content_blocks, message_files
                ):
                    model_id_display = form_data.get("model", "unknown")
                    if _stream_api_error:
                        # Real API error caused the empty content — show truthful error
                        log.warning(
                            "[API ERROR] Model %s returned error: code=%s message=%s",
                            model_id_display,
                            _stream_api_error.get("code", "unknown"),
                            str(_stream_api_error.get("message", ""))[:200],
                        )
                        finalize_error_payload = _build_api_error_payload(
                            _stream_api_error,
                            model_id_display,
                            status_override=_stream_response_status,
                        )
                    elif _stream_response_status and _stream_response_status >= 400:
                        log.warning(
                            "[API ERROR] Model %s returned HTTP %s without a structured SSE error payload",
                            model_id_display,
                            _stream_response_status,
                        )
                        finalize_error_payload = _build_api_error_payload(
                            {
                                "message": "\n".join(_stream_non_sse_error_lines[-10:]),
                            },
                            model_id_display,
                            status_override=_stream_response_status,
                        )
                    else:
                        # Genuine empty response (HTTP 200 but no content)
                        log.warning(
                            "[EMPTY RESPONSE] Model %s returned empty content (0 text tokens). "
                            "This may be caused by content filtering, an unavailable model, or a proxy issue.",
                            model_id_display,
                        )
                        finalize_error_payload = {
                            "type": "empty_response",
                            "model_id": model_id_display,
                            "content": f"模型 {model_id_display} 返回了空响应（0 token）。",
                            "reasons": [
                                "content_filter",
                                "proxy_error",
                                "model_unavailable",
                            ],
                            "suggestion": "retry_or_switch",
                        }

                completed_at = int(time.time())
                data = {
                    "done": True,
                    "content": serialize_content_blocks(content_blocks),
                    "title": title,
                    "completedAt": completed_at,
                    **({"files": message_files} if message_files else {}),
                }

                if accumulated_usage:
                    data["usage"] = accumulated_usage
                    # When realtime chat saving is enabled, we persist content incrementally during
                    # streaming, but usage (token counts) is typically only available at the end.
                    # Persist it once here so admin analytics (chat_message table aggregates) can
                    # reflect accurate token usage.
                    if ENABLE_REALTIME_CHAT_SAVE:
                        try:
                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata["chat_id"],
                                metadata["message_id"],
                                {
                                    "done": True,
                                    "completedAt": completed_at,
                                    "usage": accumulated_usage,
                                },
                            )
                        except Exception as e:
                            log.warning(f"Failed to persist usage for analytics: {e}")
                elif ENABLE_REALTIME_CHAT_SAVE:
                    try:
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata["chat_id"],
                            metadata["message_id"],
                            {
                                "done": True,
                                "completedAt": completed_at,
                            },
                        )
                    except Exception as e:
                        log.warning(
                            f"Failed to persist stream completion metadata: {e}"
                        )

                if finalize_error_payload:
                    data["error"] = finalize_error_payload

                if finalize_error_payload and ENABLE_REALTIME_CHAT_SAVE:
                    try:
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata["chat_id"],
                            metadata["message_id"],
                            {
                                "error": finalize_error_payload,
                            },
                        )
                    except Exception as e:
                        log.warning(f"Failed to persist stream error payload: {e}")

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    _save_payload = {
                        "content": serialize_content_blocks(content_blocks),
                        "done": True,
                        "completedAt": completed_at,
                        **({"files": message_files} if message_files else {}),
                    }
                    if accumulated_usage:
                        _save_payload["usage"] = accumulated_usage
                    if finalize_error_payload:
                        _save_payload["error"] = finalize_error_payload
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        _save_payload,
                    )

                # The assistant reply is already finalized at this point. Any remaining
                # work is post-response bookkeeping (webhooks/title/tags/follow-ups),
                # which should not make the refreshed UI think the last assistant
                # message is still streaming.
                set_current_task_blocks_completion(False)

                # Send a webhook notification if the user is not active
                if get_active_status_by_user_id(user.id) is None:
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                            {
                                "action": "chat",
                                "message": content,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )

                await background_tasks_handler()
            except asyncio.CancelledError:
                log.warning("Task was cancelled!")
                await event_emitter({"type": "task-cancelled"})

                if accumulated_usage and ENABLE_REALTIME_CHAT_SAVE:
                    try:
                        Chats.upsert_message_to_chat_by_id_and_message_id(
                            metadata["chat_id"],
                            metadata["message_id"],
                            {
                                "usage": accumulated_usage,
                            },
                        )
                    except Exception as e:
                        log.warning(
                            f"Failed to persist usage for analytics on cancel: {e}"
                        )

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    _cancel_payload = {
                        "content": serialize_content_blocks(content_blocks),
                    }
                    if accumulated_usage:
                        _cancel_payload["usage"] = accumulated_usage
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        _cancel_payload,
                    )

            if response.background is not None:
                await response.background()

        # background_tasks.add_task(post_response_handler, response, events)
        task_id, _ = create_task(
            post_response_handler(response, events), id=metadata["chat_id"]
        )
        return {"status": True, "task_id": task_id}

    else:
        # Fallback to the original response
        async def stream_wrapper(original_generator, events):
            def wrap_item(item):
                return f"data: {item}\n\n"

            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield wrap_item(json.dumps(event))

            async for data in original_generator:
                data, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=data,
                    extra_params=extra_params,
                )

                if data:
                    yield data

        return StreamingResponse(
            stream_wrapper(response.body_iterator, events),
            headers=dict(response.headers),
            background=response.background,
        )
