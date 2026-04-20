import json
import logging
import requests
from typing import Iterator, List, Literal, Optional, Union

from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document
from open_webui.env import SRC_LOG_LEVELS
from open_webui.retrieval.web.tavily import build_tavily_api_url

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])


class TavilyExtractError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        response_text: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class TavilyExtractAuthError(TavilyExtractError):
    pass


def _extract_tavily_error_text(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            candidate = payload.get("detail") or payload.get("message") or payload.get(
                "error"
            )
            if isinstance(candidate, dict):
                candidate = (
                    candidate.get("message")
                    or candidate.get("detail")
                    or json.dumps(candidate, ensure_ascii=False)
                )
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    except Exception:
        pass

    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text[:500]

    return f"HTTP {response.status_code}"


class TavilyLoader(BaseLoader):
    """Extract web page content from URLs using Tavily Extract API.

    This is a LangChain document loader that uses Tavily's Extract API to
    retrieve content from web pages and return it as Document objects.

    Args:
        urls: URL or list of URLs to extract content from.
        api_key: The Tavily API key.
        extract_depth: Depth of extraction, either "basic" or "advanced".
        continue_on_failure: Whether to continue if extraction of a URL fails.
    """

    def __init__(
        self,
        urls: Union[str, List[str]],
        api_key: str,
        extract_depth: Literal["basic", "advanced"] = "basic",
        continue_on_failure: bool = True,
        api_base_url: Optional[str] = None,
        force_mode: bool = False,
    ) -> None:
        """Initialize Tavily Extract client.

        Args:
            urls: URL or list of URLs to extract content from.
            api_key: The Tavily API key.
            include_images: Whether to include images in the extraction.
            extract_depth: Depth of extraction, either "basic" or "advanced".
                advanced extraction retrieves more data, including tables and
                embedded content, with higher success but may increase latency.
                basic costs 1 credit per 5 successful URL extractions,
                advanced costs 2 credits per 5 successful URL extractions.
            continue_on_failure: Whether to continue if extraction of a URL fails.
        """
        if not urls:
            raise ValueError("At least one URL must be provided.")
        if not str(api_key or "").strip():
            raise ValueError("TAVILY_API_KEY is required for the Tavily loader.")

        self.api_key = api_key
        self.urls = urls if isinstance(urls, list) else [urls]
        self.extract_depth = extract_depth
        self.continue_on_failure = continue_on_failure
        self.api_url = build_tavily_api_url(
            api_base_url,
            "extract",
            force_mode=force_mode,
        )

    def lazy_load(self) -> Iterator[Document]:
        """Extract and yield documents from the URLs using Tavily Extract API."""
        batch_size = 20
        for i in range(0, len(self.urls), batch_size):
            batch_urls = self.urls[i : i + batch_size]
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                }
                # Use string for single URL, array for multiple URLs
                urls_param = batch_urls[0] if len(batch_urls) == 1 else batch_urls
                payload = {"urls": urls_param, "extract_depth": self.extract_depth}
                # Make the API call
                response = requests.post(self.api_url, headers=headers, json=payload)
                if response.status_code >= 400:
                    error_text = _extract_tavily_error_text(response)
                    error_cls = (
                        TavilyExtractAuthError
                        if response.status_code in (401, 403)
                        else TavilyExtractError
                    )
                    raise error_cls(
                        f"Tavily extract request failed: {error_text}",
                        status_code=response.status_code,
                        response_text=error_text,
                    )
                response_data = response.json()
                # Process successful results
                for result in response_data.get("results", []):
                    url = result.get("url", "")
                    content = result.get("raw_content", "")
                    if not content:
                        log.warning(f"No content extracted from {url}")
                        continue
                    # Add URLs as metadata
                    metadata = {"source": url}
                    yield Document(
                        page_content=content,
                        metadata=metadata,
                    )
                for failed in response_data.get("failed_results", []):
                    url = failed.get("url", "")
                    error = failed.get("error", "Unknown error")
                    log.error(f"Failed to extract content from {url}: {error}")
            except TavilyExtractAuthError:
                raise
            except Exception as e:
                if self.continue_on_failure:
                    log.error(f"Error extracting content from batch {batch_urls}: {e}")
                else:
                    raise e
