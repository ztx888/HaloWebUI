import pathlib
import sys
import asyncio
from types import SimpleNamespace

from fastapi import HTTPException


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.routers import images as images_router  # noqa: E402
from open_webui.routers.images import _apply_image_model_regex_filter  # noqa: E402
from open_webui.routers.images import _discover_image_models  # noqa: E402
from open_webui.routers.images import _normalize_image_provider_base_url  # noqa: E402
from open_webui.routers.images import _generate_via_xai_images  # noqa: E402
from open_webui.routers.images import _resolve_image_provider_source  # noqa: E402
from open_webui.routers.images import _select_runtime_image_provider_source  # noqa: E402
from open_webui.routers.images import _sync_image_provider_config_state  # noqa: E402
from open_webui.utils import middleware  # noqa: E402


def test_image_size_auto_normalizes_to_no_exact_size():
    assert images_router._normalize_exact_image_size("auto") is None
    assert images_router._normalize_exact_image_size("") is None
    assert images_router._normalize_exact_image_size("1024x1536") == "1024x1536"


def test_image_size_auto_keeps_derived_dimensions_safe():
    assert images_router._size_to_aspect_ratio("auto") is None
    assert images_router._size_to_gemini_image_size("auto") is None


def test_chat_image_generation_handler_removes_legacy_size_before_generate_form(monkeypatch):
    captured = {}
    events = []

    async def fake_image_generations(request, form_data, user):
        captured["form_data"] = form_data
        return [{"url": "/api/v1/files/generated"}]

    async def fake_event_emitter(event):
        events.append(event)

    monkeypatch.setattr(middleware, "image_generations", fake_image_generations)

    metadata = {
        "image_generation_options": {
            "model": "gpt-image-2",
            "size": "900x1600",
            "image_size": "1K",
            "aspect_ratio": "16:9",
            "unknown": "must be removed",
        }
    }

    asyncio.run(
        middleware.chat_image_generation_handler(
            request=SimpleNamespace(),
            form_data={"messages": [{"role": "user", "content": "生成一张图"}]},
            extra_params={
                "__event_emitter__": fake_event_emitter,
                "__metadata__": metadata,
            },
            user=SimpleNamespace(id="user-1", role="admin"),
        )
    )

    form_data = captured["form_data"]
    assert form_data.model == "gpt-image-2"
    assert form_data.size is None
    assert form_data.image_size == "1K"
    assert form_data.aspect_ratio == "16:9"
    assert events[0]["data"]["description"] == "Generating an image"
    assert metadata["local_response"]["choices"][0]["message"]["images"] == [
        {"type": "image", "url": "/api/v1/files/generated"}
    ]


def test_openai_image_settings_auto_append_v1():
    normalized, force_mode = _normalize_image_provider_base_url(
        "https://api.example.com",
        "/v1",
    )

    assert normalized == "https://api.example.com/v1"
    assert force_mode is False


def test_openai_image_settings_preserve_irregular_version_path():
    normalized, force_mode = _normalize_image_provider_base_url(
        "https://relay.example.com/api/v3",
        "/v1",
    )

    assert normalized == "https://relay.example.com/api/v3"
    assert force_mode is False


def test_openai_image_settings_strip_known_endpoint_suffixes():
    normalized, force_mode = _normalize_image_provider_base_url(
        "https://api.example.com/v1/chat/completions",
        "/v1",
    )

    assert normalized == "https://api.example.com/v1"
    assert force_mode is False


def test_openai_image_settings_hash_enables_exact_mode():
    normalized, force_mode = _normalize_image_provider_base_url(
        "https://relay.example.com/custom/path#",
        "/v1",
    )

    assert normalized == "https://relay.example.com/custom/path"
    assert force_mode is True


def test_gemini_image_settings_force_mode_is_preserved_from_payload():
    normalized, force_mode = _normalize_image_provider_base_url(
        "https://generativelanguage.googleapis.com/custom",
        "/v1beta",
        force_mode=True,
    )

    assert normalized == "https://generativelanguage.googleapis.com/custom"
    assert force_mode is True


def test_image_settings_source_does_not_inherit_global_openai_auth_config_when_key_is_explicit():
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="https://api.example.com/v1",
        IMAGES_OPENAI_API_KEY="image-key",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        OPENAI_API_BASE_URLS=["https://api.example.com/v1"],
        OPENAI_API_KEYS=["global-key"],
        OPENAI_API_CONFIGS={"0": {"auth_type": "api-key", "force_mode": True}},
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    source = _resolve_image_provider_source(
        request,
        user=None,
        provider="openai",
        context="settings",
    )

    assert source is not None
    assert source["key"] == "image-key"
    assert source["api_config"] == {}


def test_image_runtime_shared_source_keeps_image_force_mode_while_merging_global_config():
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="https://api.example.com/v1",
        IMAGES_OPENAI_API_KEY="image-key",
        IMAGES_OPENAI_API_FORCE_MODE=True,
        OPENAI_API_BASE_URLS=["https://api.example.com/v1"],
        OPENAI_API_KEYS=["global-key"],
        OPENAI_API_CONFIGS={"0": {"auth_type": "bearer"}},
        ENABLE_IMAGE_GENERATION_SHARED_KEY=True,
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    source = _resolve_image_provider_source(
        request,
        user=None,
        provider="openai",
        context="runtime",
        credential_source="shared",
    )

    assert source is not None
    assert source["api_config"]["auth_type"] == "bearer"
    assert source["api_config"]["force_mode"] is True


def test_image_runtime_explicit_shared_source_uses_shared_config_even_when_toggle_is_disabled():
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="https://api.example.com/v1",
        IMAGES_OPENAI_API_KEY="image-key",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        OPENAI_API_BASE_URLS=["https://api.example.com/v1"],
        OPENAI_API_KEYS=["global-key"],
        OPENAI_API_CONFIGS={},
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    source = _resolve_image_provider_source(
        request,
        user=None,
        provider="openai",
        context="runtime",
        credential_source="shared",
    )

    assert source is not None
    assert source["effective_source"] == "shared"
    assert source["key"] == "image-key"


def test_image_runtime_explicit_shared_source_can_fallback_to_global_key():
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="https://api.example.com/v1",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        OPENAI_API_BASE_URLS=["https://api.example.com/v1"],
        OPENAI_API_KEYS=["global-key"],
        OPENAI_API_CONFIGS={"0": {"auth_type": "bearer"}},
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    source = _resolve_image_provider_source(
        request,
        user=None,
        provider="openai",
        context="runtime",
        credential_source="shared",
    )

    assert source is not None
    assert source["effective_source"] == "shared"
    assert source["key"] == "global-key"
    assert source["api_config"]["auth_type"] == "bearer"


def test_image_settings_source_normalizes_legacy_openai_base_url_without_v1():
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="https://api.example.com",
        IMAGES_OPENAI_API_KEY="image-key",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        OPENAI_API_BASE_URLS=[],
        OPENAI_API_KEYS=[],
        OPENAI_API_CONFIGS={},
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    source = _resolve_image_provider_source(
        request,
        user=None,
        provider="openai",
        context="settings",
    )

    assert source is not None
    assert source["base_url"] == "https://api.example.com/v1"
    assert source["api_config"] == {}


def test_sync_image_provider_config_state_persists_normalized_legacy_urls():
    class DummyConfig(SimpleNamespace):
        def __setattr__(self, key, value):
            super().__setattr__(key, value)

    cfg = DummyConfig(
        IMAGES_OPENAI_API_BASE_URL="https://api.example.com",
        IMAGES_OPENAI_API_KEY="image-key",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGES_GEMINI_API_BASE_URL="https://generativelanguage.googleapis.com",
        IMAGES_GEMINI_API_KEY="gemini-key",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        IMAGES_GROK_API_BASE_URL="https://api.x.ai",
        IMAGES_GROK_API_KEY="grok-key",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    _sync_image_provider_config_state(request)

    assert cfg.IMAGES_OPENAI_API_BASE_URL == "https://api.example.com/v1"
    assert cfg.IMAGES_OPENAI_API_FORCE_MODE is False
    assert cfg.IMAGES_GEMINI_API_BASE_URL == "https://generativelanguage.googleapis.com/v1beta"
    assert cfg.IMAGES_GEMINI_API_FORCE_MODE is False
    assert cfg.IMAGES_GROK_API_BASE_URL == "https://api.x.ai/v1"


def test_image_settings_update_accepts_partial_admin_payload_and_preserves_legacy_settings():
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="openai",
        ENABLE_IMAGE_PROMPT_GENERATION=True,
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="https://openai.example.com/v1",
        IMAGES_OPENAI_API_FORCE_MODE=True,
        IMAGES_OPENAI_API_KEY="openai-key",
        IMAGES_GEMINI_API_BASE_URL="https://gemini.example.com/v1beta",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        IMAGES_GEMINI_API_KEY="gemini-key",
        IMAGES_GROK_API_BASE_URL="https://grok.example.com/v1",
        IMAGES_GROK_API_KEY="grok-key",
        AUTOMATIC1111_BASE_URL="http://automatic1111",
        AUTOMATIC1111_API_AUTH="user:pass",
        AUTOMATIC1111_CFG_SCALE=7.0,
        AUTOMATIC1111_SAMPLER="Euler",
        AUTOMATIC1111_SCHEDULER="Automatic",
        COMFYUI_BASE_URL="http://comfyui",
        COMFYUI_API_KEY="comfy-key",
        COMFYUI_WORKFLOW="{}",
        COMFYUI_WORKFLOW_NODES=[{"type": "prompt"}],
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    response = asyncio.run(
        images_router.update_config(
            request,
            images_router.ConfigForm(enabled=False, shared_key_enabled=True),
            user=SimpleNamespace(id="admin"),
        )
    )

    assert response["enabled"] is False
    assert response["shared_key_enabled"] is True
    assert cfg.IMAGE_GENERATION_ENGINE == "openai"
    assert cfg.ENABLE_IMAGE_PROMPT_GENERATION is True
    assert cfg.IMAGES_OPENAI_API_BASE_URL == "https://openai.example.com/v1"
    assert cfg.IMAGES_OPENAI_API_FORCE_MODE is True
    assert cfg.IMAGES_OPENAI_API_KEY == "openai-key"
    assert cfg.COMFYUI_BASE_URL == "http://comfyui"
    assert cfg.AUTOMATIC1111_CFG_SCALE == 7.0


def test_image_config_update_accepts_filter_only_without_touching_model(monkeypatch):
    cfg = SimpleNamespace(
        IMAGE_GENERATION_MODEL="old-model",
        IMAGE_SIZE="1024x1024",
        IMAGE_ASPECT_RATIO="16:9",
        IMAGE_RESOLUTION="2k",
        IMAGE_STEPS=30,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    def fail_set_image_model(*_args, **_kwargs):
        raise AssertionError("set_image_model must not run when MODEL is omitted")

    monkeypatch.setattr(images_router, "set_image_model", fail_set_image_model)

    response = asyncio.run(
        images_router.update_image_config(
            request,
            images_router.ImageConfigForm(IMAGE_MODEL_FILTER_REGEX="gpt-image|imagen"),
            user=SimpleNamespace(id="admin"),
        )
    )

    assert response["MODEL"] == "old-model"
    assert response["IMAGE_SIZE"] == "1024x1024"
    assert response["IMAGE_ASPECT_RATIO"] == "16:9"
    assert response["IMAGE_RESOLUTION"] == "2k"
    assert response["IMAGE_STEPS"] == 30
    assert response["IMAGE_MODEL_FILTER_REGEX"] == "gpt-image|imagen"


def test_image_config_update_rejects_invalid_filter_regex():
    cfg = SimpleNamespace(
        IMAGE_GENERATION_MODEL="old-model",
        IMAGE_SIZE="1024x1024",
        IMAGE_ASPECT_RATIO="16:9",
        IMAGE_RESOLUTION="2k",
        IMAGE_STEPS=30,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    try:
        asyncio.run(
            images_router.update_image_config(
                request,
                images_router.ImageConfigForm(IMAGE_MODEL_FILTER_REGEX="["),
                user=SimpleNamespace(id="admin"),
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "invalid regex pattern" in str(exc.detail)
    else:
        raise AssertionError("invalid regex should be rejected")


def test_auto_runtime_source_matches_selected_model_across_personal_connections(monkeypatch):
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="https://shared.example.com/v1",
        IMAGES_OPENAI_API_KEY="shared-key",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        OPENAI_API_BASE_URLS=["https://shared.example.com/v1"],
        OPENAI_API_KEYS=["shared-key"],
        OPENAI_API_CONFIGS={},
        ENABLE_IMAGE_GENERATION_SHARED_KEY=True,
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "https://ark.cn-beijing.volces.com/api/v3",
            ],
            ["aliyun-key", "volc-key"],
            {},
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        if "volces.com" in source.get("base_url", ""):
            return [{"id": "doubao-seedream-4-5-251128"}]
        return [{"id": "wanx2.1-t2i-turbo"}]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    source, discovered_models = asyncio.run(
        _select_runtime_image_provider_source(
            request,
            user,
            "openai",
            selected_model="doubao-seedream-4-5-251128",
        )
    )

    assert source is not None
    assert source["effective_source"] == "personal"
    assert source["connection_index"] == 1
    assert discovered_models == [{"id": "doubao-seedream-4-5-251128"}]


def test_runtime_image_models_merge_all_available_sources(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_GENERATION_ENGINE="openai",
                    IMAGE_MODEL_FILTER_REGEX="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    sources = [
        {
            "provider": "openai",
            "effective_source": "personal",
            "base_url": "https://personal.example.com/v1",
            "connection_index": 2,
            "connection_name": "Cherry",
        },
        {
            "provider": "openai",
            "effective_source": "shared",
            "base_url": "https://shared.example.com/v1",
            "connection_index": None,
            "connection_name": "OpenAI",
        },
    ]

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        if source["effective_source"] == "shared":
            return [
                {
                    "id": "gpt-image-2",
                    "name": "gpt-image-2",
                    "selection_key": "shared-key",
                    "source": "shared",
                    "connection_name": "OpenAI",
                }
            ]
        return [
            {
                "id": "flux-kontext-pro",
                "name": "flux-kontext-pro",
                "selection_key": "personal-key",
                "source": "personal",
                "connection_name": "Cherry",
            }
        ]

    monkeypatch.setattr(images_router, "_list_image_provider_sources", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    models = asyncio.run(
        _discover_image_models(
            request,
            user,
            context="runtime",
            credential_source="auto",
        )
    )

    assert [model["id"] for model in models] == ["flux-kontext-pro", "gpt-image-2"]
    assert {model["source"] for model in models} == {"personal", "shared"}


def test_runtime_image_models_keep_duplicate_ids_with_distinct_selection_keys(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_GENERATION_ENGINE="openai",
                    IMAGE_MODEL_FILTER_REGEX="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    sources = [
        {
            "provider": "openai",
            "effective_source": "shared",
            "base_url": "https://shared.example.com/v1",
            "connection_index": None,
            "connection_name": "OpenAI",
        },
        {
            "provider": "openai",
            "effective_source": "personal",
            "base_url": "https://personal.example.com/v1",
            "connection_index": 1,
            "connection_name": "Cherry",
        },
    ]

    async def fake_discover(_request, _user, _engine, source):
        base_url = source["base_url"]
        return [
            {
                "id": "gpt-image-2",
                "name": "gpt-image-2",
                "selection_key": f"{base_url}::gpt-image-2",
                "source": source["effective_source"],
                "connection_name": source["connection_name"],
            }
        ]

    monkeypatch.setattr(images_router, "_list_image_provider_sources", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    models = asyncio.run(
        _discover_image_models(
            request,
            user,
            context="runtime",
            credential_source="auto",
        )
    )

    assert len(models) == 2
    assert [model["source"] for model in models] == ["shared", "personal"]
    assert len({model["selection_key"] for model in models}) == 2


def test_runtime_image_selection_key_selects_duplicate_model_connection(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_GENERATION_ENGINE="openai",
                    ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
                    IMAGES_OPENAI_API_BASE_URL="",
                    IMAGES_OPENAI_API_KEY="",
                    IMAGES_OPENAI_API_FORCE_MODE=False,
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    same_base_url = "https://relay.example.com/v1"

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [same_base_url, same_base_url],
            ["key-a", "key-b"],
            {
                "0": {"remark": "A"},
                "1": {"remark": "B"},
            },
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    sources = images_router._list_image_provider_sources(
        request,
        user,
        "openai",
        context="runtime",
        credential_source="auto",
    )
    selected_model = images_router._build_image_model_selection_key(
        "gpt-image-2",
        sources[1],
    )

    source, discovered_models = asyncio.run(
        _select_runtime_image_provider_source(
            request,
            user,
            "openai",
            selected_model=selected_model,
        )
    )

    assert source is not None
    assert source["connection_index"] == 1
    assert source["key"] == "key-b"
    assert discovered_models[0]["id"] == "gpt-image-2"


def test_runtime_image_models_keep_successful_sources_when_one_source_fails(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_GENERATION_ENGINE="openai",
                    IMAGE_MODEL_FILTER_REGEX="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    sources = [
        {
            "provider": "openai",
            "effective_source": "personal",
            "base_url": "https://bad.example.com/v1",
            "connection_index": 0,
            "connection_name": "Bad",
        },
        {
            "provider": "openai",
            "effective_source": "shared",
            "base_url": "https://shared.example.com/v1",
            "connection_index": None,
            "connection_name": "OpenAI",
        },
    ]

    async def fake_discover(_request, _user, _engine, source):
        if source["effective_source"] == "personal":
            raise HTTPException(status_code=400, detail="bad source")
        return [
            {
                "id": "gpt-image-2",
                "name": "gpt-image-2",
                "selection_key": "shared-key",
                "source": "shared",
                "connection_name": "OpenAI",
            }
        ]

    monkeypatch.setattr(images_router, "_list_image_provider_sources", lambda *_args, **_kwargs: sources)
    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    models = asyncio.run(
        _discover_image_models(
            request,
            user,
            context="runtime",
            credential_source="auto",
        )
    )

    assert [model["id"] for model in models] == ["gpt-image-2"]
    assert models[0]["source"] == "shared"


def test_runtime_image_models_regex_filter_still_applies_to_final_list():
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_MODEL_FILTER_REGEX="gpt-image|imagen"
                )
            )
        )
    )

    filtered = _apply_image_model_regex_filter(
        request,
        [
            {"id": "gpt-image-2"},
            {"id": "imagen-4-preview"},
            {"id": "flux-kontext-pro"},
        ],
    )

    assert [model["id"] for model in filtered] == ["gpt-image-2", "imagen-4-preview"]


def test_image_settings_context_still_uses_single_source(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_GENERATION_ENGINE="openai",
                    IMAGE_MODEL_FILTER_REGEX="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    resolved_source = {
        "provider": "openai",
        "effective_source": "settings",
        "base_url": "https://settings.example.com/v1",
        "connection_name": "OpenAI",
    }

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        assert source == resolved_source
        return [{"id": "gpt-image-2", "name": "gpt-image-2"}]

    monkeypatch.setattr(images_router, "_resolve_image_provider_source", lambda *_args, **_kwargs: resolved_source)
    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    models = asyncio.run(
        _discover_image_models(
            request,
            user,
            context="settings",
        )
    )

    assert models == [{"id": "gpt-image-2", "name": "gpt-image-2"}]


def test_grok_settings_source_uses_grok_shared_config():
    cfg = SimpleNamespace(
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        OPENAI_API_BASE_URLS=[],
        OPENAI_API_KEYS=[],
        OPENAI_API_CONFIGS={},
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        GEMINI_API_BASE_URLS=[],
        GEMINI_API_KEYS=[],
        GEMINI_API_CONFIGS={},
        IMAGES_GROK_API_BASE_URL="https://api.x.ai/v1",
        IMAGES_GROK_API_KEY="grok-key",
        GROK_API_BASE_URLS=["https://api.x.ai/v1"],
        GROK_API_KEYS=["grok-key"],
        GROK_API_CONFIGS={"0": {"auth_type": "bearer"}},
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))

    source = _resolve_image_provider_source(
        request,
        user=None,
        provider="grok",
        context="settings",
    )

    assert source is not None
    assert source["base_url"] == "https://api.x.ai/v1"
    assert source["key"] == "grok-key"


def test_openai_chat_image_returns_upstream_error_without_images_endpoint_fallback(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace(id="user-1")
    captured = {}

    monkeypatch.setattr(images_router, "_build_openai_image_headers", lambda *_args, **_kwargs: {})

    class FakeResponse:
        status_code = 429
        headers = {"content-type": "application/json"}

        async def aread(self):
            return b'{"error":{"message":"rate limited"}}'

    class FakeStream:
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *_args):
            return False

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            captured["stream_args"] = _args
            captured["stream_kwargs"] = _kwargs
            return FakeStream()

    async def fail_images_endpoint(*_args, **_kwargs):
        raise AssertionError("chat image generation must not fall back to /images/generations")

    monkeypatch.setattr(images_router.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(images_router, "_generate_via_openai_images_endpoint", fail_images_endpoint)

    try:
        asyncio.run(
            images_router._generate_via_openai_chat_image(
                request,
                user,
                model_id="relay-image-preview",
                prompt="draw a dog",
                source={
                    "base_url": "https://openrouter.ai/api/v1",
                    "key": "relay-key",
                    "api_config": {},
                },
            )
        )
        assert False, "expected upstream HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 429
    assert captured["stream_args"][1] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["stream_kwargs"]["json"]["stream"] is True


def test_openai_chat_image_uses_responses_api_when_enabled(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace(id="user-1")
    captured = {}

    monkeypatch.setattr(images_router, "_build_openai_image_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(images_router, "upload_image", lambda *_args, **_kwargs: "/api/v1/files/generated")

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        text = '{"output":[{"type":"image_generation_call","result":"YWJj"}],"usage":{"output_tokens":1}}'

        def json(self):
            return {
                "output": [{"type": "image_generation_call", "result": "YWJj"}],
                "usage": {"output_tokens": 1},
            }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, *args, **kwargs):
            captured["post_args"] = args
            captured["post_kwargs"] = kwargs
            return FakeResponse()

        def stream(self, *_args, **_kwargs):
            raise AssertionError("responses image generation must not use chat/completions stream")

    monkeypatch.setattr(images_router.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(
        images_router._generate_via_openai_chat_image(
            request,
            user,
            model_id="relay-image-preview",
            prompt="draw a dog",
            source={
                "base_url": "https://api.openai.com/v1",
                "key": "openai-key",
                "api_config": {"use_responses_api": True},
            },
        )
    )

    assert result[0]["url"] == "/api/v1/files/generated"
    assert result[0]["usage"]["output_tokens"] == 1
    assert captured["post_args"][0] == "https://api.openai.com/v1/responses"
    assert captured["post_kwargs"]["json"]["model"] == "relay-image-preview"
    assert captured["post_kwargs"]["json"]["stream"] is False
    assert captured["post_kwargs"]["json"]["tools"] == [{"type": "image_generation"}]


def test_chat_openai_dedicated_image_url_uses_image_edit_path(monkeypatch):
    same_base_url = "https://relay.example.com/v1"
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="openai",
        IMAGE_GENERATION_MODEL="",
        IMAGE_SIZE="auto",
        IMAGE_ASPECT_RATIO="1:1",
        IMAGE_RESOLUTION="1k",
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [same_base_url],
            ["key-a"],
            {"0": {"remark": "A", "use_responses_api": True}},
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    captured = {}

    async def fake_edits_endpoint(_request, _user, **kwargs):
        captured.update(kwargs)
        return [{"url": "/api/v1/files/generated"}]

    async def fail_chat_image(*_args, **_kwargs):
        raise AssertionError("dedicated image generation must not use chat/completions")

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)
    monkeypatch.setattr(images_router, "_generate_via_openai_chat_image", fail_chat_image)
    monkeypatch.setattr(images_router, "_generate_via_openai_image_edits_endpoint", fake_edits_endpoint)

    source = images_router._list_image_provider_sources(
        request,
        user,
        "openai",
        context="runtime",
        credential_source="auto",
    )[0]
    selected_model = images_router._build_image_model_selection_key(
        "gpt-image-2", source
    )

    result = asyncio.run(
        images_router.image_generations(
            request,
            images_router.GenerateImageForm(
                prompt="draw a dog",
                model=selected_model,
                image_url="/api/v1/files/source/content",
                chat_generation=True,
            ),
            user=user,
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["model_id"] == "gpt-image-2"
    assert captured["image_url"] == "/api/v1/files/source/content"
    assert captured["source"]["key"] == "key-a"
    assert captured["source"]["api_config"]["use_responses_api"] is True


def test_chat_openai_dedicated_image_without_reference_uses_image_generation_path(monkeypatch):
    same_base_url = "https://relay.example.com/v1"
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="openai",
        IMAGE_GENERATION_MODEL="",
        IMAGE_SIZE="auto",
        IMAGE_ASPECT_RATIO="1:1",
        IMAGE_RESOLUTION="1k",
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [same_base_url],
            ["key-a"],
            {"0": {"remark": "A", "use_responses_api": True}},
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    captured = {}

    async def fake_images_endpoint(_request, _user, **kwargs):
        captured.update(kwargs)
        return [{"url": "/api/v1/files/generated"}]

    async def fail_chat_image(*_args, **_kwargs):
        raise AssertionError("dedicated image generation must not use chat/completions")

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)
    monkeypatch.setattr(images_router, "_generate_via_openai_chat_image", fail_chat_image)
    monkeypatch.setattr(images_router, "_generate_via_openai_images_endpoint", fake_images_endpoint)

    source = images_router._list_image_provider_sources(
        request,
        user,
        "openai",
        context="runtime",
        credential_source="auto",
    )[0]
    selected_model = images_router._build_image_model_selection_key(
        "gpt-image-2", source
    )

    result = asyncio.run(
        images_router.image_generations(
            request,
            images_router.GenerateImageForm(
                prompt="draw a dog",
                model=selected_model,
                chat_generation=True,
            ),
            user=user,
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["model_id"] == "gpt-image-2"
    assert captured["source"]["key"] == "key-a"
    assert captured["source"]["api_config"]["use_responses_api"] is True


def test_openai_gpt_image_edit_payload_uses_single_image_without_streaming(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace(id="user-1", role="admin")
    captured = {}

    monkeypatch.setattr(images_router, "_build_openai_image_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        images_router,
        "_resolve_image_edit_input",
        lambda *_args, **_kwargs: ("image/jpeg", b"source-image"),
    )
    monkeypatch.setattr(images_router, "upload_image", lambda *_args, **_kwargs: "/api/v1/files/generated")

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {},
            "elapsed_ms": 10,
            "response_body": '{"data":[{"b64_json":"YWJj"}]}',
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)

    result = asyncio.run(
        images_router._generate_via_openai_image_edits_endpoint(
            request,
            user,
            model_id="gpt-image-2",
            prompt="draw a dog",
            image_url="/api/v1/files/source/content",
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://relay.example.com/v1",
                "key": "key-a",
                "api_config": {},
            },
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["request_kind"] == "multipart"
    assert captured["url"] == "https://relay.example.com/v1/images/edits"
    assert captured["files"][0]["field_name"] == "image"
    assert "stream" not in captured["form_fields"]
    assert "partial_images" not in captured["form_fields"]
    assert "response_format" not in captured["form_fields"]


def test_openai_compatible_dedicated_image_edit_payload_uses_single_image(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace(id="user-1", role="admin")
    captured = {}

    monkeypatch.setattr(images_router, "_build_openai_image_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        images_router,
        "_resolve_image_edit_input",
        lambda *_args, **_kwargs: ("image/png", b"source-image"),
    )
    monkeypatch.setattr(images_router, "upload_image", lambda *_args, **_kwargs: "/api/v1/files/generated")

    async def fake_send(**kwargs):
        captured.update(kwargs)
        return {
            "status": 200,
            "headers": {},
            "elapsed_ms": 10,
            "response_body": '{"data":[{"b64_json":"YWJj"}]}',
        }

    monkeypatch.setattr(images_router, "_send_openai_image_request", fake_send)

    result = asyncio.run(
        images_router._generate_via_openai_image_edits_endpoint(
            request,
            user,
            model_id="flux-kontext-pro",
            prompt="draw a dog",
            image_url="/api/v1/files/source/content",
            n=1,
            size=None,
            background=None,
            source={
                "base_url": "https://relay.example.com/v1",
                "key": "key-a",
                "api_config": {},
            },
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["files"][0]["field_name"] == "image"
    assert captured["form_fields"]["response_format"] == "b64_json"


def test_xai_generation_payload_only_uses_supported_fields(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace(id="user-1")
    captured_payloads = []

    monkeypatch.setattr(images_router, "_build_openai_image_headers", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(images_router, "upload_image", lambda *_args, **_kwargs: "/api/v1/files/generated")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"data": [{"b64_json": "YWJj" * 64}]}

    def fake_post(_url, json=None, headers=None, timeout=None, verify=None):
        captured_payloads.append(dict(json or {}))
        return FakeResponse()

    monkeypatch.setattr(images_router.requests, "post", fake_post)

    result = asyncio.run(
        _generate_via_xai_images(
            request,
            user,
            model_id="grok-imagine-image",
            prompt="health check",
            n=1,
            source={
                "base_url": "https://api.x.ai/v1",
                "key": "grok-key",
                "api_config": {},
            },
            aspect_ratio="16:9",
            resolution="2k",
            fallback_size="1024x1024",
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured_payloads == [
        {
            "model": "grok-imagine-image",
            "prompt": "health check",
            "n": 1,
            "response_format": "b64_json",
            "aspect_ratio": "16:9",
            "resolution": "2k",
        }
    ]


def test_image_generation_uses_selection_key_to_pick_duplicate_openai_connection(monkeypatch):
    same_base_url = "https://relay.example.com/v1"
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="openai",
        IMAGE_GENERATION_MODEL="",
        IMAGE_SIZE="auto",
        IMAGE_ASPECT_RATIO="1:1",
        IMAGE_RESOLUTION="1k",
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [same_base_url, same_base_url],
            ["key-a", "key-b"],
            {
                "0": {"remark": "A"},
                "1": {"remark": "B"},
            },
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    captured = {}

    async def fake_images_endpoint(_request, _user, **kwargs):
        captured.update(kwargs)
        return [{"url": "/api/v1/files/generated"}]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)
    monkeypatch.setattr(
        images_router,
        "_generate_via_openai_images_endpoint",
        fake_images_endpoint,
    )

    sources = images_router._list_image_provider_sources(
        request,
        user,
        "openai",
        context="runtime",
        credential_source="auto",
    )
    selected_model = images_router._build_image_model_selection_key(
        "gpt-image-2",
        sources[1],
    )

    result = asyncio.run(
        images_router.image_generations(
            request,
            images_router.GenerateImageForm(
                prompt="draw a dog",
                model=selected_model,
            ),
            user=user,
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["model_id"] == "gpt-image-2"
    assert captured["source"]["connection_index"] == 1
    assert captured["source"]["key"] == "key-b"


def test_chat_image_generation_does_not_inherit_global_image_size(monkeypatch):
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="openai",
        IMAGE_GENERATION_MODEL="",
        IMAGE_SIZE="900x1600",
        IMAGE_ASPECT_RATIO="1:1",
        IMAGE_RESOLUTION="1k",
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            ["https://relay.example.com/v1"],
            ["key-a"],
            {"0": {"remark": "A"}},
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    captured = {}

    async def fake_images_endpoint(_request, _user, **kwargs):
        captured.update(kwargs)
        return [{"url": "/api/v1/files/generated"}]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)
    monkeypatch.setattr(
        images_router,
        "_generate_via_openai_images_endpoint",
        fake_images_endpoint,
    )

    result = asyncio.run(
        images_router.image_generations(
            request,
            images_router.GenerateImageForm(
                prompt="draw a dog",
                model="gpt-image-2",
                chat_generation=True,
            ),
            user=user,
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["model_id"] == "gpt-image-2"
    assert captured["size"] is None


def test_chat_image_generation_uses_model_ref_to_pick_prefixed_openai_connection(monkeypatch):
    official_base_url = "https://api.openai.com/v1"
    relay_base_url = "https://relay.example.com/v1"
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="openai",
        IMAGE_GENERATION_MODEL="",
        IMAGE_SIZE="auto",
        IMAGE_ASPECT_RATIO="1:1",
        IMAGE_RESOLUTION="1k",
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [official_base_url, relay_base_url],
            ["official-key", "relay-key"],
            {
                "0": {"remark": "Official", "prefix_id": "00000000"},
                "1": {"remark": "computer", "prefix_id": "7ad57b3e"},
            },
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    captured = {}

    async def fake_edits_endpoint(_request, _user, **kwargs):
        captured.update(kwargs)
        return [{"url": "/api/v1/files/generated"}]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)
    monkeypatch.setattr(
        images_router,
        "_generate_via_openai_image_edits_endpoint",
        fake_edits_endpoint,
    )

    result = asyncio.run(
        images_router.image_generations(
            request,
            images_router.GenerateImageForm(
                prompt="draw a dog",
                model="gpt-image-2",
                model_ref={
                    "provider": "openai",
                    "source": "personal",
                    "connection_id": "7ad57b3e",
                },
                image_url="/api/v1/files/source/content",
                chat_generation=True,
            ),
            user=user,
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["model_id"] == "gpt-image-2"
    assert captured["image_url"] == "/api/v1/files/source/content"
    assert captured["source"]["connection_index"] == 1
    assert captured["source"]["base_url"] == relay_base_url
    assert captured["source"]["key"] == "relay-key"


def test_image_model_ref_with_legacy_index_does_not_guess_when_multiple_sources(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
                    IMAGES_OPENAI_API_BASE_URL="",
                    IMAGES_OPENAI_API_KEY="",
                    IMAGES_OPENAI_API_FORCE_MODE=False,
                    IMAGES_GEMINI_API_BASE_URL="",
                    IMAGES_GEMINI_API_FORCE_MODE=False,
                    IMAGES_GROK_API_BASE_URL="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            ["https://api.openai.com/v1", "https://relay.example.com/v1"],
            ["official-key", "relay-key"],
            {
                "0": {"remark": "Official", "prefix_id": "00000000"},
                "1": {"remark": "Relay", "prefix_id": "7ad57b3e"},
            },
        ),
    )

    source = images_router._select_runtime_image_provider_source_from_ref(
        request,
        user,
        "openai",
        {"provider": "openai", "source": "personal", "connection_index": 0},
        model_id="gpt-image-2",
    )

    assert source is None


def test_image_model_ref_with_legacy_index_uses_unique_configured_model(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
                    IMAGES_OPENAI_API_BASE_URL="",
                    IMAGES_OPENAI_API_KEY="",
                    IMAGES_OPENAI_API_FORCE_MODE=False,
                    IMAGES_GEMINI_API_BASE_URL="",
                    IMAGES_GEMINI_API_FORCE_MODE=False,
                    IMAGES_GROK_API_BASE_URL="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            ["https://api.openai.com/v1", "https://relay.example.com/v1"],
            ["official-key", "relay-key"],
            {
                "0": {
                    "remark": "Official",
                    "prefix_id": "00000000",
                    "model_ids": ["gpt-image-1"],
                },
                "1": {
                    "remark": "Relay",
                    "prefix_id": "7ad57b3e",
                    "model_ids": ["gpt-image-2"],
                },
            },
        ),
    )

    source = images_router._select_runtime_image_provider_source_from_ref(
        request,
        user,
        "openai",
        {"provider": "openai", "source": "personal", "connection_index": 0},
        model_id="gpt-image-2",
    )

    assert source is not None
    assert source["connection_index"] == 1
    assert source["key"] == "relay-key"


def test_runtime_image_plain_model_keeps_selecting_only_matching_openai_connection(monkeypatch):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    IMAGE_GENERATION_ENGINE="openai",
                    ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
                    IMAGES_OPENAI_API_BASE_URL="",
                    IMAGES_OPENAI_API_KEY="",
                    IMAGES_OPENAI_API_FORCE_MODE=False,
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    same_base_url = "https://relay.example.com/v1"

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            [same_base_url, same_base_url],
            ["key-a", "key-b"],
            {
                "0": {"remark": "A"},
                "1": {"remark": "B"},
            },
        ),
    )

    async def fake_discover(_request, _user, engine, source):
        assert engine == "openai"
        if source["connection_index"] == 0:
            return [
                images_router._build_image_model_entry(
                    model_id="gpt-image-1",
                    name="gpt-image-1",
                    generation_mode="openai_images",
                    detection_method="metadata",
                    supports_background=False,
                    supports_batch=True,
                    size_mode="exact",
                    text_output_supported=False,
                    source=source,
                )
            ]
        return [
            images_router._build_image_model_entry(
                model_id="gpt-image-2",
                name="gpt-image-2",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)

    source, discovered_models = asyncio.run(
        _select_runtime_image_provider_source(
            request,
            user,
            "openai",
            selected_model="gpt-image-2",
        )
    )

    assert source is not None
    assert source["connection_index"] == 1
    assert source["key"] == "key-b"
    assert discovered_models[0]["id"] == "gpt-image-2"


def test_gemini_named_image_model_on_openai_compatible_connection_uses_openai_source(monkeypatch):
    cfg = SimpleNamespace(
        ENABLE_IMAGE_GENERATION=True,
        IMAGE_GENERATION_ENGINE="gemini",
        IMAGE_GENERATION_MODEL="old-admin-default",
        IMAGE_SIZE="2048x2048",
        IMAGE_ASPECT_RATIO="16:9",
        IMAGE_RESOLUTION="2k",
        ENABLE_IMAGE_GENERATION_SHARED_KEY=False,
        IMAGES_OPENAI_API_BASE_URL="",
        IMAGES_OPENAI_API_KEY="",
        IMAGES_OPENAI_API_FORCE_MODE=False,
        IMAGES_GEMINI_API_BASE_URL="",
        IMAGES_GEMINI_API_KEY="",
        IMAGES_GEMINI_API_FORCE_MODE=False,
        IMAGES_GROK_API_BASE_URL="",
        IMAGES_GROK_API_KEY="",
        IMAGE_MODEL_FILTER_REGEX="",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg)))
    user = SimpleNamespace(id="user-1", role="admin")

    monkeypatch.setattr(
        images_router.openai_router,
        "_get_openai_user_config",
        lambda _user: (
            ["https://openai-compatible.example.com/v1"],
            ["openai-compatible-key"],
            {"0": {"remark": "OpenAI Compatible"}},
        ),
    )
    monkeypatch.setattr(
        images_router.gemini_router,
        "_get_gemini_user_config",
        lambda _user: ([], [], {}),
    )
    monkeypatch.setattr(
        images_router.grok_router,
        "_get_grok_user_config",
        lambda _user: ([], [], {}),
    )

    async def fake_discover(_request, _user, engine, source):
        if engine != "openai":
            return []
        return [
            images_router._build_image_model_entry(
                model_id="gemini-3.1-flash-image-preview",
                name="gemini-3.1-flash-image-preview",
                generation_mode="openai_images",
                detection_method="metadata",
                supports_background=False,
                supports_batch=True,
                size_mode="exact",
                text_output_supported=False,
                source=source,
            )
        ]

    captured = {}

    async def fake_images_endpoint(_request, _user, **kwargs):
        captured.update(kwargs)
        return [{"url": "/api/v1/files/generated"}]

    monkeypatch.setattr(images_router, "_discover_image_models_for_source", fake_discover)
    monkeypatch.setattr(
        images_router,
        "_generate_via_openai_images_endpoint",
        fake_images_endpoint,
    )

    source = images_router._list_image_provider_sources(
        request,
        user,
        "openai",
        context="runtime",
        credential_source="auto",
    )[0]
    selected_model = images_router._build_image_model_selection_key(
        "gemini-3.1-flash-image-preview",
        source,
    )

    result = asyncio.run(
        images_router.image_generations(
            request,
            images_router.GenerateImageForm(
                prompt="draw a cat",
                model=selected_model,
            ),
            user=user,
        )
    )

    assert result == [{"url": "/api/v1/files/generated"}]
    assert captured["model_id"] == "gemini-3.1-flash-image-preview"
    assert captured["source"]["provider"] == "openai"
    assert captured["source"]["key"] == "openai-compatible-key"
    assert captured["size"] is None
