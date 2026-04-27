from __future__ import annotations

from copy import deepcopy
from typing import Any


CHAT_IMAGE_GENERATION_OPTION_KEYS = (
    "model",
    "model_ref",
    "image_size",
    "aspect_ratio",
    "resolution",
    "n",
    "negative_prompt",
    "credential_source",
    "connection_index",
    "steps",
    "background",
)

_IMAGE_GENERATION_OPTIONS_KEYS = ("image_generation_options", "imageGenerationOptions")


def sanitize_chat_image_generation_options(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for key in CHAT_IMAGE_GENERATION_OPTION_KEYS:
        if key not in value:
            continue

        option_value = value.get(key)
        if option_value is None or option_value == "":
            continue

        if key == "model_ref":
            if isinstance(option_value, dict) and option_value:
                cleaned[key] = deepcopy(option_value)
            continue

        cleaned[key] = deepcopy(option_value)

    return cleaned


def sanitize_chat_image_generation_options_in_mapping(mapping: Any) -> bool:
    if not isinstance(mapping, dict):
        return False

    changed = False
    for key in _IMAGE_GENERATION_OPTIONS_KEYS:
        if key not in mapping:
            continue

        original = mapping.get(key)
        cleaned = sanitize_chat_image_generation_options(original)
        if cleaned:
            if original != cleaned:
                mapping[key] = cleaned
                changed = True
        else:
            mapping.pop(key, None)
            changed = True

    return changed


def sanitize_chat_payload_image_generation_options(chat_payload: Any) -> tuple[Any, bool]:
    if not isinstance(chat_payload, dict):
        return chat_payload, False

    cleaned_payload = deepcopy(chat_payload)
    changed = sanitize_chat_image_generation_options_in_mapping(cleaned_payload)

    composer_state = cleaned_payload.get("composer_state")
    if isinstance(composer_state, dict):
        changed = sanitize_chat_image_generation_options_in_mapping(composer_state) or changed

    if not changed:
        return chat_payload, False

    return cleaned_payload, True
