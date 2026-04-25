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


def test_image_size_auto_normalizes_to_no_exact_size():
    assert images_router._normalize_exact_image_size("auto") is None
    assert images_router._normalize_exact_image_size("") is None
    assert images_router._normalize_exact_image_size("1024x1536") == "1024x1536"


def test_image_size_auto_keeps_derived_dimensions_safe():
    assert images_router._size_to_aspect_ratio("auto") is None
    assert images_router._size_to_gemini_image_size("auto") is None


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


def test_volcengine_chat_image_falls_back_to_images_endpoint(monkeypatch):
    request = SimpleNamespace()
    user = SimpleNamespace(id="user-1")

    monkeypatch.setattr(images_router, "_build_openai_image_headers", lambda *_args, **_kwargs: {})

    class FakeResponse:
        status_code = 429

    monkeypatch.setattr(images_router.requests, "post", lambda *args, **kwargs: FakeResponse())

    async def fake_images_endpoint(_request, _user, **kwargs):
        assert kwargs["model_id"] == "doubao-seedream-4-5-251128"
        assert kwargs["size"] == "1024x1024"
        return [{"url": "/api/v1/files/fallback-image"}]

    monkeypatch.setattr(
        images_router,
        "_generate_via_openai_images_endpoint",
        fake_images_endpoint,
    )

    result = asyncio.run(
        images_router._generate_via_openai_chat_image(
            request,
            user,
            model_id="doubao-seedream-4-5-251128",
            prompt="draw a dog",
            n=1,
            size="1024x1024",
            background=None,
            source={
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                "key": "volc-key",
                "api_config": {},
            },
            model_meta={"text_output_supported": True},
        )
    )

    assert result == [{"url": "/api/v1/files/fallback-image"}]


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
