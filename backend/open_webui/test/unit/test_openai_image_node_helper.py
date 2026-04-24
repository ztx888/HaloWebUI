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


def test_build_node_openai_image_request_manifest_json():
    manifest = images_router._build_node_openai_image_request_manifest(
        url="https://api.openai.com/v1/images/generations",
        headers={"Authorization": "Bearer test"},
        request_kind="json",
        json_body={"model": "gpt-image-2", "prompt": "cat"},
        response_body_path="/tmp/body.json",
    )

    assert manifest["url"] == "https://api.openai.com/v1/images/generations"
    assert manifest["request_kind"] == "json"
    assert manifest["json_body"]["model"] == "gpt-image-2"
    assert manifest["response_body_path"] == "/tmp/body.json"


def test_node_openai_image_helper_does_not_require_open_as_blob():
    helper_source = images_router.OPENAI_IMAGE_NODE_HELPER_PATH.read_text(encoding="utf-8")

    assert "openAsBlob" not in helper_source
    assert "new Blob" in helper_source


def test_run_node_openai_image_request_reads_structured_result(tmp_path, monkeypatch):
    helper_path = tmp_path / "openai-image-fetch.mjs"
    helper_path.write_text("// helper placeholder", encoding="utf-8")
    monkeypatch.setattr(images_router, "OPENAI_IMAGE_NODE_HELPER_PATH", helper_path)
    monkeypatch.setattr(images_router.shutil, "which", lambda name: "/usr/bin/node")

    def fake_run(cmd, capture_output, text, env):
        manifest_path = pathlib.Path(cmd[2])
        result_path = pathlib.Path(cmd[3])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        assert manifest["request_kind"] == "multipart"
        assert manifest["files"][0]["field_name"] == "image"
        assert pathlib.Path(manifest["files"][0]["path"]).read_bytes() == b"png-bytes"

        pathlib.Path(manifest["response_body_path"]).write_text(
            '{"data":[{"b64_json":"aGVsbG8="}]}',
            encoding="utf-8",
        )
        result_path.write_text(
            json.dumps(
                {
                    "status": 200,
                    "headers": {"content-type": "application/json"},
                    "elapsed_ms": 123,
                    "response_body_path": manifest["response_body_path"],
                    "error_type": None,
                    "error_message": None,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(images_router.subprocess, "run", fake_run)

    result = images_router._run_node_openai_image_request(
        url="https://api.openai.com/v1/images/edits",
        headers={"Authorization": "Bearer test"},
        request_kind="multipart",
        form_fields={"model": "gpt-image-2", "prompt": "cat", "n": 1},
        files=[
            {
                "field_name": "image",
                "filename": "generated-image.png",
                "mime": "image/png",
                "data": b"png-bytes",
            }
        ],
    )

    assert result["status"] == 200
    assert result["headers"]["content-type"] == "application/json"
    assert result["response_body"] == '{"data":[{"b64_json":"aGVsbG8="}]}'


def test_generate_via_openai_images_endpoint_uses_node_helper(monkeypatch):
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

    monkeypatch.setattr(images_router, "_send_openai_image_request_via_node", fake_send)
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

    monkeypatch.setattr(images_router, "_send_openai_image_request_via_node", fake_send)
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

    monkeypatch.setattr(images_router, "_send_openai_image_request_via_node", fake_send)
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

    monkeypatch.setattr(images_router, "_send_openai_image_request_via_node", fake_send)
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


def test_generate_via_openai_image_edits_endpoint_uses_node_helper(monkeypatch):
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

    monkeypatch.setattr(images_router, "_send_openai_image_request_via_node", fake_send)
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

    monkeypatch.setattr(images_router, "_send_openai_image_request_via_node", fake_send)
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
