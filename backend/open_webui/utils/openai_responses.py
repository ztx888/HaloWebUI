import json
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple


def _stringify_tool_call_args(arguments: Any) -> str:
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except Exception:
        return str(arguments)


def _tool_call_id_from_item(item: Any, event: Optional[dict] = None) -> str:
    if not isinstance(item, dict):
        item = {}
    event = event or {}
    # The official streaming events use item_id; proxies may use id/call_id.
    return (
        item.get("call_id")
        or item.get("id")
        or item.get("item_id")
        or event.get("item_id")
        or event.get("call_id")
        or ""
    )


def _tool_call_name_from_item(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return (
        item.get("name")
        or item.get("function_name")
        or item.get("tool_name")
        or item.get("function", {}).get("name")
        or ""
    )


def _tool_call_args_from_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return ""
    return (
        item.get("arguments")
        or item.get("parameters")
        or item.get("args")
        or item.get("function", {}).get("arguments")
        or item.get("function", {}).get("parameters")
        or ""
    )


def _stringify_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            item_type = item.get("type", "")
            if item_type in ("text", "output_text", "input_text"):
                text = item.get("text") or ""
                if text:
                    parts.append(text)
                    continue
            # Fallback keys used by some proxies.
            text = item.get("content") or item.get("value") or ""
            if text:
                parts.append(str(text))
        return "".join(parts)
    if isinstance(content, dict):
        return (
            content.get("text")
            or content.get("content")
            or content.get("value")
            or ""
        )
    return ""


def convert_tools_chat_to_responses(tools: Any) -> Any:
    """
    Chat Completions tools:
        {"type":"function","function":{name,description,parameters,strict?}}
    Responses tools (per API reference):
        {"type":"function","name","description?","parameters", "strict"?}
    """
    if not isinstance(tools, list):
        return tools

    out: List[dict] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue

        tool_type = tool.get("type")
        if tool_type == "function":
            fn = tool.get("function")
            if isinstance(fn, dict):
                name = fn.get("name")
                params = fn.get("parameters")
                if name and params is not None:
                    out.append(
                        {
                            "type": "function",
                            "name": name,
                            **({"description": fn.get("description")} if fn.get("description") else {}),
                            "parameters": params,
                            # Responses defaults strict=true; keep explicit if provided.
                            **({"strict": fn.get("strict")} if "strict" in fn else {}),
                        }
                    )
                    continue

            # Already in responses-ish format.
            if tool.get("name") and tool.get("parameters") is not None:
                out.append(tool)
                continue

        # Built-in tools like {"type":"web_search"} pass through.
        out.append(tool)

    return out


def convert_tool_choice_chat_to_responses(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str):
        return tool_choice
    if not isinstance(tool_choice, dict):
        return tool_choice

    # Chat format: {"type":"function","function":{"name":"my_fn"}}
    if tool_choice.get("type") == "function":
        fn = tool_choice.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            return {"type": "function", "name": fn["name"]}
        if tool_choice.get("name"):
            return {"type": "function", "name": tool_choice.get("name")}

    return tool_choice


def sanitize_stream_options_for_responses(stream_options: Any) -> Optional[dict]:
    """
    Responses API does not support Chat Completions' `stream_options.include_usage`.
    Keep only options that are valid for Responses streaming.
    """
    if not isinstance(stream_options, dict):
        return None

    allowed_keys = {"include_obfuscation"}
    sanitized = {
        key: value for key, value in stream_options.items() if key in allowed_keys
    }
    return sanitized or None


def _content_chat_to_responses(content: Any, *, role: str = "user") -> Any:
    """
    Convert Chat Completions message content into Responses message content parts.

    Important: When providing conversation history to the Responses API, assistant
    message parts must be typed as output content (e.g. "output_text"), not
    input content ("input_text"). Otherwise the upstream will reject the request
    with schema errors like:
      Invalid value: 'input_text'. Supported values are: 'output_text' and 'refusal'.
    """
    text_part_type = "output_text" if role == "assistant" else "input_text"

    # Always return content parts for message items (more standard).
    if isinstance(content, str):
        return [{"type": text_part_type, "text": content}]

    # Multimodal list -> Responses content parts.
    if isinstance(content, list):
        parts: List[dict] = []
        for item in content:
            if isinstance(item, str):
                if item:
                    parts.append({"type": text_part_type, "text": item})
                continue
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            # Chat format
            if t in ("text", "input_text", "output_text"):
                text = item.get("text") or ""
                parts.append({"type": text_part_type, "text": text})
                continue
            if t in ("image_url", "image", "input_image"):
                # Responses assistant message parts don't support input_image; stringify instead.
                if role == "assistant":
                    image_url = item.get("image_url")
                    url = None
                    if isinstance(image_url, dict):
                        url = image_url.get("url")
                    elif isinstance(image_url, str):
                        url = image_url
                    if url is None and t == "input_image":
                        url = item.get("image_url")
                    parts.append({"type": text_part_type, "text": f"[image]{' ' + url if url else ''}"})
                    continue
                image_url = item.get("image_url")
                url = None
                if isinstance(image_url, dict):
                    url = image_url.get("url")
                elif isinstance(image_url, str):
                    url = image_url
                # Already normalized input_image might use "image_url" as string directly.
                if url is None and t == "input_image":
                    url = item.get("image_url")
                if url:
                    parts.append({"type": "input_image", "image_url": url})
                continue
            if t in ("file", "input_file"):
                if role == "assistant":
                    file_id = item.get("file_id") or item.get("id") or ""
                    parts.append(
                        {
                            "type": text_part_type,
                            "text": f"[file]{' ' + str(file_id) if file_id else ''}",
                        }
                    )
                    continue

                file_id = item.get("file_id") or item.get("id")
                if file_id:
                    parts.append({"type": "input_file", "file_id": str(file_id)})
                continue
        # If we couldn't map anything, fall back to a string.
        if not parts:
            return [{"type": text_part_type, "text": _stringify_message_content(content)}]
        return parts

    return [{"type": text_part_type, "text": _stringify_message_content(content)}]


def convert_chat_completions_to_responses_payload(
    chat_payload: Dict[str, Any],
    *,
    native_web_search_tool_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convert a Chat Completions-style payload into a Responses API payload.

    Notes:
    - This is a structural mapping, not a fallback. If upstream rejects the schema,
      callers should surface that error to users.
    - We intentionally keep the payload minimal: only include optional fields when present.
    """
    responses_payload: Dict[str, Any] = {}

    model = chat_payload.get("model")
    if model:
        responses_payload["model"] = model

    # System messages -> instructions (merge if multiple).
    instructions_parts: List[str] = []

    messages = chat_payload.get("messages", []) or []
    input_items: List[dict] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            text = _stringify_message_content(content)
            if text:
                instructions_parts.append(text)
            continue

        if role in ("tool", "function"):
            call_id = msg.get("tool_call_id") or msg.get("id") or msg.get("name") or ""
            if not call_id:
                call_id = f"call_{uuid.uuid4().hex}"
            output = (
                content
                if isinstance(content, str)
                else json.dumps(content, ensure_ascii=False, default=str)
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            )
            continue

        # Normal message (user/assistant/developer).
        if role not in ("user", "assistant", "developer"):
            role = "user"

        input_items.append(
            {
                "type": "message",
                "role": role,
                "content": _content_chat_to_responses(content, role=role),
            }
        )

        # If this assistant message includes tool_calls (chat format), carry them as
        # input items so the next Responses call can continue the tool loop.
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get("id") or tc.get("call_id") or f"call_{uuid.uuid4().hex}"
                fn = tc.get("function") or {}
                name = None
                args = None
                if isinstance(fn, dict):
                    name = fn.get("name")
                    args = fn.get("arguments")
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": tc_id,
                        "name": name or "",
                        "arguments": _stringify_tool_call_args(args),
                    }
                )

    if instructions_parts:
        responses_payload["instructions"] = "\n\n".join(instructions_parts)

    # Core input.
    responses_payload["input"] = input_items

    # Streaming / sampling params.
    if "stream" in chat_payload:
        responses_payload["stream"] = bool(chat_payload.get("stream"))

    # Responses API has its own stream_options schema and does not accept
    # Chat Completions' `stream_options.include_usage`.
    # Keep only options that are valid for Responses streaming.
    if responses_payload.get("stream") is True and isinstance(chat_payload.get("stream_options"), dict):
        stream_options = sanitize_stream_options_for_responses(
            chat_payload.get("stream_options")
        )
        if stream_options:
            responses_payload["stream_options"] = stream_options

    # max_tokens (chat) -> max_output_tokens (responses)
    max_tokens = chat_payload.get("max_tokens")
    if max_tokens is None and chat_payload.get("max_completion_tokens") is not None:
        max_tokens = chat_payload.get("max_completion_tokens")
    if max_tokens is not None:
        responses_payload["max_output_tokens"] = max_tokens

    for key in ("temperature", "top_p"):
        if key in chat_payload:
            responses_payload[key] = chat_payload[key]

    # Reasoning effort (UI uses reasoning_effort for some providers).
    reasoning_effort = chat_payload.get("reasoning_effort")
    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        responses_payload["reasoning"] = {"effort": reasoning_effort.strip().lower()}

    # Tools/tool_choice.
    if "tools" in chat_payload:
        responses_payload["tools"] = convert_tools_chat_to_responses(chat_payload.get("tools"))
    if "tool_choice" in chat_payload:
        responses_payload["tool_choice"] = convert_tool_choice_chat_to_responses(
            chat_payload.get("tool_choice")
        )

    # Optional: native web search injection (Responses built-in tool).
    if native_web_search_tool_type:
        tools = responses_payload.get("tools")
        if not isinstance(tools, list):
            tools = []
        # Don't duplicate an existing web_search tool.
        has_web_search = any(
            isinstance(t, dict) and t.get("type", "").startswith("web_search")
            for t in tools
        )
        if not has_web_search:
            tools.append({"type": native_web_search_tool_type})
        responses_payload["tools"] = tools

        # Make tool_choice default to auto when enabling web search.
        if "tool_choice" not in responses_payload:
            responses_payload["tool_choice"] = "auto"

    return responses_payload


def convert_responses_to_chat_completions(responses_data: Dict[str, Any], model_id: str) -> Dict[str, Any]:
    output = responses_data.get("output", []) or []
    content = ""
    tool_calls: List[dict] = []
    tool_call_index = 0

    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "")
        if item_type == "message":
            for part in item.get("content", []) or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("output_text", "text"):
                    content += part.get("text", "") or ""
        elif item_type in ("tool_call", "function_call"):
            call_id = _tool_call_id_from_item(item)
            name = _tool_call_name_from_item(item)
            args = _stringify_tool_call_args(_tool_call_args_from_item(item))
            tool_calls.append(
                {
                    "index": tool_call_index,
                    "id": call_id or f"call_{uuid.uuid4().hex}",
                    "type": "function",
                    "function": {"name": name, "arguments": args},
                }
            )
            tool_call_index += 1

    # Some proxies put the final text in a top-level "output_text" or "text" field.
    if not content:
        for key in ("output_text", "text"):
            v = responses_data.get(key)
            if isinstance(v, str) and v:
                content = v
                break

    return {
        "id": responses_data.get("id", f"chatcmpl-{uuid.uuid4().hex}"),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                },
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": responses_data.get("usage", {}) or {},
    }


def _try_json_loads(text: str) -> Optional[dict]:
    try:
        obj = json.loads(text)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


async def iter_responses_events(
    byte_iter: AsyncIterator[bytes],
    *,
    content_type: str = "",
) -> AsyncIterator[dict]:
    """
    Parse a Responses streaming response into event dicts.

    Supports:
    - text/event-stream (SSE) with `data:` lines
    - application/x-ndjson (one JSON object per line)

    Many proxies lie about Content-Type, so we also sniff for `data:`.
    """
    buffer = ""
    mode = "sse" if "text/event-stream" in (content_type or "").lower() else "ndjson"

    async for chunk in byte_iter:
        if not chunk:
            continue
        buffer += chunk.decode("utf-8", errors="replace")

        # Sniff SSE regardless of header.
        if mode != "sse" and "data:" in buffer[:20]:
            mode = "sse"

        if mode == "sse":
            # Prefer full SSE event frames, but also tolerate single-line `data:` frames.
            while True:
                if "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    data_lines: List[str] = []
                    for raw in frame.splitlines():
                        line = raw.strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                    if not data_lines:
                        continue
                    data = "\n".join(data_lines).strip()
                    if data == "[DONE]":
                        return
                    event = _try_json_loads(data)
                    if event is not None:
                        yield event
                    continue

                # Fallback: parse one line at a time if we don't see a full frame yet.
                if "\n" not in buffer:
                    break
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                event = _try_json_loads(data)
                if event is not None:
                    yield event
            continue

        # NDJSON-ish: one JSON object per line.
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            event = _try_json_loads(line)
            if event is not None:
                yield event


async def responses_events_to_chat_completions_sse(
    events: AsyncIterator[dict],
    *,
    model_id: str,
) -> AsyncIterator[str]:
    """
    Convert Responses streaming events to Chat Completions SSE chunks.
    """
    stream_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    tool_index_by_id: Dict[str, int] = {}
    tool_index_by_fallback: Dict[str, int] = {}
    tool_state_by_id: Dict[str, Dict[str, Any]] = {}
    tool_call_emitted: set[str] = set()
    next_tool_index = 0
    saw_tool_calls = False
    saw_content = False
    saw_text_content = False

    def _tool_state(stable_id: str, idx: int) -> Dict[str, Any]:
        st = tool_state_by_id.get(stable_id)
        if not isinstance(st, dict):
            st = {"name": "", "args": "", "idx": idx}
            tool_state_by_id[stable_id] = st
        st["idx"] = idx
        if "name" not in st:
            st["name"] = ""
        if "args" not in st:
            st["args"] = ""
        return st

    def get_tool_index(call_id: str, provided_index: Optional[int] = None) -> int:
        nonlocal next_tool_index
        if call_id:
            if call_id in tool_index_by_id:
                return tool_index_by_id[call_id]
            tool_index_by_id[call_id] = next_tool_index
            next_tool_index += 1
            return tool_index_by_id[call_id]

        if provided_index is not None:
            key = str(provided_index)
            if key in tool_index_by_fallback:
                return tool_index_by_fallback[key]
            tool_index_by_fallback[key] = next_tool_index
            next_tool_index += 1
            return tool_index_by_fallback[key]

        idx = next_tool_index
        next_tool_index += 1
        return idx

    def make_chunk(
        *,
        delta_content: Optional[str] = None,
        reasoning_content: Optional[str] = None,
        delta_annotations: Optional[list] = None,
        tool_calls: Optional[List[dict]] = None,
        finish_reason: Optional[str] = None,
        usage: Optional[dict] = None,
    ) -> dict:
        delta: Dict[str, Any] = {}
        if delta_content is not None:
            delta["content"] = delta_content
        if reasoning_content is not None:
            delta["reasoning_content"] = reasoning_content
        if delta_annotations is not None:
            delta["annotations"] = delta_annotations
        if tool_calls is not None:
            delta["tool_calls"] = tool_calls
        chunk: Dict[str, Any] = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_id,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        if usage is not None:
            chunk["usage"] = usage
        return chunk

    def _extract_provided_index(item: Any, event_obj: dict) -> Optional[int]:
        candidates = []
        if isinstance(item, dict):
            candidates.extend(
                [
                    item.get("index"),
                    item.get("item_index"),
                    item.get("output_index"),
                ]
            )
        if isinstance(event_obj, dict):
            candidates.extend(
                [
                    event_obj.get("index"),
                    event_obj.get("item_index"),
                    event_obj.get("output_index"),
                ]
            )
        for c in candidates:
            try:
                if c is None:
                    continue
                return int(c)
            except Exception:
                continue
        return None

    def _stable_tool_call_id(call_id: str, tool_index: int) -> str:
        return call_id or f"call_{stream_id}_{tool_index}"

    async for event in events:
        if not isinstance(event, dict):
            continue

        # Some proxies wrap errors without a top-level "type".
        if "error" in event and isinstance(event.get("error"), (dict, str)):
            err = event.get("error")
            msg = err.get("message") if isinstance(err, dict) else str(err)
            yield f"data: {json.dumps({'error': {'message': msg, 'type': 'responses_api_error', 'code': 'upstream_error'}}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        event_type = event.get("type", "") or ""

        if event_type == "response.output_text.delta":
            delta = event.get("delta", "")
            delta_text = ""
            annotations = None
            if isinstance(delta, str):
                delta_text = delta
            elif isinstance(delta, dict):
                delta_text = delta.get("text") or delta.get("content") or ""
                ann = delta.get("annotations")
                if isinstance(ann, list) and ann:
                    annotations = ann
            if delta_text or annotations:
                saw_content = True
                if delta_text:
                    saw_text_content = True
                yield f"data: {json.dumps(make_chunk(delta_content=delta_text, delta_annotations=annotations), ensure_ascii=False)}\n\n"
            continue

        # Some providers emit a full text at the end instead of deltas.
        if event_type == "response.output_text.done":
            if not saw_text_content:
                text = event.get("text") or ""
                if isinstance(text, str) and text:
                    saw_content = True
                    saw_text_content = True
                    yield f"data: {json.dumps(make_chunk(delta_content=text), ensure_ascii=False)}\n\n"
            continue

        # Reasoning summary stream (best-effort).
        if event_type in (
            "response.reasoning_summary_text.delta",
            "response.reasoning.delta",
        ):
            delta_text = event.get("delta", "")
            if delta_text:
                yield f"data: {json.dumps(make_chunk(reasoning_content=delta_text), ensure_ascii=False)}\n\n"
            continue

        if event_type == "response.function_call_arguments.delta":
            call_id = event.get("item_id") or event.get("call_id") or ""
            provided_index = _extract_provided_index(None, event)
            idx = get_tool_index(call_id, provided_index)
            stable_id = _stable_tool_call_id(call_id, idx)
            delta_args = event.get("delta", "") or ""
            if not isinstance(delta_args, str):
                delta_args = str(delta_args)
            if not delta_args:
                continue
            saw_tool_calls = True
            st = _tool_state(stable_id, idx)
            st["args"] = (st.get("args") or "") + delta_args
            continue

        if event_type == "response.function_call_arguments.done":
            call_id = event.get("item_id") or event.get("call_id") or ""
            name = event.get("name") or ""
            provided_index = _extract_provided_index(None, event)
            idx = get_tool_index(call_id, provided_index)
            stable_id = _stable_tool_call_id(call_id, idx)
            saw_tool_calls = True
            st = _tool_state(stable_id, idx)
            if name:
                st["name"] = name
            if stable_id and stable_id not in tool_call_emitted and st.get("name"):
                tool_call_emitted.add(stable_id)
                tool_calls = [
                    {
                        "index": idx,
                        "id": stable_id,
                        "type": "function",
                        "function": {
                            "name": st.get("name") or "",
                            "arguments": st.get("args") or "",
                        },
                    }
                ]
                yield f"data: {json.dumps(make_chunk(tool_calls=tool_calls), ensure_ascii=False)}\n\n"
            continue

        # Compatibility: tool calls and/or text can arrive as output items.
        if event_type in ("response.output_item.added", "response.output_item.delta", "response.output_item.done"):
            item = event.get("item") or event.get("delta") or {}
            if isinstance(item, dict):
                item_type = item.get("type") or ""

                if item_type in ("tool_call", "function_call"):
                    raw_call_id = _tool_call_id_from_item(item, event)
                    provided_index = _extract_provided_index(item, event)
                    idx = get_tool_index(raw_call_id, provided_index)
                    call_id = _stable_tool_call_id(raw_call_id, idx)
                    name = _tool_call_name_from_item(item)
                    args = _stringify_tool_call_args(_tool_call_args_from_item(item))

                    if name or args or event_type == "response.output_item.done":
                        saw_tool_calls = True
                    st = _tool_state(call_id, idx)
                    if name:
                        st["name"] = name
                    if args:
                        # Prefer full args from output items when present.
                        st["args"] = args

                    # Skip pure placeholders (common in "added"/"delta").
                    if event_type != "response.output_item.done":
                        if not name and not args:
                            continue
                        # Accumulate state only; emit on done for stability.
                        continue

                    if call_id and call_id not in tool_call_emitted and st.get("name"):
                        tool_call_emitted.add(call_id)
                        saw_tool_calls = True
                        tool_calls = [
                            {
                                "index": idx,
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": st.get("name") or "",
                                    "arguments": st.get("args") or "",
                                },
                            }
                        ]
                        yield f"data: {json.dumps(make_chunk(tool_calls=tool_calls), ensure_ascii=False)}\n\n"
                    continue

                if item_type == "message" and not saw_text_content and event_type == "response.output_item.done":
                    # Fallback: extract text from final message content when deltas are missing.
                    content_items = item.get("content") or []
                    if isinstance(content_items, list):
                        parts = []
                        for part in content_items:
                            if not isinstance(part, dict):
                                continue
                            if part.get("type") in ("output_text", "text"):
                                t = part.get("text") or ""
                                if t:
                                    parts.append(t)
                        if parts:
                            saw_content = True
                            saw_text_content = True
                            yield f"data: {json.dumps(make_chunk(delta_content=''.join(parts)), ensure_ascii=False)}\n\n"
                            continue

        if event_type in ("response.completed", "response.done"):
            # Attach usage when available (some proxies include it here).
            usage = None
            if isinstance(event.get("response"), dict):
                usage = event["response"].get("usage")
            elif isinstance(event.get("usage"), dict):
                usage = event.get("usage")

            # Emit any remaining tool calls (best-effort) before closing the stream.
            pending_tool_calls: List[dict] = []
            for stable_id, st in tool_state_by_id.items():
                if stable_id in tool_call_emitted:
                    continue
                if not isinstance(st, dict):
                    continue
                name = st.get("name") or ""
                if not name:
                    continue
                try:
                    idx = int(st.get("idx") or 0)
                except Exception:
                    idx = 0
                args = st.get("args") or ""
                tool_call_emitted.add(stable_id)
                pending_tool_calls.append(
                    {
                        "index": idx,
                        "id": stable_id,
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    }
                )
            if pending_tool_calls:
                pending_tool_calls.sort(key=lambda x: int(x.get("index") or 0))
                saw_tool_calls = True
                yield f"data: {json.dumps(make_chunk(tool_calls=pending_tool_calls), ensure_ascii=False)}\n\n"

            finish_reason = "tool_calls" if saw_tool_calls and not saw_text_content else "stop"
            yield f"data: {json.dumps(make_chunk(finish_reason=finish_reason, usage=usage), ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        # Unknown event types: ignore (compat).

    # If upstream ends without a done event, close the stream.
    yield "data: [DONE]\n\n"
