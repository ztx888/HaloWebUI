import json
import logging
import mimetypes
import os
import shutil

import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, List, Literal, Optional, Sequence, Union
from urllib.parse import urlparse

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    Request,
    status,
    APIRouter,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
import tiktoken


from langchain.text_splitter import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
)
from langchain_core.documents import Document

from open_webui.models.files import FileModel, Files
from open_webui.models.knowledge import Knowledges
from open_webui.storage.provider import Storage


from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT

from open_webui.retrieval.loaders.youtube import YoutubeLoader

# Web search engines
from open_webui.retrieval.web.main import SearchResult
from open_webui.retrieval.web.brave import search_brave
from open_webui.retrieval.web.kagi import search_kagi
from open_webui.retrieval.web.mojeek import search_mojeek
from open_webui.retrieval.web.bocha import search_bocha
from open_webui.retrieval.web.duckduckgo import search_duckduckgo
from open_webui.retrieval.web.google_pse import search_google_pse
from open_webui.retrieval.web.jina_search import search_jina
from open_webui.retrieval.web.searchapi import search_searchapi
from open_webui.retrieval.web.serpapi import search_serpapi
from open_webui.retrieval.web.searxng import search_searxng
from open_webui.retrieval.web.serper import search_serper
from open_webui.retrieval.web.serply import search_serply
from open_webui.retrieval.web.serpstack import search_serpstack
from open_webui.retrieval.web.tavily import normalize_tavily_api_base_url, search_tavily
from open_webui.retrieval.web.bing import search_bing
from open_webui.retrieval.web.exa import search_exa
from open_webui.retrieval.web.perplexity import search_perplexity
from open_webui.retrieval.web.grok import search_grok
from open_webui.retrieval.web.sougou import search_sougou

from open_webui.retrieval.utils import (
    query_collection,
    query_collection_with_hybrid_search,
    query_doc,
    query_doc_with_hybrid_search,
)
from open_webui.retrieval.runtime import (
    get_safe_reranking_runtime,
    get_runtime_capabilities,
    reset_embedding_runtime,
    reset_reranking_runtime,
)
from open_webui.retrieval.document_processing import (
    DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    FILE_PROCESSING_MODE_FULL_CONTEXT,
    FILE_PROCESSING_MODE_NATIVE_FILE,
    FILE_PROCESSING_MODE_RETRIEVAL,
    build_processing_notice,
    extract_documents_for_file,
    get_file_effective_processing_mode,
    normalize_document_provider,
    normalize_file_processing_mode,
    resolve_document_provider_configs,
    resolve_file_processing_mode_from_config,
    should_extract_for_mode,
    should_index_for_mode,
)
from open_webui.utils.misc import (
    calculate_sha256_string,
)
from open_webui.utils.auth import get_admin_user, get_verified_user

from open_webui.config import (
    ENV,
    UPLOAD_DIR,
    DEFAULT_LOCALE,
    RAG_EMBEDDING_CONTENT_PREFIX,
    RAG_EMBEDDING_QUERY_PREFIX,
)
from open_webui.env import SRC_LOG_LEVELS
from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.file_upload_diagnostics import (
    build_file_upload_error_detail,
    classify_file_upload_error,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])

##########################################
#
# API routes
#
##########################################


router = APIRouter()


def _normalize_tavily_config_url(
    url: Optional[str],
    endpoint: Literal["search", "extract"],
    *,
    force_mode: bool = False,
) -> tuple[str, bool]:
    try:
        return normalize_tavily_api_base_url(
            url,
            endpoint,
            force_mode=force_mode,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


def _log_text_content_summary(
    context: str,
    *,
    text_content: Optional[str],
    collection_name: Optional[str] = None,
    processing_mode: Optional[str] = None,
    provider: Optional[str] = None,
) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return

    log.debug(
        "%s text_content_len=%d collection_name=%s processing_mode=%s provider=%s",
        context,
        len(text_content or ""),
        collection_name,
        processing_mode,
        provider,
    )


def _log_web_results_summary(engine: str, web_results: list[SearchResult]) -> None:
    if not log.isEnabledFor(logging.DEBUG):
        return

    log.debug("web_results_count=%d engine=%s", len(web_results), engine)


class CollectionNameForm(BaseModel):
    collection_name: Optional[str] = None


class ProcessUrlForm(CollectionNameForm):
    url: str


class SearchForm(BaseModel):
    query: str


@router.get("/")
async def get_status(request: Request):
    return {
        "status": True,
        "chunk_size": request.app.state.config.CHUNK_SIZE,
        "chunk_overlap": request.app.state.config.CHUNK_OVERLAP,
        "template": request.app.state.config.RAG_TEMPLATE,
        "embedding_engine": request.app.state.config.RAG_EMBEDDING_ENGINE,
        "embedding_model": request.app.state.config.RAG_EMBEDDING_MODEL,
        "reranking_engine": request.app.state.config.RAG_RERANKING_ENGINE,
        "reranking_model": request.app.state.config.RAG_RERANKING_MODEL,
        "embedding_batch_size": request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
        "embedding_concurrent_requests": request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
    }


@router.get("/embedding")
async def get_embedding_config(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "embedding_engine": request.app.state.config.RAG_EMBEDDING_ENGINE,
        "embedding_model": request.app.state.config.RAG_EMBEDDING_MODEL,
        "embedding_batch_size": request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
        "enable_async_embedding": getattr(
            request.app.state.config, "ENABLE_ASYNC_EMBEDDING", True
        ),
        "embedding_concurrent_requests": request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
        "openai_config": {
            "url": request.app.state.config.RAG_OPENAI_API_BASE_URL,
            "key": request.app.state.config.RAG_OPENAI_API_KEY,
        },
        "azure_openai_config": {
            "url": getattr(request.app.state.config, "RAG_AZURE_OPENAI_BASE_URL", ""),
            "key": getattr(request.app.state.config, "RAG_AZURE_OPENAI_API_KEY", ""),
            "version": getattr(request.app.state.config, "RAG_AZURE_OPENAI_API_VERSION", ""),
        },
        "ollama_config": {
            "url": request.app.state.config.RAG_OLLAMA_BASE_URL,
            "key": request.app.state.config.RAG_OLLAMA_API_KEY,
        },
    }


@router.get("/reranking")
async def get_reraanking_config(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "reranking_engine": request.app.state.config.RAG_RERANKING_ENGINE,
        "reranking_model": request.app.state.config.RAG_RERANKING_MODEL,
        "api_config": {
            "url": request.app.state.config.RAG_RERANKING_API_BASE_URL,
            "key": request.app.state.config.RAG_RERANKING_API_KEY,
            "timeout": request.app.state.config.RAG_RERANKING_TIMEOUT,
        },
    }


class OpenAIConfigForm(BaseModel):
    url: str
    key: str


class AzureOpenAIConfigForm(BaseModel):
    url: str
    key: str
    version: str


class OllamaConfigForm(BaseModel):
    url: str
    key: str


class RerankingAPIConfigForm(BaseModel):
    url: str
    key: str
    timeout: Optional[str] = None


class EmbeddingModelUpdateForm(BaseModel):
    openai_config: Optional[OpenAIConfigForm] = None
    azure_openai_config: Optional[AzureOpenAIConfigForm] = None
    ollama_config: Optional[OllamaConfigForm] = None
    embedding_engine: str
    embedding_model: str
    embedding_batch_size: Optional[int] = 1
    enable_async_embedding: Optional[bool] = None
    embedding_concurrent_requests: Optional[int] = None


@router.post("/embedding/update")
async def update_embedding_config(
    request: Request, form_data: EmbeddingModelUpdateForm, user=Depends(get_admin_user)
):
    log.info(
        f"Updating embedding model: {request.app.state.config.RAG_EMBEDDING_MODEL} to {form_data.embedding_model}"
    )
    try:
        request.app.state.config.RAG_EMBEDDING_ENGINE = form_data.embedding_engine
        request.app.state.config.RAG_EMBEDDING_MODEL = form_data.embedding_model

        if request.app.state.config.RAG_EMBEDDING_ENGINE in ["ollama", "openai", "azure_openai"]:
            if form_data.openai_config is not None:
                request.app.state.config.RAG_OPENAI_API_BASE_URL = (
                    form_data.openai_config.url
                )
                request.app.state.config.RAG_OPENAI_API_KEY = (
                    form_data.openai_config.key
                )

            if form_data.ollama_config is not None:
                request.app.state.config.RAG_OLLAMA_BASE_URL = (
                    form_data.ollama_config.url
                )
                request.app.state.config.RAG_OLLAMA_API_KEY = (
                    form_data.ollama_config.key
                )

            if form_data.azure_openai_config is not None:
                request.app.state.config.RAG_AZURE_OPENAI_BASE_URL = (
                    form_data.azure_openai_config.url
                )
                request.app.state.config.RAG_AZURE_OPENAI_API_KEY = (
                    form_data.azure_openai_config.key
                )
                request.app.state.config.RAG_AZURE_OPENAI_API_VERSION = (
                    form_data.azure_openai_config.version
                )

            request.app.state.config.RAG_EMBEDDING_BATCH_SIZE = (
                form_data.embedding_batch_size
            )
            if form_data.enable_async_embedding is not None:
                request.app.state.config.ENABLE_ASYNC_EMBEDDING = (
                    form_data.enable_async_embedding
                )
            if form_data.embedding_concurrent_requests is not None:
                request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS = (
                    form_data.embedding_concurrent_requests
                )

        reset_embedding_runtime(request.app)

        return {
            "status": True,
            "embedding_engine": request.app.state.config.RAG_EMBEDDING_ENGINE,
            "embedding_model": request.app.state.config.RAG_EMBEDDING_MODEL,
            "embedding_batch_size": request.app.state.config.RAG_EMBEDDING_BATCH_SIZE,
            "enable_async_embedding": getattr(
                request.app.state.config, "ENABLE_ASYNC_EMBEDDING", True
            ),
            "embedding_concurrent_requests": request.app.state.config.RAG_EMBEDDING_CONCURRENT_REQUESTS,
            "openai_config": {
                "url": request.app.state.config.RAG_OPENAI_API_BASE_URL,
                "key": request.app.state.config.RAG_OPENAI_API_KEY,
            },
            "azure_openai_config": {
                "url": getattr(request.app.state.config, "RAG_AZURE_OPENAI_BASE_URL", ""),
                "key": getattr(request.app.state.config, "RAG_AZURE_OPENAI_API_KEY", ""),
                "version": getattr(request.app.state.config, "RAG_AZURE_OPENAI_API_VERSION", ""),
            },
            "ollama_config": {
                "url": request.app.state.config.RAG_OLLAMA_BASE_URL,
                "key": request.app.state.config.RAG_OLLAMA_API_KEY,
            },
        }
    except Exception as e:
        log.exception(f"Problem updating embedding model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


class RerankingModelUpdateForm(BaseModel):
    reranking_engine: Optional[str] = None
    reranking_model: str
    api_config: Optional[RerankingAPIConfigForm] = None


@router.post("/reranking/update")
async def update_reranking_config(
    request: Request, form_data: RerankingModelUpdateForm, user=Depends(get_admin_user)
):
    log.info(
        f"Updating reranking model: {request.app.state.config.RAG_RERANKING_MODEL} to {form_data.reranking_model}"
    )
    try:
        request.app.state.config.RAG_RERANKING_ENGINE = (
            form_data.reranking_engine
            if form_data.reranking_engine is not None
            else request.app.state.config.RAG_RERANKING_ENGINE
        )
        request.app.state.config.RAG_RERANKING_MODEL = form_data.reranking_model
        if form_data.api_config is not None:
            request.app.state.config.RAG_RERANKING_API_BASE_URL = (
                form_data.api_config.url
            )
            request.app.state.config.RAG_RERANKING_API_KEY = form_data.api_config.key
            if form_data.api_config.timeout is not None:
                request.app.state.config.RAG_RERANKING_TIMEOUT = (
                    form_data.api_config.timeout
                )

        reset_reranking_runtime(request.app)

        return {
            "status": True,
            "reranking_engine": request.app.state.config.RAG_RERANKING_ENGINE,
            "reranking_model": request.app.state.config.RAG_RERANKING_MODEL,
            "api_config": {
                "url": request.app.state.config.RAG_RERANKING_API_BASE_URL,
                "key": request.app.state.config.RAG_RERANKING_API_KEY,
                "timeout": request.app.state.config.RAG_RERANKING_TIMEOUT,
            },
        }
    except Exception as e:
        log.exception(f"Problem updating reranking model: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.get("/config")
async def get_rag_config(request: Request, user=Depends(get_admin_user)):
    return {
        "status": True,
        "capabilities": get_runtime_capabilities(),
        # RAG settings
        "RAG_TEMPLATE": request.app.state.config.RAG_TEMPLATE,
        "RAG_SYSTEM_CONTEXT": request.app.state.config.RAG_SYSTEM_CONTEXT,
        "TOP_K": request.app.state.config.TOP_K,
        "BYPASS_EMBEDDING_AND_RETRIEVAL": request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL,
        "RAG_FULL_CONTEXT": request.app.state.config.RAG_FULL_CONTEXT,
        "FILE_PROCESSING_DEFAULT_MODE": request.app.state.config.FILE_PROCESSING_DEFAULT_MODE,
        # Hybrid search settings
        "ENABLE_RAG_HYBRID_SEARCH": request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
        "RAG_HYBRID_SEARCH_BM25_WEIGHT": request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT,
        "HYBRID_BM25_WEIGHT": getattr(
            request.app.state.config,
            "HYBRID_BM25_WEIGHT",
            request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT,
        ),
        "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS": getattr(
            request.app.state.config, "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS", False
        ),
        "TOP_K_RERANKER": request.app.state.config.TOP_K_RERANKER,
        "RELEVANCE_THRESHOLD": request.app.state.config.RELEVANCE_THRESHOLD,
        # Content extraction settings
        "DOCUMENT_PROVIDER": request.app.state.config.DOCUMENT_PROVIDER,
        "DOCUMENT_PROVIDER_CONFIGS": request.app.state.config.DOCUMENT_PROVIDER_CONFIGS,
        "CONTENT_EXTRACTION_ENGINE": request.app.state.config.CONTENT_EXTRACTION_ENGINE,
        "DATALAB_MARKER_API_KEY": getattr(request.app.state.config, "DATALAB_MARKER_API_KEY", ""),
        "DATALAB_MARKER_API_BASE_URL": getattr(
            request.app.state.config, "DATALAB_MARKER_API_BASE_URL", ""
        ),
        "DATALAB_MARKER_ADDITIONAL_CONFIG": getattr(
            request.app.state.config, "DATALAB_MARKER_ADDITIONAL_CONFIG", ""
        ),
        "DATALAB_MARKER_SKIP_CACHE": getattr(
            request.app.state.config, "DATALAB_MARKER_SKIP_CACHE", False
        ),
        "DATALAB_MARKER_FORCE_OCR": getattr(
            request.app.state.config, "DATALAB_MARKER_FORCE_OCR", False
        ),
        "DATALAB_MARKER_PAGINATE": getattr(
            request.app.state.config, "DATALAB_MARKER_PAGINATE", False
        ),
        "DATALAB_MARKER_STRIP_EXISTING_OCR": getattr(
            request.app.state.config, "DATALAB_MARKER_STRIP_EXISTING_OCR", False
        ),
        "DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION": getattr(
            request.app.state.config, "DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION", False
        ),
        "DATALAB_MARKER_FORMAT_LINES": getattr(
            request.app.state.config, "DATALAB_MARKER_FORMAT_LINES", False
        ),
        "DATALAB_MARKER_USE_LLM": getattr(
            request.app.state.config, "DATALAB_MARKER_USE_LLM", False
        ),
        "DATALAB_MARKER_OUTPUT_FORMAT": getattr(
            request.app.state.config, "DATALAB_MARKER_OUTPUT_FORMAT", "markdown"
        ),
        "EXTERNAL_DOCUMENT_LOADER_URL": getattr(
            request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_URL", ""
        ),
        "EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH": getattr(
            request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH", False
        ),
        "EXTERNAL_DOCUMENT_LOADER_API_KEY": getattr(
            request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_API_KEY", ""
        ),
        "PDF_EXTRACT_IMAGES": request.app.state.config.PDF_EXTRACT_IMAGES,
        "PDF_LOADING_MODE": request.app.state.config.PDF_LOADING_MODE,
        "PDF_LOADER_MODE": request.app.state.config.PDF_LOADING_MODE,
        "TIKA_SERVER_URL": request.app.state.config.TIKA_SERVER_URL,
        "DOCLING_SERVER_URL": request.app.state.config.DOCLING_SERVER_URL,
        "DOCLING_API_KEY": getattr(request.app.state.config, "DOCLING_API_KEY", ""),
        "DOCLING_PARAMS": getattr(request.app.state.config, "DOCLING_PARAMS", {}),
        "DOCUMENT_INTELLIGENCE_ENDPOINT": request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT,
        "DOCUMENT_INTELLIGENCE_KEY": request.app.state.config.DOCUMENT_INTELLIGENCE_KEY,
        "DOCUMENT_INTELLIGENCE_MODEL": getattr(
            request.app.state.config, "DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout"
        ),
        "MISTRAL_OCR_API_BASE_URL": getattr(
            request.app.state.config, "MISTRAL_OCR_API_BASE_URL", "https://api.mistral.ai/v1"
        ),
        "MISTRAL_OCR_API_KEY": request.app.state.config.MISTRAL_OCR_API_KEY,
        "MINERU_API_MODE": getattr(request.app.state.config, "MINERU_API_MODE", "local"),
        "MINERU_API_URL": getattr(request.app.state.config, "MINERU_API_URL", "http://localhost:8000"),
        "MINERU_API_KEY": getattr(request.app.state.config, "MINERU_API_KEY", ""),
        "MINERU_API_TIMEOUT": getattr(request.app.state.config, "MINERU_API_TIMEOUT", "300"),
        "MINERU_PARAMS": getattr(request.app.state.config, "MINERU_PARAMS", {}),
        # Chunking settings
        "TEXT_SPLITTER": request.app.state.config.TEXT_SPLITTER,
        "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER": getattr(
            request.app.state.config, "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER", False
        ),
        "CHUNK_SIZE": request.app.state.config.CHUNK_SIZE,
        "CHUNK_OVERLAP": request.app.state.config.CHUNK_OVERLAP,
        "CHUNK_MIN_SIZE": request.app.state.config.CHUNK_MIN_SIZE,
        "CHUNK_MIN_SIZE_TARGET": getattr(
            request.app.state.config, "CHUNK_MIN_SIZE_TARGET", 0
        ),
        # File upload settings
        "FILE_MAX_SIZE": request.app.state.config.FILE_MAX_SIZE,
        "FILE_MAX_COUNT": request.app.state.config.FILE_MAX_COUNT,
        "FILE_IMAGE_COMPRESSION_WIDTH": getattr(
            request.app.state.config, "FILE_IMAGE_COMPRESSION_WIDTH", None
        ),
        "FILE_IMAGE_COMPRESSION_HEIGHT": getattr(
            request.app.state.config, "FILE_IMAGE_COMPRESSION_HEIGHT", None
        ),
        "ALLOWED_FILE_EXTENSIONS": getattr(
            request.app.state.config, "ALLOWED_FILE_EXTENSIONS", []
        ),
        # Integration settings
        "ENABLE_GOOGLE_DRIVE_INTEGRATION": request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION,
        "ENABLE_ONEDRIVE_INTEGRATION": request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION,
        # Web search settings
        "web": {
            "ENABLE_WEB_SEARCH": request.app.state.config.ENABLE_WEB_SEARCH,
            "ENABLE_NATIVE_WEB_SEARCH": request.app.state.config.ENABLE_NATIVE_WEB_SEARCH,
            "WEB_SEARCH_ENGINE": request.app.state.config.WEB_SEARCH_ENGINE,
            "WEB_SEARCH_TRUST_ENV": request.app.state.config.WEB_SEARCH_TRUST_ENV,
            "WEB_SEARCH_RESULT_COUNT": request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            "WEB_SEARCH_CONCURRENT_REQUESTS": request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
            "WEB_SEARCH_DOMAIN_FILTER_LIST": request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            "BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL": request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL,
            "SEARXNG_QUERY_URL": request.app.state.config.SEARXNG_QUERY_URL,
            "GOOGLE_PSE_API_KEY": request.app.state.config.GOOGLE_PSE_API_KEY,
            "GOOGLE_PSE_ENGINE_ID": request.app.state.config.GOOGLE_PSE_ENGINE_ID,
            "BRAVE_SEARCH_API_KEY": request.app.state.config.BRAVE_SEARCH_API_KEY,
            "KAGI_SEARCH_API_KEY": request.app.state.config.KAGI_SEARCH_API_KEY,
            "MOJEEK_SEARCH_API_KEY": request.app.state.config.MOJEEK_SEARCH_API_KEY,
            "BOCHA_SEARCH_API_KEY": request.app.state.config.BOCHA_SEARCH_API_KEY,
            "SERPSTACK_API_KEY": request.app.state.config.SERPSTACK_API_KEY,
            "SERPSTACK_HTTPS": request.app.state.config.SERPSTACK_HTTPS,
            "SERPER_API_KEY": request.app.state.config.SERPER_API_KEY,
            "SERPLY_API_KEY": request.app.state.config.SERPLY_API_KEY,
            "TAVILY_API_KEY": request.app.state.config.TAVILY_API_KEY,
            "TAVILY_SEARCH_API_BASE_URL": request.app.state.config.TAVILY_SEARCH_API_BASE_URL,
            "TAVILY_SEARCH_API_FORCE_MODE": request.app.state.config.TAVILY_SEARCH_API_FORCE_MODE,
            "SEARCHAPI_API_KEY": request.app.state.config.SEARCHAPI_API_KEY,
            "SEARCHAPI_ENGINE": request.app.state.config.SEARCHAPI_ENGINE,
            "SERPAPI_API_KEY": request.app.state.config.SERPAPI_API_KEY,
            "SERPAPI_ENGINE": request.app.state.config.SERPAPI_ENGINE,
            "JINA_API_KEY": request.app.state.config.JINA_API_KEY,
            "BING_SEARCH_V7_ENDPOINT": request.app.state.config.BING_SEARCH_V7_ENDPOINT,
            "BING_SEARCH_V7_SUBSCRIPTION_KEY": request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            "EXA_API_KEY": request.app.state.config.EXA_API_KEY,
            "PERPLEXITY_API_KEY": request.app.state.config.PERPLEXITY_API_KEY,
            "GROK_API_KEY": request.app.state.config.GROK_API_KEY,
            "GROK_API_BASE_URL": request.app.state.config.GROK_API_BASE_URL,
            "GROK_API_MODEL": request.app.state.config.GROK_API_MODEL,
            "GROK_API_MODE": request.app.state.config.GROK_API_MODE,
            "SOUGOU_API_SID": request.app.state.config.SOUGOU_API_SID,
            "SOUGOU_API_SK": request.app.state.config.SOUGOU_API_SK,
            "WEB_LOADER_ENGINE": request.app.state.config.WEB_LOADER_ENGINE,
            "ENABLE_WEB_LOADER_SSL_VERIFICATION": request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            "PLAYWRIGHT_WS_URL": request.app.state.config.PLAYWRIGHT_WS_URL,
            "PLAYWRIGHT_TIMEOUT": request.app.state.config.PLAYWRIGHT_TIMEOUT,
            "FIRECRAWL_API_KEY": request.app.state.config.FIRECRAWL_API_KEY,
            "FIRECRAWL_API_BASE_URL": request.app.state.config.FIRECRAWL_API_BASE_URL,
            "TAVILY_EXTRACT_DEPTH": request.app.state.config.TAVILY_EXTRACT_DEPTH,
            "TAVILY_EXTRACT_API_BASE_URL": request.app.state.config.TAVILY_EXTRACT_API_BASE_URL,
            "TAVILY_EXTRACT_API_FORCE_MODE": request.app.state.config.TAVILY_EXTRACT_API_FORCE_MODE,
            "YOUTUBE_LOADER_LANGUAGE": request.app.state.config.YOUTUBE_LOADER_LANGUAGE,
            "YOUTUBE_LOADER_PROXY_URL": request.app.state.config.YOUTUBE_LOADER_PROXY_URL,
            "YOUTUBE_LOADER_TRANSLATION": request.app.state.YOUTUBE_LOADER_TRANSLATION,
            "DDGS_BACKEND": request.app.state.config.DDGS_BACKEND,
            "JINA_API_BASE_URL": request.app.state.config.JINA_API_BASE_URL,
            "FIRECRAWL_TIMEOUT": request.app.state.config.FIRECRAWL_TIMEOUT,
        },
    }


class WebConfig(BaseModel):
    ENABLE_WEB_SEARCH: Optional[bool] = None
    ENABLE_NATIVE_WEB_SEARCH: Optional[bool] = None
    WEB_SEARCH_ENGINE: Optional[str] = None
    WEB_SEARCH_TRUST_ENV: Optional[bool] = None
    WEB_SEARCH_RESULT_COUNT: Optional[int] = None
    WEB_SEARCH_CONCURRENT_REQUESTS: Optional[int] = None
    WEB_SEARCH_DOMAIN_FILTER_LIST: Optional[List[str]] = []
    BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL: Optional[bool] = None
    SEARXNG_QUERY_URL: Optional[str] = None
    GOOGLE_PSE_API_KEY: Optional[str] = None
    GOOGLE_PSE_ENGINE_ID: Optional[str] = None
    BRAVE_SEARCH_API_KEY: Optional[str] = None
    KAGI_SEARCH_API_KEY: Optional[str] = None
    MOJEEK_SEARCH_API_KEY: Optional[str] = None
    BOCHA_SEARCH_API_KEY: Optional[str] = None
    SERPSTACK_API_KEY: Optional[str] = None
    SERPSTACK_HTTPS: Optional[bool] = None
    SERPER_API_KEY: Optional[str] = None
    SERPLY_API_KEY: Optional[str] = None
    TAVILY_API_KEY: Optional[str] = None
    TAVILY_SEARCH_API_BASE_URL: Optional[str] = None
    TAVILY_SEARCH_API_FORCE_MODE: Optional[bool] = None
    SEARCHAPI_API_KEY: Optional[str] = None
    SEARCHAPI_ENGINE: Optional[str] = None
    SERPAPI_API_KEY: Optional[str] = None
    SERPAPI_ENGINE: Optional[str] = None
    JINA_API_KEY: Optional[str] = None
    BING_SEARCH_V7_ENDPOINT: Optional[str] = None
    BING_SEARCH_V7_SUBSCRIPTION_KEY: Optional[str] = None
    EXA_API_KEY: Optional[str] = None
    PERPLEXITY_API_KEY: Optional[str] = None
    GROK_API_KEY: Optional[str] = None
    GROK_API_BASE_URL: Optional[str] = None
    GROK_API_MODEL: Optional[str] = None
    GROK_API_MODE: Optional[str] = None
    SOUGOU_API_SID: Optional[str] = None
    SOUGOU_API_SK: Optional[str] = None
    WEB_LOADER_ENGINE: Optional[str] = None
    ENABLE_WEB_LOADER_SSL_VERIFICATION: Optional[bool] = None
    PLAYWRIGHT_WS_URL: Optional[str] = None
    PLAYWRIGHT_TIMEOUT: Optional[int] = None
    FIRECRAWL_API_KEY: Optional[str] = None
    FIRECRAWL_API_BASE_URL: Optional[str] = None
    TAVILY_EXTRACT_DEPTH: Optional[str] = None
    TAVILY_EXTRACT_API_BASE_URL: Optional[str] = None
    TAVILY_EXTRACT_API_FORCE_MODE: Optional[bool] = None
    YOUTUBE_LOADER_LANGUAGE: Optional[List[str]] = None
    YOUTUBE_LOADER_PROXY_URL: Optional[str] = None
    YOUTUBE_LOADER_TRANSLATION: Optional[str] = None
    DDGS_BACKEND: Optional[str] = None
    JINA_API_BASE_URL: Optional[str] = None
    FIRECRAWL_TIMEOUT: Optional[int] = None


class ConfigForm(BaseModel):
    # RAG settings
    RAG_TEMPLATE: Optional[str] = None
    RAG_SYSTEM_CONTEXT: Optional[str] = None
    TOP_K: Optional[int] = None
    BYPASS_EMBEDDING_AND_RETRIEVAL: Optional[bool] = None
    RAG_FULL_CONTEXT: Optional[bool] = None
    FILE_PROCESSING_DEFAULT_MODE: Optional[str] = None

    # Hybrid search settings
    ENABLE_RAG_HYBRID_SEARCH: Optional[bool] = None
    RAG_HYBRID_SEARCH_BM25_WEIGHT: Optional[float] = None
    HYBRID_BM25_WEIGHT: Optional[float] = None
    ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS: Optional[bool] = None
    TOP_K_RERANKER: Optional[int] = None
    RELEVANCE_THRESHOLD: Optional[float] = None

    # Content extraction settings
    DOCUMENT_PROVIDER: Optional[str] = None
    DOCUMENT_PROVIDER_CONFIGS: Optional[dict] = None
    CONTENT_EXTRACTION_ENGINE: Optional[str] = None
    DATALAB_MARKER_API_KEY: Optional[str] = None
    DATALAB_MARKER_API_BASE_URL: Optional[str] = None
    DATALAB_MARKER_ADDITIONAL_CONFIG: Optional[str] = None
    DATALAB_MARKER_SKIP_CACHE: Optional[bool] = None
    DATALAB_MARKER_FORCE_OCR: Optional[bool] = None
    DATALAB_MARKER_PAGINATE: Optional[bool] = None
    DATALAB_MARKER_STRIP_EXISTING_OCR: Optional[bool] = None
    DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION: Optional[bool] = None
    DATALAB_MARKER_FORMAT_LINES: Optional[bool] = None
    DATALAB_MARKER_USE_LLM: Optional[bool] = None
    DATALAB_MARKER_OUTPUT_FORMAT: Optional[str] = None
    EXTERNAL_DOCUMENT_LOADER_URL: Optional[str] = None
    EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH: Optional[bool] = None
    EXTERNAL_DOCUMENT_LOADER_API_KEY: Optional[str] = None
    PDF_EXTRACT_IMAGES: Optional[bool] = None
    PDF_LOADING_MODE: Optional[str] = None
    PDF_LOADER_MODE: Optional[str] = None
    TIKA_SERVER_URL: Optional[str] = None
    DOCLING_SERVER_URL: Optional[str] = None
    DOCLING_API_KEY: Optional[str] = None
    DOCLING_PARAMS: Optional[dict] = None
    DOCUMENT_INTELLIGENCE_ENDPOINT: Optional[str] = None
    DOCUMENT_INTELLIGENCE_KEY: Optional[str] = None
    DOCUMENT_INTELLIGENCE_MODEL: Optional[str] = None
    MISTRAL_OCR_API_BASE_URL: Optional[str] = None
    MISTRAL_OCR_API_KEY: Optional[str] = None
    MINERU_API_MODE: Optional[str] = None
    MINERU_API_URL: Optional[str] = None
    MINERU_API_KEY: Optional[str] = None
    MINERU_API_TIMEOUT: Optional[str] = None
    MINERU_PARAMS: Optional[dict] = None

    # Chunking settings
    TEXT_SPLITTER: Optional[str] = None
    ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER: Optional[bool] = None
    CHUNK_SIZE: Optional[int] = None
    CHUNK_OVERLAP: Optional[int] = None
    CHUNK_MIN_SIZE: Optional[int] = None
    CHUNK_MIN_SIZE_TARGET: Optional[int] = None

    # File upload settings
    FILE_MAX_SIZE: Optional[Union[int, str]] = None
    FILE_MAX_COUNT: Optional[Union[int, str]] = None
    FILE_IMAGE_COMPRESSION_WIDTH: Optional[Union[int, str]] = None
    FILE_IMAGE_COMPRESSION_HEIGHT: Optional[Union[int, str]] = None
    ALLOWED_FILE_EXTENSIONS: Optional[List[str]] = None

    # Integration settings
    ENABLE_GOOGLE_DRIVE_INTEGRATION: Optional[bool] = None
    ENABLE_ONEDRIVE_INTEGRATION: Optional[bool] = None

    # Web search settings
    web: Optional[WebConfig] = None


@router.post("/config/update")
async def update_rag_config(
    request: Request, form_data: ConfigForm, user=Depends(get_admin_user)
):
    # RAG settings
    request.app.state.config.RAG_TEMPLATE = (
        form_data.RAG_TEMPLATE
        if form_data.RAG_TEMPLATE is not None
        else request.app.state.config.RAG_TEMPLATE
    )
    request.app.state.config.RAG_SYSTEM_CONTEXT = (
        form_data.RAG_SYSTEM_CONTEXT
        if form_data.RAG_SYSTEM_CONTEXT is not None
        else request.app.state.config.RAG_SYSTEM_CONTEXT
    )
    request.app.state.config.TOP_K = (
        form_data.TOP_K
        if form_data.TOP_K is not None
        else request.app.state.config.TOP_K
    )
    if form_data.FILE_PROCESSING_DEFAULT_MODE is not None:
        request.app.state.config.FILE_PROCESSING_DEFAULT_MODE = normalize_file_processing_mode(
            form_data.FILE_PROCESSING_DEFAULT_MODE,
            request.app.state.config.FILE_PROCESSING_DEFAULT_MODE,
        )
    elif form_data.BYPASS_EMBEDDING_AND_RETRIEVAL is not None:
        request.app.state.config.FILE_PROCESSING_DEFAULT_MODE = (
            FILE_PROCESSING_MODE_FULL_CONTEXT
            if form_data.BYPASS_EMBEDDING_AND_RETRIEVAL
            else FILE_PROCESSING_MODE_RETRIEVAL
        )

    request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL = (
        request.app.state.config.FILE_PROCESSING_DEFAULT_MODE
        == FILE_PROCESSING_MODE_FULL_CONTEXT
    )
    request.app.state.config.RAG_FULL_CONTEXT = (
        form_data.RAG_FULL_CONTEXT
        if form_data.RAG_FULL_CONTEXT is not None
        else request.app.state.config.RAG_FULL_CONTEXT
    )

    # Hybrid search settings
    request.app.state.config.ENABLE_RAG_HYBRID_SEARCH = (
        form_data.ENABLE_RAG_HYBRID_SEARCH
        if form_data.ENABLE_RAG_HYBRID_SEARCH is not None
        else request.app.state.config.ENABLE_RAG_HYBRID_SEARCH
    )
    request.app.state.config.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS = (
        form_data.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS
        if form_data.ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS is not None
        else getattr(
            request.app.state.config, "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS", False
        )
    )
    # Free up memory if hybrid search is disabled
    if not request.app.state.config.ENABLE_RAG_HYBRID_SEARCH:
        reset_reranking_runtime(request.app)

    request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT = (
        form_data.RAG_HYBRID_SEARCH_BM25_WEIGHT
        if form_data.RAG_HYBRID_SEARCH_BM25_WEIGHT is not None
        else (
            form_data.HYBRID_BM25_WEIGHT
            if form_data.HYBRID_BM25_WEIGHT is not None
            else request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT
        )
    )
    request.app.state.config.HYBRID_BM25_WEIGHT = (
        request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT
        if request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT is not None
        else request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT
    )

    request.app.state.config.TOP_K_RERANKER = (
        form_data.TOP_K_RERANKER
        if form_data.TOP_K_RERANKER is not None
        else request.app.state.config.TOP_K_RERANKER
    )
    request.app.state.config.RELEVANCE_THRESHOLD = (
        form_data.RELEVANCE_THRESHOLD
        if form_data.RELEVANCE_THRESHOLD is not None
        else request.app.state.config.RELEVANCE_THRESHOLD
    )

    # Content extraction settings
    if form_data.DOCUMENT_PROVIDER is not None:
        request.app.state.config.DOCUMENT_PROVIDER = normalize_document_provider(
            form_data.DOCUMENT_PROVIDER,
            request.app.state.config.DOCUMENT_PROVIDER,
        )
    if form_data.DOCUMENT_PROVIDER_CONFIGS is not None:
        request.app.state.config.DOCUMENT_PROVIDER_CONFIGS = (
            resolve_document_provider_configs(form_data.DOCUMENT_PROVIDER_CONFIGS)
        )
    request.app.state.config.CONTENT_EXTRACTION_ENGINE = (
        form_data.CONTENT_EXTRACTION_ENGINE
        if form_data.CONTENT_EXTRACTION_ENGINE is not None
        else request.app.state.config.CONTENT_EXTRACTION_ENGINE
    )
    request.app.state.config.DATALAB_MARKER_API_KEY = (
        form_data.DATALAB_MARKER_API_KEY
        if form_data.DATALAB_MARKER_API_KEY is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_API_KEY", "")
    )
    request.app.state.config.DATALAB_MARKER_API_BASE_URL = (
        form_data.DATALAB_MARKER_API_BASE_URL
        if form_data.DATALAB_MARKER_API_BASE_URL is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_API_BASE_URL", "")
    )
    request.app.state.config.DATALAB_MARKER_ADDITIONAL_CONFIG = (
        form_data.DATALAB_MARKER_ADDITIONAL_CONFIG
        if form_data.DATALAB_MARKER_ADDITIONAL_CONFIG is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_ADDITIONAL_CONFIG", "")
    )
    request.app.state.config.DATALAB_MARKER_SKIP_CACHE = (
        form_data.DATALAB_MARKER_SKIP_CACHE
        if form_data.DATALAB_MARKER_SKIP_CACHE is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_SKIP_CACHE", False)
    )
    request.app.state.config.DATALAB_MARKER_FORCE_OCR = (
        form_data.DATALAB_MARKER_FORCE_OCR
        if form_data.DATALAB_MARKER_FORCE_OCR is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_FORCE_OCR", False)
    )
    request.app.state.config.DATALAB_MARKER_PAGINATE = (
        form_data.DATALAB_MARKER_PAGINATE
        if form_data.DATALAB_MARKER_PAGINATE is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_PAGINATE", False)
    )
    request.app.state.config.DATALAB_MARKER_STRIP_EXISTING_OCR = (
        form_data.DATALAB_MARKER_STRIP_EXISTING_OCR
        if form_data.DATALAB_MARKER_STRIP_EXISTING_OCR is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_STRIP_EXISTING_OCR", False)
    )
    request.app.state.config.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION = (
        form_data.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION
        if form_data.DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION is not None
        else getattr(
            request.app.state.config, "DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION", False
        )
    )
    request.app.state.config.DATALAB_MARKER_FORMAT_LINES = (
        form_data.DATALAB_MARKER_FORMAT_LINES
        if form_data.DATALAB_MARKER_FORMAT_LINES is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_FORMAT_LINES", False)
    )
    request.app.state.config.DATALAB_MARKER_USE_LLM = (
        form_data.DATALAB_MARKER_USE_LLM
        if form_data.DATALAB_MARKER_USE_LLM is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_USE_LLM", False)
    )
    request.app.state.config.DATALAB_MARKER_OUTPUT_FORMAT = (
        form_data.DATALAB_MARKER_OUTPUT_FORMAT
        if form_data.DATALAB_MARKER_OUTPUT_FORMAT is not None
        else getattr(request.app.state.config, "DATALAB_MARKER_OUTPUT_FORMAT", "markdown")
    )
    request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL = (
        form_data.EXTERNAL_DOCUMENT_LOADER_URL
        if form_data.EXTERNAL_DOCUMENT_LOADER_URL is not None
        else getattr(request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_URL", "")
    )
    request.app.state.config.EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH = (
        form_data.EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH
        if form_data.EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH is not None
        else getattr(
            request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH", False
        )
    )
    request.app.state.config.EXTERNAL_DOCUMENT_LOADER_API_KEY = (
        form_data.EXTERNAL_DOCUMENT_LOADER_API_KEY
        if form_data.EXTERNAL_DOCUMENT_LOADER_API_KEY is not None
        else getattr(request.app.state.config, "EXTERNAL_DOCUMENT_LOADER_API_KEY", "")
    )
    request.app.state.config.PDF_EXTRACT_IMAGES = (
        form_data.PDF_EXTRACT_IMAGES
        if form_data.PDF_EXTRACT_IMAGES is not None
        else request.app.state.config.PDF_EXTRACT_IMAGES
    )
    request.app.state.config.PDF_LOADING_MODE = (
        form_data.PDF_LOADING_MODE
        if form_data.PDF_LOADING_MODE is not None
        else (
            form_data.PDF_LOADER_MODE
            if form_data.PDF_LOADER_MODE is not None
            else request.app.state.config.PDF_LOADING_MODE
        )
    )
    request.app.state.config.PDF_LOADER_MODE = (
        request.app.state.config.PDF_LOADING_MODE
        if request.app.state.config.PDF_LOADING_MODE is not None
        else request.app.state.config.PDF_LOADING_MODE
    )
    request.app.state.config.TIKA_SERVER_URL = (
        form_data.TIKA_SERVER_URL
        if form_data.TIKA_SERVER_URL is not None
        else request.app.state.config.TIKA_SERVER_URL
    )
    request.app.state.config.DOCLING_SERVER_URL = (
        form_data.DOCLING_SERVER_URL
        if form_data.DOCLING_SERVER_URL is not None
        else request.app.state.config.DOCLING_SERVER_URL
    )
    request.app.state.config.DOCLING_API_KEY = (
        form_data.DOCLING_API_KEY
        if form_data.DOCLING_API_KEY is not None
        else getattr(request.app.state.config, "DOCLING_API_KEY", "")
    )
    request.app.state.config.DOCLING_PARAMS = (
        form_data.DOCLING_PARAMS
        if form_data.DOCLING_PARAMS is not None
        else getattr(request.app.state.config, "DOCLING_PARAMS", {})
    )
    request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT = (
        form_data.DOCUMENT_INTELLIGENCE_ENDPOINT
        if form_data.DOCUMENT_INTELLIGENCE_ENDPOINT is not None
        else request.app.state.config.DOCUMENT_INTELLIGENCE_ENDPOINT
    )
    request.app.state.config.DOCUMENT_INTELLIGENCE_KEY = (
        form_data.DOCUMENT_INTELLIGENCE_KEY
        if form_data.DOCUMENT_INTELLIGENCE_KEY is not None
        else request.app.state.config.DOCUMENT_INTELLIGENCE_KEY
    )
    request.app.state.config.DOCUMENT_INTELLIGENCE_MODEL = (
        form_data.DOCUMENT_INTELLIGENCE_MODEL
        if form_data.DOCUMENT_INTELLIGENCE_MODEL is not None
        else getattr(request.app.state.config, "DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout")
    )
    request.app.state.config.MISTRAL_OCR_API_BASE_URL = (
        form_data.MISTRAL_OCR_API_BASE_URL
        if form_data.MISTRAL_OCR_API_BASE_URL is not None
        else getattr(request.app.state.config, "MISTRAL_OCR_API_BASE_URL", "https://api.mistral.ai/v1")
    )
    request.app.state.config.MISTRAL_OCR_API_KEY = (
        form_data.MISTRAL_OCR_API_KEY
        if form_data.MISTRAL_OCR_API_KEY is not None
        else request.app.state.config.MISTRAL_OCR_API_KEY
    )
    request.app.state.config.MINERU_API_MODE = (
        form_data.MINERU_API_MODE
        if form_data.MINERU_API_MODE is not None
        else getattr(request.app.state.config, "MINERU_API_MODE", "local")
    )
    request.app.state.config.MINERU_API_URL = (
        form_data.MINERU_API_URL
        if form_data.MINERU_API_URL is not None
        else getattr(request.app.state.config, "MINERU_API_URL", "http://localhost:8000")
    )
    request.app.state.config.MINERU_API_KEY = (
        form_data.MINERU_API_KEY
        if form_data.MINERU_API_KEY is not None
        else getattr(request.app.state.config, "MINERU_API_KEY", "")
    )
    request.app.state.config.MINERU_API_TIMEOUT = (
        form_data.MINERU_API_TIMEOUT
        if form_data.MINERU_API_TIMEOUT is not None
        else getattr(request.app.state.config, "MINERU_API_TIMEOUT", "300")
    )
    request.app.state.config.MINERU_PARAMS = (
        form_data.MINERU_PARAMS
        if form_data.MINERU_PARAMS is not None
        else getattr(request.app.state.config, "MINERU_PARAMS", {})
    )

    # Chunking settings
    requested_text_splitter = (
        form_data.TEXT_SPLITTER
        if form_data.TEXT_SPLITTER is not None
        else request.app.state.config.TEXT_SPLITTER
    )
    if requested_text_splitter == "markdown":
        request.app.state.config.TEXT_SPLITTER = ""
        request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER = (
            form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER
            if form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER is not None
            else True
        )
    else:
        request.app.state.config.TEXT_SPLITTER = requested_text_splitter
        request.app.state.config.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER = (
            form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER
            if form_data.ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER is not None
            else getattr(
                request.app.state.config,
                "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER",
                False,
            )
        )
    request.app.state.config.CHUNK_SIZE = (
        form_data.CHUNK_SIZE
        if form_data.CHUNK_SIZE is not None
        else request.app.state.config.CHUNK_SIZE
    )
    request.app.state.config.CHUNK_OVERLAP = (
        form_data.CHUNK_OVERLAP
        if form_data.CHUNK_OVERLAP is not None
        else request.app.state.config.CHUNK_OVERLAP
    )
    request.app.state.config.CHUNK_MIN_SIZE = (
        form_data.CHUNK_MIN_SIZE
        if form_data.CHUNK_MIN_SIZE is not None
        else request.app.state.config.CHUNK_MIN_SIZE
    )
    request.app.state.config.CHUNK_MIN_SIZE_TARGET = (
        form_data.CHUNK_MIN_SIZE_TARGET
        if form_data.CHUNK_MIN_SIZE_TARGET is not None
        else getattr(request.app.state.config, "CHUNK_MIN_SIZE_TARGET", 0)
    )

    # File upload settings
    if form_data.FILE_MAX_SIZE is not None:
        request.app.state.config.FILE_MAX_SIZE = (
            None if form_data.FILE_MAX_SIZE == "" else form_data.FILE_MAX_SIZE
        )
    if form_data.FILE_MAX_COUNT is not None:
        request.app.state.config.FILE_MAX_COUNT = (
            None if form_data.FILE_MAX_COUNT == "" else form_data.FILE_MAX_COUNT
        )
    if form_data.FILE_IMAGE_COMPRESSION_WIDTH is not None:
        request.app.state.config.FILE_IMAGE_COMPRESSION_WIDTH = (
            None
            if form_data.FILE_IMAGE_COMPRESSION_WIDTH == ""
            else form_data.FILE_IMAGE_COMPRESSION_WIDTH
        )
    if form_data.FILE_IMAGE_COMPRESSION_HEIGHT is not None:
        request.app.state.config.FILE_IMAGE_COMPRESSION_HEIGHT = (
            None
            if form_data.FILE_IMAGE_COMPRESSION_HEIGHT == ""
            else form_data.FILE_IMAGE_COMPRESSION_HEIGHT
        )
    if form_data.ALLOWED_FILE_EXTENSIONS is not None:
        request.app.state.config.ALLOWED_FILE_EXTENSIONS = [
            str(ext).strip().lower().lstrip(".")
            for ext in form_data.ALLOWED_FILE_EXTENSIONS
            if str(ext).strip()
        ]
    else:
        request.app.state.config.ALLOWED_FILE_EXTENSIONS = getattr(
            request.app.state.config, "ALLOWED_FILE_EXTENSIONS", []
        )

    # Integration settings
    request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION = (
        form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION
        if form_data.ENABLE_GOOGLE_DRIVE_INTEGRATION is not None
        else request.app.state.config.ENABLE_GOOGLE_DRIVE_INTEGRATION
    )
    request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION = (
        form_data.ENABLE_ONEDRIVE_INTEGRATION
        if form_data.ENABLE_ONEDRIVE_INTEGRATION is not None
        else request.app.state.config.ENABLE_ONEDRIVE_INTEGRATION
    )

    if form_data.web is not None:
        tavily_search_api_base_url, tavily_search_api_force_mode = _normalize_tavily_config_url(
            form_data.web.TAVILY_SEARCH_API_BASE_URL
            if form_data.web.TAVILY_SEARCH_API_BASE_URL is not None
            else request.app.state.config.TAVILY_SEARCH_API_BASE_URL,
            "search",
            force_mode=(
                form_data.web.TAVILY_SEARCH_API_FORCE_MODE
                if form_data.web.TAVILY_SEARCH_API_FORCE_MODE is not None
                else request.app.state.config.TAVILY_SEARCH_API_FORCE_MODE
            ),
        )
        tavily_extract_api_base_url, tavily_extract_api_force_mode = _normalize_tavily_config_url(
            form_data.web.TAVILY_EXTRACT_API_BASE_URL
            if form_data.web.TAVILY_EXTRACT_API_BASE_URL is not None
            else request.app.state.config.TAVILY_EXTRACT_API_BASE_URL,
            "extract",
            force_mode=(
                form_data.web.TAVILY_EXTRACT_API_FORCE_MODE
                if form_data.web.TAVILY_EXTRACT_API_FORCE_MODE is not None
                else request.app.state.config.TAVILY_EXTRACT_API_FORCE_MODE
            ),
        )
        tavily_api_key = str(
            (
                form_data.web.TAVILY_API_KEY
                if form_data.web.TAVILY_API_KEY is not None
                else request.app.state.config.TAVILY_API_KEY
            )
            or ""
        ).strip()
        effective_web_search_engine = (
            form_data.web.WEB_SEARCH_ENGINE
            if form_data.web.WEB_SEARCH_ENGINE is not None
            else request.app.state.config.WEB_SEARCH_ENGINE
        )
        effective_web_loader_engine = (
            form_data.web.WEB_LOADER_ENGINE
            if form_data.web.WEB_LOADER_ENGINE is not None
            else request.app.state.config.WEB_LOADER_ENGINE
        )
        if (
            effective_web_search_engine == "tavily"
            or effective_web_loader_engine == "tavily"
        ) and not tavily_api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tavily API Key is required when Tavily search or loader is enabled.",
            )

        # Web search settings
        request.app.state.config.ENABLE_WEB_SEARCH = form_data.web.ENABLE_WEB_SEARCH
        request.app.state.config.ENABLE_NATIVE_WEB_SEARCH = (
            form_data.web.ENABLE_NATIVE_WEB_SEARCH
        )
        request.app.state.config.WEB_SEARCH_ENGINE = form_data.web.WEB_SEARCH_ENGINE
        request.app.state.config.WEB_SEARCH_TRUST_ENV = (
            form_data.web.WEB_SEARCH_TRUST_ENV
        )
        request.app.state.config.WEB_SEARCH_RESULT_COUNT = (
            form_data.web.WEB_SEARCH_RESULT_COUNT
        )
        request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS = (
            form_data.web.WEB_SEARCH_CONCURRENT_REQUESTS
        )
        request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST = (
            form_data.web.WEB_SEARCH_DOMAIN_FILTER_LIST
        )
        request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL = (
            form_data.web.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
        )
        request.app.state.config.SEARXNG_QUERY_URL = form_data.web.SEARXNG_QUERY_URL
        request.app.state.config.GOOGLE_PSE_API_KEY = form_data.web.GOOGLE_PSE_API_KEY
        request.app.state.config.GOOGLE_PSE_ENGINE_ID = (
            form_data.web.GOOGLE_PSE_ENGINE_ID
        )
        request.app.state.config.BRAVE_SEARCH_API_KEY = (
            form_data.web.BRAVE_SEARCH_API_KEY
        )
        request.app.state.config.KAGI_SEARCH_API_KEY = form_data.web.KAGI_SEARCH_API_KEY
        request.app.state.config.MOJEEK_SEARCH_API_KEY = (
            form_data.web.MOJEEK_SEARCH_API_KEY
        )
        request.app.state.config.BOCHA_SEARCH_API_KEY = (
            form_data.web.BOCHA_SEARCH_API_KEY
        )
        request.app.state.config.SERPSTACK_API_KEY = form_data.web.SERPSTACK_API_KEY
        request.app.state.config.SERPSTACK_HTTPS = form_data.web.SERPSTACK_HTTPS
        request.app.state.config.SERPER_API_KEY = form_data.web.SERPER_API_KEY
        request.app.state.config.SERPLY_API_KEY = form_data.web.SERPLY_API_KEY
        request.app.state.config.TAVILY_API_KEY = tavily_api_key
        request.app.state.config.TAVILY_SEARCH_API_BASE_URL = tavily_search_api_base_url
        request.app.state.config.TAVILY_SEARCH_API_FORCE_MODE = (
            tavily_search_api_force_mode
        )
        request.app.state.config.SEARCHAPI_API_KEY = form_data.web.SEARCHAPI_API_KEY
        request.app.state.config.SEARCHAPI_ENGINE = form_data.web.SEARCHAPI_ENGINE
        request.app.state.config.SERPAPI_API_KEY = form_data.web.SERPAPI_API_KEY
        request.app.state.config.SERPAPI_ENGINE = form_data.web.SERPAPI_ENGINE
        request.app.state.config.JINA_API_KEY = form_data.web.JINA_API_KEY
        request.app.state.config.BING_SEARCH_V7_ENDPOINT = (
            form_data.web.BING_SEARCH_V7_ENDPOINT
        )
        request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY = (
            form_data.web.BING_SEARCH_V7_SUBSCRIPTION_KEY
        )
        request.app.state.config.EXA_API_KEY = form_data.web.EXA_API_KEY
        request.app.state.config.PERPLEXITY_API_KEY = form_data.web.PERPLEXITY_API_KEY
        request.app.state.config.GROK_API_KEY = form_data.web.GROK_API_KEY
        request.app.state.config.GROK_API_BASE_URL = form_data.web.GROK_API_BASE_URL
        request.app.state.config.GROK_API_MODEL = form_data.web.GROK_API_MODEL
        request.app.state.config.GROK_API_MODE = form_data.web.GROK_API_MODE
        request.app.state.config.SOUGOU_API_SID = form_data.web.SOUGOU_API_SID
        request.app.state.config.SOUGOU_API_SK = form_data.web.SOUGOU_API_SK

        # Web loader settings
        request.app.state.config.WEB_LOADER_ENGINE = form_data.web.WEB_LOADER_ENGINE
        request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION = (
            form_data.web.ENABLE_WEB_LOADER_SSL_VERIFICATION
        )
        request.app.state.config.PLAYWRIGHT_WS_URL = form_data.web.PLAYWRIGHT_WS_URL
        request.app.state.config.PLAYWRIGHT_TIMEOUT = form_data.web.PLAYWRIGHT_TIMEOUT
        request.app.state.config.FIRECRAWL_API_KEY = form_data.web.FIRECRAWL_API_KEY
        request.app.state.config.FIRECRAWL_API_BASE_URL = (
            form_data.web.FIRECRAWL_API_BASE_URL
        )
        request.app.state.config.TAVILY_EXTRACT_DEPTH = (
            form_data.web.TAVILY_EXTRACT_DEPTH
        )
        request.app.state.config.TAVILY_EXTRACT_API_BASE_URL = (
            tavily_extract_api_base_url
        )
        request.app.state.config.TAVILY_EXTRACT_API_FORCE_MODE = (
            tavily_extract_api_force_mode
        )
        request.app.state.config.YOUTUBE_LOADER_LANGUAGE = (
            form_data.web.YOUTUBE_LOADER_LANGUAGE
        )
        request.app.state.config.YOUTUBE_LOADER_PROXY_URL = (
            form_data.web.YOUTUBE_LOADER_PROXY_URL
        )
        request.app.state.YOUTUBE_LOADER_TRANSLATION = (
            form_data.web.YOUTUBE_LOADER_TRANSLATION
        )
        if form_data.web.DDGS_BACKEND is not None:
            request.app.state.config.DDGS_BACKEND = form_data.web.DDGS_BACKEND
        if form_data.web.JINA_API_BASE_URL is not None:
            request.app.state.config.JINA_API_BASE_URL = (
                form_data.web.JINA_API_BASE_URL
            )
        if form_data.web.FIRECRAWL_TIMEOUT is not None:
            request.app.state.config.FIRECRAWL_TIMEOUT = (
                form_data.web.FIRECRAWL_TIMEOUT
            )

    return await get_rag_config(request, user)

####################################
#
# Document process and retrieval
#
####################################


def can_merge_chunks(a: Document, b: Document) -> bool:
    if a.metadata.get("source") != b.metadata.get("source"):
        return False

    a_file_id = a.metadata.get("file_id")
    b_file_id = b.metadata.get("file_id")

    if a_file_id is not None and b_file_id is not None:
        return a_file_id == b_file_id

    return True


def merge_docs_to_target_size(
    request: Request,
    chunks: list[Document],
) -> list[Document]:
    min_chunk_size_target = getattr(request.app.state.config, "CHUNK_MIN_SIZE_TARGET", 0)
    max_chunk_size = request.app.state.config.CHUNK_SIZE

    if min_chunk_size_target <= 0:
        return chunks

    measure_chunk_size = len
    if request.app.state.config.TEXT_SPLITTER == "token":
        encoding = tiktoken.get_encoding(
            str(request.app.state.config.TIKTOKEN_ENCODING_NAME)
        )
        measure_chunk_size = lambda text: len(encoding.encode(text))

    processed_chunks: list[Document] = []

    current_chunk: Optional[Document] = None
    current_content = ""

    for next_chunk in chunks:
        if current_chunk is None:
            current_chunk = next_chunk
            current_content = next_chunk.page_content
            continue

        proposed_content = f"{current_content}\n\n{next_chunk.page_content}"
        can_merge = (
            can_merge_chunks(current_chunk, next_chunk)
            and measure_chunk_size(current_content) < min_chunk_size_target
            and measure_chunk_size(proposed_content) <= max_chunk_size
        )

        if can_merge:
            current_content = proposed_content
        else:
            processed_chunks.append(
                Document(
                    page_content=current_content,
                    metadata={**current_chunk.metadata},
                )
            )
            current_chunk = next_chunk
            current_content = next_chunk.page_content

    if current_chunk is not None:
        processed_chunks.append(
            Document(
                page_content=current_content,
                metadata={**current_chunk.metadata},
            )
        )

    return processed_chunks


def save_docs_to_vector_db(
    request: Request,
    docs,
    collection_name,
    metadata: Optional[dict] = None,
    overwrite: bool = False,
    split: bool = True,
    add: bool = False,
    user=None,
) -> bool:
    def _get_docs_info(docs: list[Document]) -> str:
        docs_info = set()

        # Trying to select relevant metadata identifying the document.
        for doc in docs:
            metadata = getattr(doc, "metadata", {})
            doc_name = metadata.get("name", "")
            if not doc_name:
                doc_name = metadata.get("title", "")
            if not doc_name:
                doc_name = metadata.get("source", "")
            if doc_name:
                docs_info.add(doc_name)

        return ", ".join(docs_info)

    log.info(
        f"save_docs_to_vector_db: document {_get_docs_info(docs)} {collection_name}"
    )

    # Check if entries with the same hash (metadata.hash) already exist
    if metadata and "hash" in metadata:
        result = VECTOR_DB_CLIENT.query(
            collection_name=collection_name,
            filter={"hash": metadata["hash"]},
        )

        if result is not None and result.ids and len(result.ids) > 0:
            existing_doc_ids = result.ids[0]
            if existing_doc_ids:
                if overwrite:
                    log.info(f"Overwriting document with hash {metadata['hash']} ({len(existing_doc_ids)} chunks)")
                    VECTOR_DB_CLIENT.delete(
                        collection_name=collection_name,
                        ids=existing_doc_ids,
                    )
                else:
                    log.info(f"Document with hash {metadata['hash']} already exists")
                    raise ValueError(ERROR_MESSAGES.DUPLICATE_CONTENT)

    # Clean up stale vectors: when a file is re-embedded with a new hash,
    # old vectors (from the previous hash) for the same file_id must be removed.
    if metadata and "file_id" in metadata and overwrite:
        try:
            stale_result = VECTOR_DB_CLIENT.query(
                collection_name=collection_name,
                filter={"file_id": metadata["file_id"]},
            )
            if stale_result is not None:
                stale_ids = stale_result.ids[0]
                if stale_ids:
                    log.info(
                        f"Removing {len(stale_ids)} stale vectors for file_id={metadata['file_id']}"
                    )
                    VECTOR_DB_CLIENT.delete(
                        collection_name=collection_name,
                        ids=stale_ids,
                    )
        except Exception as e:
            log.warning(f"Failed to clean stale vectors for file_id={metadata.get('file_id')}: {e}")

    if split:
        if getattr(
            request.app.state.config, "ENABLE_MARKDOWN_HEADER_TEXT_SPLITTER", False
        ):
            markdown_splitter = MarkdownHeaderTextSplitter(
                headers_to_split_on=[
                    ("#", "Header 1"),
                    ("##", "Header 2"),
                    ("###", "Header 3"),
                    ("####", "Header 4"),
                    ("#####", "Header 5"),
                    ("######", "Header 6"),
                ],
                strip_headers=False,
            )

            split_docs = []
            for doc in docs:
                split_docs.extend(
                    [
                        Document(
                            page_content=split_chunk.page_content,
                            metadata={**doc.metadata},
                        )
                        for split_chunk in markdown_splitter.split_text(doc.page_content)
                    ]
                )

            docs = split_docs
            if getattr(request.app.state.config, "CHUNK_MIN_SIZE_TARGET", 0) > 0:
                docs = merge_docs_to_target_size(request, docs)

        if request.app.state.config.TEXT_SPLITTER in ["", "character"]:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=request.app.state.config.CHUNK_SIZE,
                chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                add_start_index=True,
            )
            docs = text_splitter.split_documents(docs)
        elif request.app.state.config.TEXT_SPLITTER == "token":
            log.info(
                f"Using token text splitter: {request.app.state.config.TIKTOKEN_ENCODING_NAME}"
            )

            tiktoken.get_encoding(str(request.app.state.config.TIKTOKEN_ENCODING_NAME))
            text_splitter = TokenTextSplitter(
                encoding_name=str(request.app.state.config.TIKTOKEN_ENCODING_NAME),
                chunk_size=request.app.state.config.CHUNK_SIZE,
                chunk_overlap=request.app.state.config.CHUNK_OVERLAP,
                add_start_index=True,
            )
            docs = text_splitter.split_documents(docs)
        else:
            raise ValueError(ERROR_MESSAGES.DEFAULT("Invalid text splitter"))

        # Merge chunks smaller than CHUNK_MIN_SIZE with adjacent chunks
        min_size = request.app.state.config.CHUNK_MIN_SIZE
        if min_size and min_size > 0 and len(docs) > 1:
            merged = [docs[0]]
            for doc in docs[1:]:
                if len(merged[-1].page_content) < min_size:
                    merged[-1].page_content += "\n" + doc.page_content
                else:
                    merged.append(doc)
            # Handle trailing small chunk
            if len(merged) > 1 and len(merged[-1].page_content) < min_size:
                merged[-2].page_content += "\n" + merged[-1].page_content
                merged.pop()
            docs = merged

    if len(docs) == 0:
        raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)

    texts = [doc.page_content for doc in docs]
    metadatas = [
        {
            **doc.metadata,
            **(metadata if metadata else {}),
            "embedding_config": json.dumps(
                {
                    "engine": request.app.state.config.RAG_EMBEDDING_ENGINE,
                    "model": request.app.state.config.RAG_EMBEDDING_MODEL,
                }
            ),
        }
        for doc in docs
    ]

    # ChromaDB does not like datetime formats
    # for meta-data so convert them to string.
    for metadata in metadatas:
        for key, value in metadata.items():
            if (
                isinstance(value, datetime)
                or isinstance(value, list)
                or isinstance(value, dict)
            ):
                metadata[key] = str(value)

    try:
        if VECTOR_DB_CLIENT.has_collection(collection_name=collection_name):
            log.info(f"collection {collection_name} already exists")

            if overwrite:
                VECTOR_DB_CLIENT.delete_collection(collection_name=collection_name)
                log.info(f"deleting existing collection {collection_name}")
            elif add is False:
                log.info(
                    f"collection {collection_name} already exists, overwrite is False and add is False"
                )
                return True

        log.info(f"adding to collection {collection_name}")
        embeddings = request.app.state.EMBEDDING_FUNCTION(
            list(map(lambda x: x.replace("\n", " "), texts)),
            prefix=RAG_EMBEDDING_CONTENT_PREFIX,
            user=user,
        )

        items = [
            {
                "id": str(uuid.uuid4()),
                "text": text,
                "vector": embeddings[idx],
                "metadata": metadatas[idx],
            }
            for idx, text in enumerate(texts)
        ]

        VECTOR_DB_CLIENT.insert(
            collection_name=collection_name,
            items=items,
        )

        return True
    except Exception as e:
        log.exception(e)
        raise e


class ProcessFileForm(BaseModel):
    file_id: str
    content: Optional[str] = None
    collection_name: Optional[str] = None
    overwrite: bool = False
    processing_mode: Optional[str] = None
    document_provider: Optional[str] = None
    allow_provider_local_fallback: bool = True


def _delete_collection_if_exists(collection_name: Optional[str]) -> None:
    if not collection_name:
        return
    try:
        if VECTOR_DB_CLIENT.has_collection(collection_name=collection_name):
            VECTOR_DB_CLIENT.delete_collection(collection_name=collection_name)
    except Exception:
        pass


def _clear_standalone_file_collection(file_id: str) -> None:
    _delete_collection_if_exists(f"file-{file_id}")


def _build_docs_from_text(file, text_content: str) -> list[Document]:
    return [
        Document(
            page_content=text_content.replace("<br/>", "\n"),
            metadata={
                **(file.meta or {}),
                "name": file.filename,
                "created_by": file.user_id,
                "file_id": file.id,
                "source": file.filename,
            },
        )
    ]


def _resolve_processing_mode_for_request(
    request: Request, form_data: ProcessFileForm
) -> str:
    if form_data.collection_name:
        return FILE_PROCESSING_MODE_RETRIEVAL
    return resolve_file_processing_mode_from_config(
        request.app.state.config, form_data.processing_mode
    )


def _resolve_requested_document_provider(
    request: Request, form_data: ProcessFileForm
) -> str:
    return normalize_document_provider(
        form_data.document_provider or request.app.state.config.DOCUMENT_PROVIDER,
        DOCUMENT_PROVIDER_LOCAL_DEFAULT,
    )


def _get_cached_file_collection_docs(file) -> list[Document]:
    result = VECTOR_DB_CLIENT.query(
        collection_name=f"file-{file.id}", filter={"file_id": file.id}
    )
    if result is not None and len(result.ids[0]) > 0:
        return [
            Document(
                page_content=result.documents[0][idx],
                metadata=result.metadatas[0][idx],
            )
            for idx, _ in enumerate(result.ids[0])
        ]
    return []


def _prepare_documents_for_processing(
    request: Request,
    file,
    *,
    processing_mode: str,
    requested_provider: str,
    content: Optional[str] = None,
    allow_cached_collection_docs: bool = False,
    allow_provider_local_fallback: bool = True,
) -> tuple[
    list[Document],
    str,
    str,
    Optional[str],
    list[str],
    str,
    Optional[str],
    Optional[str],
    Optional[str],
]:
    resolved_provider = requested_provider
    processing_notice = None
    processing_fallbacks: list[str] = []
    primary_provider_error = None
    fallback_provider = None
    fallback_reason = None
    current_mode = get_file_effective_processing_mode(file)
    current_provider = normalize_document_provider(
        (file.meta or {}).get("processing_provider") or requested_provider,
        requested_provider,
    )

    if content is not None:
        effective_mode = (
            FILE_PROCESSING_MODE_FULL_CONTEXT
            if processing_mode == FILE_PROCESSING_MODE_NATIVE_FILE
            else processing_mode
        )
        return (
            _build_docs_from_text(file, content),
            content,
            requested_provider,
            None,
            [],
            effective_mode,
            None,
            None,
            None,
        )

    if allow_cached_collection_docs:
        cached_docs = _get_cached_file_collection_docs(file)
        if cached_docs and current_mode == FILE_PROCESSING_MODE_RETRIEVAL and current_provider == requested_provider:
            return (
                cached_docs,
                " ".join(doc.page_content for doc in cached_docs),
                current_provider,
                (file.meta or {}).get("processing_notice"),
                list((file.meta or {}).get("processing_fallbacks") or []),
                processing_mode,
                (file.meta or {}).get("primary_provider_error"),
                (file.meta or {}).get("fallback_provider"),
                (file.meta or {}).get("fallback_reason"),
            )

    can_reuse_cached_text = (
        (file.data or {}).get("content")
        and current_mode != FILE_PROCESSING_MODE_NATIVE_FILE
        and current_provider == requested_provider
    )
    if can_reuse_cached_text:
        text_content = (file.data or {}).get("content", "")
        return (
            _build_docs_from_text(file, text_content),
            text_content,
            current_provider,
            (file.meta or {}).get("processing_notice"),
            list((file.meta or {}).get("processing_fallbacks") or []),
            processing_mode,
            (file.meta or {}).get("primary_provider_error"),
            (file.meta or {}).get("fallback_provider"),
            (file.meta or {}).get("fallback_reason"),
        )

    if file.path and should_extract_for_mode(processing_mode):
        extraction = extract_documents_for_file(
            request,
            file,
            provider=requested_provider,
            allow_local_fallback=allow_provider_local_fallback,
        )
        docs = extraction.docs
        return (
            docs,
            " ".join(doc.page_content for doc in docs),
            extraction.provider,
            extraction.notice,
            extraction.fallbacks,
            processing_mode,
            extraction.primary_provider_error,
            extraction.fallback_provider,
            extraction.fallback_reason,
        )

    text_content = (file.data or {}).get("content", "") or ""
    return (
        _build_docs_from_text(file, text_content),
        text_content,
        current_provider,
        (file.meta or {}).get("processing_notice"),
        list((file.meta or {}).get("processing_fallbacks") or []),
        processing_mode,
        (file.meta or {}).get("primary_provider_error"),
        (file.meta or {}).get("fallback_provider"),
        (file.meta or {}).get("fallback_reason"),
    )


@router.post("/process/file")
def process_file(
    request: Request,
    form_data: ProcessFileForm,
    user=Depends(get_verified_user),
):
    file = None
    try:
        file = Files.get_file_by_id(form_data.file_id)
        if not file:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )

        collection_name = form_data.collection_name

        if collection_name is None:
            collection_name = f"file-{file.id}"

        processing_mode = _resolve_processing_mode_for_request(request, form_data)
        requested_provider = _resolve_requested_document_provider(request, form_data)
        resolved_provider = requested_provider
        processing_notice = None
        processing_fallbacks: list[str] = []
        primary_provider_error = None
        fallback_provider = None
        fallback_reason = None
        text_content = None
        docs: list[Document] = []

        if form_data.content:
            # Update the content in the file
            # Usage: /files/{file_id}/data/content/update, /files/ (audio file upload pipeline)

            try:
                # /files/{file_id}/data/content/update
                VECTOR_DB_CLIENT.delete_collection(collection_name=f"file-{file.id}")
            except:
                # Audio file upload pipeline
                pass

            (
                docs,
                text_content,
                resolved_provider,
                processing_notice,
                processing_fallbacks,
                processing_mode,
                primary_provider_error,
                fallback_provider,
                fallback_reason,
            ) = _prepare_documents_for_processing(
                request,
                file,
                processing_mode=processing_mode,
                requested_provider=requested_provider,
                content=form_data.content,
                allow_provider_local_fallback=form_data.allow_provider_local_fallback,
            )
        elif form_data.collection_name:
            (
                docs,
                text_content,
                resolved_provider,
                processing_notice,
                processing_fallbacks,
                processing_mode,
                primary_provider_error,
                fallback_provider,
                fallback_reason,
            ) = _prepare_documents_for_processing(
                request,
                file,
                processing_mode=processing_mode,
                requested_provider=requested_provider,
                allow_cached_collection_docs=True,
                allow_provider_local_fallback=form_data.allow_provider_local_fallback,
            )
        else:
            if processing_mode == FILE_PROCESSING_MODE_NATIVE_FILE:
                _clear_standalone_file_collection(file.id)
                Files.update_file_data_by_id(file.id, {"content": None})
                Files.update_file_hash_by_id(file.id, "")
                Files.update_file_metadata_by_id(
                    file.id,
                    {
                        "collection_name": None,
                        "processing_mode": FILE_PROCESSING_MODE_NATIVE_FILE,
                        "resolved_processing_mode": FILE_PROCESSING_MODE_NATIVE_FILE,
                        "processing_provider": FILE_PROCESSING_MODE_NATIVE_FILE,
                        "requested_document_provider": requested_provider,
                        "processing_notice": None,
                        "processing_fallbacks": [],
                        "primary_provider_error": None,
                        "fallback_provider": None,
                        "fallback_reason": None,
                    },
                )
                return {
                    "status": True,
                    "collection_name": None,
                    "filename": file.filename,
                    "content": None,
                    "processing_mode": FILE_PROCESSING_MODE_NATIVE_FILE,
                    "processing_provider": FILE_PROCESSING_MODE_NATIVE_FILE,
                    "notice": None,
                    "primary_provider_error": None,
                    "fallback_provider": None,
                    "fallback_reason": None,
                }

            (
                docs,
                text_content,
                resolved_provider,
                processing_notice,
                processing_fallbacks,
                processing_mode,
                primary_provider_error,
                fallback_provider,
                fallback_reason,
            ) = _prepare_documents_for_processing(
                request,
                file,
                processing_mode=processing_mode,
                requested_provider=requested_provider,
                allow_provider_local_fallback=form_data.allow_provider_local_fallback,
            )

        text_content = text_content or ""
        _log_text_content_summary(
            "process_file",
            text_content=text_content,
            collection_name=collection_name,
            processing_mode=processing_mode,
            provider=resolved_provider,
        )
        Files.update_file_data_by_id(file.id, {"content": text_content})

        hash = calculate_sha256_string(text_content)
        Files.update_file_hash_by_id(file.id, hash)

        if should_index_for_mode(processing_mode):
            result = save_docs_to_vector_db(
                request,
                docs=docs,
                collection_name=collection_name,
                metadata={
                    "file_id": file.id,
                    "name": file.filename,
                    "hash": hash,
                },
                overwrite=form_data.overwrite,
                add=(True if form_data.collection_name else False),
                user=user,
            )

            if result:
                Files.update_file_metadata_by_id(
                    file.id,
                    {
                        "collection_name": collection_name,
                        "processing_mode": processing_mode,
                        "resolved_processing_mode": processing_mode,
                        "processing_provider": resolved_provider,
                        "requested_document_provider": requested_provider,
                        "processing_notice": processing_notice,
                        "processing_fallbacks": processing_fallbacks,
                        "primary_provider_error": primary_provider_error,
                        "fallback_provider": fallback_provider,
                        "fallback_reason": fallback_reason,
                    },
                )

                return {
                    "status": True,
                    "collection_name": collection_name,
                    "filename": file.filename,
                    "content": text_content,
                    "processing_mode": processing_mode,
                    "processing_provider": resolved_provider,
                    "notice": processing_notice,
                    "primary_provider_error": primary_provider_error,
                    "fallback_provider": fallback_provider,
                    "fallback_reason": fallback_reason,
                }
        else:
            _clear_standalone_file_collection(file.id)
            Files.update_file_metadata_by_id(
                file.id,
                {
                    "collection_name": None,
                    "processing_mode": processing_mode,
                    "resolved_processing_mode": processing_mode,
                    "processing_provider": resolved_provider,
                    "requested_document_provider": requested_provider,
                    "processing_notice": processing_notice,
                    "processing_fallbacks": processing_fallbacks,
                    "primary_provider_error": primary_provider_error,
                    "fallback_provider": fallback_provider,
                    "fallback_reason": fallback_reason,
                },
            )
            return {
                "status": True,
                "collection_name": None,
                "filename": file.filename,
                "content": text_content,
                "processing_mode": processing_mode,
                "processing_provider": resolved_provider,
                "notice": processing_notice,
                "primary_provider_error": primary_provider_error,
                "fallback_provider": fallback_provider,
                "fallback_reason": fallback_reason,
            }

    except Exception as e:
        log.exception(e)
        if isinstance(e, HTTPException):
            raise e
        if "No pandoc was found" in str(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.PANDOC_NOT_INSTALLED,
            )
        else:
            file_name = file.filename if file else None
            file_content_type = file.meta.get("content_type") if file and file.meta else None
            diagnostic = classify_file_upload_error(
                e,
                filename=file_name,
                content_type=file_content_type,
                user=user,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=build_file_upload_error_detail(diagnostic),
            )


class ProcessTextForm(BaseModel):
    name: str
    content: str
    collection_name: Optional[str] = None


@router.post("/process/text")
def process_text(
    request: Request,
    form_data: ProcessTextForm,
    user=Depends(get_verified_user),
):
    collection_name = form_data.collection_name
    if collection_name is None:
        collection_name = calculate_sha256_string(form_data.content)

    docs = [
        Document(
            page_content=form_data.content,
            metadata={"name": form_data.name, "created_by": user.id},
        )
    ]
    text_content = form_data.content
    _log_text_content_summary(
        "process_text",
        text_content=text_content,
        collection_name=collection_name,
    )

    result = save_docs_to_vector_db(request, docs, collection_name, user=user)
    if result:
        return {
            "status": True,
            "collection_name": collection_name,
            "content": text_content,
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT(),
        )


@router.post("/process/youtube")
def process_youtube_video(
    request: Request, form_data: ProcessUrlForm, user=Depends(get_verified_user)
):
    try:
        collection_name = form_data.collection_name
        if not collection_name:
            collection_name = calculate_sha256_string(form_data.url)[:63]

        loader = YoutubeLoader(
            form_data.url,
            language=request.app.state.config.YOUTUBE_LOADER_LANGUAGE,
            proxy_url=request.app.state.config.YOUTUBE_LOADER_PROXY_URL,
        )

        docs = loader.load()
        content = " ".join(doc.page_content for doc in docs)
        _log_text_content_summary(
            "process_youtube_video",
            text_content=content,
            collection_name=collection_name,
            provider="youtube",
        )

        save_docs_to_vector_db(
            request, docs, collection_name, overwrite=True, user=user
        )

        return {
            "status": True,
            "collection_name": collection_name,
            "filename": form_data.url,
            "file": {
                "data": {
                    "content": content,
                },
                "meta": {
                    "name": form_data.url,
                },
            },
        }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


@router.post("/process/web")
def process_web(
    request: Request, form_data: ProcessUrlForm, user=Depends(get_verified_user)
):
    try:
        collection_name = form_data.collection_name
        if not collection_name:
            collection_name = calculate_sha256_string(form_data.url)[:63]

        from open_webui.retrieval.web.utils import get_web_loader

        loader = get_web_loader(
            form_data.url,
            verify_ssl=request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            requests_per_second=request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
        )
        docs = loader.load()
        content = " ".join(doc.page_content for doc in docs)
        _log_text_content_summary(
            "process_web",
            text_content=content,
            collection_name=collection_name,
            provider="web",
        )

        if not request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL:
            save_docs_to_vector_db(
                request, docs, collection_name, overwrite=True, user=user
            )
        else:
            collection_name = None

        return {
            "status": True,
            "collection_name": collection_name,
            "filename": form_data.url,
            "file": {
                "data": {
                    "content": content,
                },
                "meta": {
                    "name": form_data.url,
                    "source": form_data.url,
                },
            },
        }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


def search_web(request: Request, engine: str, query: str) -> list[SearchResult]:
    """Search the web using a search engine and return the results as a list of SearchResult objects.
    Will look for a search engine API key in environment variables in the following order:
    - SEARXNG_QUERY_URL
    - GOOGLE_PSE_API_KEY + GOOGLE_PSE_ENGINE_ID
    - BRAVE_SEARCH_API_KEY
    - KAGI_SEARCH_API_KEY
    - MOJEEK_SEARCH_API_KEY
    - BOCHA_SEARCH_API_KEY
    - SERPSTACK_API_KEY
    - SERPER_API_KEY
    - SERPLY_API_KEY
    - TAVILY_API_KEY
    - EXA_API_KEY
    - PERPLEXITY_API_KEY
    - SOUGOU_API_SID + SOUGOU_API_SK
    - SEARCHAPI_API_KEY + SEARCHAPI_ENGINE (by default `google`)
    - SERPAPI_API_KEY + SERPAPI_ENGINE (by default `google`)
    Args:
        query (str): The query to search for
    """

    # TODO: add playwright to search the web
    if engine == "searxng":
        if request.app.state.config.SEARXNG_QUERY_URL:
            return search_searxng(
                request.app.state.config.SEARXNG_QUERY_URL,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SEARXNG_QUERY_URL found in environment variables")
    elif engine == "google_pse":
        if (
            request.app.state.config.GOOGLE_PSE_API_KEY
            and request.app.state.config.GOOGLE_PSE_ENGINE_ID
        ):
            return search_google_pse(
                request.app.state.config.GOOGLE_PSE_API_KEY,
                request.app.state.config.GOOGLE_PSE_ENGINE_ID,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception(
                "No GOOGLE_PSE_API_KEY or GOOGLE_PSE_ENGINE_ID found in environment variables"
            )
    elif engine == "brave":
        if request.app.state.config.BRAVE_SEARCH_API_KEY:
            return search_brave(
                request.app.state.config.BRAVE_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No BRAVE_SEARCH_API_KEY found in environment variables")
    elif engine == "kagi":
        if request.app.state.config.KAGI_SEARCH_API_KEY:
            return search_kagi(
                request.app.state.config.KAGI_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No KAGI_SEARCH_API_KEY found in environment variables")
    elif engine == "mojeek":
        if request.app.state.config.MOJEEK_SEARCH_API_KEY:
            return search_mojeek(
                request.app.state.config.MOJEEK_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No MOJEEK_SEARCH_API_KEY found in environment variables")
    elif engine == "bocha":
        if request.app.state.config.BOCHA_SEARCH_API_KEY:
            return search_bocha(
                request.app.state.config.BOCHA_SEARCH_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No BOCHA_SEARCH_API_KEY found in environment variables")
    elif engine == "serpstack":
        if request.app.state.config.SERPSTACK_API_KEY:
            return search_serpstack(
                request.app.state.config.SERPSTACK_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                https_enabled=request.app.state.config.SERPSTACK_HTTPS,
            )
        else:
            raise Exception("No SERPSTACK_API_KEY found in environment variables")
    elif engine == "serper":
        if request.app.state.config.SERPER_API_KEY:
            return search_serper(
                request.app.state.config.SERPER_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SERPER_API_KEY found in environment variables")
    elif engine == "serply":
        if request.app.state.config.SERPLY_API_KEY:
            return search_serply(
                request.app.state.config.SERPLY_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SERPLY_API_KEY found in environment variables")
    elif engine == "duckduckgo":
        return search_duckduckgo(
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            backend=request.app.state.config.DDGS_BACKEND,
        )
    elif engine == "tavily":
        if request.app.state.config.TAVILY_API_KEY:
            return search_tavily(
                request.app.state.config.TAVILY_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                api_base_url=request.app.state.config.TAVILY_SEARCH_API_BASE_URL,
                force_mode=request.app.state.config.TAVILY_SEARCH_API_FORCE_MODE,
            )
        else:
            raise Exception("No TAVILY_API_KEY found in environment variables")
    elif engine == "searchapi":
        if request.app.state.config.SEARCHAPI_API_KEY:
            return search_searchapi(
                request.app.state.config.SEARCHAPI_API_KEY,
                request.app.state.config.SEARCHAPI_ENGINE,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SEARCHAPI_API_KEY found in environment variables")
    elif engine == "serpapi":
        if request.app.state.config.SERPAPI_API_KEY:
            return search_serpapi(
                request.app.state.config.SERPAPI_API_KEY,
                request.app.state.config.SERPAPI_ENGINE,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception("No SERPAPI_API_KEY found in environment variables")
    elif engine == "jina":
        return search_jina(
            request.app.state.config.JINA_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            api_base_url=request.app.state.config.JINA_API_BASE_URL,
        )
    elif engine == "bing":
        return search_bing(
            request.app.state.config.BING_SEARCH_V7_SUBSCRIPTION_KEY,
            request.app.state.config.BING_SEARCH_V7_ENDPOINT,
            str(DEFAULT_LOCALE),
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "exa":
        return search_exa(
            request.app.state.config.EXA_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "perplexity":
        return search_perplexity(
            request.app.state.config.PERPLEXITY_API_KEY,
            query,
            request.app.state.config.WEB_SEARCH_RESULT_COUNT,
            request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
        )
    elif engine == "grok":
        if request.app.state.config.GROK_API_KEY:
            return search_grok(
                request.app.state.config.GROK_API_KEY,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
                api_base_url=request.app.state.config.GROK_API_BASE_URL,
                model=request.app.state.config.GROK_API_MODEL,
                api_mode=request.app.state.config.GROK_API_MODE,
            )
        else:
            raise Exception("No GROK_API_KEY found in environment variables")
    elif engine == "sougou":
        if (
            request.app.state.config.SOUGOU_API_SID
            and request.app.state.config.SOUGOU_API_SK
        ):
            return search_sougou(
                request.app.state.config.SOUGOU_API_SID,
                request.app.state.config.SOUGOU_API_SK,
                query,
                request.app.state.config.WEB_SEARCH_RESULT_COUNT,
                request.app.state.config.WEB_SEARCH_DOMAIN_FILTER_LIST,
            )
        else:
            raise Exception(
                "No SOUGOU_API_SID or SOUGOU_API_SK found in environment variables"
            )
    else:
        raise Exception("No search engine API key found in environment variables")


def _fill_favicons(results: list[SearchResult]) -> list[SearchResult]:
    """Fill missing favicons with Google's favicon service as fallback."""
    for result in results:
        if not result.favicon:
            try:
                domain = urlparse(result.link).netloc
                if domain:
                    result.favicon = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
            except Exception:
                pass
    return results


def _build_direct_docs_from_web_results(
    query: str,
    results: list[SearchResult],
    engine: str,
) -> Optional[dict]:
    docs = []
    filenames = []

    for idx, result in enumerate(results):
        content = str(result.snippet or "").strip()
        if not content:
            continue

        title = str(result.title or "").strip() or "Search Result"
        link = str(result.link or "").strip()
        source = link or f"{engine or 'web'}://search/{calculate_sha256_string(f'{query}-{idx}')}"

        metadata = {
            "source": source,
            "title": title,
            "name": title,
            "query": query,
            "engine": engine,
        }
        if link:
            metadata["url"] = link

        docs.append(
            {
                "content": content,
                "metadata": metadata,
            }
        )
        filenames.append(source)

    if not docs:
        return None

    return {
        "status": True,
        "collection_name": None,
        "filenames": filenames,
        "docs": docs,
        "loaded_count": len(docs),
        "failed_count": 0,
        "direct_content_only": True,
    }


@router.post("/process/web/search")
async def process_web_search(
    request: Request, form_data: SearchForm, user=Depends(get_verified_user)
):
    engine = str(request.app.state.config.WEB_SEARCH_ENGINE or "").strip()
    try:
        logging.info(
            f"trying to web search with {engine, form_data.query}"
        )
        web_results = _fill_favicons(search_web(
            request, engine, form_data.query
        ))
    except Exception as e:
        log.exception(e)

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.WEB_SEARCH_ERROR(e),
        )

    _log_web_results_summary(engine, web_results)

    try:
        urls = [str(result.link or "").strip() for result in web_results if str(result.link or "").strip()]
        if not urls:
            direct_docs = _build_direct_docs_from_web_results(
                form_data.query,
                web_results,
                engine,
            )
            if direct_docs is not None:
                log.info(
                    "Web search returned no fetchable URLs; passing raw search content directly"
                )
                return direct_docs

            return {
                "status": True,
                "collection_name": None,
                "filenames": [],
                "docs": [],
                "loaded_count": 0,
                "failed_count": 0,
                "direct_content_only": True,
            }

        from open_webui.retrieval.web.utils import get_web_loader

        loader = get_web_loader(
            urls,
            verify_ssl=request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
            requests_per_second=request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
            trust_env=request.app.state.config.WEB_SEARCH_TRUST_ENV,
        )
        docs = await loader.aload()
        urls = [
            doc.metadata["source"] for doc in docs
        ]  # only keep URLs which could be retrieved

        if request.app.state.config.BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL:
            return {
                "status": True,
                "collection_name": None,
                "filenames": urls,
                "docs": [
                    {
                        "content": doc.page_content,
                        "metadata": doc.metadata,
                    }
                    for doc in docs
                ],
                "loaded_count": len(docs),
            }
        else:
            MAX_WEB_PAGE_SIZE = 100_000  # ~100KB, prevent huge pages from blocking embedding
            collection_names = []
            failed_count = 0
            # Known challenge / block page title prefixes (lowercase).
            # These pages contain no useful content — skip to save embedding quota.
            _CHALLENGE_TITLES = ("just a moment", "checking your browser", "attention required", "access denied")

            for doc_idx, doc in enumerate(docs):
                if doc and doc.page_content:
                    content_stripped = doc.page_content.strip()
                    content_len = len(content_stripped)
                    title_lower = (doc.metadata.get("title", "") or "").strip().lower()

                    # Skip pages with extremely short content (error / empty pages)
                    if content_len < 50:
                        log.info(
                            f"Skipping low-quality web page {urls[doc_idx]} "
                            f"(content_len={content_len}, title='{doc.metadata.get('title', '')}')"
                        )
                        failed_count += 1
                        continue

                    # Skip known challenge pages (Cloudflare etc.) with short content
                    if any(title_lower.startswith(t) for t in _CHALLENGE_TITLES) and content_len < 500:
                        log.info(
                            f"Skipping challenge page {urls[doc_idx]} "
                            f"(title='{doc.metadata.get('title', '')}', content_len={content_len})"
                        )
                        failed_count += 1
                        continue

                    if len(doc.page_content) > MAX_WEB_PAGE_SIZE:
                        log.warning(
                            f"Truncating large web page {urls[doc_idx]} "
                            f"from {len(doc.page_content)} to {MAX_WEB_PAGE_SIZE} chars"
                        )
                        doc.page_content = doc.page_content[:MAX_WEB_PAGE_SIZE]

                    collection_name = f"web-search-{calculate_sha256_string(form_data.query + '-' + urls[doc_idx])}"[
                        :63
                    ]

                    try:
                        await run_in_threadpool(
                            save_docs_to_vector_db,
                            request,
                            [doc],
                            collection_name,
                            overwrite=True,
                            user=user,
                        )
                        collection_names.append(collection_name)
                    except Exception as e:
                        log.warning(
                            f"Failed to index web document {urls[doc_idx]}: {e}"
                        )
                        failed_count += 1

            return {
                "status": True,
                "collection_names": collection_names,
                "filenames": urls,
                "loaded_count": len(docs),
                "failed_count": failed_count,
            }
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


class QueryDocForm(BaseModel):
    collection_name: str
    query: str
    k: Optional[int] = None
    k_reranker: Optional[int] = None
    r: Optional[float] = None
    hybrid: Optional[bool] = None


@router.post("/query/doc")
def query_doc_handler(
    request: Request,
    form_data: QueryDocForm,
    user=Depends(get_verified_user),
):
    try:
        reranking_function = (
            get_safe_reranking_runtime(request.app)
            if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH
            else None
        )
        if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH:
            collection_results = {}
            collection_results[form_data.collection_name] = VECTOR_DB_CLIENT.get(
                collection_name=form_data.collection_name
            )
            return query_doc_with_hybrid_search(
                collection_name=form_data.collection_name,
                collection_result=collection_results[form_data.collection_name],
                query=form_data.query,
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
                reranking_function=reranking_function,
                k_reranker=form_data.k_reranker
                or request.app.state.config.TOP_K_RERANKER,
                r=(
                    form_data.r
                    if form_data.r
                    else request.app.state.config.RELEVANCE_THRESHOLD
                ),
                bm25_weight=request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT,
                enable_enriched_texts=getattr(
                    request.app.state.config,
                    "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS",
                    False,
                ),
                user=user,
            )
        else:
            return query_doc(
                collection_name=form_data.collection_name,
                query_embedding=request.app.state.EMBEDDING_FUNCTION(
                    form_data.query, prefix=RAG_EMBEDDING_QUERY_PREFIX, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
                user=user,
            )
    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


class QueryCollectionsForm(BaseModel):
    collection_names: list[str]
    query: str
    k: Optional[int] = None
    k_reranker: Optional[int] = None
    r: Optional[float] = None
    hybrid: Optional[bool] = None


@router.post("/query/collection")
def query_collection_handler(
    request: Request,
    form_data: QueryCollectionsForm,
    user=Depends(get_verified_user),
):
    try:
        reranking_function = (
            get_safe_reranking_runtime(request.app)
            if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH
            else None
        )
        if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH:
            return query_collection_with_hybrid_search(
                collection_names=form_data.collection_names,
                queries=[form_data.query],
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
                reranking_function=reranking_function,
                k_reranker=form_data.k_reranker
                or request.app.state.config.TOP_K_RERANKER,
                r=(
                    form_data.r
                    if form_data.r
                    else request.app.state.config.RELEVANCE_THRESHOLD
                ),
                bm25_weight=request.app.state.config.RAG_HYBRID_SEARCH_BM25_WEIGHT,
                enable_enriched_texts=getattr(
                    request.app.state.config,
                    "ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS",
                    False,
                ),
            )
        else:
            return query_collection(
                collection_names=form_data.collection_names,
                queries=[form_data.query],
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=form_data.k if form_data.k else request.app.state.config.TOP_K,
            )

    except Exception as e:
        log.exception(e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(e),
        )


####################################
#
# Vector DB operations
#
####################################


class DeleteForm(BaseModel):
    collection_name: str
    file_id: str


@router.post("/delete")
def delete_entries_from_collection(form_data: DeleteForm, user=Depends(get_admin_user)):
    try:
        if VECTOR_DB_CLIENT.has_collection(collection_name=form_data.collection_name):
            file = Files.get_file_by_id(form_data.file_id)
            hash = file.hash

            VECTOR_DB_CLIENT.delete(
                collection_name=form_data.collection_name,
                metadata={"hash": hash},
            )
            return {"status": True}
        else:
            return {"status": False}
    except Exception as e:
        log.exception(e)
        return {"status": False}


@router.post("/reset/db")
def reset_vector_db(user=Depends(get_admin_user)):
    VECTOR_DB_CLIENT.reset()
    Knowledges.delete_all_knowledge()


@router.post("/reset/uploads")
def reset_upload_dir(user=Depends(get_admin_user)) -> bool:
    folder = f"{UPLOAD_DIR}"
    try:
        # Check if the directory exists
        if os.path.exists(folder):
            # Iterate over all the files and directories in the specified directory
            for filename in os.listdir(folder):
                file_path = os.path.join(folder, filename)
                try:
                    if os.path.isfile(file_path) or os.path.islink(file_path):
                        os.unlink(file_path)  # Remove the file or link
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)  # Remove the directory
                except Exception as e:
                    log.exception(f"Failed to delete {file_path}. Reason: {e}")
        else:
            log.warning(f"The directory {folder} does not exist")
    except Exception as e:
        log.exception(f"Failed to process the directory {folder}. Reason: {e}")
    return True


if ENV == "dev":

    @router.get("/ef/{text}")
    async def get_embeddings(request: Request, text: Optional[str] = "Hello World!"):
        return {
            "result": request.app.state.EMBEDDING_FUNCTION(
                text, prefix=RAG_EMBEDDING_QUERY_PREFIX
            )
        }


class BatchProcessFilesForm(BaseModel):
    files: List[FileModel]
    collection_name: str


class BatchProcessFilesResult(BaseModel):
    file_id: str
    status: str
    error: Optional[str] = None


class BatchProcessFilesResponse(BaseModel):
    results: List[BatchProcessFilesResult]
    errors: List[BatchProcessFilesResult]


@router.post("/process/files/batch")
def process_files_batch(
    request: Request,
    form_data: BatchProcessFilesForm,
    user=Depends(get_verified_user),
) -> BatchProcessFilesResponse:
    """
    Process a batch of files and save them to the vector database.
    """
    results: List[BatchProcessFilesResult] = []
    errors: List[BatchProcessFilesResult] = []
    all_docs: List[Document] = []
    collection_name = form_data.collection_name
    for file in form_data.files:
        try:
            (
                docs,
                text_content,
                resolved_provider,
                processing_notice,
                processing_fallbacks,
                _resolved_mode,
                primary_provider_error,
                fallback_provider,
                fallback_reason,
            ) = _prepare_documents_for_processing(
                request,
                file,
                processing_mode=FILE_PROCESSING_MODE_RETRIEVAL,
                requested_provider=normalize_document_provider(
                    request.app.state.config.DOCUMENT_PROVIDER,
                    DOCUMENT_PROVIDER_LOCAL_DEFAULT,
                ),
                allow_cached_collection_docs=True,
            )
            file_hash = calculate_sha256_string(text_content or "")
            Files.update_file_hash_by_id(file.id, file_hash)
            Files.update_file_data_by_id(file.id, {"content": text_content or ""})
            Files.update_file_metadata_by_id(
                file.id,
                {
                    "processing_mode": FILE_PROCESSING_MODE_RETRIEVAL,
                    "resolved_processing_mode": FILE_PROCESSING_MODE_RETRIEVAL,
                    "processing_provider": resolved_provider,
                    "requested_document_provider": normalize_document_provider(
                        request.app.state.config.DOCUMENT_PROVIDER,
                        DOCUMENT_PROVIDER_LOCAL_DEFAULT,
                    ),
                    "processing_notice": processing_notice,
                    "processing_fallbacks": processing_fallbacks,
                    "primary_provider_error": primary_provider_error,
                    "fallback_provider": fallback_provider,
                    "fallback_reason": fallback_reason,
                },
            )
            all_docs.extend(docs)
            results.append(BatchProcessFilesResult(file_id=file.id, status="prepared"))
        except Exception as e:
            log.error(f"process_files_batch: Error processing file {file.id}: {str(e)}")
            errors.append(
                BatchProcessFilesResult(file_id=file.id, status="failed", error=str(e))
            )

    if all_docs:
        try:
            save_docs_to_vector_db(
                request=request,
                docs=all_docs,
                collection_name=collection_name,
                add=True,
                user=user,
            )
            for result in results:
                Files.update_file_metadata_by_id(
                    result.file_id,
                    {
                        "collection_name": collection_name,
                        "processing_mode": FILE_PROCESSING_MODE_RETRIEVAL,
                        "resolved_processing_mode": FILE_PROCESSING_MODE_RETRIEVAL,
                    },
                )
                result.status = "completed"
        except Exception as e:
            log.error(
                f"process_files_batch: Error saving documents to vector DB: {str(e)}"
            )
            for result in results:
                result.status = "failed"
                errors.append(
                    BatchProcessFilesResult(file_id=result.file_id, error=str(e))
                )

    return BatchProcessFilesResponse(results=results, errors=errors)
