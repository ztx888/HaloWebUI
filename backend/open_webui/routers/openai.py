import asyncio
import base64
import copy
import hashlib
import io
import json
import logging
import os
import re
import uuid
import time
from typing import Optional
from urllib.parse import urlparse

import aiohttp
from aiocache import cached
import requests

from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from fastapi import Depends, HTTPException, Request, APIRouter
from fastapi.responses import (
    FileResponse,
    StreamingResponse,
    JSONResponse,
    PlainTextResponse,
)
from pydantic import BaseModel, ConfigDict

from sqlalchemy.orm import Session

from open_webui.internal.db import get_session

from open_webui.models.models import Models
from open_webui.models.access_grants import AccessGrants
from open_webui.models.groups import Groups
from open_webui.models.files import Files, FileForm
from open_webui.storage.provider import Storage
from open_webui.config import (
    CACHE_DIR,
    UPLOAD_DIR,
)
from open_webui.env import (
    MODELS_CACHE_TTL,
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    BYPASS_MODEL_ACCESS_CONTROL,
)
from open_webui.models.users import UserModel

from open_webui.constants import ERROR_MESSAGES


from open_webui.utils.payload import (
    apply_model_params_to_body_openai,
    apply_system_prompt_to_body,
)
from open_webui.utils.misc import (
    cleanup_response,
    convert_logit_bias_input_to_json,
    stream_chunks_handler,
    stream_wrapper,
)

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.headers import include_user_info_headers
from open_webui.utils.anthropic import is_anthropic_url, get_anthropic_models

log = logging.getLogger(__name__)


##########################################
#
# Utility functions
#
##########################################



async def error_stream_generator(model_id, error_msg):
    """
    Generate an SSE error stream that the frontend can recognize and display with red error styling.
    The frontend checks for 'error' field in SSE data to trigger error rendering.
    """
    # Send error object that frontend can detect
    error_chunk = {
        "error": {
            "message": error_msg,
            "type": "api_error",
            "code": "upstream_error"
        }
    }
    yield f"data: {json.dumps(error_chunk)}\n\n"
    yield "data: [DONE]\n\n"


def _get_upstream_host(request_url: str) -> str:
    try:
        return urlparse(request_url).hostname or ""
    except Exception:
        return ""


def _format_upstream_timeout_message(request_url: str) -> str:
    host = _get_upstream_host(request_url)
    upstream_info = (
        f"Upstream error 524. {host} | 524: A timeout occurred"
        if host
        else "Upstream error 524. 524: A timeout occurred"
    )
    return (
        "\u554a\u54e6\uff01\u56de\u7b54\u6709\u95ee\u9898 "
        + upstream_info
        + " \u4e0a\u6e38\u54cd\u5e94\u592a\u6162/\u88ab\u7f51\u5173\u65ad\u5f00\u3002"
    )


def _build_upstream_error_message(status: int, response, request_url: str) -> str:
    if status == 524:
        return _format_upstream_timeout_message(request_url)

    error_msg = f"HTTP Error {status}"
    if isinstance(response, dict) and "error" in response:
        err = response["error"]
        if isinstance(err, dict):
            error_msg = f"{err.get('message', str(err))} (Code: {status})"
        else:
            error_msg = f"{str(err)} (Code: {status})"
    elif isinstance(response, str):
        error_msg = f"{response} (Code: {status})"
    return error_msg


def _build_upstream_error_payload(status: int, response, request_url: str) -> dict:
    error_msg = _build_upstream_error_message(status, response, request_url)
    code = "upstream_timeout" if status == 524 else "upstream_error"
    return {
        "error": {
            "message": error_msg,
            "type": "api_error",
            "code": code,
        }
    }


WEB_SEARCH_TOOL_TYPES = ("web_search", "web_search_preview", "browser_search")


def _find_web_search_tool_type(tools):
    if not isinstance(tools, list):
        return None
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") in WEB_SEARCH_TOOL_TYPES:
            return tool.get("type")
    return None


def _has_native_web_search_tool(payload: dict) -> bool:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") in WEB_SEARCH_TOOL_TYPES:
            return True
        if "googleSearch" in tool:
            return True
    return False


def _set_web_search_tool_type(payload: dict, tool_type: str) -> None:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        tools = []
        payload["tools"] = tools

    filtered = []
    for tool in tools:
        if isinstance(tool, dict) and tool.get("type") in WEB_SEARCH_TOOL_TYPES:
            continue
        filtered.append(tool)

    filtered.append({"type": tool_type})
    payload["tools"] = filtered


def _remove_native_web_search_tools(payload: dict) -> None:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return

    filtered = []
    for tool in tools:
        if not isinstance(tool, dict):
            filtered.append(tool)
            continue
        if tool.get("type") in WEB_SEARCH_TOOL_TYPES:
            continue
        if "googleSearch" in tool:
            continue
        filtered.append(tool)

    if filtered:
        payload["tools"] = filtered
    else:
        payload.pop("tools", None)


def _error_text_from_response(response) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        error = response.get("error", response)
        if isinstance(error, dict):
            return str(error.get("message") or error)
        return str(error)
    return ""


def _is_native_web_search_unsupported_error(response) -> bool:
    text = _error_text_from_response(response).lower()
    if not text:
        return False

    if "tools[0].type" in text and "invalid value" in text:
        return True

    if "function_declarations" in text and "invalid function name" in text:
        return True

    for token in ("web_search", "web_search_preview", "browser_search", "googlesearch"):
        if token in text and any(
            phrase in text
            for phrase in ("not supported", "unsupported", "invalid", "not allowed")
        ):
            return True

    return False


def _get_native_web_search_tool_types(api_config: dict, base_url: str) -> list[str]:
    config = api_config or {}
    tool_types = config.get("native_web_search_tool_types")
    if isinstance(tool_types, list):
        normalized = [str(t) for t in tool_types if t]
        if normalized:
            return normalized

    tool_type = config.get("native_web_search_tool")
    if tool_type:
        return [str(tool_type)]

    if "api.openai.com" in (base_url or ""):
        return ["web_search_preview", "web_search"]

    return ["web_search", "web_search_preview", "browser_search"]


def convert_tools_to_responses_format(tools):
    if not isinstance(tools, list):
        return tools

    converted = []
    for tool in tools:
        if not isinstance(tool, dict):
            converted.append(tool)
            continue

        tool_type = tool.get("type", "function")
        function_spec = tool.get("function")

        # Native web_search tools: pass through as-is for maximum compatibility
        # Don't add default search_context_size to avoid schema errors with proxies that don't support it
        if tool_type == "web_search":
            converted.append(dict(tool))
            continue

        # Already in Responses format; drop nested function if present.
        if "name" in tool:
            converted_tool = {k: v for k, v in tool.items() if k != "function"}
            converted.append(converted_tool)
            continue

        # Convert Chat Completions function tool -> Responses format.
        if tool_type == "function" and isinstance(function_spec, dict):
            converted_tool = {"type": "function"}
            converted_tool.update(function_spec)
            # Preserve any extra tool-level fields except the nested function.
            for key, value in tool.items():
                if key in ("function",):
                    continue
                converted_tool.setdefault(key, value)
            converted.append(converted_tool)
            continue

        # Fallback: keep as-is for non-function tool types.
        converted.append(tool)

    return converted


def convert_tool_choice_to_responses_format(tool_choice):
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            function_spec = tool_choice.get("function", {})
            if isinstance(function_spec, dict) and function_spec.get("name"):
                converted = {"type": "function", "name": function_spec.get("name")}
                for key, value in tool_choice.items():
                    if key not in ("type", "function"):
                        converted.setdefault(key, value)
                return converted
        return tool_choice

    return tool_choice


def _tool_call_id_from_item(item, event=None):
    if not isinstance(item, dict):
        item = {}
    if event is None or not isinstance(event, dict):
        event = {}

    for key in ("id", "call_id", "tool_call_id"):
        value = item.get(key)
        if value:
            return value

    for key in ("call_id", "tool_call_id", "id"):
        value = event.get(key)
        if value:
            return value

    for key in ("item_id", "output_id"):
        value = event.get(key)
        if value:
            return value

    return None


def _tool_call_name_from_item(item):
    if not isinstance(item, dict):
        return ""
    return (
        item.get("name")
        or item.get("name_delta")
        or item.get("function_name")
        or item.get("tool_name")
        or item.get("function", {}).get("name")
        or ""
    )


def _tool_call_args_from_item(item):
    if not isinstance(item, dict):
        return ""
    return (
        item.get("arguments")
        or item.get("arguments_delta")
        or item.get("arguments_text")
        or item.get("arguments_text_delta")
        or item.get("input")
        or item.get("parameters")
        or item.get("args")
        or item.get("function", {}).get("arguments")
        or item.get("function", {}).get("parameters")
        or ""
    )


def _stringify_tool_call_args(arguments):
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except Exception:
        return str(arguments)


def convert_to_responses_payload(chat_payload: dict) -> dict:
    """
    Convert Chat Completions API format to Responses API format.

    Chat Completions:
        {"messages": [...], "model": "...", "stream": true, ...}

    Responses API:
        {"input": [...], "model": "...", "stream": true, ...}

    Uses EasyInputMessageParam format which supports all roles (user, assistant, system, developer)
    and allows simple string content for easier compatibility.
    """
    responses_payload = {}

    # Model is the same
    if "model" in chat_payload:
        responses_payload["model"] = chat_payload["model"]

    # Convert messages to input items using EasyInputMessageParam format
    messages = chat_payload.get("messages", [])
    input_items = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # DEBUG: Log each message being processed
        log.info(f"[DEBUG RESPONSES PAYLOAD] Processing message role={role}, has_content={bool(content)}, has_tool_calls={bool(msg.get('tool_calls'))}, tool_call_id={msg.get('tool_call_id')}")

        if role == "system":
            # System messages become instructions in Responses API
            responses_payload["instructions"] = content if isinstance(content, str) else str(content)
            continue
        if role in ("tool", "function"):
            tool_call_id = (
                msg.get("tool_call_id")
                or msg.get("id")
                or msg.get("name")
            )
            output = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, default=str)
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id,
                    "output": output,
                }
            )
            continue
        else:
            # Map role: assistant -> assistant, user -> user, anything else -> user
            mapped_role = role if role in ["user", "assistant"] else "user"

            # Build the message item using EasyInputMessageParam format
            if isinstance(content, str):
                # User messages: use input_text structure (most proxies expect this format)
                if mapped_role == "user":
                    item = {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": content}]
                    }
                else:
                    # Assistant messages: use simple string content
                    item = {
                        "type": "message",
                        "role": "assistant",
                        "content": content
                    }
            elif isinstance(content, list):
                # Multimodal content - need to build content array
                # For user messages, use input_text and input_image types
                # For assistant messages, we just extract text and use it as string
                if mapped_role == "assistant":
                    # Assistant messages: extract text content as simple string
                    text_parts = []
                    for c in content:
                        if isinstance(c, dict):
                            if c.get("type") == "text":
                                text_parts.append(c.get("text", ""))
                        elif isinstance(c, str):
                            text_parts.append(c)
                    item = {
                        "type": "message",
                        "role": "assistant",
                        "content": "".join(text_parts)
                    }
                else:
                    # User messages with multimodal content
                    content_parts = []
                    for c in content:
                        if isinstance(c, dict):
                            if c.get("type") == "text":
                                content_parts.append({"type": "input_text", "text": c.get("text", "")})
                            elif c.get("type") == "image_url":
                                image_url = c.get("image_url", {}).get("url", "")
                                content_parts.append({"type": "input_image", "image_url": image_url})
                        elif isinstance(c, str):
                            content_parts.append({"type": "input_text", "text": c})
                    
                    if content_parts:
                        item = {
                            "type": "message",
                            "role": "user",
                            "content": content_parts
                        }
                    else:
                        # Fallback to empty string if no content parts
                        item = {
                            "type": "message",
                            "role": "user",
                            "content": ""
                        }
            else:
                # Fallback for other content types
                item = {
                    "type": "message",
                    "role": mapped_role,
                    "content": str(content) if content else ""
                }
            
            # For assistant messages with tool_calls but no content, skip adding empty message
            # Only add function_call items to match Responses API expected format
            has_tool_calls = mapped_role == "assistant" and msg.get("tool_calls")
            has_actual_content = bool(content) if isinstance(content, str) else bool(content)
            
            if has_tool_calls and not has_actual_content:
                # Skip empty message, only add function_call items
                log.info(f"[DEBUG RESPONSES PAYLOAD] Skipping empty assistant message, adding function_calls only")
            else:
                input_items.append(item)

            if has_tool_calls:
                for tool_call in msg.get("tool_calls", []):
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_id = _tool_call_id_from_item(tool_call)
                    tool_call_name = _tool_call_name_from_item(tool_call)
                    tool_call_args = _stringify_tool_call_args(
                        _tool_call_args_from_item(tool_call)
                    )
                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": tool_call_id or f"call_{uuid.uuid4().hex}",
                            "name": tool_call_name,
                            "arguments": tool_call_args,
                        }
                    )
    
    responses_payload["input"] = input_items

    # Copy other common parameters
    if "stream" in chat_payload:
        responses_payload["stream"] = chat_payload["stream"]
    if "max_tokens" in chat_payload:
        responses_payload["max_output_tokens"] = chat_payload["max_tokens"]
    if "temperature" in chat_payload:
        responses_payload["temperature"] = chat_payload["temperature"]
    if "top_p" in chat_payload:
        responses_payload["top_p"] = chat_payload["top_p"]

    # 设置 store 参数 - 默认为 False（让思考链正常显示的关键参数）
    # 但允许用户通过高级参数覆盖（只接受 bool 类型，避免发送 null）
    store_value = chat_payload.get("store")
    responses_payload["store"] = store_value if isinstance(store_value, bool) else RESPONSES_DEFAULT_STORE
    log.info(f"[RESPONSES API] store = {responses_payload['store']}")

    # Handle reasoning parameters - convert reasoning_effort to Responses API format
    reasoning_effort = chat_payload.get("reasoning_effort") or chat_payload.get("reasoning", {}).get("effort")
    reasoning_summary = chat_payload.get("reasoning", {}).get("summary") or "auto"  # None/空字符串回退到 "auto"
    model_name = chat_payload.get("model", "")
    is_reasoning_model = is_openai_reasoning_model(model_name)

    if reasoning_effort:
        # Responses API format: reasoning.effort and reasoning.summary are inside the same object
        reasoning_obj = {"effort": reasoning_effort.lower(), "summary": reasoning_summary}
        responses_payload["reasoning"] = reasoning_obj
        log.info(f"[RESPONSES API] reasoning.effort = {reasoning_effort}, reasoning.summary = {reasoning_summary}")

    elif is_reasoning_model:
        # For reasoning models without explicit reasoning_effort, still send summary
        reasoning_obj = {"summary": reasoning_summary}
        responses_payload["reasoning"] = reasoning_obj
        log.info(f"[RESPONSES API] Auto-enabled reasoning for reasoning model: {model_name}, summary = {reasoning_summary}")

    # Convert tools/tool_choice from Chat Completions format to Responses format.
    if "tools" in chat_payload:
        tools = convert_tools_to_responses_format(chat_payload["tools"])
        responses_payload["tools"] = tools

    # Handle tool_choice
    if "tool_choice" in chat_payload:
        responses_payload["tool_choice"] = convert_tool_choice_to_responses_format(
            chat_payload["tool_choice"]
        )
    elif isinstance(responses_payload.get("tools"), list):
        has_web_search = any(
            isinstance(tool, dict) and tool.get("type") == "web_search"
            for tool in responses_payload["tools"]
        )
        if has_web_search:
            responses_payload["tool_choice"] = "auto"

    # DEBUG: Log the last few input items to see function_call and function_call_output
    if len(input_items) > 5:
        last_items = input_items[-5:]
    else:
        last_items = input_items
    log.info(f"[DEBUG RESPONSES PAYLOAD] Last {len(last_items)} input items: {json.dumps(last_items, ensure_ascii=False, default=str)[:1500]}")

    log.info(f"Responses API payload: {json.dumps(responses_payload, ensure_ascii=False, default=str)[:2000]}")
    return responses_payload



async def responses_stream_to_chat_completions_stream(response: aiohttp.ClientResponse, model_id: str):
    """
    Convert Responses API streaming events to Chat Completions format.
    
    Responses API events:
        {"type": "response.output_text.delta", "delta": "..."} 
        {"type": "response.done", ...}
    
    Converts to Chat Completions format:
        {"choices": [{"delta": {"content": "..."}}]}
    """
    stream_id = f"chatcmpl-{uuid.uuid4().hex}"
    timestamp = int(time.time())
    
    # Log response info for debugging
    log.info(f"Responses API stream: status={response.status}, content_type={response.headers.get('Content-Type', 'unknown')}")
    
    buffer = ""
    
    def create_chunk(
        delta_content=None,
        reasoning_content=None,
        tool_calls=None,
        finish_reason=None,
    ):
        """Helper to create a chat completion chunk."""
        delta = {}
        if delta_content is not None:
            delta["content"] = delta_content
        if reasoning_content is not None:
            delta["reasoning_content"] = reasoning_content
        if tool_calls is not None:
            delta["tool_calls"] = tool_calls
        
        return {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": timestamp,
            "model": model_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]
        }
    
    tool_call_index_by_id = {}
    tool_call_index_by_fallback = {}
    next_tool_call_index = 0
    seen_tool_call_ids = set()
    tool_call_from_added = {}
    tool_call_has_delta = set()
    content_started = False
    reasoning_source = None
    reasoning_streamed = False  # Track if reasoning was already streamed via delta events

    def get_tool_call_index(tool_call_id, provided_index=None):
        nonlocal next_tool_call_index

        if tool_call_id:
            if tool_call_id not in tool_call_index_by_id:
                tool_call_index_by_id[tool_call_id] = next_tool_call_index
                next_tool_call_index += 1
            return tool_call_index_by_id[tool_call_id]

        if provided_index is not None:
            if provided_index not in tool_call_index_by_fallback:
                tool_call_index_by_fallback[provided_index] = next_tool_call_index
                next_tool_call_index += 1
            return tool_call_index_by_fallback[provided_index]

        tool_call_index = next_tool_call_index
        next_tool_call_index += 1
        return tool_call_index

    def build_tool_call_delta(item, event=None, use_delta=False):
        if event is None:
            event = {}
        if not isinstance(item, dict):
            return None

        tool_type = (
            item.get("type")
            or event.get("item", {}).get("type")
            or event.get("delta", {}).get("type")
            or ""
        )
        if tool_type not in ("tool_call", "function_call"):
            return None

        tool_call_id = _tool_call_id_from_item(item, event)
        provided_index = (
            item.get("index")
            or event.get("output_index")
            or event.get("item_index")
            or event.get("index")
        )
        tool_call_index = get_tool_call_index(tool_call_id, provided_index)

        name = _tool_call_name_from_item(item)
        arguments = _tool_call_args_from_item(item)
        if use_delta and not name and not arguments:
            return None

        name = name or ""
        arguments = _stringify_tool_call_args(arguments)

        if tool_call_id:
            tool_call_id_value = tool_call_id
        else:
            tool_call_id_value = f"call_{stream_id}_{tool_call_index}"

        tool_call = {
            "index": tool_call_index,
            "id": tool_call_id_value,
            "type": "function",
            "function": {
                "name": name,
                "arguments": arguments,
            },
        }
        return tool_call

    try:
        chunk_count = 0
        async for chunk in response.content.iter_any():
            chunk_count += 1
            if not chunk:
                log.info(f"[RESPONSES STREAM DEBUG] Received empty chunk #{chunk_count}")
                continue

            log.info(f"[RESPONSES STREAM DEBUG] Received chunk #{chunk_count}, size={len(chunk)}")
            buffer += chunk.decode("utf-8", errors="replace")
            
            # Process SSE events
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                
                if not line:
                    continue
                
                # Handle SSE data: prefix
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield f"data: [DONE]\n\n"
                        return
                    
                    try:
                        event = json.loads(data)
                        event_type = event.get("type", "")

                        # Debug: log event types
                        log.info(f"Responses API event: type={event_type}")

                        # Debug: log all reasoning-related events
                        if "reasoning" in event_type.lower():
                            log.info(f"[REASONING EVENT DEBUG] Full event: {json.dumps(event, default=str)[:500]}")

                        # Handle text delta (main response content)
                        if event_type == "response.output_text.delta":
                            delta_text = event.get("delta", "")
                            if delta_text:
                                content_started = True
                            yield f"data: {json.dumps(create_chunk(delta_content=delta_text))}\n\n"
                        
                        # Handle reasoning/thinking delta - just forward as reasoning_content
                        # middleware.py will handle the <details> tag wrapping
                        elif event_type in ("response.reasoning_summary_text.delta", "response.reasoning.delta", "response.reasoning_summary_part.delta"):
                            reasoning_text = event.get("delta", "")
                            # Also try to get text from nested structure
                            if not reasoning_text and isinstance(event.get("delta"), dict):
                                reasoning_text = event.get("delta", {}).get("text", "")
                            log.info(f"[REASONING DEBUG] Received reasoning delta: type={event_type}, text={reasoning_text[:100] if reasoning_text else 'empty'}")
                            if reasoning_text:
                                reasoning_streamed = True  # Mark that we've streamed reasoning via delta events
                                event_source = (
                                    "summary"
                                    if event_type in ("response.reasoning_summary_text.delta", "response.reasoning_summary_part.delta")
                                    else "reasoning"
                                )
                                if reasoning_source is None:
                                    reasoning_source = event_source
                                # Only skip if we already have a different reasoning source
                                # (e.g., don't mix summary and full reasoning)
                                if reasoning_source != event_source:
                                    continue
                                yield f"data: {json.dumps(create_chunk(reasoning_content=reasoning_text))}\n\n"

                        # Handle reasoning summary part added - some APIs send this instead of delta
                        elif event_type == "response.reasoning_summary_part.added":
                            part = event.get("part", {})
                            if isinstance(part, dict):
                                reasoning_text = part.get("text", "")
                                if reasoning_text:
                                    log.info(f"[REASONING DEBUG] Received reasoning_summary_part.added: text={reasoning_text[:100]}")
                                    reasoning_streamed = True
                                    yield f"data: {json.dumps(create_chunk(reasoning_content=reasoning_text))}\n\n"

                        elif event_type == "response.output_item.added":
                            item = event.get("item", {}) or {}
                            if not isinstance(item, dict):
                                item = {}
                            # Debug: log the full item to see its structure
                            log.info(f"[TOOL DEBUG] output_item.added - item: {json.dumps(item, default=str)[:500]}")
                            tool_call = build_tool_call_delta(item, event)
                            if tool_call:
                                log.info(f"[TOOL DEBUG] Built tool_call from added: {json.dumps(tool_call, default=str)}")
                                tool_call_from_added[tool_call.get("id")] = tool_call

                        elif event_type == "response.output_item.delta":
                            item = event.get("item", {})
                            delta = event.get("delta", {})

                            tool_item = {}
                            if isinstance(delta, dict):
                                tool_item.update(delta)
                            if isinstance(item, dict) and "type" not in tool_item:
                                tool_item["type"] = item.get("type")

                            tool_call = build_tool_call_delta(
                                tool_item, event, use_delta=True
                            )
                            if tool_call:
                                tool_call_has_delta.add(tool_call.get("id"))
                                yield f"data: {json.dumps(create_chunk(tool_calls=[tool_call]))}\n\n"

                        elif event_type == "response.output_item.done":
                            item = event.get("item", {}) or {}
                            if not isinstance(item, dict):
                                item = {}
                            # Debug: log the full item to see its structure
                            log.info(f"[TOOL DEBUG] output_item.done - item: {json.dumps(item, default=str)[:1000]}")

                            # Handle reasoning items - fallback for APIs that don't stream reasoning_summary_text.delta
                            item_type = item.get("type", "")
                            if item_type == "reasoning":
                                # Only emit reasoning from here if we didn't already stream it via delta events
                                if not reasoning_streamed:
                                    # Try multiple possible fields for reasoning content
                                    reasoning_emitted = False

                                    # Try summary field first (standard OpenAI format)
                                    summary_list = item.get("summary", [])
                                    if isinstance(summary_list, list) and summary_list:
                                        log.info(f"[REASONING DEBUG] Fallback: Found reasoning summary in output_item.done: {len(summary_list)} items")
                                        for summary_item in summary_list:
                                            if isinstance(summary_item, dict):
                                                summary_text = summary_item.get("text", "")
                                                if summary_text:
                                                    log.info(f"[REASONING DEBUG] Fallback: Emitting reasoning from summary: {summary_text[:100]}")
                                                    yield f"data: {json.dumps(create_chunk(reasoning_content=summary_text))}\n\n"
                                                    reasoning_emitted = True
                                            elif isinstance(summary_item, str) and summary_item:
                                                log.info(f"[REASONING DEBUG] Fallback: Emitting reasoning from summary (str): {summary_item[:100]}")
                                                yield f"data: {json.dumps(create_chunk(reasoning_content=summary_item))}\n\n"
                                                reasoning_emitted = True

                                    # Try content field (some APIs use this)
                                    if not reasoning_emitted:
                                        content_list = item.get("content", [])
                                        if isinstance(content_list, list) and content_list:
                                            log.info(f"[REASONING DEBUG] Fallback: Found reasoning content in output_item.done: {len(content_list)} items")
                                            for content_item in content_list:
                                                if isinstance(content_item, dict):
                                                    content_text = content_item.get("text", "") or content_item.get("content", "")
                                                    if content_text:
                                                        log.info(f"[REASONING DEBUG] Fallback: Emitting reasoning from content: {content_text[:100]}")
                                                        yield f"data: {json.dumps(create_chunk(reasoning_content=content_text))}\n\n"
                                                        reasoning_emitted = True
                                                elif isinstance(content_item, str) and content_item:
                                                    log.info(f"[REASONING DEBUG] Fallback: Emitting reasoning from content (str): {content_item[:100]}")
                                                    yield f"data: {json.dumps(create_chunk(reasoning_content=content_item))}\n\n"
                                                    reasoning_emitted = True
                                        elif isinstance(content_list, str) and content_list:
                                            log.info(f"[REASONING DEBUG] Fallback: Emitting reasoning from content string: {content_list[:100]}")
                                            yield f"data: {json.dumps(create_chunk(reasoning_content=content_list))}\n\n"
                                            reasoning_emitted = True

                                    # Try text field directly (some APIs put it here)
                                    if not reasoning_emitted:
                                        text_field = item.get("text", "")
                                        if text_field:
                                            log.info(f"[REASONING DEBUG] Fallback: Emitting reasoning from text field: {text_field[:100]}")
                                            yield f"data: {json.dumps(create_chunk(reasoning_content=text_field))}\n\n"
                                            reasoning_emitted = True
                                continue

                            tool_call_id = _tool_call_id_from_item(item, event)
                            if tool_call_id and tool_call_id in tool_call_has_delta:
                                continue
                            tool_call = build_tool_call_delta(item, event)
                            if tool_call:
                                log.info(f"[TOOL DEBUG] Built tool_call from done: {json.dumps(tool_call, default=str)}")
                                tool_call_id_value = tool_call.get("id")
                                if tool_call_id_value in tool_call_has_delta:
                                    continue
                                # FIX: Use the current tool_call directly (has full arguments)
                                # Do NOT use tool_call_from_added which has empty arguments from the initial event
                                seen_tool_call_ids.add(tool_call_id_value)
                                yield f"data: {json.dumps(create_chunk(tool_calls=[tool_call]))}\n\n"
                        
                        # Handle completion (OpenAI uses "response.completed", not "response.done")
                        elif event_type in ("response.completed", "response.done"):
                            # Log full event for debugging
                            log.info(f"Completion event received: {json.dumps(event, default=str)[:2000]}")

                            # Extract usage info from completion event
                            response_data = event.get("response", {}) or {}
                            if not isinstance(response_data, dict):
                                response_data = {}

                            # Last chance fallback: try to extract reasoning from output array if not already streamed
                            if not reasoning_streamed:
                                output_array = response_data.get("output", [])
                                if isinstance(output_array, list):
                                    for output_item in output_array:
                                        if isinstance(output_item, dict) and output_item.get("type") == "reasoning":
                                            log.info(f"[REASONING DEBUG] Final fallback: Found reasoning in response.completed output: {json.dumps(output_item, default=str)[:500]}")
                                            # Try summary field
                                            summary_list = output_item.get("summary", [])
                                            if isinstance(summary_list, list) and summary_list:
                                                for summary_item in summary_list:
                                                    if isinstance(summary_item, dict):
                                                        summary_text = summary_item.get("text", "")
                                                        if summary_text:
                                                            log.info(f"[REASONING DEBUG] Final fallback: Emitting reasoning: {summary_text[:100]}")
                                                            yield f"data: {json.dumps(create_chunk(reasoning_content=summary_text))}\n\n"
                                                    elif isinstance(summary_item, str) and summary_item:
                                                        yield f"data: {json.dumps(create_chunk(reasoning_content=summary_item))}\n\n"
                                            # Try content field
                                            content_list = output_item.get("content", [])
                                            if isinstance(content_list, list) and content_list:
                                                for content_item in content_list:
                                                    if isinstance(content_item, dict):
                                                        content_text = content_item.get("text", "") or content_item.get("content", "")
                                                        if content_text:
                                                            log.info(f"[REASONING DEBUG] Final fallback: Emitting reasoning from content: {content_text[:100]}")
                                                            yield f"data: {json.dumps(create_chunk(reasoning_content=content_text))}\n\n"
                                                    elif isinstance(content_item, str) and content_item:
                                                        yield f"data: {json.dumps(create_chunk(reasoning_content=content_item))}\n\n"
                                            elif isinstance(content_list, str) and content_list:
                                                yield f"data: {json.dumps(create_chunk(reasoning_content=content_list))}\n\n"
                                            # Try text field
                                            text_field = output_item.get("text", "")
                                            if text_field:
                                                log.info(f"[REASONING DEBUG] Final fallback: Emitting reasoning from text: {text_field[:100]}")
                                                yield f"data: {json.dumps(create_chunk(reasoning_content=text_field))}\n\n"

                            usage = response_data.get("usage", {})

                            # Also try to get usage directly from event (some APIs put it there)
                            if not usage:
                                usage = event.get("usage", {})
                            
                            # Extract usage data
                            usage_data = None
                            if usage:
                                log.info(f"Extracted usage from Responses API: {usage}")
                                usage_data = {
                                    "prompt_tokens": usage.get("input_tokens", 0),
                                    "completion_tokens": usage.get("output_tokens", 0),
                                    "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                                }

                                # Extract reasoning_tokens and put in completion_tokens_details (frontend expects this structure)
                                output_details = usage.get("output_tokens_details", {})
                                if isinstance(output_details, dict):
                                    reasoning_tokens = output_details.get("reasoning_tokens", 0)
                                    # Always include completion_tokens_details so frontend can show "not reported" message
                                    usage_data["completion_tokens_details"] = {"reasoning_tokens": reasoning_tokens}

                                # Extract cached_tokens and put in prompt_tokens_details (frontend expects this structure)
                                input_details = usage.get("input_tokens_details", {})
                                if isinstance(input_details, dict):
                                    cached_tokens = input_details.get("cached_tokens", 0)
                                    if cached_tokens:
                                        usage_data["prompt_tokens_details"] = {"cached_tokens": cached_tokens}
                                
                                log.info(f"Using usage data: {usage_data}")
                            else:
                                log.warning(f"No usage found in response.completed event. response_data keys: {list(response_data.keys())}")
                            
                            # Send finish chunk with usage info (OpenAI spec)
                            finish_chunk = {
                                "id": stream_id,
                                "object": "chat.completion.chunk",
                                "created": timestamp,
                                "model": model_id,
                                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                            }
                            
                            if usage_data:
                                finish_chunk["usage"] = usage_data

                            yield f"data: {json.dumps(finish_chunk)}\n\n"
                            yield f"data: [DONE]\n\n"
                            return
                        
                        # Handle errors
                        elif event_type == "error":
                            error_msg = event.get("error", {}).get("message", "Unknown error")
                            error_chunk = {
                                "error": {
                                    "message": error_msg,
                                    "type": "api_error",
                                    "code": "responses_api_error"
                                }
                            }
                            yield f"data: {json.dumps(error_chunk)}\n\n"
                            yield f"data: [DONE]\n\n"
                            return
                            
                    except json.JSONDecodeError:
                        log.warning(f"Failed to decode Responses API event: {data[:100]}")
                        continue
    

    except aiohttp.ClientPayloadError as e:
        # Handle transfer encoding errors (e.g., stream interrupted)
        # This often means the API returned an error mid-stream
        log.error(f"Stream payload error: {e}")
        
        # Try to extract any error message from the buffer
        error_message = f"Stream interrupted: {str(e)}"
        if buffer:
            log.error(f"Buffer content at error: {buffer[:500]}")
            # Try to parse any JSON error in the buffer
            try:
                # Look for JSON object in buffer
                import re
                json_match = re.search(r'\{[^{}]*"error"[^{}]*\}', buffer)
                if json_match:
                    error_data = json.loads(json_match.group())
                    if "error" in error_data:
                        err = error_data["error"]
                        if isinstance(err, dict):
                            error_message = err.get("message", error_message)
                        else:
                            error_message = str(err)
            except Exception:
                pass
        
        error_chunk = {
            "error": {
                "message": error_message,
                "type": "stream_error",
                "code": "transfer_encoding_error"
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
    except Exception as e:
        log.error(f"Stream processing error: {e}")
        error_chunk = {
            "error": {
                "message": f"Stream error: {str(e)}",
                "type": "stream_error",
                "code": "unknown_error"
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
    
    # Ensure we always send [DONE]
    yield f"data: [DONE]\n\n"


def convert_responses_to_chat_completions(responses_data: dict, model_id: str) -> dict:
    """
    Convert non-streaming Responses API response to Chat Completions format.
    """
    output = responses_data.get("output", [])
    content = ""
    tool_calls = []
    tool_call_index = 0
    
    # DEBUG: Log the output structure
    log.info(f"[DEBUG RESPONSES->CHAT] output count: {len(output)}, text field exists: {bool(responses_data.get('text'))}")
    
    # Extract text from output items
    for item in output:
        item_type = item.get("type", "")
        log.info(f"[DEBUG RESPONSES->CHAT] item type: {item_type}")
        
        if item_type == "message":
            for part in item.get("content", []):
                part_type = part.get("type", "")
                if part_type in ("output_text", "text"):
                    content += part.get("text", "")
        elif item_type in ("tool_call", "function_call"):
            tool_call_id = _tool_call_id_from_item(item)
            tool_call_name = _tool_call_name_from_item(item)
            tool_call_args = _stringify_tool_call_args(
                _tool_call_args_from_item(item)
            )
            tool_calls.append(
                {
                    "index": tool_call_index,
                    "id": tool_call_id or f"call_{uuid.uuid4().hex}",
                    "type": "function",
                    "function": {
                        "name": tool_call_name,
                        "arguments": tool_call_args,
                    },
                }
            )
            tool_call_index += 1
    
    # Also check top-level 'text' field (some Responses API implementations put content here)
    if not content and responses_data.get("text"):
        text_field = responses_data.get("text")
        log.info(f"[DEBUG RESPONSES->CHAT] text field type: {type(text_field).__name__}, value: {str(text_field)[:200]}")
        if isinstance(text_field, str):
            content = text_field
        elif isinstance(text_field, dict):
            content = text_field.get("value", "") or text_field.get("text", "") or text_field.get("content", "")
        elif isinstance(text_field, list):
            # If text is a list of content items, extract text from them
            parts = []
            for item in text_field:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(item.get("text", "") or item.get("value", "") or "")
            content = "".join(parts)
    
    log.info(f"[DEBUG RESPONSES->CHAT] extracted content length: {len(content)}, tool_calls: {len(tool_calls)}")
    
    return {
        "id": responses_data.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
                **({"tool_calls": tool_calls} if tool_calls else {}),
            },
            "finish_reason": "tool_calls" if tool_calls else "stop"
        }],
        "usage": responses_data.get("usage", {})
    }


# Regex to find markdown image tags with data URIs: ![alt](data:image/...;base64,...)
_DATA_URI_IMAGE_RE = re.compile(
    r'(!\[([^\]]*)\]\()(data:image/[^;]+;base64,[A-Za-z0-9+/=\s]+)(\))'
)


def _replace_large_data_uris_in_string(content: str) -> str:
    """
    Scan a string for markdown image tags containing large base64 data URIs.
    If any exceed the threshold, save them as files and replace with file URLs.
    """
    if "data:image/" not in content or ";base64," not in content:
        return content  # Fast exit: no data URIs present

    img_counter = [0]  # Use list for mutability in closure

    def _replacer(match):
        prefix = match.group(1)   # e.g. "![alt]("
        data_uri = match.group(3)  # e.g. "data:image/jpeg;base64,..."
        suffix = match.group(4)    # ")"

        if len(data_uri) > _IMAGE_FILE_THRESHOLD:
            idx = img_counter[0]
            img_counter[0] += 1
            # Extract mime
            header_end = data_uri.find(",")
            if header_end > 0:
                header = data_uri[:header_end]
                mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
            else:
                mime = "image/png"

            file_url = _save_b64_image_to_file(data_uri, mime, idx)
            if file_url:
                return f"{prefix}{file_url}{suffix}"

        return match.group(0)  # Keep original if small or save failed

    return _DATA_URI_IMAGE_RE.sub(_replacer, content)


def _stringify_message_content(content) -> str:
    """
    Convert message.content (which can be a string, list of parts, or dict)
    into a plain string. Handles multimodal content by converting image_url
    parts to markdown image tags, routing large base64 through file saving.
    """
    if isinstance(content, str):
        return _replace_large_data_uris_in_string(content)
    if isinstance(content, list):
        parts = []
        img_idx = 0
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, dict):
                item_type = item.get("type", "")

                # Handle image_url parts from multimodal completions API
                if item_type == "image_url":
                    image_url_obj = item.get("image_url")
                    if isinstance(image_url_obj, dict):
                        url = image_url_obj.get("url", "")
                    elif isinstance(image_url_obj, str):
                        url = image_url_obj
                    else:
                        url = item.get("url", "")

                    if url:
                        parts.append(_make_image_markdown(img_idx, url))
                        img_idx += 1
                    continue

                # Handle text parts
                if item_type in ("text", "output_text"):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(text)
                        continue

                # Fallback for other dict formats
                text = item.get("text") or item.get("content") or item.get("value") or ""
                if text:
                    parts.append(text)

                # Also check for inline image data in dict without explicit type
                b64_data = item.get("b64_json") or item.get("base64") or item.get("data")
                if b64_data and not text:
                    mime = item.get("content_type") or item.get("mime_type") or "image/png"
                    if b64_data.startswith("data:"):
                        data_uri = b64_data
                    else:
                        data_uri = f"data:{mime};base64,{b64_data}"
                    parts.append(_make_image_markdown(img_idx, data_uri))
                    img_idx += 1

        result = "\n\n".join(parts) if any(p.startswith("![") for p in parts) else "".join(parts)
        return result
    if isinstance(content, dict):
        return content.get("text") or content.get("content") or content.get("value") or ""
    return ""


# Threshold in bytes: base64 images larger than this will be saved as files
# instead of being inlined as data URIs. 1MB is chosen because:
# - Socket.IO default max_http_buffer_size is 1MB
# - Browsers struggle rendering very large data: URIs in <img> tags
# - 4K images in JPEG can be 5-15MB in base64
_IMAGE_FILE_THRESHOLD = 1 * 1024 * 1024  # 1 MB


def _save_b64_image_to_file(b64_data: str, mime_type: str, idx: int) -> str:
    """
    Save a base64-encoded image to disk and register it in the Files table.
    Returns a server-relative URL like /api/v1/files/{id}/content/image.png
    that the frontend can use in <img> tags.

    Falls back to data URI if saving fails for any reason.
    """
    try:
        # Strip data URI prefix if present
        raw_b64 = b64_data
        detected_mime = mime_type
        if raw_b64.startswith("data:"):
            # e.g. data:image/jpeg;base64,/9j/4AAQ...
            header, _, raw_b64 = raw_b64.partition(",")
            if ";" in header:
                detected_mime = header.split(":", 1)[1].split(";", 1)[0]

        image_bytes = base64.b64decode(raw_b64)

        # Determine file extension from mime type
        ext_map = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/gif": "gif",
            "image/webp": "webp",
            "image/svg+xml": "svg",
        }
        ext = ext_map.get(detected_mime, "png")
        file_id = str(uuid.uuid4())
        filename = f"generated_image_{idx}.{ext}"
        storage_filename = f"{file_id}_{filename}"

        # Save file directly to UPLOAD_DIR
        file_path = os.path.join(str(UPLOAD_DIR), storage_filename)
        with open(file_path, "wb") as f:
            f.write(image_bytes)

        # Register in Files table so /api/v1/files/{id}/content works
        Files.insert_new_file(
            user_id="system",  # System-generated image, no specific user
            form_data=FileForm(
                id=file_id,
                filename=filename,
                path=file_path,
                data={},
                meta={
                    "name": filename,
                    "content_type": detected_mime,
                    "size": len(image_bytes),
                    "source": "ai_generated",
                },
            ),
        )

        url = f"/api/v1/files/{file_id}/content"
        log.info(
            "[NORMALIZE] Saved large image (%d bytes) as file %s -> %s",
            len(image_bytes), file_id, url,
        )
        return url

    except Exception as e:
        log.warning("[NORMALIZE] Failed to save image as file: %s. Falling back to data URI.", e)
        # Return None to signal caller to fall back to data URI
        return None


def _make_image_markdown(idx: int, data_uri: str) -> str:
    """
    Build a markdown image tag. If the data URI is large,
    save it as a file and use a server URL instead.
    """
    # Estimate the size: base64 string length ≈ encoded byte size
    if len(data_uri) > _IMAGE_FILE_THRESHOLD:
        # Extract mime type and b64 data for saving
        if data_uri.startswith("data:"):
            header_end = data_uri.find(",")
            if header_end > 0:
                header = data_uri[:header_end]
                mime = header.split(":", 1)[1].split(";", 1)[0] if ":" in header else "image/png"
            else:
                mime = "image/png"
        else:
            mime = "image/png"

        file_url = _save_b64_image_to_file(data_uri, mime, idx)
        if file_url:
            return f"![image_{idx}]({file_url})"

    # Fallback: inline data URI (small images or save failure)
    return f"![image_{idx}]({data_uri})"


def _normalize_chat_completion_response(response: dict) -> dict:
    if not isinstance(response, dict):
        return response

    choices = response.get("choices")
    if not isinstance(choices, list):
        return response

    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            if isinstance(choice.get("text"), str):
                choice["message"] = {
                    "role": "assistant",
                    "content": choice.get("text"),
                }
            continue

        # Handle non-standard 'images' field from upstream APIs
        # (e.g. NewAPI proxying Gemini drawing models returns base64 images
        #  in message.images instead of in message.content)
        # Known formats:
        #   1) [{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}]
        #   2) [{"url":"data:image/...;base64,..."}]
        #   3) [{"b64_json":"..."}]
        #   4) ["<raw_base64_string>"]
        images = message.get("images")
        if isinstance(images, list) and images:
            image_parts = []
            for idx, img in enumerate(images):
                b64_data = None
                mime_type = "image/png"
                if isinstance(img, dict):
                    # Format 1: OpenAI multimodal nested format
                    # {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
                    image_url_obj = img.get("image_url")
                    if isinstance(image_url_obj, dict):
                        nested_url = image_url_obj.get("url", "")
                        if nested_url:
                            image_parts.append(_make_image_markdown(idx, nested_url))
                            continue

                    # Format 2: flat url
                    url_val = img.get("url", "")
                    if url_val.startswith("data:"):
                        image_parts.append(_make_image_markdown(idx, url_val))
                        continue

                    # Format 3: b64_json or similar
                    b64_data = img.get("b64_json") or img.get("base64") or img.get("data")
                    mime_type = img.get("content_type") or img.get("mime_type") or "image/png"
                elif isinstance(img, str):
                    # Format 4: raw base64 string
                    b64_data = img

                if b64_data:
                    if b64_data.startswith("data:"):
                        data_uri = b64_data
                    else:
                        data_uri = f"data:{mime_type};base64,{b64_data}"
                    image_parts.append(_make_image_markdown(idx, data_uri))

            if image_parts:
                existing_content = message.get("content") or ""
                images_md = "\n\n".join(image_parts)
                message["content"] = (
                    f"{existing_content}\n\n{images_md}" if existing_content else images_md
                )
                log.info(
                    "[NORMALIZE] Injected %d image(s) from message.images into content",
                    len(image_parts),
                )

        content = message.get("content")
        normalized = _stringify_message_content(content)
        if normalized:
            message["content"] = normalized
            continue

        for key in ("text", "output_text", "answer", "result"):
            value = message.get(key)
            if isinstance(value, str) and value:
                message["content"] = value
                break

    return response


async def _safe_response_json(response: aiohttp.ClientResponse):
    try:
        body = await response.text()
    except Exception as e:
        log.debug("Failed reading response body from %s: %s", response.url, e)
        return None

    if not body:
        return None

    try:
        return json.loads(body)
    except Exception:
        snippet = body[:500].replace("\n", " ").replace("\r", " ")
        log.debug("Non-JSON response from %s: %s", response.url, snippet)
        return None


async def send_get_request(url, key=None, user: UserModel = None, timeout_seconds=None):
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            headers = {
                **({"Authorization": f"Bearer {key}"} if key else {}),
            }

            if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                headers = include_user_info_headers(headers, user)

            async with session.get(
                url,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as response:
                return await response.json()
    except Exception as e:
        # Handle connection error here
        log.error(f"Connection error: {e}")
        return None


async def get_models_request(url, key=None, user: UserModel = None):
    if is_anthropic_url(url):
        return await get_anthropic_models(url, key, user=user)
    return await send_get_request(f"{url}/models", key, user=user)


def openai_reasoning_model_handler(payload):
    """
    Handle reasoning model specific parameters
    """
    if "max_tokens" in payload:
        # Convert "max_tokens" to "max_completion_tokens" for all reasoning models
        payload["max_completion_tokens"] = payload["max_tokens"]
        del payload["max_tokens"]

    # Handle system role conversion based on model type
    if payload["messages"][0]["role"] == "system":
        model_lower = payload["model"].lower()
        # Legacy models use "user" role instead of "system"
        if model_lower.startswith("o1-mini") or model_lower.startswith("o1-preview"):
            payload["messages"][0]["role"] = "user"
        else:
            payload["messages"][0]["role"] = "developer"

    return payload


async def get_headers_and_cookies(
    request: Request,
    url,
    key=None,
    config=None,
    metadata: Optional[dict] = None,
    user: UserModel = None,
):
    cookies = {}
    headers = {
        "Content-Type": "application/json",
        **(
            {
                "HTTP-Referer": "https://openwebui.com/",
                "X-Title": "Open WebUI",
            }
            if "openrouter.ai" in url
            else {}
        ),
    }

    if ENABLE_FORWARD_USER_INFO_HEADERS and user:
        headers = include_user_info_headers(headers, user)
        if metadata and metadata.get("chat_id"):
            headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = metadata.get("chat_id")

    token = None
    auth_type = config.get("auth_type")

    if auth_type == "bearer" or auth_type is None:
        # Default to bearer if not specified
        token = f"{key}"
    elif auth_type == "none":
        token = None
    elif auth_type == "session":
        cookies = request.cookies
        token = request.state.token.credentials
    elif auth_type == "system_oauth":
        cookies = request.cookies

        oauth_token = None
        try:
            if request.cookies.get("oauth_session_id", None):
                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                    user.id,
                    request.cookies.get("oauth_session_id", None),
                )
        except Exception as e:
            log.error(f"Error getting OAuth token: {e}")

        if oauth_token:
            token = f"{oauth_token.get('access_token', '')}"

    elif auth_type in ("azure_ad", "microsoft_entra_id"):
        token = get_microsoft_entra_id_access_token()

    if token:
        headers["Authorization"] = f"Bearer {token}"

    if config.get("headers") and isinstance(config.get("headers"), dict):
        headers = {**headers, **config.get("headers")}

    return headers, cookies


def get_microsoft_entra_id_access_token():
    """
    Get Microsoft Entra ID access token using DefaultAzureCredential for Azure OpenAI.
    Returns the token string or None if authentication fails.
    """
    try:
        token_provider = get_bearer_token_provider(
            DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
        )
        return token_provider()
    except Exception as e:
        log.error(f"Error getting Microsoft Entra ID access token: {e}")
        return None


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
        key = request.app.state.config.OPENAI_API_KEYS[idx]
        api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
            str(idx),
            request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
        )

        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        r = None
        try:
            r = requests.post(
                url=f"{url}/audio/speech",
                data=body,
                headers=headers,
                cookies=cookies,
                stream=True,
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

            detail = None
            if r is not None:
                try:
                    res = r.json()
                    if "error" in res:
                        detail = f"External: {res['error']}"
                except Exception:
                    detail = f"External: {e}"

            raise HTTPException(
                status_code=r.status_code if r else 500,
                detail=detail if detail else "Open WebUI: Server Connection Error",
            )

    except ValueError:
        raise HTTPException(status_code=401, detail=ERROR_MESSAGES.OPENAI_NOT_FOUND)


async def get_all_models_responses(request: Request, user: UserModel) -> list:
    if not request.app.state.config.ENABLE_OPENAI_API:
        return []

    # Cache config values locally to avoid repeated Redis lookups.
    # Each access to request.app.state.config.<KEY> triggers a Redis GET;
    # caching here avoids hundreds of redundant round-trips.
    api_base_urls = request.app.state.config.OPENAI_API_BASE_URLS
    api_keys = list(request.app.state.config.OPENAI_API_KEYS)
    api_configs = request.app.state.config.OPENAI_API_CONFIGS

    # Check if API KEYS length is same than API URLS length
    num_urls = len(api_base_urls)
    num_keys = len(api_keys)

    if num_keys != num_urls:
        # if there are more keys than urls, remove the extra keys
        if num_keys > num_urls:
            api_keys = api_keys[:num_urls]
            request.app.state.config.OPENAI_API_KEYS = api_keys
        # if there are more urls than keys, add empty keys
        else:
            api_keys += [""] * (num_urls - num_keys)
            request.app.state.config.OPENAI_API_KEYS = api_keys

    request_tasks = []
    for idx, url in enumerate(api_base_urls):
        if (str(idx) not in api_configs) and (url not in api_configs):  # Legacy support
            request_tasks.append(get_models_request(url, api_keys[idx], user=user))
        else:
            api_config = api_configs.get(
                str(idx),
                api_configs.get(url, {}),  # Legacy support
            )

            enable = api_config.get("enable", True)
            model_ids = api_config.get("model_ids", [])

            if enable:
                if len(model_ids) == 0:
                    request_tasks.append(
                        get_models_request(url, api_keys[idx], user=user)
                    )
                else:
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

                    request_tasks.append(
                        asyncio.ensure_future(asyncio.sleep(0, model_list))
                    )
            else:
                request_tasks.append(asyncio.ensure_future(asyncio.sleep(0, None)))

    responses = await asyncio.gather(*request_tasks)

    for idx, response in enumerate(responses):
        if response:
            url = api_base_urls[idx]
            api_config = api_configs.get(
                str(idx),
                api_configs.get(url, {}),  # Legacy support
            )

            connection_type = api_config.get("connection_type", "external")
            prefix_id = api_config.get("prefix_id", None)
            tags = api_config.get("tags", [])

            model_list = (
                response if isinstance(response, list) else response.get("data", [])
            )
            if not isinstance(model_list, list):
                # Catch non-list responses
                model_list = []

            for model in model_list:
                # Remove name key if its value is None #16689
                if "name" in model and model["name"] is None:
                    del model["name"]

                if prefix_id:
                    model["id"] = (
                        f"{prefix_id}.{model.get('id', model.get('name', ''))}"
                    )

                if tags:
                    model["tags"] = tags

                if connection_type:
                    model["connection_type"] = connection_type

    log.debug(f"get_all_models:responses() {responses}")
    return responses


async def get_filtered_models(models, user, db=None):
    # Filter models based on user access control
    model_ids = [model["id"] for model in models.get("data", [])]
    model_infos = {
        model_info.id: model_info
        for model_info in Models.get_models_by_ids(model_ids, db=db)
    }
    user_group_ids = {
        group.id for group in Groups.get_groups_by_member_id(user.id, db=db)
    }

    # Batch-fetch accessible resource IDs in a single query instead of N has_access calls
    accessible_model_ids = AccessGrants.get_accessible_resource_ids(
        user_id=user.id,
        resource_type="model",
        resource_ids=list(model_infos.keys()),
        permission="read",
        user_group_ids=user_group_ids,
        db=db,
    )

    filtered_models = []
    for model in models.get("data", []):
        model_info = model_infos.get(model["id"])
        if model_info:
            if user.id == model_info.user_id or model_info.id in accessible_model_ids:
                filtered_models.append(model)
    return filtered_models


@cached(
    ttl=MODELS_CACHE_TTL,
    key=lambda _, user: f"openai_all_models_{user.id}" if user else "openai_all_models",
)
async def get_all_models(request: Request, user: UserModel) -> dict[str, list]:
    log.info("get_all_models()")

    if not request.app.state.config.ENABLE_OPENAI_API:
        return {"data": []}

    # Cache config value locally to avoid repeated Redis lookups inside
    # the nested loop in get_merged_models (one GET per model otherwise).
    api_base_urls = request.app.state.config.OPENAI_API_BASE_URLS

    responses = await get_all_models_responses(request, user=user)

    def extract_data(response):
        if response and "data" in response:
            return response["data"]
        if isinstance(response, list):
            return response
        return None

    def is_supported_openai_models(model_id):
        if any(
            name in model_id
            for name in [
                "babbage",
                "dall-e",
                "davinci",
                "embedding",
                "tts",
                "whisper",
            ]
        ):
            return False
        return True

    def get_merged_models(model_lists):
        log.debug(f"merge_models_lists {model_lists}")
        models = {}

        for idx, model_list in enumerate(model_lists):
            if model_list is not None and "error" not in model_list:
                for model in model_list:
                    model_id = model.get("id") or model.get("name")

                    base_url = api_base_urls[idx]
                    hostname = urlparse(base_url).hostname if base_url else None
                    if hostname == "api.openai.com" and not is_supported_openai_models(
                        model_id
                    ):
                        # Skip unwanted OpenAI models
                        continue

                    if model_id and model_id not in models:
                        models[model_id] = {
                            **model,
                            "name": model.get("name", model_id),
                            "owned_by": "openai",
                            "openai": model,
                            "connection_type": model.get("connection_type", "external"),
                            "urlIdx": idx,
                        }

        return models

    models = get_merged_models(map(extract_data, responses))
    log.debug(f"models: {models}")

    request.app.state.OPENAI_MODELS = models
    return {"data": list(models.values())}


@router.get("/models")
@router.get("/models/{url_idx}")
async def get_models(
    request: Request, url_idx: Optional[int] = None, user=Depends(get_verified_user)
):
    if not request.app.state.config.ENABLE_OPENAI_API:
        raise HTTPException(status_code=503, detail="OpenAI API is disabled")

    models = {
        "data": [],
    }

    if url_idx is None:
        models = await get_all_models(request, user=user)
    else:
        url = request.app.state.config.OPENAI_API_BASE_URLS[url_idx]
        key = request.app.state.config.OPENAI_API_KEYS[url_idx]

        api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
            str(url_idx),
            request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
        )

        r = None
        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
        ) as session:
            try:
                headers, cookies = await get_headers_and_cookies(
                    request, url, key, api_config, user=user
                )

                if api_config.get("azure", False):
                    models = {
                        "data": api_config.get("model_ids", []) or [],
                        "object": "list",
                    }
                elif is_anthropic_url(url):
                    models = await get_anthropic_models(url, key, user=user)
                    if models is None:
                        raise Exception("Failed to connect to Anthropic API")
                else:
                    async with session.get(
                        f"{url}/models",
                        headers=headers,
                        cookies=cookies,
                        ssl=AIOHTTP_CLIENT_SESSION_SSL,
                    ) as r:
                        if r.status != 200:
                            error_detail = f"HTTP Error: {r.status}"
                            try:
                                res = await r.json()
                                if "error" in res:
                                    error_detail = f"External Error: {res['error']}"
                            except Exception:
                                pass
                            raise Exception(error_detail)

                        response_data = await r.json()

                        if "api.openai.com" in url:
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

                        models = response_data
            except aiohttp.ClientError as e:
                # ClientError covers all aiohttp requests issues
                log.exception(f"Client error: {str(e)}")
                raise HTTPException(
                    status_code=500, detail="Open WebUI: Server Connection Error"
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


@router.post("/verify")
async def verify_connection(
    request: Request,
    form_data: ConnectionVerificationForm,
    user=Depends(get_admin_user),
):
    url = form_data.url
    key = form_data.key

    api_config = form_data.config or {}

    async with aiohttp.ClientSession(
        trust_env=True,
        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST),
    ) as session:
        try:
            headers, cookies = await get_headers_and_cookies(
                request, url, key, api_config, user=user
            )

            if api_config.get("azure", False):
                # Only set api-key header if not using Azure Entra ID authentication
                auth_type = api_config.get("auth_type", "bearer")
                if auth_type not in ("azure_ad", "microsoft_entra_id"):
                    headers["api-key"] = key

                api_version = api_config.get("api_version", "") or "2023-03-15-preview"
                async with session.get(
                    url=f"{url}/openai/models?api-version={api_version}",
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json()
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(
                                status_code=r.status, content=response_data
                            )
                        else:
                            return PlainTextResponse(
                                status_code=r.status, content=response_data
                            )

                    return response_data
            elif is_anthropic_url(url):
                result = await get_anthropic_models(url, key)
                if result is None:
                    raise HTTPException(
                        status_code=500, detail="Failed to connect to Anthropic API"
                    )
                if "error" in result:
                    raise HTTPException(status_code=500, detail=result["error"])
                return result
            else:
                async with session.get(
                    f"{url}/models",
                    headers=headers,
                    cookies=cookies,
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as r:
                    try:
                        response_data = await r.json()
                    except Exception:
                        response_data = await r.text()

                    if r.status != 200:
                        if isinstance(response_data, (dict, list)):
                            return JSONResponse(
                                status_code=r.status, content=response_data
                            )
                        else:
                            return PlainTextResponse(
                                status_code=r.status, content=response_data
                            )

                    return response_data

        except aiohttp.ClientError as e:
            # ClientError covers all aiohttp requests issues
            log.exception(f"Client error: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Open WebUI: Server Connection Error"
            )
        except Exception as e:
            log.exception(f"Unexpected error: {e}")
            raise HTTPException(
                status_code=500, detail="Open WebUI: Server Connection Error"
            )


def get_azure_allowed_params(api_version: str) -> set[str]:
    allowed_params = {
        "messages",
        "temperature",
        "role",
        "content",
        "contentPart",
        "contentPartImage",
        "enhancements",
        "dataSources",
        "n",
        "stream",
        "stop",
        "max_tokens",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
        "function_call",
        "functions",
        "tools",
        "tool_choice",
        "top_p",
        "log_probs",
        "top_logprobs",
        "response_format",
        "seed",
        "max_completion_tokens",
        "reasoning_effort",
    }

    try:
        if api_version >= "2024-09-01-preview":
            allowed_params.add("stream_options")
    except ValueError:
        log.debug(
            f"Invalid API version {api_version} for Azure OpenAI. Defaulting to allowed parameters."
        )

    return allowed_params


def is_openai_reasoning_model(model: str) -> bool:
    return model.lower().startswith(("o1", "o3", "o4", "gpt-5"))


def convert_to_azure_payload(url, payload: dict, api_version: str):
    model = payload.get("model", "")

    # Filter allowed parameters based on Azure OpenAI API
    allowed_params = get_azure_allowed_params(api_version)

    # Special handling for o-series models
    if is_openai_reasoning_model(model):
        # Convert max_tokens to max_completion_tokens for o-series models
        if "max_tokens" in payload:
            payload["max_completion_tokens"] = payload["max_tokens"]
            del payload["max_tokens"]

        # Remove temperature if not 1 for o-series models
        if "temperature" in payload and payload["temperature"] != 1:
            log.debug(
                f"Removing temperature parameter for o-series model {model} as only default value (1) is supported"
            )
            del payload["temperature"]

    # Filter out unsupported parameters
    payload = {k: v for k, v in payload.items() if k in allowed_params}

    url = f"{url}/openai/deployments/{model}"
    return url, payload


def convert_to_responses_payload(payload: dict) -> dict:
    """
    Convert Chat Completions payload to Responses API format.

    Chat Completions: { messages: [{role, content}], ... }
    Responses API: { input: [{type: "message", role, content: [...]}], instructions: "system" }
    """
    messages = payload.pop("messages", [])

    system_content = ""
    input_items = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        # Check for stored output items (from previous Responses API turn)
        stored_output = msg.get("output")
        if stored_output and isinstance(stored_output, list):
            input_items.extend(stored_output)
            continue

        if role == "system":
            if isinstance(content, str):
                system_content = content
            elif isinstance(content, list):
                system_content = "\n".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
            continue

        # Convert content format
        text_type = "output_text" if role == "assistant" else "input_text"

        if isinstance(content, str):
            content_parts = [{"type": text_type, "text": content}]
        elif isinstance(content, list):
            content_parts = []
            for part in content:
                if part.get("type") == "text":
                    content_parts.append(
                        {"type": text_type, "text": part.get("text", "")}
                    )
                elif part.get("type") == "image_url":
                    url_data = part.get("image_url", {})
                    url = (
                        url_data.get("url", "")
                        if isinstance(url_data, dict)
                        else url_data
                    )
                    content_parts.append({"type": "input_image", "image_url": url})
        else:
            content_parts = [{"type": text_type, "text": str(content)}]

        input_items.append({"type": "message", "role": role, "content": content_parts})

    responses_payload = {**payload, "input": input_items}

    if system_content:
        responses_payload["instructions"] = system_content

    if "max_tokens" in responses_payload:
        responses_payload["max_output_tokens"] = responses_payload.pop("max_tokens")

    # Remove Chat Completions-only parameters not supported by the Responses API
    for unsupported_key in (
        "stream_options",
        "logit_bias",
        "frequency_penalty",
        "presence_penalty",
        "stop",
    ):
        responses_payload.pop(unsupported_key, None)

    # Convert Chat Completions tools format to Responses API format
    # Chat Completions: {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    # Responses API:    {"type": "function", "name": ..., "description": ..., "parameters": ...}
    if "tools" in responses_payload and isinstance(responses_payload["tools"], list):
        converted_tools = []
        for tool in responses_payload["tools"]:
            if isinstance(tool, dict) and "function" in tool:
                func = tool["function"]
                converted_tool = {"type": tool.get("type", "function")}
                if isinstance(func, dict):
                    converted_tool["name"] = func.get("name", "")
                    if "description" in func:
                        converted_tool["description"] = func["description"]
                    if "parameters" in func:
                        converted_tool["parameters"] = func["parameters"]
                    if "strict" in func:
                        converted_tool["strict"] = func["strict"]
                converted_tools.append(converted_tool)
            else:
                # Already in correct format or unknown format, pass through
                converted_tools.append(tool)
        responses_payload["tools"] = converted_tools

    return responses_payload


def convert_responses_result(response: dict) -> dict:
    """
    Convert non-streaming Responses API result.
    Just add done flag - pass through raw response, frontend handles output.
    """
    response["done"] = True
    return response


@router.post("/chat/completions")
async def generate_chat_completion(
    request: Request,
    form_data: dict,
    user=Depends(get_verified_user),
    bypass_filter: Optional[bool] = False,
    bypass_system_prompt: bool = False,
):
    # NOTE: We intentionally do NOT use Depends(get_session) here.
    # Database operations (get_model_by_id, AccessGrants.has_access) manage their own short-lived sessions.
    # This prevents holding a connection during the entire LLM call (30-60+ seconds),
    # which would exhaust the connection pool under concurrent load.
    if BYPASS_MODEL_ACCESS_CONTROL:
        bypass_filter = True

    idx = 0

    payload = {**form_data}
    metadata = payload.pop("metadata", None)

    model_id = form_data.get("model")
    model_info = Models.get_model_by_id(model_id)

    # Check model info and override the payload
    if model_info:
        if model_info.base_model_id:
            base_model_id = (
                request.base_model_id
                if hasattr(request, "base_model_id")
                else model_info.base_model_id
            )  # Use request's base_model_id if available
            payload["model"] = base_model_id
            model_id = base_model_id

        params = model_info.params.model_dump()

        if params:
            system = params.pop("system", None)

            payload = apply_model_params_to_body_openai(params, payload)
            if not bypass_system_prompt:
                payload = apply_system_prompt_to_body(system, payload, metadata, user)

        # Check if user has access to the model
        if not bypass_filter and user.role == "user":
            user_group_ids = {
                group.id for group in Groups.get_groups_by_member_id(user.id)
            }
            if not (
                user.id == model_info.user_id
                or AccessGrants.has_access(
                    user_id=user.id,
                    resource_type="model",
                    resource_id=model_info.id,
                    permission="read",
                    user_group_ids=user_group_ids,
                )
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Model not found",
                )
    elif not bypass_filter:
        if user.role != "admin":
            raise HTTPException(
                status_code=403,
                detail="Model not found",
            )

    # Check if model is already in app state cache to avoid expensive get_all_models() call
    models = request.app.state.OPENAI_MODELS
    if not models or model_id not in models:
        await get_all_models(request, user=user)
        models = request.app.state.OPENAI_MODELS
    model = models.get(model_id)

    if model:
        idx = model["urlIdx"]
    else:
        raise HTTPException(
            status_code=404,
            detail="Model not found",
        )

    # Get the API config for the model
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    prefix_id = api_config.get("prefix_id", None)
    if prefix_id:
        payload["model"] = payload["model"].replace(f"{prefix_id}.", "")

    # Add user info to the payload if the model is a pipeline
    if "pipeline" in model and model.get("pipeline"):
        payload["user"] = {
            "name": user.name,
            "id": user.id,
            "email": user.email,
            "role": user.role,
        }

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]

    # Check if model is a reasoning model that needs special handling
    if is_openai_reasoning_model(payload["model"]):
        payload = openai_reasoning_model_handler(payload)
    elif "api.openai.com" not in url:
        # Remove "max_completion_tokens" from the payload for backward compatibility
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload["max_completion_tokens"]
            del payload["max_completion_tokens"]

    if "max_tokens" in payload and "max_completion_tokens" in payload:
        del payload["max_tokens"]

    # Convert the modified body back to JSON
    if "logit_bias" in payload and payload["logit_bias"]:
        logit_bias = convert_logit_bias_input_to_json(payload["logit_bias"])

        if logit_bias:
            payload["logit_bias"] = json.loads(logit_bias)

    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, metadata, user=user
    )

    is_responses = api_config.get("api_type") == "responses"

    if api_config.get("azure", False):
        api_version = api_config.get("api_version", "2023-03-15-preview")
        request_url, payload = convert_to_azure_payload(url, payload, api_version)

        # Only set api-key header if not using Azure Entra ID authentication
        auth_type = api_config.get("auth_type", "bearer")
        if auth_type not in ("azure_ad", "microsoft_entra_id"):
            headers["api-key"] = key

        headers["api-version"] = api_version

        if is_responses:
            payload = convert_to_responses_payload(payload)
            request_url = f"{request_url}/responses?api-version={api_version}"
        else:
            request_url = f"{request_url}/chat/completions?api-version={api_version}"
    else:
        if is_responses:
            payload = convert_to_responses_payload(payload)
            request_url = f"{url}/responses"
        else:
            request_url = f"{url}/chat/completions"

    payload = json.dumps(payload)

    r = None
    session = None
    streaming = False
    response = None

    try:
        session = aiohttp.ClientSession(
            trust_env=True, timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT)
        )

        r = await session.request(
            method="POST",
            url=request_url,
            data=payload,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session, stream_chunks_handler),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response = await r.json()
            except Exception as e:
                log.error(e)
                response = await r.text()

            if r.status >= 400:
                if isinstance(response, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response)
                else:
                    return PlainTextResponse(status_code=r.status, content=response)

            # Convert Responses API result to simple format
            if is_responses and isinstance(response, dict):
                response = convert_responses_result(response)

            return response
    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


async def embeddings(request: Request, form_data: dict, user):
    """
    Calls the embeddings endpoint for OpenAI-compatible providers.

    Args:
        request (Request): The FastAPI request context.
        form_data (dict): OpenAI-compatible embeddings payload.
        user (UserModel): The authenticated user.

    Returns:
        dict: OpenAI-compatible embeddings response.
    """
    idx = 0
    # Prepare payload/body
    body = json.dumps(form_data)
    # Find correct backend url/key based on model
    model_id = form_data.get("model")
    # Check if model is already in app state cache to avoid expensive get_all_models() call
    models = request.app.state.OPENAI_MODELS
    if not models or model_id not in models:
        await get_all_models(request, user=user)
        models = request.app.state.OPENAI_MODELS
    if model_id in models:
        idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    headers, cookies = await get_headers_and_cookies(
        request, url, key, api_config, user=user
    )
    try:
        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )
        r = await session.request(
            method="POST",
            url=f"{url}/embeddings",
            data=body,
            headers=headers,
            cookies=cookies,
        )

        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


class ResponsesForm(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    input: Optional[list | str] = None
    instructions: Optional[str] = None
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    top_p: Optional[float] = None
    tools: Optional[list] = None
    tool_choice: Optional[str | dict] = None
    text: Optional[dict] = None
    truncation: Optional[str] = None
    metadata: Optional[dict] = None
    store: Optional[bool] = None
    reasoning: Optional[dict] = None
    previous_response_id: Optional[str] = None


@router.post("/responses")
async def responses(
    request: Request,
    form_data: ResponsesForm,
    user=Depends(get_verified_user),
):
    """
    Forward requests to the OpenAI Responses API endpoint.
    Routes to the correct upstream backend based on the model field.
    """
    payload = form_data.model_dump(exclude_none=True)
    body = json.dumps(payload)

    idx = 0
    model_id = form_data.model
    if model_id:
        models = request.app.state.OPENAI_MODELS
        if not models or model_id not in models:
            await get_all_models(request, user=user)
            models = request.app.state.OPENAI_MODELS
        if model_id in models:
            idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(url, {}),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    try:
        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        if api_config.get("azure", False):
            api_version = api_config.get("api_version", "2023-03-15-preview")

            auth_type = api_config.get("auth_type", "bearer")
            if auth_type not in ("azure_ad", "microsoft_entra_id"):
                headers["api-key"] = key

            headers["api-version"] = api_version

            model = payload.get("model", "")
            request_url = (
                f"{url}/openai/deployments/{model}/responses?api-version={api_version}"
            )
        else:
            request_url = f"{url}/responses"

        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )
        r = await session.request(
            method="POST",
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy(path: str, request: Request, user=Depends(get_verified_user)):
    """
    Deprecated: proxy all requests to OpenAI API
    """

    body = await request.body()

    # Parse JSON body to resolve model-based routing
    payload = None
    if body:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            payload = None

    idx = 0
    model_id = payload.get("model") if isinstance(payload, dict) else None
    if model_id:
        models = request.app.state.OPENAI_MODELS
        if not models or model_id not in models:
            await get_all_models(request, user=user)
            models = request.app.state.OPENAI_MODELS
        if model_id in models:
            idx = models[model_id]["urlIdx"]

    url = request.app.state.config.OPENAI_API_BASE_URLS[idx]
    key = request.app.state.config.OPENAI_API_KEYS[idx]
    api_config = request.app.state.config.OPENAI_API_CONFIGS.get(
        str(idx),
        request.app.state.config.OPENAI_API_CONFIGS.get(
            request.app.state.config.OPENAI_API_BASE_URLS[idx], {}
        ),  # Legacy support
    )

    r = None
    session = None
    streaming = False

    try:
        headers, cookies = await get_headers_and_cookies(
            request, url, key, api_config, user=user
        )

        if api_config.get("azure", False):
            api_version = api_config.get("api_version", "2023-03-15-preview")

            # Only set api-key header if not using Azure Entra ID authentication
            auth_type = api_config.get("auth_type", "bearer")
            if auth_type not in ("azure_ad", "microsoft_entra_id"):
                headers["api-key"] = key

            headers["api-version"] = api_version

            payload = json.loads(body)
            url, payload = convert_to_azure_payload(url, payload, api_version)
            body = json.dumps(payload).encode()

            request_url = f"{url}/{path}?api-version={api_version}"
        else:
            request_url = f"{url}/{path}"

        session = aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        )
        r = await session.request(
            method=request.method,
            url=request_url,
            data=body,
            headers=headers,
            cookies=cookies,
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        )

        # Check if response is SSE
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            streaming = True
            return StreamingResponse(
                stream_wrapper(r, session),
                status_code=r.status,
                headers=dict(r.headers),
            )
        else:
            try:
                response_data = await r.json()
            except Exception:
                response_data = await r.text()

            if r.status >= 400:
                if isinstance(response_data, (dict, list)):
                    return JSONResponse(status_code=r.status, content=response_data)
                else:
                    return PlainTextResponse(
                        status_code=r.status, content=response_data
                    )

            return response_data

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=r.status if r else 500,
            detail="Open WebUI: Server Connection Error",
        )
    finally:
        if not streaming:
            await cleanup_response(r, session)
