import pathlib
import sys


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.utils.middleware import (  # noqa: E402
    WEB_SEARCH_MODE_HALO,
    WEB_SEARCH_MODE_NATIVE,
    WEB_SEARCH_MODE_OFF,
    _consume_stream_image_delta,
    _extract_stream_content_and_files,
    _get_builtin_web_tools_to_suppress,
    _has_nonempty_text_content,
    _has_visible_assistant_output,
    _has_visible_message_files,
    _merge_message_files,
    merge_message_files,
    normalize_message_files,
)


def test_extract_stream_content_and_files_handles_structured_image_parts():
    text, files = _extract_stream_content_and_files(
        [
            {"type": "output_text", "text": "caption"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}},
        ]
    )

    assert text == "caption"
    assert files == [{"type": "image", "url": "data:image/png;base64,abcd"}]


def test_extract_stream_content_and_files_strips_markdown_data_images_from_text():
    text, files = _extract_stream_content_and_files(
        "hello\n![Generated Image](data:image/png;base64,abcd)\nworld"
    )

    assert text == "hello\n\nworld"
    assert files == [{"type": "image", "url": "data:image/png;base64,abcd"}]


def test_consume_stream_image_delta_reassembles_final_image_file():
    pending_images = {}

    assert (
        _consume_stream_image_delta(
            pending_images,
            {
                "id": "img_1",
                "mime_type": "image/png",
                "data": "abcd",
                "final": False,
            },
        )
        is None
    )

    image_file = _consume_stream_image_delta(
        pending_images,
        {
            "id": "img_1",
            "mime_type": "image/png",
            "data": "efgh",
            "final": True,
        },
    )

    assert image_file == {
        "type": "image",
        "url": "data:image/png;base64,abcdefgh",
    }
    assert pending_images == {}


def test_has_visible_assistant_output_accepts_text_only():
    content_blocks = [{"type": "text", "content": "caption"}]

    assert _has_nonempty_text_content(content_blocks) is True
    assert _has_visible_assistant_output(content_blocks, []) is True


def test_has_visible_assistant_output_accepts_files_only():
    files = [{"type": "image", "url": "data:image/png;base64,abcd"}]

    assert _has_visible_message_files(files) is True
    assert _has_visible_assistant_output([{"type": "text", "content": ""}], files) is True


def test_has_visible_assistant_output_rejects_empty_text_and_files():
    assert _has_nonempty_text_content([{"type": "text", "content": "   "}]) is False
    assert _has_visible_message_files([]) is False
    assert _has_visible_message_files([{"type": "file", "id": "file_123"}]) is False
    assert _has_visible_assistant_output([{"type": "text", "content": ""}], []) is False


def test_merge_message_files_preserves_existing_non_image_files_and_deduplicates():
    merged = _merge_message_files(
        [
            {"type": "web_search_results", "url": "/tmp/search.json", "name": "search"},
            {"type": "image", "url": "data:image/png;base64,abcd"},
        ],
        [
            {"type": "image", "url": "data:image/png;base64,abcd"},
            {"type": "image", "url": "data:image/png;base64,efgh"},
        ],
    )

    assert merged == [
        {"type": "web_search_results", "url": "/tmp/search.json", "name": "search"},
        {"type": "image", "url": "data:image/png;base64,abcd"},
        {"type": "image", "url": "data:image/png;base64,efgh"},
    ]


def test_legacy_message_file_helper_aliases_remain_compatible():
    files = normalize_message_files(
        [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abcd"}},
            {"type": "image", "url": "data:image/png;base64,abcd"},
        ]
    )

    assert files == [{"type": "image", "url": "data:image/png;base64,abcd"}]
    assert merge_message_files(
        [{"type": "image", "url": "data:image/png;base64,abcd"}],
        [{"type": "image", "url": "data:image/png;base64,efgh"}],
    ) == [
        {"type": "image", "url": "data:image/png;base64,abcd"},
        {"type": "image", "url": "data:image/png;base64,efgh"},
    ]


def test_builtin_web_tools_suppression_matches_runtime_mode():
    assert _get_builtin_web_tools_to_suppress(WEB_SEARCH_MODE_OFF) == set()
    assert _get_builtin_web_tools_to_suppress(WEB_SEARCH_MODE_HALO) == {"search_web"}
    assert _get_builtin_web_tools_to_suppress(WEB_SEARCH_MODE_NATIVE) == {
        "search_web",
        "fetch_url",
        "fetch_url_rendered",
    }
