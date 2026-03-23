import asyncio
import json
import pathlib
import sys


# Ensure `open_webui` is importable when running tests from repo root.
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.utils.openai_responses import (
    convert_chat_completions_to_responses_payload,
    convert_responses_to_chat_completions,
    iter_responses_events,
    responses_events_to_chat_completions_sse,
)


async def _aiter(chunks):
    for c in chunks:
        yield c


async def _collect_async(gen):
    items = []
    async for x in gen:
        items.append(x)
    return items


def test_convert_chat_completions_to_responses_payload_basic():
    chat = {
        "model": "gpt-test",
        "stream": True,
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": "Hi",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "do", "arguments": "{\"x\":1}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"ok\":true}"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "do",
                    "description": "does things",
                    "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}},
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "do"}},
        "max_tokens": 123,
    }

    r = convert_chat_completions_to_responses_payload(chat, native_web_search_tool_type=None)
    assert r["model"] == "gpt-test"
    assert r["stream"] is True
    assert r["instructions"] == "You are helpful."
    assert r["max_output_tokens"] == 123
    assert isinstance(r["input"], list)

    # user message preserved
    assert any(
        i.get("type") == "message"
        and i.get("role") == "user"
        and isinstance(i.get("content"), list)
        and i["content"][0].get("type") == "input_text"
        and i["content"][0].get("text") == "Hello"
        for i in r["input"]
    )
    # assistant message preserved (assistant history must use output_* content types)
    assert any(
        i.get("type") == "message"
        and i.get("role") == "assistant"
        and isinstance(i.get("content"), list)
        and i["content"][0].get("type") == "output_text"
        and i["content"][0].get("text") == "Hi"
        for i in r["input"]
    )
    # tool output preserved
    assert any(i.get("type") == "function_call_output" and i.get("call_id") == "call_1" for i in r["input"])
    # tool call carried as input item
    assert any(i.get("type") == "function_call" and i.get("call_id") == "call_1" for i in r["input"])

    # tools converted to Responses function tool format
    assert r["tools"][0]["type"] == "function"
    assert r["tools"][0]["name"] == "do"
    assert "parameters" in r["tools"][0]

    # tool_choice converted
    assert r["tool_choice"] == {"type": "function", "name": "do"}


def test_convert_responses_to_chat_completions_basic():
    responses = {
        "id": "resp_1",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "Hello"}]},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    cc = convert_responses_to_chat_completions(responses, model_id="gpt-test")
    assert cc["id"] == "resp_1"
    assert cc["choices"][0]["message"]["content"] == "Hello"
    assert cc["usage"]["output_tokens"] == 1


def test_convert_chat_completions_to_responses_payload_injects_native_web_search_tool():
    chat = {
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "Find the latest updates"}],
    }

    r = convert_chat_completions_to_responses_payload(
        chat,
        native_web_search_tool_type="web_search",
    )

    assert r["tools"] == [{"type": "web_search"}]
    assert r["tool_choice"] == "auto"


def test_convert_chat_completions_to_responses_payload_strips_include_usage_stream_option():
    chat = {
        "model": "gpt-test",
        "stream": True,
        "stream_options": {"include_usage": True, "include_obfuscation": True},
        "messages": [{"role": "user", "content": "Hello"}],
    }

    r = convert_chat_completions_to_responses_payload(
        chat,
        native_web_search_tool_type=None,
    )

    assert r["stream"] is True
    assert r["stream_options"] == {"include_obfuscation": True}


def test_convert_chat_completions_to_responses_payload_preserves_input_files():
    chat = {
        "model": "gpt-test",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read these files"},
                    {"type": "input_file", "file_id": "file_123"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    {"type": "file", "file_id": "file_456"},
                ],
            }
        ],
    }

    r = convert_chat_completions_to_responses_payload(
        chat,
        native_web_search_tool_type=None,
    )

    assert r["input"][0]["type"] == "message"
    content = r["input"][0]["content"]
    assert {"type": "input_text", "text": "Read these files"} in content
    assert {"type": "input_file", "file_id": "file_123"} in content
    assert {"type": "input_file", "file_id": "file_456"} in content
    assert {"type": "input_image", "image_url": "https://example.com/a.png"} in content


def test_iter_responses_events_sse_fragmented():
    event1 = json.dumps({"type": "response.output_text.delta", "delta": "Hi"})
    event2 = json.dumps({"type": "response.completed", "response": {"usage": {"output_tokens": 2}}})
    payload = (
        f"data: {event1}\n\n"
        f"data: {event2}\n\n"
        "data: [DONE]\n\n"
    ).encode("utf-8")

    # Fragment across chunks to ensure buffer stitching works.
    chunks = [payload[:10], payload[10:35], payload[35:]]

    async def run():
        evs = iter_responses_events(_aiter(chunks), content_type="text/event-stream")
        return await _collect_async(evs)

    events = asyncio.run(run())
    assert events[0]["type"] == "response.output_text.delta"
    assert events[1]["type"] == "response.completed"


def test_iter_responses_events_ndjson():
    line1 = json.dumps({"type": "response.output_text.delta", "delta": "A"}) + "\n"
    line2 = json.dumps({"type": "response.completed"}) + "\n"
    chunks = [(line1 + line2).encode("utf-8")]

    async def run():
        evs = iter_responses_events(_aiter(chunks), content_type="application/x-ndjson")
        return await _collect_async(evs)

    events = asyncio.run(run())
    assert [e["type"] for e in events] == ["response.output_text.delta", "response.completed"]


def test_responses_events_to_chat_sse_text_and_done():
    events = [
        {"type": "response.output_text.delta", "delta": "Hello"},
        {"type": "response.completed", "response": {"usage": {"output_tokens": 1}}},
    ]

    async def run():
        sse = responses_events_to_chat_completions_sse(_aiter(events), model_id="gpt-test")
        return await _collect_async(sse)

    lines = asyncio.run(run())
    assert any('"content": "Hello"' in line for line in lines)
    assert lines[-1].strip() == "data: [DONE]"


def test_responses_events_to_chat_sse_tool_call_delta_and_name():
    events = [
        {"type": "response.function_call_arguments.delta", "item_id": "call_x", "delta": "{\"a\":"},
        {"type": "response.function_call_arguments.delta", "item_id": "call_x", "delta": "1}"},
        {"type": "response.function_call_arguments.done", "item_id": "call_x", "name": "do", "arguments": "{\"a\":1}"},
        {"type": "response.completed"},
    ]

    async def run():
        sse = responses_events_to_chat_completions_sse(_aiter(events), model_id="gpt-test")
        return await _collect_async(sse)

    lines = asyncio.run(run())
    # At least one chunk includes tool_calls.
    assert any('"tool_calls"' in line for line in lines)
    # Name should be emitted at least once.
    assert any('"name": "do"' in line for line in lines)
