import importlib
from typing import Any, Optional

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import DEVICE_TYPE, DOCKER
from open_webui.retrieval.utils import get_embedding_function, get_model_path
from open_webui.utils.optional_dependencies import (
    format_optional_dependency_error,
    require_module,
)
from open_webui.config import (
    RAG_EMBEDDING_MODEL_AUTO_UPDATE,
    RAG_EMBEDDING_MODEL_TRUST_REMOTE_CODE,
    RAG_RERANKING_MODEL_AUTO_UPDATE,
    RAG_RERANKING_MODEL_TRUST_REMOTE_CODE,
)


def _module_available(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def get_runtime_capabilities() -> dict[str, Any]:
    local_embedding_available = _module_available("sentence_transformers")
    local_reranking_available = _module_available("sentence_transformers")
    colbert_reranking_available = _module_available("colbert")
    playwright_available = _module_available("playwright")
    firecrawl_available = _module_available(
        "langchain_community.document_loaders.firecrawl"
    )

    return {
        "local_embedding_available": local_embedding_available,
        "local_reranking_available": local_reranking_available,
        "colbert_reranking_available": colbert_reranking_available,
        "playwright_available": playwright_available,
        "firecrawl_available": firecrawl_available,
        "messages": {
            "local_embedding": (
                None
                if local_embedding_available
                else format_optional_dependency_error(
                    feature="Local embedding models",
                    packages=["sentence-transformers", "transformers", "accelerate"],
                    install_profiles=["rag-local", "local-rag", "full"],
                )
            ),
            "local_reranking": (
                None
                if local_reranking_available
                else format_optional_dependency_error(
                    feature="Local reranking models",
                    packages=["sentence-transformers", "transformers", "accelerate"],
                    install_profiles=["rerank-local", "local-rag", "full"],
                )
            ),
            "colbert_reranking": (
                None
                if colbert_reranking_available
                else format_optional_dependency_error(
                    feature="Local ColBERT reranking",
                    packages=["colbert-ai"],
                    install_profiles=["rerank-local", "local-rag", "full"],
                )
            ),
            "playwright": (
                None
                if playwright_available
                else format_optional_dependency_error(
                    feature="Playwright web loader",
                    packages=["playwright"],
                    install_profiles=["web-playwright", "full"],
                )
            ),
            "firecrawl": (
                None
                if firecrawl_available
                else format_optional_dependency_error(
                    feature="Firecrawl web loader",
                    packages=["firecrawl-py"],
                    install_profiles=["web-playwright", "full"],
                )
            ),
        },
    }


def get_ef(
    engine: str,
    embedding_model: str,
    auto_update: bool = False,
):
    ef = None
    if embedding_model and engine == "":
        sentence_transformers = require_module(
            "sentence_transformers",
            feature="Local embedding models",
            packages=["sentence-transformers", "transformers", "accelerate"],
            install_profiles=["rag-local", "local-rag", "full"],
        )

        try:
            ef = sentence_transformers.SentenceTransformer(
                get_model_path(embedding_model, auto_update),
                device=DEVICE_TYPE,
                trust_remote_code=RAG_EMBEDDING_MODEL_TRUST_REMOTE_CODE,
            )
        except Exception as exc:
            raise Exception(ERROR_MESSAGES.DEFAULT(exc))

    return ef


def get_rf(
    reranking_engine: str = "local",
    reranking_model: Optional[str] = None,
    api_base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    auto_update: bool = False,
):
    rf = None
    if reranking_model:
        if reranking_engine == "jina":
            if not api_base_url:
                raise ValueError(
                    "RAG_RERANKING_API_BASE_URL is required when RAG_RERANKING_ENGINE=jina."
                )
            from open_webui.retrieval.models.jina import JinaReranker

            rf = JinaReranker(
                model=reranking_model,
                api_base_url=api_base_url,
                api_key=api_key,
            )
        elif reranking_engine in ["", "local"] and any(
            model in reranking_model for model in ["jinaai/jina-colbert-v2"]
        ):
            require_module(
                "colbert",
                feature="Local ColBERT reranking",
                packages=["colbert-ai"],
                install_profiles=["rerank-local", "local-rag", "full"],
            )
            try:
                from open_webui.retrieval.models.colbert import ColBERT

                rf = ColBERT(
                    get_model_path(reranking_model, auto_update),
                    env="docker" if DOCKER else None,
                )
            except Exception as exc:
                raise Exception(ERROR_MESSAGES.DEFAULT(exc))
        elif reranking_engine in ["", "local"]:
            sentence_transformers = require_module(
                "sentence_transformers",
                feature="Local reranking models",
                packages=["sentence-transformers", "transformers", "accelerate"],
                install_profiles=["rerank-local", "local-rag", "full"],
            )

            try:
                rf = sentence_transformers.CrossEncoder(
                    get_model_path(reranking_model, auto_update),
                    device=DEVICE_TYPE,
                    trust_remote_code=RAG_RERANKING_MODEL_TRUST_REMOTE_CODE,
                )
            except Exception:
                raise Exception(ERROR_MESSAGES.DEFAULT("CrossEncoder error"))
        else:
            raise ValueError(f"Unsupported reranking engine: {reranking_engine}")
    return rf


def reset_embedding_runtime(app) -> None:
    app.state.ef = None
    app.state._EMBEDDING_FUNCTION_IMPL = None


def reset_reranking_runtime(app) -> None:
    app.state.rf = None


def ensure_embedding_runtime(app):
    if getattr(app.state, "_EMBEDDING_FUNCTION_IMPL", None) is None:
        if app.state.config.RAG_EMBEDDING_ENGINE == "":
            app.state.ef = get_ef(
                app.state.config.RAG_EMBEDDING_ENGINE,
                app.state.config.RAG_EMBEDDING_MODEL,
                RAG_EMBEDDING_MODEL_AUTO_UPDATE,
            )

        app.state._EMBEDDING_FUNCTION_IMPL = get_embedding_function(
            app.state.config.RAG_EMBEDDING_ENGINE,
            app.state.config.RAG_EMBEDDING_MODEL,
            app.state.ef,
            (
                app.state.config.RAG_OPENAI_API_BASE_URL
                if app.state.config.RAG_EMBEDDING_ENGINE == "openai"
                else app.state.config.RAG_OLLAMA_BASE_URL
            ),
            (
                app.state.config.RAG_OPENAI_API_KEY
                if app.state.config.RAG_EMBEDDING_ENGINE == "openai"
                else app.state.config.RAG_OLLAMA_API_KEY
            ),
            app.state.config.RAG_EMBEDDING_BATCH_SIZE,
        )

    return app.state._EMBEDDING_FUNCTION_IMPL


def ensure_reranking_runtime(app):
    if not app.state.config.RAG_RERANKING_MODEL:
        app.state.rf = None
        return None

    if app.state.rf is None:
        app.state.rf = get_rf(
            app.state.config.RAG_RERANKING_ENGINE,
            app.state.config.RAG_RERANKING_MODEL,
            app.state.config.RAG_RERANKING_API_BASE_URL,
            app.state.config.RAG_RERANKING_API_KEY,
            RAG_RERANKING_MODEL_AUTO_UPDATE,
        )

    return app.state.rf
