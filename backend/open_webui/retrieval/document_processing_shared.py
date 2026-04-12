from __future__ import annotations

import json
from typing import Any, Optional


FILE_PROCESSING_MODE_RETRIEVAL = "retrieval"
FILE_PROCESSING_MODE_FULL_CONTEXT = "full_context"
FILE_PROCESSING_MODE_NATIVE_FILE = "native_file"
FILE_PROCESSING_MODES = {
    FILE_PROCESSING_MODE_RETRIEVAL,
    FILE_PROCESSING_MODE_FULL_CONTEXT,
    FILE_PROCESSING_MODE_NATIVE_FILE,
}

DOCUMENT_PROVIDER_LOCAL_DEFAULT = "local_default"
DOCUMENT_PROVIDER_MINERU = "mineru"
DOCUMENT_PROVIDER_OPEN_MINERU = "open_mineru"
DOCUMENT_PROVIDER_DOC2X = "doc2x"
DOCUMENT_PROVIDER_PADDLEOCR = "paddleocr"
DOCUMENT_PROVIDER_MISTRAL = "mistral"
DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE = "azure_document_intelligence"
DOCUMENT_PROVIDERS = {
    DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    DOCUMENT_PROVIDER_MINERU,
    DOCUMENT_PROVIDER_OPEN_MINERU,
    DOCUMENT_PROVIDER_DOC2X,
    DOCUMENT_PROVIDER_PADDLEOCR,
    DOCUMENT_PROVIDER_MISTRAL,
    DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE,
}

DEFAULT_DOCUMENT_PROVIDER_CONFIGS = {
    DOCUMENT_PROVIDER_LOCAL_DEFAULT: {},
    DOCUMENT_PROVIDER_MINERU: {
        "api_base_url": "https://mineru.net",
        "api_key": "",
        "token": "",
        "model_version": "vlm",
        "language": "",
        "page_range": "",
        "enable_formula": True,
        "enable_table": True,
        "is_ocr": False,
        "poll_interval": 3,
        "timeout": 180,
    },
    DOCUMENT_PROVIDER_OPEN_MINERU: {
        "api_base_url": "https://mineru.net",
        "language": "",
        "page_range": "",
        "enable_formula": True,
        "enable_table": True,
        "is_ocr": False,
        "poll_interval": 3,
        "timeout": 180,
    },
    DOCUMENT_PROVIDER_DOC2X: {
        "api_base_url": "https://v2.doc2x.noedgeai.com",
        "api_key": "",
        "poll_interval": 3,
        "timeout": 180,
    },
    DOCUMENT_PROVIDER_PADDLEOCR: {
        "server_url": "",
        "api_key": "",
        "poll_interval": 2,
        "timeout": 120,
    },
    DOCUMENT_PROVIDER_MISTRAL: {
        "api_key": "",
    },
    DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE: {
        "endpoint": "",
        "key": "",
    },
}


def build_default_document_provider_configs() -> dict[str, dict[str, Any]]:
    return json.loads(json.dumps(DEFAULT_DOCUMENT_PROVIDER_CONFIGS))


def normalize_file_processing_mode(
    mode: Optional[str], fallback: str = FILE_PROCESSING_MODE_RETRIEVAL
) -> str:
    value = str(mode or "").strip().lower()
    if value in FILE_PROCESSING_MODES:
        return value
    if value in {"full", "entire_document", "entire"}:
        return FILE_PROCESSING_MODE_FULL_CONTEXT
    if value in {"native", "native_file_input"}:
        return FILE_PROCESSING_MODE_NATIVE_FILE
    return fallback


def normalize_document_provider(
    provider: Optional[str], fallback: str = DOCUMENT_PROVIDER_LOCAL_DEFAULT
) -> str:
    value = str(provider or "").strip().lower()
    if value in DOCUMENT_PROVIDERS:
        return value
    if value == "document_intelligence":
        return DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE
    return fallback


def resolve_document_provider_configs(value: Any) -> dict[str, dict[str, Any]]:
    base = build_default_document_provider_configs()
    if not isinstance(value, dict):
        return base

    for provider_name, config in value.items():
        normalized = normalize_document_provider(provider_name, provider_name)
        if normalized not in base:
            base[normalized] = {}
        if isinstance(config, dict):
            base[normalized].update(config)
    return base


def derive_document_provider_from_legacy_engine(engine: Optional[str]) -> str:
    normalized = str(engine or "").strip().lower()
    if normalized == "document_intelligence":
        return DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE
    return DOCUMENT_PROVIDER_LOCAL_DEFAULT


def should_extract_for_mode(mode: str) -> bool:
    return normalize_file_processing_mode(mode) != FILE_PROCESSING_MODE_NATIVE_FILE


def should_index_for_mode(mode: str) -> bool:
    return normalize_file_processing_mode(mode) == FILE_PROCESSING_MODE_RETRIEVAL
