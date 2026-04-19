import pathlib
import sys


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.routers.files import _cleanup_failed_uploaded_file  # noqa: E402
from open_webui.utils.file_upload_diagnostics import (  # noqa: E402
    classify_file_upload_error,
    is_archive_file,
)


class _AdminUser:
    role = "admin"


def test_is_archive_file_detects_common_archive_types():
    assert is_archive_file("demo.rar", "application/octet-stream") is True
    assert is_archive_file("demo.txt", "application/x-rar-compressed") is True
    assert is_archive_file("demo.txt", "text/plain") is False


def test_classify_file_upload_error_returns_archive_diagnostic():
    diagnostic = classify_file_upload_error(
        None,
        filename="SHUN-logo-pack.rar",
        content_type="application/vnd.rar",
    )

    assert diagnostic["code"] == "unsupported_archive"
    assert diagnostic["blocking"] is True


def test_classify_file_upload_error_handles_missing_chardet_dependency():
    diagnostic = classify_file_upload_error(
        RuntimeError("No module named 'chardet'"),
        filename="notes.txt",
        content_type="text/plain",
    )

    assert diagnostic["code"] == "missing_text_encoding_dependency"


def test_classify_file_upload_error_handles_embedding_auth_failure():
    diagnostic = classify_file_upload_error(
        RuntimeError(
            "Embedding generation failed for batch starting at index 0: 401 Unauthorized"
        ),
        filename="report.html",
        content_type="text/html",
        user=_AdminUser(),
    )

    assert diagnostic["code"] == "embedding_provider_unauthorized"
    assert "/settings/documents" in diagnostic["hint"]


def test_classify_file_upload_error_handles_embedding_connection_failure():
    diagnostic = classify_file_upload_error(
        RuntimeError(
            "Embedding generation failed for batch starting at index 0: "
            "Failed to establish a new connection"
        ),
        filename="report.html",
        content_type="text/html",
    )

    assert diagnostic["code"] == "embedding_provider_unreachable"


def test_classify_file_upload_error_handles_embedding_timeout():
    diagnostic = classify_file_upload_error(
        RuntimeError(
            "Embedding generation failed for batch starting at index 0: read timed out"
        ),
        filename="report.html",
        content_type="text/html",
    )

    assert diagnostic["code"] == "embedding_provider_unreachable"


def test_classify_file_upload_error_preserves_provider_chain_failure_message():
    diagnostic = classify_file_upload_error(
        RuntimeError(
            "Primary provider `open_mineru` failed: upstream timeout. "
            "Fallback provider `local_default` was attempted. "
            "Fallback failed: unsupported file."
        ),
        filename="report.pdf",
        content_type="application/pdf",
    )

    assert diagnostic["code"] == "document_provider_fallback_failed"
    assert "open_mineru" in diagnostic["message"]
    assert "local_default" in diagnostic["message"]


def test_classify_file_upload_error_handles_chunk_too_large_admin():
    from open_webui.retrieval.utils import ChunkTooLargeError

    diagnostic = classify_file_upload_error(
        ChunkTooLargeError(
            "A single chunk was rejected by the embedding provider even at "
            "batch size 1, indicating the chunk exceeds the model's input "
            "token limit. Original: simulated"
        ),
        filename="paper.pdf",
        content_type="application/pdf",
        user=_AdminUser(),
    )

    assert diagnostic["code"] == "embedding_chunk_too_large"
    assert diagnostic["title"] == "Chunk exceeds embedding model limit"
    assert "Chunk Size" in diagnostic["hint"]
    # Defensive: ensure normalize_file_upload_diagnostic did not fall back to
    # the generic "Please try again later." placeholder.
    assert "Please try again later" not in diagnostic["hint"]


def test_classify_file_upload_error_handles_chunk_too_large_non_admin():
    from open_webui.retrieval.utils import ChunkTooLargeError

    diagnostic = classify_file_upload_error(
        ChunkTooLargeError("A single chunk was rejected..."),
        filename="paper.pdf",
        content_type="application/pdf",
        user=None,
    )

    assert diagnostic["code"] == "embedding_chunk_too_large"
    assert "administrator" in diagnostic["hint"].lower()
    assert "Please try again later" not in diagnostic["hint"]


def test_batch_split_recovers_from_batch_too_large():
    from open_webui.retrieval.utils import (
        BatchTooLargeError,
        _call_with_batch_split,
    )

    call_log: list[int] = []

    def flaky_call(texts):
        call_log.append(len(texts))
        if len(texts) > 2:
            raise BatchTooLargeError("simulated 413")
        return [f"vec-{t}" for t in texts]

    result = _call_with_batch_split(["a", "b", "c", "d"], flaky_call)

    assert result == ["vec-a", "vec-b", "vec-c", "vec-d"]
    # First attempt with 4 fails, then two halves of 2 succeed.
    assert call_log == [4, 2, 2]


def test_batch_split_terminates_at_size_one_with_chunk_too_large():
    import pytest
    from open_webui.retrieval.utils import (
        BatchTooLargeError,
        ChunkTooLargeError,
        _call_with_batch_split,
    )

    def always_reject(texts):
        raise BatchTooLargeError("simulated 413 always")

    with pytest.raises(ChunkTooLargeError) as excinfo:
        _call_with_batch_split(["a", "b", "c"], always_reject)

    assert "batch size 1" in str(excinfo.value)


def test_batch_split_does_not_trigger_on_other_errors():
    import pytest
    from open_webui.retrieval.utils import _call_with_batch_split

    call_count: list[int] = []

    def auth_failure(texts):
        call_count.append(1)
        raise RuntimeError("401 Unauthorized")

    with pytest.raises(RuntimeError, match="401"):
        _call_with_batch_split(["a", "b", "c"], auth_failure)

    # Must not split on non-batch-size errors: single call only.
    assert len(call_count) == 1


def test_cleanup_failed_uploaded_file_removes_collection_record_and_storage(monkeypatch):
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "open_webui.routers.files.VECTOR_DB_CLIENT.has_collection",
        lambda collection_name: collection_name == "file-file-123",
    )
    monkeypatch.setattr(
        "open_webui.routers.files.VECTOR_DB_CLIENT.delete_collection",
        lambda collection_name: events.append(("collection", collection_name)),
    )
    monkeypatch.setattr(
        "open_webui.routers.files.Files.delete_file_by_id",
        lambda file_id: events.append(("record", file_id)),
    )
    monkeypatch.setattr(
        "open_webui.routers.files.Storage.delete_file",
        lambda file_path: events.append(("storage", file_path)),
    )

    _cleanup_failed_uploaded_file("file-123", "/tmp/file-123_demo.txt")

    assert ("collection", "file-file-123") in events
    assert ("record", "file-123") in events
    assert ("storage", "/tmp/file-123_demo.txt") in events
