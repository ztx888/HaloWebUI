import pathlib
import sys


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.routers.openai import (  # noqa: E402
    _is_dashscope_compatible_connection,
    _looks_like_models_listing_unsupported,
    _normalize_openai_models_response,
)


def test_normalize_openai_models_response_accepts_models_array():
    normalized = _normalize_openai_models_response(
        {
            "models": [
                {"name": "qwen3-coder-plus"},
                "qwen3-coder-turbo",
            ]
        }
    )

    assert normalized == {
        "object": "list",
        "models": [
            {"name": "qwen3-coder-plus"},
            "qwen3-coder-turbo",
        ],
        "data": [
            {"name": "qwen3-coder-plus", "id": "qwen3-coder-plus", "object": "model"},
            {"id": "qwen3-coder-turbo", "object": "model"},
        ],
    }


def test_looks_like_models_listing_unsupported_accepts_empty_404():
    assert _looks_like_models_listing_unsupported(404, "") is True
    assert _looks_like_models_listing_unsupported(405, "<html>not found</html>") is True
    assert (
        _looks_like_models_listing_unsupported(
            400,
            {"error": {"message": "invalid request", "type": "invalid_request_error"}},
        )
        is False
    )


def test_is_dashscope_compatible_connection_matches_official_hosts_only():
    assert _is_dashscope_compatible_connection("https://dashscope.aliyuncs.com/compatible-mode/v1") is True
    assert _is_dashscope_compatible_connection("https://coding.dashscope.aliyuncs.com/v1") is True
    assert _is_dashscope_compatible_connection("https://dashscope.aliyuncs.com/v1") is False
    assert _is_dashscope_compatible_connection("https://api.openai.com/v1") is False
