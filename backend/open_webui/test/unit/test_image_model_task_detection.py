import pathlib
import sys


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.utils.task import is_dedicated_image_generation_model  # noqa: E402


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
