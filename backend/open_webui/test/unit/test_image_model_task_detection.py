import asyncio
import pathlib
import sys
from types import SimpleNamespace


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.utils.task import is_dedicated_image_generation_model  # noqa: E402
from open_webui.utils import middleware  # noqa: E402


def test_image_model_detection_falls_back_to_visible_model_name_when_base_model_is_generic():
    assert is_dedicated_image_generation_model(
        {
            "id": "gpt-image-2",
            "name": "gpt-image-2 | 蓝钛AI",
            "info": {"base_model_id": "openai"},
        }
    )


def test_image_model_detection_uses_original_id_for_prefixed_connection_model():
    assert is_dedicated_image_generation_model(
        {
            "id": "d7f188cd.gpt-image-2",
            "name": "gpt-image-2 | OpenAI",
            "original_id": "gpt-image-2",
            "info": {"base_model_id": ""},
        }
    )


def test_image_model_detection_does_not_treat_vision_model_as_generation_model():
    assert not is_dedicated_image_generation_model(
        {
            "id": "gpt-4o-vision",
            "name": "gpt-4o vision",
            "info": {"base_model_id": "gpt-4o-vision"},
        }
    )


def test_image_model_detection_covers_gemini_3_image_preview_like_cherry_studio():
    assert is_dedicated_image_generation_model(
        {
            "id": "gemini-3.1-flash-image-preview",
            "name": "gemini-3.1-flash-image-preview | 星辰AI",
            "info": {"base_model_id": "google"},
        }
    )


def test_dedicated_image_model_uses_current_chat_model_before_admin_default():
    captured = {}

    async def fake_process_filter_functions(**kwargs):
        return kwargs["form_data"], {}

    async def fake_chat_image_generation_handler(request, form_data, extra_params, user):
        captured["image_generation_options"] = extra_params["__metadata__"].get(
            "image_generation_options"
        )
        return form_data

    original_process_filter_functions = middleware.process_filter_functions
    original_get_sorted_filters = middleware.get_sorted_filters
    original_chat_image_generation_handler = middleware.chat_image_generation_handler
    try:
        middleware.process_filter_functions = fake_process_filter_functions
        middleware.get_sorted_filters = lambda model: []
        middleware.chat_image_generation_handler = fake_chat_image_generation_handler

        request = SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=SimpleNamespace(
                        ENABLE_IMAGE_GENERATION=True,
                        USER_PERMISSIONS={},
                        TASK_MODEL="",
                        TASK_MODEL_EXTERNAL="",
                        IMAGE_GENERATION_MODEL="gemini-3-pro-image-preview",
                        ENABLE_WEB_SEARCH=False,
                        WEB_SEARCH_ENGINE="",
                        BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=False,
                        NATIVE_WEB_SEARCH_PROVIDER="",
                        ENABLE_NATIVE_WEB_SEARCH=False,
                    ),
                    MODELS={},
                )
            ),
            state=SimpleNamespace(),
        )
        user = SimpleNamespace(id="user-1", email="u@example.com", name="User", role="admin")
        metadata = {}
        form_data = {
            "model": "13eca07c.gemini-3.1-flash-image-preview",
            "messages": [{"role": "user", "content": "生成一张图"}],
            "features": {
                "image_generation_options": {
                    "size": "900x1600",
                    "image_size": "1K",
                    "negative_prompt": "low quality",
                    "unknown": "must be removed",
                }
            },
        }
        model = {
            "id": "13eca07c.gemini-3.1-flash-image-preview",
            "original_id": "gemini-3.1-flash-image-preview",
            "provider": "openai",
            "source": "personal",
            "connection_index": 1,
            "connection_id": "13eca07c",
        }

        asyncio.run(
            middleware.process_chat_payload(request, form_data, user, metadata, model)
        )
    finally:
        middleware.process_filter_functions = original_process_filter_functions
        middleware.get_sorted_filters = original_get_sorted_filters
        middleware.chat_image_generation_handler = original_chat_image_generation_handler

    assert captured["image_generation_options"]["model"] == "gemini-3.1-flash-image-preview"
    assert captured["image_generation_options"]["model_ref"] == {
        "provider": "openai",
        "source": "personal",
        "connection_index": 1,
        "connection_id": "13eca07c",
    }
    assert captured["image_generation_options"]["image_size"] == "1K"
    assert captured["image_generation_options"]["negative_prompt"] == "low quality"
    assert "size" not in captured["image_generation_options"]
    assert "unknown" not in captured["image_generation_options"]
