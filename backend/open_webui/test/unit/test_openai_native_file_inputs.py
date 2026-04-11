import asyncio
import pathlib
import sys
from types import SimpleNamespace


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.routers.openai import (  # noqa: E402
    NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG,
    NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED,
    NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED,
    _connection_supports_native_file_inputs,
    _get_native_file_input_capability,
    _get_default_responses_reasoning_summary,
    _looks_like_reasoning_summary_incompatible,
    _should_use_responses_api,
)
import open_webui.utils.middleware as middleware  # noqa: E402


def test_should_use_responses_api_respects_exclude_patterns():
    assert (
        _should_use_responses_api(
            "https://api.openai.com/v1",
            {"use_responses_api": True, "responses_api_exclude_patterns": ["mini"]},
            "gpt-4.1-mini",
        )
        is False
    )
    assert (
        _should_use_responses_api(
            "https://api.openai.com/v1",
            {"use_responses_api": True, "responses_api_exclude_patterns": ["mini"]},
            "gpt-4.1",
        )
        is True
    )


def test_should_use_responses_api_is_disabled_for_azure_connections():
    assert (
        _should_use_responses_api(
            "https://example-resource.openai.azure.com/openai/v1",
            {"use_responses_api": True, "azure": True},
            "gpt-4.1",
        )
        is False
    )


def test_should_use_responses_api_can_be_forced_for_native_file_inputs():
    assert (
        _should_use_responses_api(
            "https://proxy.example.com/v1",
            {"use_responses_api": False, "native_file_inputs_enabled": True},
            "gpt-4.1",
            native_file_inputs=True,
        )
        is True
    )


def test_connection_supports_native_file_inputs_defaults_to_official_openai_only():
    assert (
        _connection_supports_native_file_inputs(
            "https://api.openai.com/v1",
            {"use_responses_api": False},
        )
        is True
    )
    assert (
        _connection_supports_native_file_inputs(
            "https://openrouter.ai/api/v1",
            {"use_responses_api": False},
        )
        is False
    )


def test_connection_supports_native_file_inputs_honors_explicit_flag_and_guards():
    assert (
        _connection_supports_native_file_inputs(
            "https://proxy.example.com/v1",
            {"use_responses_api": True, "native_file_inputs_enabled": True},
        )
        is True
    )
    assert (
        _connection_supports_native_file_inputs(
            "https://api.openai.com/v1/chat/completions",
            {"use_responses_api": False, "native_file_inputs_enabled": True, "force_mode": True},
        )
        is False
    )
    assert (
        _connection_supports_native_file_inputs(
            "https://my-azure.openai.azure.com/openai/deployments/foo",
            {"use_responses_api": False, "native_file_inputs_enabled": True, "azure": True},
        )
        is False
    )
    assert (
        _connection_supports_native_file_inputs(
            "https://proxy.example.com/v1",
            {"use_responses_api": False, "native_file_inputs_enabled": True},
        )
        is True
    )


def test_default_responses_reasoning_summary_defaults_to_auto_and_honors_overrides():
    assert _get_default_responses_reasoning_summary({"use_responses_api": True}) == "auto"
    assert (
        _get_default_responses_reasoning_summary(
            {"use_responses_api": True, "responses_reasoning_summary": False}
        )
        is None
    )
    assert (
        _get_default_responses_reasoning_summary(
            {"use_responses_api": True, "responses_reasoning_summary": "detailed"}
        )
        == "detailed"
    )


def test_looks_like_reasoning_summary_incompatible_matches_schema_errors():
    assert _looks_like_reasoning_summary_incompatible(
        400,
        {
            "error": {
                "message": "Unknown parameter: reasoning.summary",
            }
        },
    )
    assert _looks_like_reasoning_summary_incompatible(
        422,
        "Additional properties are not allowed ('summary' was unexpected in reasoning).",
    )
    assert not _looks_like_reasoning_summary_incompatible(
        400,
        {
            "error": {
                "message": "Unknown parameter: temperature",
            }
        },
    )


def test_prepare_openai_native_file_inputs_uploads_pdf_via_storage_provider(monkeypatch):
    file_id = "file_local_1"
    file_item = {"type": "file", "id": file_id, "processing_mode": "native_file"}
    file_obj = SimpleNamespace(
        id=file_id,
        path=f"/data/uploads/{file_id}_demo.pdf",
        filename="demo.pdf",
        meta={"content_type": "application/pdf"},
    )
    upload_call = {}

    monkeypatch.setattr(
        middleware.Files,
        "get_file_by_id",
        lambda current_file_id: file_obj if current_file_id == file_id else None,
    )
    monkeypatch.setattr(
        middleware,
        "_get_openai_user_config",
        lambda _user: (["https://api.openai.com/v1"], ["sk-test"], [{}]),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_openai_connection_by_model_id",
        lambda *_args, **_kwargs: (
            0,
            "https://api.openai.com/v1",
            "sk-test",
            {"use_responses_api": True},
        ),
    )
    monkeypatch.setattr(middleware, "_should_use_responses_api", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(middleware, "_get_openai_file_cache_key", lambda *_args, **_kwargs: "conn-1")
    monkeypatch.setattr(middleware, "_get_cached_openai_file_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(middleware, "_set_cached_openai_file_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        middleware.Storage,
        "get_file",
        lambda path: f"/tmp/{path.rsplit('/', 1)[-1]}",
    )

    async def fake_upload_file_to_openai(**kwargs):
        upload_call.update(kwargs)
        return "remote-file-1"

    monkeypatch.setattr(middleware, "_upload_file_to_openai", fake_upload_file_to_openai)

    request = SimpleNamespace(state=SimpleNamespace(connection_user=None))
    user = SimpleNamespace(id="user-1")
    form_data = {
        "model": "gpt-5.4",
        "messages": [
            {
                "role": "user",
                "content": "能看到这个文件吗？",
                "files": [dict(file_item)],
            }
        ],
    }
    metadata = {"files": [dict(file_item)]}

    asyncio.run(
        middleware._prepare_openai_native_file_inputs(
            request,
            form_data,
            metadata,
            user,
            {"id": "gpt-5.4", "owned_by": "openai"},
        )
    )

    assert upload_call["local_path"] == f"/tmp/{file_id}_demo.pdf"
    assert upload_call["filename"] == "demo.pdf"
    assert upload_call["content_type"] == "application/pdf"
    assert upload_call["user"] is user
    assert metadata["native_file_input_file_ids"] == [file_id]
    assert metadata["native_file_input_parts_by_message"] == {
        "0": [{"type": "input_file", "file_id": "remote-file-1"}]
    }


def test_get_native_file_input_capability_classifies_connection_policy():
    capability = _get_native_file_input_capability(
        "https://proxy.example.com/v1",
        {"native_file_inputs_enabled": False},
    )
    assert capability["status"] == NATIVE_FILE_INPUT_STATUS_DISABLED_BY_CONFIG

    capability = _get_native_file_input_capability(
        "https://proxy.example.com/v1/chat/completions",
        {"native_file_inputs_enabled": True, "force_mode": True},
    )
    assert capability["status"] == NATIVE_FILE_INPUT_STATUS_PROTOCOL_NOT_ATTEMPTED


def test_prepare_openai_native_file_inputs_can_force_responses_for_enabled_third_party(monkeypatch):
    file_id = "file_local_2"
    file_item = {"type": "file", "id": file_id, "processing_mode": "native_file"}
    file_obj = SimpleNamespace(
        id=file_id,
        path=f"/data/uploads/{file_id}_demo.pdf",
        filename="demo.pdf",
        meta={"content_type": "application/pdf"},
    )

    monkeypatch.setattr(
        middleware.Files,
        "get_file_by_id",
        lambda current_file_id: file_obj if current_file_id == file_id else None,
    )
    monkeypatch.setattr(
        middleware,
        "_get_openai_user_config",
        lambda _user: (["https://proxy.example.com/v1"], ["sk-test"], {}),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_openai_connection_by_model_id",
        lambda *_args, **_kwargs: (
            0,
            "https://proxy.example.com/v1",
            "sk-test",
            {"use_responses_api": False, "native_file_inputs_enabled": True},
        ),
    )
    monkeypatch.setattr(
        middleware,
        "_probe_responses_support_for_native_file_inputs",
        lambda **_kwargs: asyncio.sleep(
            0,
            {
                "supported": True,
                "status": "supported",
                "reason": "responses_probe_succeeded",
            },
        ),
    )
    monkeypatch.setattr(middleware, "_get_openai_file_cache_key", lambda *_args, **_kwargs: "conn-2")
    monkeypatch.setattr(middleware, "_get_cached_openai_file_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(middleware, "_set_cached_openai_file_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        middleware.Storage,
        "get_file",
        lambda path: f"/tmp/{path.rsplit('/', 1)[-1]}",
    )

    async def fake_upload_file_to_openai(**_kwargs):
        return "remote-file-2"

    monkeypatch.setattr(middleware, "_upload_file_to_openai", fake_upload_file_to_openai)

    request = SimpleNamespace(state=SimpleNamespace(connection_user=None))
    user = SimpleNamespace(id="user-1")
    form_data = {
        "model": "third-party.gpt-5.4",
        "messages": [
            {
                "role": "user",
                "content": "能看到这个文件吗？",
                "files": [dict(file_item)],
            }
        ],
    }
    metadata = {"files": [dict(file_item)]}

    asyncio.run(
        middleware._prepare_openai_native_file_inputs(
            request,
            form_data,
            metadata,
            user,
            {"id": "third-party.gpt-5.4", "owned_by": "openai"},
        )
    )

    assert metadata["native_file_input_file_ids"] == [file_id]
    assert metadata["native_file_inputs_force_responses_api"] is True


def test_prepare_openai_native_file_inputs_records_upload_failure_diagnostic(monkeypatch):
    file_id = "file_local_3"
    file_item = {"type": "file", "id": file_id, "processing_mode": "native_file"}
    file_obj = SimpleNamespace(
        id=file_id,
        path=f"/data/uploads/{file_id}_demo.pdf",
        filename="demo.pdf",
        meta={"content_type": "application/pdf"},
    )

    monkeypatch.setattr(
        middleware.Files,
        "get_file_by_id",
        lambda current_file_id: file_obj if current_file_id == file_id else None,
    )
    monkeypatch.setattr(
        middleware,
        "_get_openai_user_config",
        lambda _user: (["https://proxy.example.com/v1"], ["sk-test"], {}),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_openai_connection_by_model_id",
        lambda *_args, **_kwargs: (
            0,
            "https://proxy.example.com/v1",
            "sk-test",
            {"use_responses_api": False, "native_file_inputs_enabled": True},
        ),
    )
    monkeypatch.setattr(
        middleware,
        "_probe_responses_support_for_native_file_inputs",
        lambda **_kwargs: asyncio.sleep(
            0,
            {
                "supported": True,
                "status": "supported",
                "reason": "responses_probe_succeeded",
            },
        ),
    )
    monkeypatch.setattr(middleware, "_get_openai_file_cache_key", lambda *_args, **_kwargs: "conn-3")
    monkeypatch.setattr(middleware, "_get_cached_openai_file_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(middleware, "_set_cached_openai_file_id", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        middleware.Storage,
        "get_file",
        lambda path: f"/tmp/{path.rsplit('/', 1)[-1]}",
    )

    async def failing_upload_file_to_openai(**_kwargs):
        raise RuntimeError("HTTP 415 unsupported file")

    monkeypatch.setattr(middleware, "_upload_file_to_openai", failing_upload_file_to_openai)

    request = SimpleNamespace(state=SimpleNamespace(connection_user=None))
    user = SimpleNamespace(id="user-1")
    form_data = {
        "model": "third-party.gpt-5.4",
        "messages": [{"role": "user", "content": "x", "files": [dict(file_item)]}],
    }
    metadata = {"files": [dict(file_item)]}

    asyncio.run(
        middleware._prepare_openai_native_file_inputs(
            request,
            form_data,
            metadata,
            user,
            {"id": "third-party.gpt-5.4", "owned_by": "openai"},
        )
    )

    diagnostic = metadata["native_file_input_diagnostics"][file_id]
    assert diagnostic["status"] == NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED
    assert "Files API" in diagnostic["message"]
    assert metadata.get("native_file_input_file_ids") is None


def test_ensure_requested_chat_file_modes_prefers_local_before_remote(monkeypatch):
    file_id = "file_local_4"

    class _File:
        def __init__(self):
            self.id = file_id
            self.filename = "demo.pdf"
            self.path = "/tmp/demo.pdf"
            self.meta = {
                "content_type": "application/pdf",
                "processing_mode": "native_file",
            }
            self.data = {}

        def model_dump(self):
            return {
                "id": self.id,
                "filename": self.filename,
                "path": self.path,
                "meta": dict(self.meta),
                "data": dict(self.data),
            }

    file_obj = _File()
    calls = []
    events = []

    def fake_get_file_by_id(current_file_id):
        return file_obj if current_file_id == file_id else None

    monkeypatch.setattr(middleware.Files, "get_file_by_id", fake_get_file_by_id)
    monkeypatch.setattr(
        middleware.Files,
        "update_file_metadata_by_id",
        lambda *_args, **_kwargs: None,
    )

    def fake_process_file(_request, form_data, user):
        calls.append(
            {
                "file_id": form_data.file_id,
                "mode": form_data.processing_mode,
                "provider": form_data.document_provider,
                "allow_provider_local_fallback": form_data.allow_provider_local_fallback,
                "user": user,
            }
        )
        return {"status": True}

    async def fake_run_in_threadpool(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(middleware, "process_file", fake_process_file)
    monkeypatch.setattr(middleware, "run_in_threadpool", fake_run_in_threadpool)

    async def fake_event_emitter(event):
        events.append(event)

    metadata = {
        "files": [{"type": "file", "id": file_id, "processing_mode": "native_file"}],
        "native_file_input_diagnostics": {
            file_id: {
                "status": NATIVE_FILE_INPUT_STATUS_UPLOAD_FAILED,
                "reason": "files_api_upload_failed",
                "message": "Uploading this file to the upstream Files API failed.",
                "file_name": "demo.pdf",
            }
        },
    }
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    FILE_PROCESSING_DEFAULT_MODE="native_file",
                    DOCUMENT_PROVIDER="mineru",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")

    asyncio.run(
        middleware._ensure_requested_chat_file_modes(
            request,
            metadata,
            user,
            {"id": "gpt-5.4", "owned_by": "openai"},
            fake_event_emitter,
        )
    )

    assert len(calls) == 1
    assert calls[0]["provider"] == "local_default"
    assert calls[0]["allow_provider_local_fallback"] is False
    assert events
    assert "local document parsing" in events[0]["data"]["content"]
