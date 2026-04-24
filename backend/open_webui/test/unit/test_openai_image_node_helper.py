import asyncio
import base64
import json
import pathlib
import sys
from types import SimpleNamespace


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.routers import images as images_router  # noqa: E402


def _make_user():
    return SimpleNamespace(
        id="user-1",
        name="Test User",
        email="user@example.com",
        role="user",
    )


def test_send_openai_image_request_uses_httpx_json(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = json.dumps({"data": []})

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(images_router.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        images_router._send_openai_image_request(
            url="https://api.openai.com/v1/images/generations",
            headers={"Authorization": "Bearer test"},
            request_kind="json",
            json_body={"model": "gpt-image-2", "prompt": "cat"},
        )
    )

    assert result["status"] == 200
    assert captured["client_kwargs"]["trust_env"] is True
    assert captured["post_kwargs"]["json"]["model"] == "gpt-image-2"
    assert captured["post_kwargs"]["files"] is None


def test_send_openai_image_request_parses_official_stream(monkeypatch):
    b64_image = base64.b64encode(b"generated" * 32).decode("utf-8")

    class FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def aiter_lines(self):
            yield 'data: {"type":"image_generation.partial_image","partial_image_b64":"ignored"}'
            yield f'data: {json.dumps({"type": "image_generation.completed", "b64_json": b64_image})}'
            yield "data: [DONE]"

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def stream(self, method, url, **kwargs):
            return FakeStreamResponse()

    monkeypatch.setattr(images_router.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        images_router._send_openai_image_request(
            url="https://api.openai.com/v1/images/generations",
            headers={"Authorization": "Bearer test"},
            request_kind="json",
            json_body={"model": "gpt-image-2", "prompt": "cat", "stream": True},
        )
    )

    assert result["status"] == 200
    assert json.loads(result["response_body"])["data"] == [{"b64_json": b64_image}]


def test_send_openai_image_request_uses_httpx_multipart(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = json.dumps({"data": []})

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["post_kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(images_router.httpx, "AsyncClient", FakeAsyncClient)

    asyncio.run(
        images_router._send_openai_image_request(
            url="https://api.openai.com/v1/images/edits",
            headers={"Authorization": "Bearer test", "Content-Type": "bad"},
            request_kind="multipart",
            form_fields={"model": "gpt-image-2", "prompt": "cat", "n": 1},
            files=[
                {
                    "field_name": "image",
                    "filename": "image.png",
                    "mime": "image/png",
                    "data": b"png-bytes",
                }
            ],
        )
    )

    assert "Content-Type" not in captured["post_kwargs"]["headers"]
    assert captured["post_kwargs"]["data"] == {
        "model": "gpt-image-2",
        "prompt": "cat",
        "n": "1",
    }
    assert captured["post_kwargs"]["files"] == [
        ("image", ("image.png", b"png-bytes", "image/png"))
    ]


def test_node_openai_image_helper_does_not_require_open_as_blob():
    helper_path = _BACKEND_DIR / "open_webui" / "utils" / "openai-image-fetch.mjs"
    helper_source = helper_path.read_text(encoding="utf-8")

    assert "openAsBlob" not in helper_source
    assert "new Blob" in helper_source


def test_generate_via_openai_images_endpoint_uses_native_request(monkeypatch):
    captured = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "response_body": json.dumps(
                {
                    "data": [
                        {
                            "b64_json": base64.b64encode(b"generated" * 32).decode("utf-8")
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)
    monkeypatch.setattr(
        images_router,
        "upload_image",
        lambda request, payload, image_data, content_type, user: "/images/generated.png",
    )

    result = asyncio.run(
        images_router._generate_via_openai_images_endpoint(
            request=SimpleNamespace(),
            user=_make_user(),
            model_id="gpt-image-2",
            prompt="生成一张图",
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://api.openai.com/v1",
                "key": "sk-test",
                "api_config": {},
            },
        )
    )

    assert captured["request_kind"] == "json"
    assert captured["json_body"]["model"] == "gpt-image-2"
    assert captured["json_body"]["stream"] is True
    assert captured["json_body"]["partial_images"] == 1
    assert "size" not in captured["json_body"]
    assert result == [{"url": "/images/generated.png"}]


def test_generate_via_openai_images_endpoint_uses_configured_size(monkeypatch):
    captured = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "response_body": json.dumps(
                {
                    "data": [
                        {
                            "b64_json": base64.b64encode(b"generated" * 32).decode("utf-8")
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)
    monkeypatch.setattr(
        images_router,
        "upload_image",
        lambda request, payload, image_data, content_type, user: "/images/generated.png",
    )

    asyncio.run(
        images_router._generate_via_openai_images_endpoint(
            request=SimpleNamespace(),
            user=_make_user(),
            model_id="gpt-image-2",
            prompt="生成一张图",
            n=1,
            size="1024x1024",
            background=None,
            source={
                "base_url": "https://api.openai.com/v1",
                "key": "sk-test",
                "api_config": {},
            },
        )
    )

    assert captured["json_body"]["model"] == "gpt-image-2"
    assert captured["json_body"]["size"] == "1024x1024"


def test_generate_via_openai_images_endpoint_strips_connection_prefix(monkeypatch):
    captured = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "response_body": json.dumps(
                {
                    "data": [
                        {
                            "b64_json": base64.b64encode(b"generated" * 32).decode("utf-8")
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)
    monkeypatch.setattr(
        images_router,
        "upload_image",
        lambda request, payload, image_data, content_type, user: "/images/generated.png",
    )

    asyncio.run(
        images_router._generate_via_openai_images_endpoint(
            request=SimpleNamespace(),
            user=_make_user(),
            model_id="d7f188cd.gpt-image-2",
            prompt="生成一张图",
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://cpa.example.com/v1",
                "key": "sk-test",
                "api_config": {"prefix_id": "d7f188cd"},
            },
        )
    )

    assert captured["json_body"]["model"] == "gpt-image-2"
    assert "stream" not in captured["json_body"]
    assert "partial_images" not in captured["json_body"]


def test_generate_via_openai_images_endpoint_strips_internal_prefix_without_config_prefix(monkeypatch):
    captured = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "response_body": json.dumps(
                {
                    "data": [
                        {
                            "b64_json": base64.b64encode(b"generated" * 32).decode("utf-8")
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)
    monkeypatch.setattr(
        images_router,
        "upload_image",
        lambda request, payload, image_data, content_type, user: "/images/generated.png",
    )

    asyncio.run(
        images_router._generate_via_openai_images_endpoint(
            request=SimpleNamespace(),
            user=_make_user(),
            model_id="d7f188cd.gpt-image-2",
            prompt="生成一张图",
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://cpa.example.com/v1",
                "key": "sk-test",
                "api_config": {},
            },
        )
    )

    assert captured["json_body"]["model"] == "gpt-image-2"


def test_generate_via_openai_image_edits_endpoint_uses_native_request(monkeypatch):
    captured = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "response_body": json.dumps(
                {
                    "data": [
                        {
                            "b64_json": base64.b64encode(b"edited" * 32).decode("utf-8")
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)
    monkeypatch.setattr(
        images_router,
        "upload_image",
        lambda request, payload, image_data, content_type, user: "/images/edited.png",
    )

    request = SimpleNamespace(
        base_url="https://example.com/",
        state=SimpleNamespace(token=None),
    )
    image_url = "data:image/png;base64," + base64.b64encode(b"source").decode("utf-8")

    result = asyncio.run(
        images_router._generate_via_openai_image_edits_endpoint(
            request=request,
            user=_make_user(),
            model_id="gpt-image-2",
            prompt="把猫改成黑白奶牛猫",
            image_url=image_url,
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://api.openai.com/v1",
                "key": "sk-test",
                "api_config": {},
            },
        )
    )

    assert captured["request_kind"] == "multipart"
    assert captured["form_fields"]["model"] == "gpt-image-2"
    assert not any(key.lower() == "content-type" for key in captured["headers"])
    assert captured["files"][0]["field_name"] == "image"
    assert captured["files"][0]["mime"] == "image/png"
    assert captured["files"][0]["data"] == b"source"
    assert result == [{"url": "/images/edited.png"}]


def test_generate_via_openai_image_edits_endpoint_strips_connection_prefix(monkeypatch):
    captured = {}

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "response_body": json.dumps(
                {
                    "data": [
                        {
                            "b64_json": base64.b64encode(b"edited" * 32).decode("utf-8")
                        }
                    ]
                }
            ),
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)
    monkeypatch.setattr(
        images_router,
        "upload_image",
        lambda request, payload, image_data, content_type, user: "/images/edited.png",
    )

    request = SimpleNamespace(
        base_url="https://example.com/",
        state=SimpleNamespace(token=None),
    )
    image_url = "data:image/png;base64," + base64.b64encode(b"source").decode("utf-8")

    asyncio.run(
        images_router._generate_via_openai_image_edits_endpoint(
            request=request,
            user=_make_user(),
            model_id="d7f188cd.gpt-image-2",
            prompt="把猫改成黑白奶牛猫",
            image_url=image_url,
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://cpa.example.com/v1",
                "key": "sk-test",
                "api_config": {"prefix_id": "d7f188cd"},
            },
        )
    )

    assert captured["form_fields"]["model"] == "gpt-image-2"
