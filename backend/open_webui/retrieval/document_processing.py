from __future__ import annotations

import base64
import io
import json
import logging
import mimetypes
import os
import time
import zipfile
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from langchain_core.documents import Document

from open_webui.models.files import FileModel, Files
from open_webui.retrieval.document_processing_shared import (
    DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE,
    DOCUMENT_PROVIDER_DOC2X,
    DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    DOCUMENT_PROVIDER_MINERU,
    DOCUMENT_PROVIDER_MISTRAL,
    DOCUMENT_PROVIDER_OPEN_MINERU,
    DOCUMENT_PROVIDER_PADDLEOCR,
    FILE_PROCESSING_MODE_FULL_CONTEXT,
    FILE_PROCESSING_MODE_NATIVE_FILE,
    FILE_PROCESSING_MODE_RETRIEVAL,
    build_default_document_provider_configs,
    derive_document_provider_from_legacy_engine,
    normalize_document_provider,
    normalize_file_processing_mode,
    resolve_document_provider_configs,
    should_extract_for_mode,
    should_index_for_mode,
)
from open_webui.retrieval.loaders.main import Loader
from open_webui.retrieval.loaders.mistral import MistralLoader
from open_webui.storage.provider import Storage
from open_webui.utils.error_handling import extract_error_detail, read_requests_error_payload
from open_webui.utils.file_upload_diagnostics import (
    FileUploadDiagnosticError,
    classify_file_upload_error,
)

log = logging.getLogger(__name__)

STRICT_LOCAL_FALLBACK_PROVIDERS = {
    DOCUMENT_PROVIDER_DOC2X,
    DOCUMENT_PROVIDER_MINERU,
    DOCUMENT_PROVIDER_MISTRAL,
    DOCUMENT_PROVIDER_OPEN_MINERU,
    DOCUMENT_PROVIDER_PADDLEOCR,
}


def resolve_file_processing_mode_from_config(
    config: Any, override: Optional[str] = None
) -> str:
    if override is not None:
        return normalize_file_processing_mode(
            override,
            normalize_file_processing_mode(
                getattr(config, "FILE_PROCESSING_DEFAULT_MODE", None),
                FILE_PROCESSING_MODE_RETRIEVAL,
            ),
        )

    return normalize_file_processing_mode(
        getattr(config, "FILE_PROCESSING_DEFAULT_MODE", None),
        FILE_PROCESSING_MODE_RETRIEVAL,
    )


def get_file_effective_processing_mode(
    file_obj: Optional[FileModel],
    *,
    default_mode: str = FILE_PROCESSING_MODE_RETRIEVAL,
) -> str:
    if not file_obj:
        return normalize_file_processing_mode(None, default_mode)

    meta = file_obj.meta or {}
    return normalize_file_processing_mode(
        meta.get("resolved_processing_mode") or meta.get("processing_mode"),
        default_mode,
    )


def get_requested_processing_mode_for_file_item(
    file_item: Any,
    *,
    file_obj: Optional[FileModel] = None,
    default_mode: str = FILE_PROCESSING_MODE_RETRIEVAL,
) -> str:
    if isinstance(file_item, dict):
        if file_item.get("context") == "full":
            return FILE_PROCESSING_MODE_FULL_CONTEXT

        if file_item.get("processing_mode"):
            return normalize_file_processing_mode(
                file_item.get("processing_mode"), default_mode
            )

        nested_file = file_item.get("file")
        if isinstance(nested_file, dict):
            nested_meta = nested_file.get("meta") or {}
            if nested_meta.get("processing_mode") or nested_meta.get(
                "resolved_processing_mode"
            ):
                return normalize_file_processing_mode(
                    nested_meta.get("resolved_processing_mode")
                    or nested_meta.get("processing_mode"),
                    default_mode,
                )

    return get_file_effective_processing_mode(file_obj, default_mode=default_mode)


def is_native_file_processing_mode(mode: Optional[str]) -> bool:
    return normalize_file_processing_mode(mode) == FILE_PROCESSING_MODE_NATIVE_FILE


def provider_supports_file(
    provider: str, filename: str, content_type: Optional[str]
) -> bool:
    provider = normalize_document_provider(provider)
    ext = os.path.splitext(filename or "")[1].lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()

    if provider == DOCUMENT_PROVIDER_LOCAL_DEFAULT:
        return True
    if provider == DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE:
        return ext in {".pdf", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"} or mime in {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    if provider == DOCUMENT_PROVIDER_MISTRAL:
        return ext in {".pdf", ".png", ".jpg", ".jpeg", ".webp"} or mime.startswith(
            "image/"
        )
    if provider == DOCUMENT_PROVIDER_DOC2X:
        return ext in {".pdf", ".png", ".jpg", ".jpeg", ".webp"} or mime.startswith(
            "image/"
        )
    if provider == DOCUMENT_PROVIDER_PADDLEOCR:
        return ext in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp"} or mime.startswith(
            "image/"
        )
    if provider == DOCUMENT_PROVIDER_OPEN_MINERU:
        return ext in {".pdf", ".png", ".jpg", ".jpeg"} or mime in {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
        }
    if provider == DOCUMENT_PROVIDER_MINERU:
        return ext in {
            ".pdf",
            ".doc",
            ".docx",
            ".ppt",
            ".pptx",
            ".png",
            ".jpg",
            ".jpeg",
            ".html",
            ".htm",
        } or mime in {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/html",
            "image/png",
            "image/jpeg",
            "image/jpg",
        }
    return False


def build_processing_notice(
    mode: str,
    provider: str,
    fallback_provider: Optional[str] = None,
    reason: Optional[str] = None,
) -> Optional[str]:
    if fallback_provider:
        return f"{provider} 无法处理该文件，本次已回退到 {fallback_provider} 继续处理。"
    if reason:
        return f"{provider} 处理提醒：{reason}"
    return None


def _get_loader_for_provider(
    request: Any,
    provider: str,
    provider_config: dict[str, Any],
    *,
    force_local_engine: bool = False,
) -> Loader:
    engine = request.app.state.config.CONTENT_EXTRACTION_ENGINE
    if provider == DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE:
        engine = "document_intelligence"
    elif force_local_engine:
        engine = ""

    return Loader(
        engine=engine,
        user=getattr(request.state, "user", None),
        EXTERNAL_DOCUMENT_LOADER_URL=getattr(
            request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_URL", ""
        ),
        EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH=getattr(
            request.app.state.config,
            "EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH",
            False,
        ),
        EXTERNAL_DOCUMENT_LOADER_API_KEY=getattr(
            request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_API_KEY", ""
        ),
        TIKA_SERVER_URL=request.app.state.config.TIKA_SERVER_URL,
        DOCLING_SERVER_URL=request.app.state.config.DOCLING_SERVER_URL,
        DOCLING_API_KEY=getattr(request.app.state.config, "DOCLING_API_KEY", ""),
        DOCLING_PARAMS=getattr(request.app.state.config, "DOCLING_PARAMS", {}),
        DATALAB_MARKER_API_KEY=getattr(
            request.app.state.config, "DATALAB_MARKER_API_KEY", ""
        ),
        DATALAB_MARKER_API_BASE_URL=getattr(
            request.app.state.config, "DATALAB_MARKER_API_BASE_URL", ""
        ),
        DATALAB_MARKER_ADDITIONAL_CONFIG=getattr(
            request.app.state.config, "DATALAB_MARKER_ADDITIONAL_CONFIG", ""
        ),
        DATALAB_MARKER_USE_LLM=getattr(
            request.app.state.config, "DATALAB_MARKER_USE_LLM", False
        ),
        DATALAB_MARKER_SKIP_CACHE=getattr(
            request.app.state.config, "DATALAB_MARKER_SKIP_CACHE", False
        ),
        DATALAB_MARKER_FORCE_OCR=getattr(
            request.app.state.config, "DATALAB_MARKER_FORCE_OCR", False
        ),
        DATALAB_MARKER_PAGINATE=getattr(
            request.app.state.config, "DATALAB_MARKER_PAGINATE", False
        ),
        DATALAB_MARKER_STRIP_EXISTING_OCR=getattr(
            request.app.state.config, "DATALAB_MARKER_STRIP_EXISTING_OCR", False
        ),
        DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION=getattr(
            request.app.state.config, "DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION", False
        ),
        DATALAB_MARKER_FORMAT_LINES=getattr(
            request.app.state.config, "DATALAB_MARKER_FORMAT_LINES", False
        ),
        DATALAB_MARKER_OUTPUT_FORMAT=getattr(
            request.app.state.config, "DATALAB_MARKER_OUTPUT_FORMAT", "markdown"
        ),
        PDF_EXTRACT_IMAGES=request.app.state.config.PDF_EXTRACT_IMAGES,
        PDF_LOADING_MODE=getattr(request.app.state.config, "PDF_LOADING_MODE", ""),
        PDF_LOADER_MODE=getattr(request.app.state.config, "PDF_LOADING_MODE", "page"),
        DOCUMENT_INTELLIGENCE_ENDPOINT=provider_config.get("endpoint")
        or request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        DOCUMENT_INTELLIGENCE_KEY=provider_config.get("key")
        or request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
        DOCUMENT_INTELLIGENCE_MODEL=getattr(
            request.app.state.config, "DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout"
        ),
        MINERU_API_MODE=getattr(request.app.state.config, "MINERU_API_MODE", "local"),
        MINERU_API_URL=getattr(
            request.app.state.config, "MINERU_API_URL", "http://localhost:8000"
        ),
        MINERU_API_KEY=getattr(request.app.state.config, "MINERU_API_KEY", ""),
        MINERU_API_TIMEOUT=getattr(request.app.state.config, "MINERU_API_TIMEOUT", "300"),
        MINERU_PARAMS=getattr(request.app.state.config, "MINERU_PARAMS", {}),
        MISTRAL_OCR_API_BASE_URL=getattr(
            request.app.state.config, "MISTRAL_OCR_API_BASE_URL", "https://api.mistral.ai/v1"
        ),
        MISTRAL_OCR_API_KEY=provider_config.get("api_key")
        or request.app.state.config.MISTRAL_OCR_API_KEY,
    )


def _merge_document_metadata(
    file_obj: FileModel, docs: list[Document]
) -> list[Document]:
    return [
        Document(
            page_content=doc.page_content,
            metadata={
                **(doc.metadata or {}),
                **(file_obj.meta or {}),
                "name": file_obj.filename,
                "created_by": file_obj.user_id,
                "file_id": file_obj.id,
                "source": file_obj.filename,
            },
        )
        for doc in docs
    ]


def _merge_pdf_single_mode(request: Any, file_obj: FileModel, docs: list[Document]) -> list[Document]:
    if (
        request.app.state.config.PDF_LOADING_MODE == "single"
        and file_obj.filename.lower().endswith(".pdf")
        and len(docs) > 1
    ):
        merged_content = "\n\n".join(doc.page_content for doc in docs)
        return [Document(page_content=merged_content, metadata=docs[0].metadata)]
    return docs


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as file_handle:
        return file_handle.read()


def _requests_json(response: requests.Response) -> dict[str, Any]:
    if not response.ok:
        payload = read_requests_error_payload(response)
        detail = (
            extract_error_detail(payload)
            or extract_error_detail(getattr(response, "text", None))
            or response.reason
            or "Request failed."
        )
        raise RuntimeError(
            f"HTTP {response.status_code} {response.reason or ''}: {detail}".strip()
        )
    if not response.content:
        return {}
    return response.json()


def _raise_provider_api_error(payload: Any, provider_name: str) -> None:
    if not isinstance(payload, dict):
        return

    code = payload.get("code")
    if code in (None, 0, "0"):
        return

    msg = extract_error_detail(payload.get("msg")) or f"{provider_name} API returned an error."
    trace_id = extract_error_detail(payload.get("trace_id"))
    detail = f"{provider_name} API error {code}: {msg}"
    if trace_id:
        detail = f"{detail} (trace_id: {trace_id})"
    raise RuntimeError(detail)


def _download_text(url: str, headers: Optional[dict[str, str]] = None) -> str:
    response = requests.get(url, headers=headers or {}, timeout=120)
    response.raise_for_status()
    return response.text


def _download_bytes(url: str, headers: Optional[dict[str, str]] = None) -> bytes:
    response = requests.get(url, headers=headers or {}, timeout=120)
    response.raise_for_status()
    return response.content


def _get_nested_value(payload: Any, *paths: tuple[Any, ...]) -> Any:
    for path in paths:
        cur = payload
        valid = True
        for key in path:
            if isinstance(cur, dict) and key in cur:
                cur = cur[key]
            else:
                valid = False
                break
        if valid:
            return cur
    return None


def _coerce_timeout(config: dict[str, Any], default_value: int) -> int:
    try:
        return max(1, int(config.get("timeout", default_value)))
    except Exception:
        return default_value


def _coerce_poll_interval(config: dict[str, Any], default_value: int) -> int:
    try:
        return max(1, int(config.get("poll_interval", default_value)))
    except Exception:
        return default_value


def _extract_markdown_from_zip(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zip_file:
        preferred_names = [
            "full.md",
            "markdown/full.md",
            "output/full.md",
        ]
        for name in preferred_names:
            if name in zip_file.namelist():
                return zip_file.read(name).decode("utf-8", errors="ignore")

        for name in zip_file.namelist():
            if name.lower().endswith(".md"):
                return zip_file.read(name).decode("utf-8", errors="ignore")
            if name.lower().endswith(".txt"):
                return zip_file.read(name).decode("utf-8", errors="ignore")

    raise RuntimeError("No markdown content found in provider archive response.")


def _build_document_list(text: str, metadata: Optional[dict[str, Any]] = None) -> list[Document]:
    content = str(text or "").strip()
    if not content:
        raise RuntimeError("No text content found in provider response.")
    return [Document(page_content=content, metadata=metadata or {})]


@dataclass
class ExtractionResult:
    docs: list[Document]
    provider: str
    requested_provider: str
    notice: Optional[str] = None
    fallbacks: list[str] = field(default_factory=list)
    primary_provider_error: Optional[str] = None
    fallback_provider: Optional[str] = None
    fallback_reason: Optional[str] = None


def _stringify_provider_error(error: Any) -> str:
    detail = extract_error_detail(error)
    return detail or "Unknown document provider error."


def _should_use_strict_local_fallback(provider: str) -> bool:
    return provider in STRICT_LOCAL_FALLBACK_PROVIDERS


def _build_provider_chain_message(
    requested_provider: str,
    primary_provider_error: str,
    fallback_provider: str,
    fallback_error: Optional[str] = None,
) -> str:
    message = (
        f"Primary provider `{requested_provider}` failed: {primary_provider_error}. "
        f"Fallback provider `{fallback_provider}` was attempted."
    )
    if fallback_error:
        return f"{message} Fallback failed: {fallback_error}."
    return message


def _fallback_to_local_default(
    request: Any,
    file_obj: FileModel,
    *,
    requested_provider: str,
    provider_configs: dict[str, dict[str, Any]],
    primary_provider_error: str,
    fallback_reason: str,
) -> ExtractionResult:
    fallback_provider = DOCUMENT_PROVIDER_LOCAL_DEFAULT
    strict_local_only = _should_use_strict_local_fallback(requested_provider)
    log.warning(
        "Document extraction fallback requested: %s -> %s (strict_local_only=%s, reason=%s)",
        requested_provider,
        fallback_provider,
        strict_local_only,
        fallback_reason,
    )

    try:
        local_docs = _extract_docs_with_provider(
            request,
            file_obj,
            fallback_provider,
            provider_configs.get(fallback_provider, {}),
            strict_local_only=strict_local_only,
        )
    except Exception as fallback_exc:
        fallback_error = _stringify_provider_error(fallback_exc)
        log.error(
            "Document extraction chain failed: %s -> %s (primary_error=%s, fallback_error=%s)",
            requested_provider,
            fallback_provider,
            primary_provider_error,
            fallback_error,
        )
        raise RuntimeError(
            _build_provider_chain_message(
                requested_provider,
                primary_provider_error,
                fallback_provider,
                fallback_error=fallback_error,
            )
        ) from fallback_exc

    log.info(
        "Document extraction chain succeeded: %s -> %s",
        requested_provider,
        fallback_provider,
    )
    return ExtractionResult(
        docs=local_docs,
        provider=fallback_provider,
        requested_provider=requested_provider,
        notice=build_processing_notice(
            FILE_PROCESSING_MODE_FULL_CONTEXT,
            requested_provider,
            fallback_provider=fallback_provider,
            reason=fallback_reason,
        ),
        fallbacks=[requested_provider],
        primary_provider_error=primary_provider_error,
        fallback_provider=fallback_provider,
        fallback_reason=fallback_reason,
    )


class MinerULoader:
    def __init__(self, file_obj: FileModel, file_path: str, config: dict[str, Any]):
        self.file_obj = file_obj
        self.file_path = file_path
        self.config = config

    def load(self) -> list[Document]:
        api_key = str(self.config.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("MinerU API key is required.")

        base_url = str(self.config.get("api_base_url") or "https://mineru.net").rstrip("/")
        token = (
            str(self.config.get("token") or "").strip()
            or self.file_obj.user_id
            or "open-webui"
        )
        filename = self.file_obj.filename or "file"
        ext = os.path.splitext(filename)[1].lower()
        model_version = str(self.config.get("model_version") or "vlm").strip() or "vlm"
        if ext in {".html", ".htm"}:
            model_version = "MinerU-HTML"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "token": token,
        }
        payload: dict[str, Any] = {
            "files": [
                {
                    "name": filename,
                    "data_id": self.file_obj.id,
                }
            ],
            "model_version": model_version,
            "enable_formula": bool(self.config.get("enable_formula", True)),
            "enable_table": bool(self.config.get("enable_table", True)),
            "is_ocr": bool(self.config.get("is_ocr", False)),
        }
        if self.config.get("language"):
            payload["language"] = self.config["language"]
        if self.config.get("page_range"):
            payload["page_range"] = self.config["page_range"]

        response = requests.post(
            f"{base_url}/api/v4/file-urls/batch",
            headers=headers,
            json=payload,
            timeout=60,
        )
        data = _requests_json(response)
        _raise_provider_api_error(data, "MinerU")
        file_urls = _get_nested_value(data, ("data", "file_urls"), ("file_urls",)) or []
        if not file_urls:
            raise RuntimeError("MinerU did not return an upload URL.")

        first_file_url = file_urls[0]
        if isinstance(first_file_url, str):
            upload_url = first_file_url
        elif isinstance(first_file_url, dict):
            upload_url = first_file_url.get("url") or first_file_url.get("presigned_url")
        else:
            raise RuntimeError("MinerU returned an unsupported upload URL format.")

        batch_id = _get_nested_value(data, ("data", "batch_id"), ("batch_id",))
        if not upload_url or not batch_id:
            raise RuntimeError("MinerU batch creation response is incomplete.")

        requests.put(upload_url, data=_read_bytes(self.file_path), timeout=120).raise_for_status()

        poll_interval = _coerce_poll_interval(self.config, 3)
        timeout = _coerce_timeout(self.config, 180)
        deadline = time.time() + timeout

        result_item = None
        while time.time() < deadline:
            poll_response = requests.get(
                f"{base_url}/api/v4/extract-results/batch/{batch_id}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "token": token,
                },
                timeout=60,
            )
            poll_data = _requests_json(poll_response)
            _raise_provider_api_error(poll_data, "MinerU")
            results = (
                _get_nested_value(
                    poll_data,
                    ("data", "extract_result"),
                    ("data", "results"),
                    ("extract_result",),
                    ("results",),
                )
                or []
            )

            if isinstance(results, list):
                for candidate in results:
                    if str(candidate.get("data_id") or "") == self.file_obj.id:
                        result_item = candidate
                        break
                if result_item is None and len(results) == 1:
                    result_item = results[0]

            if result_item:
                state = str(
                    result_item.get("state")
                    or result_item.get("status")
                    or result_item.get("result_state")
                    or ""
                ).lower()
                if state in {"done", "success", "completed", "finish", "finished"}:
                    break
                if state in {"failed", "error"}:
                    raise RuntimeError(
                        str(
                            result_item.get("err_msg")
                            or result_item.get("message")
                            or result_item.get("error")
                            or "MinerU parsing failed."
                        )
                    )

            time.sleep(poll_interval)

        if not result_item:
            raise RuntimeError("Timed out while waiting for MinerU results.")

        archive_url = (
            result_item.get("full_zip_url")
            or result_item.get("zip_url")
            or result_item.get("result_zip_url")
        )
        if archive_url:
            markdown = _extract_markdown_from_zip(_download_bytes(archive_url))
            return _build_document_list(markdown)

        markdown_url = result_item.get("full_md_url") or result_item.get("markdown_url")
        if markdown_url:
            return _build_document_list(_download_text(markdown_url))

        markdown_content = result_item.get("full_md") or result_item.get("markdown")
        if markdown_content:
            return _build_document_list(markdown_content)

        raise RuntimeError("MinerU returned no usable markdown result.")


class OpenMinerULoader:
    def __init__(self, file_obj: FileModel, file_path: str, config: dict[str, Any]):
        self.file_obj = file_obj
        self.file_path = file_path
        self.config = config

    def load(self) -> list[Document]:
        base_url = str(self.config.get("api_base_url") or "https://mineru.net").rstrip("/")
        filename = self.file_obj.filename or "file"
        payload: dict[str, Any] = {
            "file_name": filename,
            "enable_formula": bool(self.config.get("enable_formula", True)),
            "enable_table": bool(self.config.get("enable_table", True)),
            "is_ocr": bool(self.config.get("is_ocr", False)),
        }
        if self.config.get("language"):
            payload["language"] = self.config["language"]
        if self.config.get("page_range"):
            payload["page_range"] = self.config["page_range"]

        response = requests.post(
            f"{base_url}/api/v1/agent/parse/file",
            json=payload,
            timeout=60,
        )
        data = _requests_json(response)
        _raise_provider_api_error(data, "Open MinerU")
        file_url = _get_nested_value(data, ("data", "file_url"), ("file_url",))
        task_id = _get_nested_value(data, ("data", "task_id"), ("task_id",))
        if not file_url or not task_id:
            raise RuntimeError("Open MinerU did not return upload information.")

        requests.put(file_url, data=_read_bytes(self.file_path), timeout=120).raise_for_status()

        poll_interval = _coerce_poll_interval(self.config, 3)
        timeout = _coerce_timeout(self.config, 180)
        deadline = time.time() + timeout
        result_payload: Optional[dict[str, Any]] = None

        while time.time() < deadline:
            poll_response = requests.get(
                f"{base_url}/api/v1/agent/parse/{task_id}",
                timeout=60,
            )
            poll_data = _requests_json(poll_response)
            _raise_provider_api_error(poll_data, "Open MinerU")
            status = str(
                _get_nested_value(poll_data, ("data", "status"), ("status",))
                or ""
            ).lower()
            if status in {"done", "success", "completed", "finish", "finished"}:
                result_payload = _get_nested_value(poll_data, ("data",), ()) or poll_data
                break
            if status in {"failed", "error"}:
                raise RuntimeError(
                    str(
                        _get_nested_value(
                            poll_data, ("data", "message"), ("message",), ("error",)
                        )
                        or "Open MinerU parsing failed."
                    )
                )
            time.sleep(poll_interval)

        if result_payload is None:
            raise RuntimeError("Timed out while waiting for Open MinerU results.")

        markdown_url = result_payload.get("markdown_url") or result_payload.get("md_url")
        if markdown_url:
            return _build_document_list(_download_text(markdown_url))

        markdown_content = result_payload.get("markdown") or result_payload.get("md_content")
        if markdown_content:
            return _build_document_list(markdown_content)

        raise RuntimeError("Open MinerU returned no markdown content.")


class Doc2XLoader:
    def __init__(self, file_obj: FileModel, file_path: str, config: dict[str, Any]):
        self.file_obj = file_obj
        self.file_path = file_path
        self.config = config

    def load(self) -> list[Document]:
        api_key = str(self.config.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Doc2X API key is required.")

        base_url = str(self.config.get("api_base_url") or "https://v2.doc2x.noedgeai.com").rstrip("/")
        ext = os.path.splitext(self.file_obj.filename or "")[1].lower()
        is_image = ext in {".png", ".jpg", ".jpeg", ".webp"}
        endpoint = "/api/v2/image" if is_image else "/api/v2/pdf"
        response = requests.post(
            f"{base_url}{endpoint}",
            headers={
                "Authorization": f"Bearer {api_key}",
            },
            timeout=60,
        )
        data = _requests_json(response)
        upload_url = _get_nested_value(data, ("data", "url"), ("url",))
        uid = _get_nested_value(data, ("data", "uid"), ("uid",))
        if not upload_url or not uid:
            raise RuntimeError("Doc2X did not return upload information.")

        requests.put(
            upload_url,
            data=_read_bytes(self.file_path),
            headers={"Content-Type": self.file_obj.meta.get("content_type") or "application/octet-stream"},
            timeout=120,
        ).raise_for_status()

        poll_interval = _coerce_poll_interval(self.config, 3)
        timeout = _coerce_timeout(self.config, 180)
        deadline = time.time() + timeout
        result_payload: Optional[dict[str, Any]] = None
        result_endpoint = "/api/v2/image/parse/status" if is_image else "/api/v2/pdf/parse/status"
        while time.time() < deadline:
            poll_response = requests.get(
                f"{base_url}{result_endpoint}",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"uid": uid},
                timeout=60,
            )
            poll_data = _requests_json(poll_response)
            status = str(
                _get_nested_value(
                    poll_data,
                    ("data", "status"),
                    ("status",),
                    ("data", "parse_status"),
                    ("parse_status",),
                )
                or ""
            ).lower()

            if status in {"success", "done", "completed", "finish", "finished"}:
                result_payload = _get_nested_value(poll_data, ("data",), ()) or poll_data
                break
            if status in {"failed", "error"}:
                raise RuntimeError(
                    str(
                        _get_nested_value(
                            poll_data, ("data", "message"), ("message",), ("error",)
                        )
                        or "Doc2X parsing failed."
                    )
                )
            time.sleep(poll_interval)

        if result_payload is None:
            raise RuntimeError("Timed out while waiting for Doc2X results.")

        pages = result_payload.get("pages") or []
        if isinstance(pages, list) and pages:
            text = "\n\n".join(
                str(page.get("md") or page.get("markdown") or "").strip()
                for page in pages
                if isinstance(page, dict)
            ).strip()
            if text:
                return _build_document_list(text)

        markdown = result_payload.get("md") or result_payload.get("markdown")
        if markdown:
            return _build_document_list(markdown)

        raise RuntimeError("Doc2X returned no markdown content.")


class PaddleOCRLoader:
    def __init__(self, file_obj: FileModel, file_path: str, config: dict[str, Any]):
        self.file_obj = file_obj
        self.file_path = file_path
        self.config = config

    def _build_headers(self) -> dict[str, str]:
        api_key = str(self.config.get("api_key") or "").strip()
        if not api_key:
            return {}

        if api_key.lower().startswith(("bearer ", "token ")):
            auth_value = api_key
        else:
            auth_value = f"token {api_key}"

        return {"Authorization": auth_value}

    def load(self) -> list[Document]:
        server_url = str(self.config.get("server_url") or "").strip()
        if not server_url:
            raise RuntimeError("PaddleOCR server URL is required.")

        mime = self.file_obj.meta.get("content_type") or mimetypes.guess_type(self.file_obj.filename or "")[0] or "application/octet-stream"
        ext = os.path.splitext(self.file_obj.filename or "")[1].lower()
        file_type = 0 if ext == ".pdf" or mime == "application/pdf" else 1
        payload = {
            "fileType": file_type,
            "file": base64.b64encode(_read_bytes(self.file_path)).decode("utf-8"),
        }
        response = requests.post(
            server_url,
            json=payload,
            headers=self._build_headers(),
            timeout=120,
        )
        data = _requests_json(response)

        results = (
            _get_nested_value(data, ("result",), ("data", "result"), ("results",)) or []
        )
        texts: list[str] = []
        for item in results if isinstance(results, list) else [results]:
            if not isinstance(item, dict):
                continue
            pruned = item.get("prunedResult") or item.get("result") or {}
            if isinstance(pruned, dict):
                rec_texts = pruned.get("rec_texts") or pruned.get("texts") or []
                if isinstance(rec_texts, list):
                    texts.extend(str(text).strip() for text in rec_texts if str(text).strip())

        if texts:
            return _build_document_list("\n".join(texts))

        raise RuntimeError("PaddleOCR returned no text content.")


def _extract_docs_with_provider(
    request: Any,
    file_obj: FileModel,
    provider: str,
    provider_config: dict[str, Any],
    *,
    strict_local_only: bool = False,
) -> list[Document]:
    file_path = Storage.get_file(file_obj.path)

    if provider in {
        DOCUMENT_PROVIDER_LOCAL_DEFAULT,
        DOCUMENT_PROVIDER_AZURE_DOCUMENT_INTELLIGENCE,
    }:
        loader = _get_loader_for_provider(
            request,
            provider,
            provider_config,
            force_local_engine=(strict_local_only and provider == DOCUMENT_PROVIDER_LOCAL_DEFAULT),
        )
        docs = loader.load(file_obj.filename, file_obj.meta.get("content_type"), file_path)
        docs = _merge_pdf_single_mode(request, file_obj, docs)
        return _merge_document_metadata(file_obj, docs)

    if provider == DOCUMENT_PROVIDER_MISTRAL:
        loader = MistralLoader(
            api_key=provider_config.get("api_key")
            or request.app.state.config.MISTRAL_OCR_API_KEY,
            file_path=file_path,
            mime_type=file_obj.meta.get("content_type"),
            base_url=getattr(
                request.app.state.config,
                "MISTRAL_OCR_API_BASE_URL",
                "https://api.mistral.ai/v1",
            ),
        )
        docs = loader.load()
        docs = _merge_pdf_single_mode(request, file_obj, docs)
        return _merge_document_metadata(file_obj, docs)

    if provider == DOCUMENT_PROVIDER_MINERU:
        docs = MinerULoader(file_obj, file_path, provider_config).load()
        return _merge_document_metadata(file_obj, docs)

    if provider == DOCUMENT_PROVIDER_OPEN_MINERU:
        docs = OpenMinerULoader(file_obj, file_path, provider_config).load()
        return _merge_document_metadata(file_obj, docs)

    if provider == DOCUMENT_PROVIDER_DOC2X:
        docs = Doc2XLoader(file_obj, file_path, provider_config).load()
        return _merge_document_metadata(file_obj, docs)

    if provider == DOCUMENT_PROVIDER_PADDLEOCR:
        docs = PaddleOCRLoader(file_obj, file_path, provider_config).load()
        return _merge_document_metadata(file_obj, docs)

    raise RuntimeError(f"Unsupported document provider: {provider}")


def extract_documents_for_file(
    request: Any,
    file_obj: FileModel,
    *,
    provider: Optional[str] = None,
    allow_local_fallback: bool = True,
) -> ExtractionResult:
    resolved_provider = normalize_document_provider(
        provider or getattr(request.app.state.config, "DOCUMENT_PROVIDER", None),
        DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    )
    provider_configs = resolve_document_provider_configs(
        getattr(request.app.state.config, "DOCUMENT_PROVIDER_CONFIGS", None)
    )
    provider_config = provider_configs.get(resolved_provider, {})
    mime = file_obj.meta.get("content_type") if file_obj.meta else None

    if not provider_supports_file(resolved_provider, file_obj.filename, mime):
        if resolved_provider == DOCUMENT_PROVIDER_LOCAL_DEFAULT or not allow_local_fallback:
            raise RuntimeError(f"{resolved_provider} does not support {file_obj.filename}.")
        primary_provider_error = (
            f"{resolved_provider} does not support {file_obj.filename}."
        )
        fallback_reason = f"{resolved_provider} 暂不支持 {file_obj.filename}"
        return _fallback_to_local_default(
            request,
            file_obj,
            requested_provider=resolved_provider,
            provider_configs=provider_configs,
            primary_provider_error=primary_provider_error,
            fallback_reason=fallback_reason,
        )

    try:
        docs = _extract_docs_with_provider(
            request,
            file_obj,
            resolved_provider,
            provider_config,
        )
        log.info(
            "Document extraction succeeded with provider=%s for filename=%s",
            resolved_provider,
            file_obj.filename,
        )
        return ExtractionResult(
            docs=docs,
            provider=resolved_provider,
            requested_provider=resolved_provider,
        )
    except Exception as exc:
        primary_provider_error = _stringify_provider_error(exc)
        if not allow_local_fallback or resolved_provider == DOCUMENT_PROVIDER_LOCAL_DEFAULT:
            raise FileUploadDiagnosticError(
                classify_file_upload_error(
                    exc,
                    filename=file_obj.filename,
                    content_type=mime,
                )
            ) from exc

        return _fallback_to_local_default(
            request,
            file_obj,
            requested_provider=resolved_provider,
            provider_configs=provider_configs,
            primary_provider_error=primary_provider_error,
            fallback_reason=primary_provider_error,
        )
