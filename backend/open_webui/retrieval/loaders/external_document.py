import logging
import os
from typing import List
from urllib.parse import quote, urlparse, urlunparse

import requests
from langchain_core.document_loaders import BaseLoader
from langchain_core.documents import Document

from open_webui.utils.headers import include_user_info_headers

log = logging.getLogger(__name__)


def _build_legacy_process_url(url: str) -> str:
    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    if path.endswith("/process"):
        return urlunparse(parsed._replace(path=path))
    return urlunparse(parsed._replace(path=f"{path}/process" if path else "/process"))


class ExternalDocumentLoader(BaseLoader):
    def __init__(
        self,
        file_path,
        url: str,
        api_key: str,
        url_is_full_path: bool = False,
        mime_type=None,
        user=None,
        **kwargs,
    ) -> None:
        self.url = url
        self.url_is_full_path = url_is_full_path
        self.api_key = api_key
        self.file_path = file_path
        self.mime_type = mime_type
        self.user = user

    def load(self) -> List[Document]:
        with open(self.file_path, "rb") as file_handle:
            data = file_handle.read()

        headers = {}
        if self.mime_type is not None:
            headers["Content-Type"] = self.mime_type

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            headers["X-Filename"] = quote(os.path.basename(self.file_path))
        except Exception:
            pass

        if self.user is not None:
            headers = include_user_info_headers(headers, self.user)

        url = str(self.url or "").strip()
        if self.url_is_full_path:
            request_url = url
        else:
            request_url = _build_legacy_process_url(url)

        try:
            response = requests.put(request_url, data=data, headers=headers)
        except Exception as exc:
            log.error(f"Error connecting to endpoint: {exc}")
            raise Exception(f"Error connecting to endpoint: {exc}") from exc

        if not response.ok:
            raise Exception(
                f"Error loading document: {response.status_code} {response.text}"
            )

        response_data = response.json()
        if not response_data:
            raise Exception("Error loading document: No content returned")

        if isinstance(response_data, dict):
            return [
                Document(
                    page_content=response_data.get("page_content"),
                    metadata=response_data.get("metadata"),
                )
            ]

        if isinstance(response_data, list):
            return [
                Document(
                    page_content=document.get("page_content"),
                    metadata=document.get("metadata"),
                )
                for document in response_data
            ]

        raise Exception("Error loading document: Unable to parse content")
