from __future__ import annotations

import os
import re
from typing import Any

from fastapi import HTTPException

from open_webui.utils.error_handling import extract_error_detail

ARCHIVE_EXTENSIONS = {
    "7z",
    "bz2",
    "gz",
    "rar",
    "tar",
    "tgz",
    "txz",
    "xz",
    "zip",
}

ARCHIVE_CONTENT_TYPES = {
    "application/gzip",
    "application/vnd.rar",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-gzip",
    "application/x-rar",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/x-xz",
    "application/x-zip",
    "application/x-zip-compressed",
    "application/zip",
    "multipart/x-zip",
}

_ENCODING_DETECTION_RE = re.compile(r"could not detect encoding for\s+(.+)", re.I)
_EMBEDDING_BATCH_RE = re.compile(
    r"Embedding generation failed for batch starting at index \d+:?\s*(.*)", re.I
)

_EMBEDDING_UNAUTHORIZED_PATTERNS = (
    "401 unauthorized",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "not authenticated",
    "authentication failed",
    "permission denied",
)

_EMBEDDING_CONNECTION_PATTERNS = (
    "connection refused",
    "temporary failure in name resolution",
    "name or service not known",
    "max retries exceeded",
    "failed to establish a new connection",
    "connection aborted",
    "connection reset",
    "read timed out",
    "connect timeout",
    "timed out",
    "timeout",
    "nodename nor servname provided",
)


def make_file_upload_diagnostic(
    code: str,
    *,
    title: str,
    message: str,
    hint: str,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "code": code,
        "title": title,
        "message": message,
        "hint": hint,
        "blocking": blocking,
    }


class FileUploadDiagnosticError(RuntimeError):
    def __init__(self, diagnostic: dict[str, Any]):
        self.diagnostic = normalize_file_upload_diagnostic(diagnostic)
        super().__init__(self.diagnostic["message"])


def normalize_file_upload_diagnostic(diagnostic: Any) -> dict[str, Any]:
    if not isinstance(diagnostic, dict):
        raise ValueError("diagnostic must be a dictionary")

    return {
        "code": str(diagnostic.get("code") or "file_processing_failed"),
        "title": str(diagnostic.get("title") or "File processing failed"),
        "message": str(diagnostic.get("message") or "File processing failed."),
        "hint": str(diagnostic.get("hint") or "Please try again later."),
        "blocking": bool(diagnostic.get("blocking", True)),
    }


def build_file_upload_error_detail(diagnostic: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_file_upload_diagnostic(diagnostic)
    return {
        "message": normalized["message"],
        "diagnostic": normalized,
    }


def is_archive_file(filename: str | None, content_type: str | None) -> bool:
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized_type in ARCHIVE_CONTENT_TYPES:
        return True

    if not filename:
        return False

    normalized_name = os.path.basename(filename).lower()
    if normalized_name.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return True

    parts = normalized_name.rsplit(".", 1)
    return len(parts) == 2 and parts[1] in ARCHIVE_EXTENSIONS


def make_archive_diagnostic(filename: str | None = None) -> dict[str, Any]:
    display_name = os.path.basename(filename) if filename else "this file"
    return make_file_upload_diagnostic(
        "unsupported_archive",
        title="Archive uploads are not supported directly.",
        message=(
            f'"{display_name}" is a compressed archive. Please extract the files inside '
            "and upload the extracted files instead."
        ),
        hint="Extract the archive first, then upload the document files inside.",
    )


def make_unsupported_binary_diagnostic(filename: str | None = None) -> dict[str, Any]:
    display_name = os.path.basename(filename) if filename else "This file"
    return make_file_upload_diagnostic(
        "unsupported_binary_file",
        title="This file format is not supported for parsing.",
        message=f"{display_name} cannot be parsed as a supported text or document file.",
        hint="Upload a supported text or document file instead.",
    )


def _get_embedding_hint(user: Any) -> str:
    if getattr(user, "role", None) == "admin":
        return (
            'Go to /settings/documents to configure an embedding model, '
            'or switch the default file processing mode to "Full Context" or "Native File".'
        )

    return (
        'Ask an administrator to configure document retrieval, or switch '
        'the default file processing mode to "Full Context" or "Native File" if you have admin access.'
    )


def _extract_existing_diagnostic(error: Any) -> dict[str, Any] | None:
    if isinstance(error, FileUploadDiagnosticError):
        return error.diagnostic

    if isinstance(error, HTTPException):
        return _extract_existing_diagnostic(error.detail)

    if isinstance(error, dict):
        diagnostic = error.get("diagnostic")
        if isinstance(diagnostic, dict):
            return normalize_file_upload_diagnostic(diagnostic)

    return None


def classify_file_upload_error(
    error: Any,
    *,
    filename: str | None = None,
    content_type: str | None = None,
    user: Any = None,
) -> dict[str, Any]:
    existing = _extract_existing_diagnostic(error)
    if existing is not None:
        return existing

    from open_webui.retrieval.utils import ChunkTooLargeError

    if isinstance(error, ChunkTooLargeError):
        is_admin = getattr(user, "role", None) == "admin"
        return make_file_upload_diagnostic(
            "embedding_chunk_too_large",
            title="Chunk exceeds embedding model limit",
            message=(
                "A single text chunk was rejected by the embedding service even after "
                "splitting the batch to one item. This usually means the chunk exceeds "
                "the embedding model's input token limit."
            ),
            hint=(
                "Go to Admin → Settings → Documents, reduce Chunk Size "
                "(and optionally Chunk Overlap), then re-upload this file."
                if is_admin
                else "Ask an administrator to reduce the document chunk size."
            ),
        )

    if is_archive_file(filename, content_type):
        return make_archive_diagnostic(filename)

    message = extract_error_detail(error) or "File processing failed."
    lowered = message.lower()

    if "no module named 'chardet'" in lowered:
        return make_file_upload_diagnostic(
            "missing_text_encoding_dependency",
            title="The server is missing a required text parsing dependency.",
            message=(
                "The server is missing the text encoding detection dependency needed "
                "to read this file."
            ),
            hint=(
                "Ask an administrator to update the document parsing dependencies, "
                "then try again."
            ),
        )

    encoding_match = _ENCODING_DETECTION_RE.search(message)
    if encoding_match:
        display_name = os.path.basename(encoding_match.group(1).strip().strip("\"'"))
        return make_file_upload_diagnostic(
            "unsupported_text_encoding",
            title="The file encoding could not be detected.",
            message=(
                f'"{display_name}" could not be read as a supported text document. '
                "It may be binary data, an archive, or use an unsupported encoding."
            ),
            hint="Upload a plain text or supported document file instead.",
        )

    batch_match = _EMBEDDING_BATCH_RE.search(message)
    embedding_detail = batch_match.group(1).strip() if batch_match else ""
    embedding_message = embedding_detail or message
    embedding_lower = embedding_message.lower()
    looks_like_embedding_error = any(
        token in embedding_lower
        for token in (
            "embedding",
            "embeddings",
            "sentence-transformers",
            "rag_embedding",
            "local embedding models",
        )
    ) or batch_match is not None

    if looks_like_embedding_error:
        if "optional dependencies" in embedding_lower or "local embedding models" in embedding_lower:
            return make_file_upload_diagnostic(
                "embedding_unavailable",
                title="No embedding model is available for document retrieval.",
                message=(
                    "The file was uploaded, but document retrieval is not configured "
                    "with a usable embedding model."
                ),
                hint=_get_embedding_hint(user),
            )

        if any(pattern in embedding_lower for pattern in _EMBEDDING_UNAUTHORIZED_PATTERNS):
            return make_file_upload_diagnostic(
                "embedding_provider_unauthorized",
                title="The embedding model service rejected the request.",
                message=(
                    "The file was uploaded, but the embedding model credentials or "
                    "authorization are invalid."
                ),
                hint=(
                    "Check the embedding model credentials and endpoint in "
                    "/settings/documents, then try again."
                    if getattr(user, "role", None) == "admin"
                    else "Ask an administrator to check the document retrieval credentials."
                ),
            )

        if any(pattern in embedding_lower for pattern in _EMBEDDING_CONNECTION_PATTERNS):
            return make_file_upload_diagnostic(
                "embedding_provider_unreachable",
                title="The embedding model service could not be reached.",
                message=(
                    "The file was uploaded, but the embedding model endpoint could not "
                    "be reached to build retrieval indexes."
                ),
                hint=(
                    "Check the embedding model endpoint or network connection in "
                    "/settings/documents, then try again."
                    if getattr(user, "role", None) == "admin"
                    else "Ask an administrator to check the document retrieval service endpoint."
                ),
            )

        return make_file_upload_diagnostic(
            "embedding_generation_failed",
            title="The file was uploaded, but indexing failed.",
            message=(
                "The system could not build retrieval indexes for this file because "
                "embedding generation failed."
            ),
            hint=_get_embedding_hint(user),
        )

    if "not supported" in lowered and "file" in lowered:
        return make_unsupported_binary_diagnostic(filename)

    if lowered.startswith("primary provider `") and "fallback provider `" in lowered:
        return make_file_upload_diagnostic(
            "document_provider_fallback_failed",
            title="Document provider fallback failed.",
            message=message,
            hint=(
                "Check the configured document provider, fallback parser, and upstream "
                "service response before trying again."
            ),
        )

    return make_file_upload_diagnostic(
        "file_processing_failed",
        title="File processing failed.",
        message="The file could not be parsed or indexed successfully.",
        hint="Please try again with a supported file, or contact an administrator.",
    )
