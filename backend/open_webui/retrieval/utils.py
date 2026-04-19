import logging
import os
import math
import time
from typing import Optional, Union

import requests
import hashlib
from concurrent.futures import ThreadPoolExecutor

from huggingface_hub import snapshot_download
from langchain.retrievers import ContextualCompressionRetriever, EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document

from open_webui.config import VECTOR_DB
from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
from open_webui.retrieval.document_processing import (
    FILE_PROCESSING_MODE_FULL_CONTEXT,
    normalize_file_processing_mode,
)

from open_webui.models.users import UserModel
from open_webui.models.files import Files

from open_webui.retrieval.vector.main import GetResult


from open_webui.env import (
    SRC_LOG_LEVELS,
    OFFLINE_MODE,
    ENABLE_FORWARD_USER_INFO_HEADERS,
)
from open_webui.config import (
    RAG_EMBEDDING_QUERY_PREFIX,
    RAG_EMBEDDING_CONTENT_PREFIX,
    RAG_EMBEDDING_PREFIX_FIELD_NAME,
)
from open_webui.utils.error_handling import (
    build_error_detail,
    read_requests_error_payload,
)
from open_webui.utils.headers import include_user_info_headers
from open_webui.utils.optional_dependencies import format_optional_dependency_error

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


class BatchTooLargeError(RuntimeError):
    """Embedding provider rejected a batch as too large. Safe to split and retry."""


class ChunkTooLargeError(RuntimeError):
    """A single chunk was rejected even after splitting to size 1.
    Caller must NOT retry; user must lower the document chunk size."""


def _call_with_batch_split(texts, call_once):
    """Invoke call_once(texts); on BatchTooLargeError, recursively split in half.
    If a size-1 batch is still rejected, raise ChunkTooLargeError instead."""
    try:
        return call_once(texts)
    except BatchTooLargeError as exc:
        if len(texts) <= 1:
            raise ChunkTooLargeError(
                "A single chunk was rejected by the embedding provider even at "
                "batch size 1, indicating the chunk exceeds the model's input "
                f"token limit. Original: {exc}"
            ) from exc
        mid = len(texts) // 2
        log.warning(
            "Embedding batch of %d rejected as too large, splitting into %d + %d and retrying",
            len(texts),
            mid,
            len(texts) - mid,
        )
        return _call_with_batch_split(texts[:mid], call_once) + _call_with_batch_split(
            texts[mid:], call_once
        )


from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.retrievers import BaseRetriever


class VectorSearchRetriever(BaseRetriever):
    collection_name: Any
    embedding_function: Any
    top_k: int

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        result = VECTOR_DB_CLIENT.search(
            collection_name=self.collection_name,
            vectors=[self.embedding_function(query, RAG_EMBEDDING_QUERY_PREFIX)],
            limit=self.top_k,
        )

        ids = result.ids[0]
        metadatas = result.metadatas[0]
        documents = result.documents[0]

        results = []
        for idx in range(len(ids)):
            results.append(
                Document(
                    metadata=metadatas[idx],
                    page_content=documents[idx],
                )
            )
        return results


def query_doc(
    collection_name: str, query_embedding: list[float], k: int, user: UserModel = None
):
    try:
        log.debug(f"query_doc:doc {collection_name}")
        result = VECTOR_DB_CLIENT.search(
            collection_name=collection_name,
            vectors=[query_embedding],
            limit=k,
        )

        if result:
            log.info(f"query_doc:result {result.ids} {result.metadatas}")

        return result
    except Exception as e:
        log.exception(f"Error querying doc {collection_name} with limit {k}: {e}")
        raise e


def get_doc(collection_name: str, user: UserModel = None):
    try:
        log.debug(f"get_doc:doc {collection_name}")
        result = VECTOR_DB_CLIENT.get(collection_name=collection_name)

        if result:
            log.info(f"query_doc:result {result.ids} {result.metadatas}")

        return result
    except Exception as e:
        log.exception(f"Error getting doc {collection_name}: {e}")
        raise e


def get_enriched_texts(collection_result: GetResult) -> list[str]:
    enriched_texts = []
    for idx, text in enumerate(collection_result.documents[0]):
        metadata = collection_result.metadatas[0][idx]
        metadata_parts = [text]

        if metadata.get("name"):
            filename = metadata["name"]
            filename_tokens = (
                filename.replace("_", " ").replace("-", " ").replace(".", " ")
            )
            metadata_parts.append(
                f"Filename: {filename} {filename_tokens} {filename_tokens}"
            )

        if metadata.get("title"):
            metadata_parts.append(f'Title: {metadata["title"]}')

        if metadata.get("headings") and isinstance(metadata["headings"], list):
            headings = " > ".join(str(h) for h in metadata["headings"])
            metadata_parts.append(f"Section: {headings}")

        if metadata.get("source"):
            metadata_parts.append(f'Source: {metadata["source"]}')

        if metadata.get("snippet"):
            metadata_parts.append(f'Snippet: {metadata["snippet"]}')

        enriched_texts.append(" ".join(metadata_parts))

    return enriched_texts


def query_doc_with_hybrid_search(
    collection_name: str,
    collection_result: GetResult,
    query: str,
    embedding_function,
    k: int,
    reranking_function,
    k_reranker: int,
    r: float,
    bm25_weight: float = 0.5,
    enable_enriched_texts: bool = False,
    user: UserModel = None,
) -> dict:
    try:
        log.debug(f"query_doc_with_hybrid_search:doc {collection_name}")
        bm25_texts = (
            get_enriched_texts(collection_result)
            if enable_enriched_texts
            else collection_result.documents[0]
        )
        bm25_retriever = BM25Retriever.from_texts(
            texts=bm25_texts,
            metadatas=collection_result.metadatas[0],
        )
        bm25_retriever.k = k

        vector_search_retriever = VectorSearchRetriever(
            collection_name=collection_name,
            embedding_function=embedding_function,
            top_k=k,
        )

        ensemble_retriever = EnsembleRetriever(
            retrievers=[bm25_retriever, vector_search_retriever], weights=[bm25_weight, 1 - bm25_weight]
        )
        compressor = RerankCompressor(
            embedding_function=embedding_function,
            top_n=k_reranker,
            reranking_function=reranking_function,
            r_score=r,
        )

        compression_retriever = ContextualCompressionRetriever(
            base_compressor=compressor, base_retriever=ensemble_retriever
        )

        result = compression_retriever.invoke(query)

        distances = [d.metadata.get("score") for d in result]
        documents = [d.page_content for d in result]
        metadatas = [d.metadata for d in result]

        # retrieve only min(k, k_reranker) items, sort and cut by distance if k < k_reranker
        if k < k_reranker:
            sorted_items = sorted(
                zip(distances, metadatas, documents), key=lambda x: x[0], reverse=True
            )
            sorted_items = sorted_items[:k]
            distances, documents, metadatas = map(list, zip(*sorted_items))

        result = {
            "distances": [distances],
            "documents": [documents],
            "metadatas": [metadatas],
        }

        log.info(
            "query_doc_with_hybrid_search:result "
            + f'{result["metadatas"]} {result["distances"]}'
        )
        return result
    except Exception as e:
        log.exception(f"Error querying doc {collection_name} with hybrid search: {e}")
        log.warning(
            "Falling back to vector search for collection %s after hybrid search failed.",
            collection_name,
        )
        fallback = query_doc(
            collection_name=collection_name,
            query_embedding=embedding_function(query, RAG_EMBEDDING_QUERY_PREFIX),
            k=k,
            user=user,
        )
        if fallback is None:
            raise
        return fallback.model_dump()


def merge_get_results(get_results: list[dict]) -> dict:
    # Initialize lists to store combined data
    combined_documents = []
    combined_metadatas = []
    combined_ids = []

    for data in get_results:
        combined_documents.extend(data["documents"][0])
        combined_metadatas.extend(data["metadatas"][0])
        combined_ids.extend(data["ids"][0])

    # Create the output dictionary
    result = {
        "documents": [combined_documents],
        "metadatas": [combined_metadatas],
        "ids": [combined_ids],
    }

    return result


def merge_and_sort_query_results(query_results: list[dict], k: int) -> dict:
    # Initialize lists to store combined data
    combined = dict()  # To store documents with unique document hashes

    for data in query_results:
        distances = data["distances"][0]
        documents = data["documents"][0]
        metadatas = data["metadatas"][0]

        for distance, document, metadata in zip(distances, documents, metadatas):
            if isinstance(document, str):
                doc_hash = hashlib.md5(
                    document.encode()
                ).hexdigest()  # Compute a hash for uniqueness

                if doc_hash not in combined.keys():
                    combined[doc_hash] = (distance, document, metadata)
                    continue  # if doc is new, no further comparison is needed

                # if doc is alredy in, but new distance is better, update
                if distance > combined[doc_hash][0]:
                    combined[doc_hash] = (distance, document, metadata)

    combined = list(combined.values())
    # Sort the list based on distances
    combined.sort(key=lambda x: x[0], reverse=True)

    # Slice to keep only the top k elements
    sorted_distances, sorted_documents, sorted_metadatas = (
        zip(*combined[:k]) if combined else ([], [], [])
    )

    # Create and return the output dictionary
    return {
        "distances": [list(sorted_distances)],
        "documents": [list(sorted_documents)],
        "metadatas": [list(sorted_metadatas)],
    }


def get_all_items_from_collections(collection_names: list[str]) -> dict:
    results = []

    for collection_name in collection_names:
        if collection_name:
            try:
                result = get_doc(collection_name=collection_name)
                if result is not None:
                    results.append(result.model_dump())
            except Exception as e:
                log.exception(f"Error when querying the collection: {e}")
        else:
            pass

    return merge_get_results(results)


def query_collection(
    collection_names: list[str],
    queries: list[str],
    embedding_function,
    k: int,
) -> dict:
    results = []
    for query in queries:
        log.debug(f"query_collection:query {query}")
        query_embedding = embedding_function(query, prefix=RAG_EMBEDDING_QUERY_PREFIX)
        for collection_name in collection_names:
            if collection_name:
                try:
                    result = query_doc(
                        collection_name=collection_name,
                        k=k,
                        query_embedding=query_embedding,
                    )
                    if result is not None:
                        results.append(result.model_dump())
                except Exception as e:
                    log.exception(f"Error when querying the collection: {e}")
            else:
                pass

    return merge_and_sort_query_results(results, k=k)


def query_collection_with_hybrid_search(
    collection_names: list[str],
    queries: list[str],
    embedding_function,
    k: int,
    reranking_function,
    k_reranker: int,
    r: float,
    bm25_weight: float = 0.5,
    enable_enriched_texts: bool = False,
) -> dict:
    results = []
    error = False
    # Fetch collection data once per collection sequentially
    # Avoid fetching the same data multiple times later
    collection_results = {}
    for collection_name in collection_names:
        try:
            log.debug(
                f"query_collection_with_hybrid_search:VECTOR_DB_CLIENT.get:collection {collection_name}"
            )
            collection_results[collection_name] = VECTOR_DB_CLIENT.get(
                collection_name=collection_name
            )
        except Exception as e:
            log.exception(f"Failed to fetch collection {collection_name}: {e}")
            collection_results[collection_name] = None

    log.info(
        f"Starting hybrid search for {len(queries)} queries in {len(collection_names)} collections..."
    )

    def process_query(collection_name, query):
        try:
            result = query_doc_with_hybrid_search(
                collection_name=collection_name,
                collection_result=collection_results[collection_name],
                query=query,
                embedding_function=embedding_function,
                k=k,
                reranking_function=reranking_function,
                k_reranker=k_reranker,
                r=r,
                bm25_weight=bm25_weight,
                enable_enriched_texts=enable_enriched_texts,
            )
            return result, None
        except Exception as e:
            log.exception(f"Error when querying the collection with hybrid_search: {e}")
            return None, e

    # Prepare tasks for all collections and queries
    # Avoid running any tasks for collections that failed to fetch data (have assigned None)
    tasks = [
        (cn, q)
        for cn in collection_names
        if collection_results[cn] is not None
        for q in queries
    ]

    with ThreadPoolExecutor() as executor:
        future_results = [executor.submit(process_query, cn, q) for cn, q in tasks]
        task_results = [future.result() for future in future_results]

    for result, err in task_results:
        if err is not None:
            error = True
        elif result is not None:
            results.append(result)

    if error and not results:
        log.warning(
            "Hybrid search failed for all collections; falling back to non-hybrid retrieval."
        )
        return query_collection(
            collection_names=collection_names,
            queries=queries,
            embedding_function=embedding_function,
            k=k,
        )

    return merge_and_sort_query_results(results, k=k)


def get_embedding_function(
    embedding_engine,
    embedding_model,
    embedding_function,
    url,
    key,
    embedding_batch_size,
    azure_api_version=None,
    enable_async=True,
    concurrent_requests=0,
):
    if embedding_engine == "":
        if embedding_function is None:
            message = format_optional_dependency_error(
                feature="Local embedding models",
                packages=["sentence-transformers", "transformers", "accelerate"],
                install_profiles=["rag-local", "local-rag", "full"],
                details="Or switch `RAG_EMBEDDING_ENGINE=openai|ollama` to use a remote embeddings API.",
            )

            def _missing_local_embedding(*args, **kwargs):
                raise RuntimeError(message)

            return _missing_local_embedding
        return lambda query, prefix=None, user=None: embedding_function.encode(
            query, **({"prompt": prefix} if prefix else {})
        ).tolist()
    elif embedding_engine in ["ollama", "openai", "azure_openai"]:
        func = lambda query, prefix=None, user=None: generate_embeddings(
            engine=embedding_engine,
            model=embedding_model,
            text=query,
            prefix=prefix,
            url=url,
            key=key,
            user=user,
            azure_api_version=azure_api_version,
        )

        def generate_multiple(query, prefix, user, func):
            if isinstance(query, list):
                batches = [
                    (i, query[i : i + embedding_batch_size])
                    for i in range(0, len(query), embedding_batch_size)
                ]

                def run_batch(index_and_batch):
                    i, batch = index_and_batch
                    try:
                        batch_result = _call_with_batch_split(
                            batch,
                            lambda sub: func(sub, prefix=prefix, user=user),
                        )
                    except ChunkTooLargeError:
                        raise
                    except Exception as exc:
                        raise RuntimeError(
                            f"Embedding generation failed for batch starting at index {i}: "
                            f"{build_error_detail(exc)}"
                        ) from exc

                    if batch_result is None:
                        raise RuntimeError(
                            "Embedding generation failed for batch starting at index "
                            f"{i}: no embedding vectors were returned."
                        )

                    return i, batch_result

                if enable_async and len(batches) > 1:
                    max_workers = concurrent_requests or len(batches)
                    max_workers = max(1, min(max_workers, len(batches)))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        batch_results = list(executor.map(run_batch, batches))
                    batch_results.sort(key=lambda item: item[0])
                else:
                    batch_results = [run_batch(item) for item in batches]

                embeddings = []
                for _, batch_result in batch_results:
                    embeddings.extend(batch_result)
                return embeddings
            else:
                return func(query, prefix, user)

        return lambda query, prefix=None, user=None: generate_multiple(
            query, prefix, user, func
        )
    else:
        raise ValueError(f"Unknown embedding engine: {embedding_engine}")


def get_sources_from_files(
    request,
    files,
    queries,
    embedding_function,
    k,
    reranking_function,
    k_reranker,
    r,
    hybrid_search,
    full_context=False,
    bm25_weight: float = 0.5,
    enable_enriched_texts: bool = False,
):
    log.debug(
        f"files: {files} {queries} {embedding_function} {reranking_function} {full_context}"
    )

    extracted_collections = []
    relevant_contexts = []

    for file in files:

        context = None
        nested_file = file.get("file") if isinstance(file.get("file"), dict) else {}
        requested_mode = normalize_file_processing_mode(
            file.get("processing_mode")
            or nested_file.get("meta", {}).get("processing_mode")
            or (
                FILE_PROCESSING_MODE_FULL_CONTEXT
                if file.get("context") == "full"
                else None
            ),
            "",
        )
        if file.get("docs"):
            # BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL
            context = {
                "documents": [[doc.get("content") for doc in file.get("docs")]],
                "metadatas": [[doc.get("metadata") for doc in file.get("docs")]],
            }
        elif requested_mode == FILE_PROCESSING_MODE_FULL_CONTEXT:
            # Manual Full Mode Toggle
            context = {
                "documents": [[nested_file.get("data", {}).get("content")]],
                "metadatas": [[{"file_id": file.get("id"), "name": file.get("name")}]],
            }
        elif (
            file.get("type") != "web_search"
            and request.app.state.config.BYPASS_EMBEDDING_AND_RETRIEVAL
        ):
            # BYPASS_EMBEDDING_AND_RETRIEVAL
            if file.get("type") == "collection":
                file_ids = file.get("data", {}).get("file_ids", [])

                documents = []
                metadatas = []
                for file_id in file_ids:
                    file_object = Files.get_file_by_id(file_id)

                    if file_object:
                        documents.append(file_object.data.get("content", ""))
                        metadatas.append(
                            {
                                "file_id": file_id,
                                "name": file_object.filename,
                                "source": file_object.filename,
                            }
                        )

                context = {
                    "documents": [documents],
                    "metadatas": [metadatas],
                }

            elif file.get("id"):
                file_object = Files.get_file_by_id(file.get("id"))
                if file_object:
                    context = {
                        "documents": [[file_object.data.get("content", "")]],
                        "metadatas": [
                            [
                                {
                                    "file_id": file.get("id"),
                                    "name": file_object.filename,
                                    "source": file_object.filename,
                                }
                            ]
                        ],
                    }
            elif nested_file.get("data"):
                context = {
                    "documents": [[nested_file.get("data", {}).get("content")]],
                    "metadatas": [
                        [nested_file.get("data", {}).get("metadata", {})]
                    ],
                }
        else:
            collection_names = []
            if file.get("type") == "collection":
                if file.get("legacy"):
                    collection_names = file.get("collection_names", [])
                else:
                    collection_names.append(file["id"])
            elif file.get("collection_name"):
                collection_names.append(file["collection_name"])
            elif file.get("id"):
                if file.get("legacy"):
                    collection_names.append(f"{file['id']}")
                else:
                    collection_names.append(f"file-{file['id']}")

            collection_names = set(collection_names).difference(extracted_collections)
            if not collection_names:
                log.debug(f"skipping {file} as it has already been extracted")
                continue

            if full_context:
                try:
                    context = get_all_items_from_collections(collection_names)
                except Exception as e:
                    log.exception(e)

            else:
                try:
                    context = None
                    if file.get("type") == "text":
                        context = file["content"]
                    else:
                        if hybrid_search:
                            try:
                                context = query_collection_with_hybrid_search(
                                    collection_names=collection_names,
                                    queries=queries,
                                    embedding_function=embedding_function,
                                    k=k,
                                    reranking_function=reranking_function,
                                    k_reranker=k_reranker,
                                    r=r,
                                    bm25_weight=bm25_weight,
                                    enable_enriched_texts=enable_enriched_texts,
                                )
                            except Exception as e:
                                log.debug(
                                    "Error when using hybrid search, using"
                                    " non hybrid search as fallback."
                                )

                        if (not hybrid_search) or (context is None):
                            context = query_collection(
                                collection_names=collection_names,
                                queries=queries,
                                embedding_function=embedding_function,
                                k=k,
                            )
                except Exception as e:
                    log.exception(e)

            extracted_collections.extend(collection_names)

        if context:
            if "data" in file:
                del file["data"]

            relevant_contexts.append({**context, "file": file})

    sources = []
    for context in relevant_contexts:
        try:
            if "documents" in context:
                if "metadatas" in context:
                    source = {
                        "source": context["file"],
                        "document": context["documents"][0],
                        "metadata": context["metadatas"][0],
                    }
                    if "distances" in context and context["distances"]:
                        source["distances"] = context["distances"][0]

                    sources.append(source)
        except Exception as e:
            log.exception(e)

    return sources


def get_model_path(model: str, update_model: bool = False):
    # Construct huggingface_hub kwargs with local_files_only to return the snapshot path
    cache_dir = os.getenv("SENTENCE_TRANSFORMERS_HOME")

    local_files_only = not update_model

    if OFFLINE_MODE:
        local_files_only = True

    snapshot_kwargs = {
        "cache_dir": cache_dir,
        "local_files_only": local_files_only,
    }

    log.debug(f"model: {model}")
    log.debug(f"snapshot_kwargs: {snapshot_kwargs}")

    # Inspiration from upstream sentence_transformers
    if (
        os.path.exists(model)
        or ("\\" in model or model.count("/") > 1)
        and local_files_only
    ):
        # If fully qualified path exists, return input, else set repo_id
        return model
    elif "/" not in model:
        # Set valid repo_id for model short-name
        model = "sentence-transformers" + "/" + model

    snapshot_kwargs["repo_id"] = model

    # Attempt to query the huggingface_hub library to determine the local path and/or to update
    try:
        model_repo_path = snapshot_download(**snapshot_kwargs)
        log.debug(f"model_repo_path: {model_repo_path}")
        return model_repo_path
    except Exception as e:
        log.exception(f"Cannot determine model snapshot path: {e}")
        return model


def generate_openai_batch_embeddings(
    model: str,
    texts: list[str],
    url: str = "https://api.openai.com/v1",
    key: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"generate_openai_batch_embeddings:model {model} batch size: {len(texts)}"
        )
        json_data = {"input": texts, "model": model}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            json_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        r = requests.post(
            f"{url}/embeddings",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
            json=json_data,
            timeout=60,
        )
        if r.status_code == 413:
            raise BatchTooLargeError(
                build_error_detail(read_requests_error_payload(r), r.reason)
            )
        if r.status_code == 400:
            payload_text = str(read_requests_error_payload(r)).lower()
            if (
                ("batch size" in payload_text and "maximum" in payload_text)
                or "too many inputs" in payload_text
            ):
                raise BatchTooLargeError(
                    build_error_detail(read_requests_error_payload(r), r.reason)
                )
        if not r.ok:
            raise RuntimeError(build_error_detail(read_requests_error_payload(r), r.reason))
        data = r.json()
        if "data" in data:
            return [elem["embedding"] for elem in data["data"]]
        else:
            raise RuntimeError("Embedding API returned no embedding data.")
    except BatchTooLargeError:
        raise
    except Exception as e:
        log.exception(f"Error generating openai batch embeddings: {e}")
        raise RuntimeError(build_error_detail(e)) from e


def generate_ollama_batch_embeddings(
    model: str,
    texts: list[str],
    url: str,
    key: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> Optional[list[list[float]]]:
    try:
        log.debug(
            f"generate_ollama_batch_embeddings:model {model} batch size: {len(texts)}"
        )
        json_data = {"input": texts, "model": model}
        if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
            json_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

        r = requests.post(
            f"{url}/api/embed",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
                **(
                    {
                        "X-OpenWebUI-User-Name": user.name,
                        "X-OpenWebUI-User-Id": user.id,
                        "X-OpenWebUI-User-Email": user.email,
                        "X-OpenWebUI-User-Role": user.role,
                    }
                    if ENABLE_FORWARD_USER_INFO_HEADERS and user
                    else {}
                ),
            },
            json=json_data,
            timeout=60,
        )
        if not r.ok:
            raise RuntimeError(build_error_detail(read_requests_error_payload(r), r.reason))
        data = r.json()

        if "embeddings" in data:
            return data["embeddings"]
        else:
            raise RuntimeError("Embedding API returned no embeddings.")
    except Exception as e:
        log.exception(f"Error generating ollama batch embeddings: {e}")
        raise RuntimeError(build_error_detail(e)) from e


def generate_azure_openai_batch_embeddings(
    model: str,
    texts: list[str],
    url: str,
    key: str = "",
    version: str = "",
    prefix: str = None,
    user: UserModel = None,
) -> list[list[float]]:
    log.debug(
        f"generate_azure_openai_batch_embeddings:deployment {model} batch size: {len(texts)}"
    )
    json_data = {"input": texts}
    if isinstance(RAG_EMBEDDING_PREFIX_FIELD_NAME, str) and isinstance(prefix, str):
        json_data[RAG_EMBEDDING_PREFIX_FIELD_NAME] = prefix

    request_url = (
        f"{str(url or '').rstrip('/')}/openai/deployments/{model}/embeddings"
        f"?api-version={version}"
    )

    for _ in range(5):
        headers = {
            "Content-Type": "application/json",
            "api-key": key,
        }
        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
            headers = include_user_info_headers(headers, user)

        response = requests.post(
            request_url,
            headers=headers,
            json=json_data,
        )
        if response.status_code == 429:
            retry = float(response.headers.get("Retry-After", "1"))
            time.sleep(retry)
            continue
        response.raise_for_status()
        data = response.json()
        if "data" in data:
            return [elem["embedding"] for elem in data["data"]]
        raise ValueError(
            "Unexpected Azure OpenAI embeddings response: missing 'data' key"
        )

    raise RuntimeError(
        "Azure OpenAI embedding request failed: max retries (429) exceeded"
    )


def generate_embeddings(
    engine: str,
    model: str,
    text: Union[str, list[str]],
    prefix: Union[str, None] = None,
    **kwargs,
):
    url = kwargs.get("url", "")
    key = kwargs.get("key", "")
    user = kwargs.get("user")

    if prefix is not None and RAG_EMBEDDING_PREFIX_FIELD_NAME is None:
        if isinstance(text, list):
            text = [f"{prefix}{text_element}" for text_element in text]
        else:
            text = f"{prefix}{text}"

    if engine == "ollama":
        if isinstance(text, list):
            embeddings = generate_ollama_batch_embeddings(
                **{
                    "model": model,
                    "texts": text,
                    "url": url,
                    "key": key,
                    "prefix": prefix,
                    "user": user,
                }
            )
        else:
            embeddings = generate_ollama_batch_embeddings(
                **{
                    "model": model,
                    "texts": [text],
                    "url": url,
                    "key": key,
                    "prefix": prefix,
                    "user": user,
                }
            )
        if embeddings is None:
            return None
        return embeddings[0] if isinstance(text, str) else embeddings
    elif engine == "openai":
        if isinstance(text, list):
            embeddings = generate_openai_batch_embeddings(
                model, text, url, key, prefix, user
            )
        else:
            embeddings = generate_openai_batch_embeddings(
                model, [text], url, key, prefix, user
            )
        if embeddings is None:
            return None
        return embeddings[0] if isinstance(text, str) else embeddings
    elif engine == "azure_openai":
        version = kwargs.get("azure_api_version", "")
        if isinstance(text, list):
            embeddings = generate_azure_openai_batch_embeddings(
                model, text, url, key, version, prefix, user
            )
        else:
            embeddings = generate_azure_openai_batch_embeddings(
                model, [text], url, key, version, prefix, user
            )
        if embeddings is None:
            return None
        return embeddings[0] if isinstance(text, str) else embeddings


import operator
from typing import Optional, Sequence

from langchain_core.callbacks import Callbacks
from langchain_core.documents import BaseDocumentCompressor, Document


class RerankCompressor(BaseDocumentCompressor):
    embedding_function: Any
    top_n: int
    reranking_function: Any
    r_score: float

    class Config:
        extra = "forbid"
        arbitrary_types_allowed = True

    def _to_list(self, values):
        if hasattr(values, "tolist"):
            values = values.tolist()
        return list(values)

    def _cosine_similarity_scores(self, query_embedding, document_embeddings):
        query_vector = self._to_list(query_embedding)
        document_vectors = [self._to_list(vector) for vector in document_embeddings]

        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        scores = []
        for document_vector in document_vectors:
            document_norm = math.sqrt(
                sum(value * value for value in document_vector)
            ) or 1.0
            dot_product = sum(
                q_value * d_value
                for q_value, d_value in zip(query_vector, document_vector)
            )
            scores.append(dot_product / (query_norm * document_norm))
        return scores

    def compress_documents(
        self,
        documents: Sequence[Document],
        query: str,
        callbacks: Optional[Callbacks] = None,
    ) -> Sequence[Document]:
        reranking = self.reranking_function is not None

        if reranking:
            scores = self.reranking_function.predict(
                [(query, doc.page_content) for doc in documents]
            )
        else:
            query_embedding = self.embedding_function(query, RAG_EMBEDDING_QUERY_PREFIX)
            document_embedding = self.embedding_function(
                [doc.page_content for doc in documents], RAG_EMBEDDING_CONTENT_PREFIX
            )
            scores = self._cosine_similarity_scores(
                query_embedding, document_embedding
            )

        docs_with_scores = list(zip(documents, self._to_list(scores)))
        if self.r_score:
            docs_with_scores = [
                (d, s) for d, s in docs_with_scores if s >= self.r_score
            ]

        result = sorted(docs_with_scores, key=operator.itemgetter(1), reverse=True)
        final_results = []
        for doc, doc_score in result[: self.top_n]:
            metadata = doc.metadata
            metadata["score"] = doc_score
            doc = Document(
                page_content=doc.page_content,
                metadata=metadata,
            )
            final_results.append(doc)
        return final_results
