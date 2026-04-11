import pathlib
import sys

import pytest


pytest.importorskip("chromadb")

_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.retrieval.vector.dbs.chroma import ChromaClient  # noqa: E402


class _MissingCollectionError(Exception):
    pass


def test_has_collection_probes_target_collection_only():
    calls = []

    class _Client:
        def get_collection(self, name):
            calls.append(name)
            return object()

    chroma_client = ChromaClient.__new__(ChromaClient)
    chroma_client.client = _Client()

    assert chroma_client.has_collection("file-demo") is True
    assert calls == ["file-demo"]


def test_has_collection_returns_false_for_missing_collection():
    class _Client:
        def get_collection(self, name):
            raise _MissingCollectionError(f"Collection {name} not found")

    chroma_client = ChromaClient.__new__(ChromaClient)
    chroma_client.client = _Client()

    assert chroma_client.has_collection("file-demo") is False


def test_has_collection_treats_legacy_config_error_as_missing():
    class _Client:
        def get_collection(self, name):
            raise KeyError("_type")

    chroma_client = ChromaClient.__new__(ChromaClient)
    chroma_client.client = _Client()

    assert chroma_client.has_collection("file-demo") is False
