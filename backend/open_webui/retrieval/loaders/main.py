import requests
import logging
import ftfy
import sys
import json
from os.path import basename

from langchain_core.documents import Document

from open_webui.retrieval.loaders.datalab_marker import DatalabMarkerLoader
from open_webui.retrieval.loaders.external_document import ExternalDocumentLoader
from open_webui.retrieval.loaders.mineru import MinerULoader
from open_webui.retrieval.loaders.mistral import MistralLoader

from open_webui.env import SRC_LOG_LEVELS, GLOBAL_LOG_LEVEL
from open_webui.utils.optional_dependencies import (
    OptionalDependencyError,
    format_optional_dependency_error,
    require_module,
)
from open_webui.utils.file_upload_diagnostics import (
    FileUploadDiagnosticError,
    classify_file_upload_error,
    is_archive_file,
    make_unsupported_binary_diagnostic,
)

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["RAG"])

known_source_ext = [
    "go",
    "py",
    "java",
    "sh",
    "bat",
    "ps1",
    "cmd",
    "js",
    "ts",
    "css",
    "cpp",
    "hpp",
    "h",
    "c",
    "cs",
    "sql",
    "log",
    "ini",
    "pl",
    "pm",
    "r",
    "dart",
    "dockerfile",
    "env",
    "php",
    "hs",
    "hsc",
    "lua",
    "nginxconf",
    "conf",
    "m",
    "mm",
    "plsql",
    "perl",
    "rb",
    "rs",
    "db2",
    "scala",
    "bash",
    "swift",
    "vue",
    "svelte",
    "msg",
    "ex",
    "exs",
    "erl",
    "tsx",
    "jsx",
    "hs",
    "lhs",
    "json",
]


class TikaLoader:
    def __init__(self, url, file_path, mime_type=None, extract_images=None):
        self.url = url
        self.file_path = file_path
        self.mime_type = mime_type
        self.extract_images = extract_images

    def load(self) -> list[Document]:
        with open(self.file_path, "rb") as f:
            data = f.read()

        if self.mime_type is not None:
            headers = {"Content-Type": self.mime_type}
        else:
            headers = {}

        endpoint = self.url
        if not endpoint.endswith("/"):
            endpoint += "/"
        endpoint += "tika/text"

        if self.extract_images is True:
            headers["X-Tika-PDFextractInlineImages"] = "true"

        r = requests.put(endpoint, data=data, headers=headers)

        if r.ok:
            raw_metadata = r.json()
            text = raw_metadata.get("X-TIKA:content", "<No text content found>").strip()

            if "Content-Type" in raw_metadata:
                headers["Content-Type"] = raw_metadata["Content-Type"]

            log.debug("Tika extracted text: %s", text)

            return [Document(page_content=text, metadata=headers)]
        else:
            raise Exception(f"Error calling Tika: {r.reason}")


class DoclingLoader:
    def __init__(self, url, api_key=None, file_path=None, mime_type=None, params=None):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.file_path = file_path
        self.mime_type = mime_type
        self.params = params or {}

    def load(self) -> list[Document]:
        with open(self.file_path, "rb") as f:
            headers = {}
            if self.api_key:
                headers["X-Api-Key"] = f"{self.api_key}"

            files = {
                "files": (
                    self.file_path,
                    f,
                    self.mime_type or "application/octet-stream",
                )
            }

            params = {
                "image_export_mode": "placeholder",
                "table_mode": "accurate",
                **self.params,
            }

            endpoint = f"{self.url}/v1alpha/convert/file"
            r = requests.post(endpoint, files=files, data=params, headers=headers)

        if r.ok:
            result = r.json()
            document_data = result.get("document", {})
            text = document_data.get("md_content", "<No text content found>")

            metadata = {"Content-Type": self.mime_type} if self.mime_type else {}

            log.debug("Docling extracted text: %s", text)

            return [Document(page_content=text, metadata=metadata)]
        else:
            error_msg = f"Error calling Docling API: {r.reason}"
            if r.text:
                try:
                    error_data = r.json()
                    if "detail" in error_data:
                        error_msg += f" - {error_data['detail']}"
                except Exception:
                    error_msg += f" - {r.text}"
            raise Exception(f"Error calling Docling: {error_msg}")


class Loader:
    def __init__(self, engine: str = "", **kwargs):
        self.engine = engine
        self.user = kwargs.get("user", None)
        self.kwargs = kwargs

    def load(
        self, filename: str, file_content_type: str, file_path: str
    ) -> list[Document]:
        loader = self._get_loader(filename, file_content_type, file_path)
        try:
            docs = loader.load()
        except Exception as exc:
            raise FileUploadDiagnosticError(
                classify_file_upload_error(
                    exc,
                    filename=filename,
                    content_type=file_content_type,
                )
            ) from exc

        return [
            Document(
                page_content=ftfy.fix_text(doc.page_content), metadata=doc.metadata
            )
            for doc in docs
        ]

    def _is_text_file(self, file_ext: str, file_content_type: str) -> bool:
        return file_ext in known_source_ext or (
            file_content_type
            and file_content_type.find("text/") >= 0
            and not file_content_type.find("html") >= 0
        )

    def _get_loader_class(
        self,
        class_name: str,
        *,
        feature: str,
        packages: list[str],
        install_profiles: list[str],
    ):
        module = require_module(
            "langchain_community.document_loaders",
            feature=feature,
            packages=packages,
            install_profiles=install_profiles,
        )
        return getattr(module, class_name)

    def _get_text_loader(self):
        return self._get_loader_class(
            "TextLoader",
            feature="basic text document parsing",
            packages=["langchain-community"],
            install_profiles=["core", "full"],
        )

    def _get_optional_docs_loader(self, class_name: str, *, feature: str):
        return self._get_loader_class(
            class_name,
            feature=feature,
            packages=[
                "unstructured",
                "nltk",
                "pypandoc",
                "pandas",
                "openpyxl",
                "pyxlsb",
                "xlrd",
            ],
            install_profiles=["docs-full", "full"],
        )

    def _raise_docs_local_requirement(self, file_ext: str):
        raise OptionalDependencyError(
            format_optional_dependency_error(
                feature=f"Local parsing for `.{file_ext}` files",
                packages=[
                    "unstructured",
                    "nltk",
                    "pypandoc",
                    "pandas",
                    "openpyxl",
                    "pyxlsb",
                    "xlrd",
                ],
                install_profiles=["docs-full", "full"],
                details=(
                    "Use `CONTENT_EXTRACTION_ENGINE=docling` or `tika` for a lean remote parser, "
                    "or install the local docs profile for Office/OCR formats."
                ),
            )
        )

    def _get_loader(self, filename: str, file_content_type: str, file_path: str):
        file_ext = filename.split(".")[-1].lower()
        TextLoader = self._get_text_loader()

        if is_archive_file(filename, file_content_type):
            raise FileUploadDiagnosticError(
                classify_file_upload_error(
                    None,
                    filename=filename,
                    content_type=file_content_type,
                )
            )

        if (
            self.engine == "external"
            and self.kwargs.get("EXTERNAL_DOCUMENT_LOADER_URL")
            and self.kwargs.get("EXTERNAL_DOCUMENT_LOADER_API_KEY")
        ):
            loader = ExternalDocumentLoader(
                file_path=file_path,
                url=self.kwargs.get("EXTERNAL_DOCUMENT_LOADER_URL"),
                url_is_full_path=self.kwargs.get(
                    "EXTERNAL_DOCUMENT_LOADER_URL_IS_FULL_PATH", False
                ),
                api_key=self.kwargs.get("EXTERNAL_DOCUMENT_LOADER_API_KEY"),
                mime_type=file_content_type,
                user=self.user,
            )
        elif self.engine == "tika" and self.kwargs.get("TIKA_SERVER_URL"):
            if self._is_text_file(file_ext, file_content_type):
                loader = TextLoader(file_path, autodetect_encoding=True)
            else:
                loader = TikaLoader(
                    url=self.kwargs.get("TIKA_SERVER_URL"),
                    file_path=file_path,
                    mime_type=file_content_type,
                    extract_images=self.kwargs.get("PDF_EXTRACT_IMAGES"),
                )
        elif (
            self.engine == "datalab_marker"
            and self.kwargs.get("DATALAB_MARKER_API_KEY")
            and file_ext
            in [
                "pdf",
                "xls",
                "xlsx",
                "ods",
                "doc",
                "docx",
                "odt",
                "ppt",
                "pptx",
                "odp",
                "html",
                "epub",
                "png",
                "jpeg",
                "jpg",
                "webp",
                "gif",
                "tiff",
            ]
        ):
            api_base_url = self.kwargs.get("DATALAB_MARKER_API_BASE_URL", "")
            if not api_base_url or api_base_url.strip() == "":
                api_base_url = "https://www.datalab.to/api/v1/marker"

            loader = DatalabMarkerLoader(
                file_path=file_path,
                api_key=self.kwargs["DATALAB_MARKER_API_KEY"],
                api_base_url=api_base_url,
                additional_config=self.kwargs.get("DATALAB_MARKER_ADDITIONAL_CONFIG"),
                use_llm=self.kwargs.get("DATALAB_MARKER_USE_LLM", False),
                skip_cache=self.kwargs.get("DATALAB_MARKER_SKIP_CACHE", False),
                force_ocr=self.kwargs.get("DATALAB_MARKER_FORCE_OCR", False),
                paginate=self.kwargs.get("DATALAB_MARKER_PAGINATE", False),
                strip_existing_ocr=self.kwargs.get("DATALAB_MARKER_STRIP_EXISTING_OCR", False),
                disable_image_extraction=self.kwargs.get("DATALAB_MARKER_DISABLE_IMAGE_EXTRACTION", False),
                format_lines=self.kwargs.get("DATALAB_MARKER_FORMAT_LINES", False),
                output_format=self.kwargs.get("DATALAB_MARKER_OUTPUT_FORMAT", "markdown"),
            )
        elif self.engine == "docling" and self.kwargs.get("DOCLING_SERVER_URL"):
            if self._is_text_file(file_ext, file_content_type):
                loader = TextLoader(file_path, autodetect_encoding=True)
            else:
                params = self.kwargs.get("DOCLING_PARAMS", {})
                if not isinstance(params, dict):
                    try:
                        params = json.loads(params)
                    except json.JSONDecodeError:
                        log.error("Invalid DOCLING_PARAMS format, expected JSON object")
                        params = {}

                loader = DoclingLoader(
                    url=self.kwargs.get("DOCLING_SERVER_URL"),
                    api_key=self.kwargs.get("DOCLING_API_KEY"),
                    file_path=file_path,
                    mime_type=file_content_type,
                    params=params,
                )
        elif (
            self.engine == "document_intelligence"
            and self.kwargs.get("DOCUMENT_INTELLIGENCE_ENDPOINT") != ""
            and self.kwargs.get("DOCUMENT_INTELLIGENCE_KEY") != ""
            and (
                file_ext in ["pdf", "xls", "xlsx", "docx", "ppt", "pptx"]
                or file_content_type
                in [
                    "application/vnd.ms-excel",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/vnd.ms-powerpoint",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ]
            )
        ):
            AzureAIDocumentIntelligenceLoader = self._get_loader_class(
                "AzureAIDocumentIntelligenceLoader",
                feature="Azure Document Intelligence parsing",
                packages=["langchain-community", "azure-ai-documentintelligence"],
                install_profiles=["docs-full", "full"],
            )
            loader = AzureAIDocumentIntelligenceLoader(
                file_path=file_path,
                api_endpoint=self.kwargs.get("DOCUMENT_INTELLIGENCE_ENDPOINT"),
                api_key=self.kwargs.get("DOCUMENT_INTELLIGENCE_KEY"),
                api_model=self.kwargs.get("DOCUMENT_INTELLIGENCE_MODEL"),
            )
        elif self.engine == "mineru" and file_ext in ["pdf"]:
            mineru_timeout = self.kwargs.get("MINERU_API_TIMEOUT", 300)
            if mineru_timeout:
                try:
                    mineru_timeout = int(mineru_timeout)
                except ValueError:
                    mineru_timeout = 300

            loader = MinerULoader(
                file_path=file_path,
                api_mode=self.kwargs.get("MINERU_API_MODE", "local"),
                api_url=self.kwargs.get("MINERU_API_URL", "http://localhost:8000"),
                api_key=self.kwargs.get("MINERU_API_KEY", ""),
                params=self.kwargs.get("MINERU_PARAMS", {}),
                timeout=mineru_timeout,
            )
        elif (
            self.engine == "mistral_ocr"
            and self.kwargs.get("MISTRAL_OCR_API_KEY") != ""
            and file_ext
            in ["pdf"]  # Mistral OCR currently only supports PDF and images
        ):
            loader = MistralLoader(
                base_url=self.kwargs.get("MISTRAL_OCR_API_BASE_URL"),
                api_key=self.kwargs.get("MISTRAL_OCR_API_KEY"),
                file_path=file_path,
                mime_type=file_content_type,
            )
        else:
            if file_ext == "pdf":
                PyPDFLoader = self._get_loader_class(
                    "PyPDFLoader",
                    feature="PDF document parsing",
                    packages=["langchain-community", "pypdf"],
                    install_profiles=["core", "full"],
                )
                loader = PyPDFLoader(
                    file_path=file_path,
                    extract_images=self.kwargs.get("PDF_EXTRACT_IMAGES"),
                )
            elif file_ext == "csv":
                CSVLoader = self._get_loader_class(
                    "CSVLoader",
                    feature="CSV document parsing",
                    packages=["langchain-community"],
                    install_profiles=["core", "full"],
                )
                loader = CSVLoader(file_path, autodetect_encoding=True)
            elif file_ext in ["htm", "html"]:
                BSHTMLLoader = self._get_loader_class(
                    "BSHTMLLoader",
                    feature="HTML document parsing",
                    packages=["langchain-community", "beautifulsoup4"],
                    install_profiles=["core", "full"],
                )
                loader = BSHTMLLoader(file_path, open_encoding="unicode_escape")
            elif file_ext == "md":
                loader = TextLoader(file_path, autodetect_encoding=True)
            elif (
                file_content_type
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                or file_ext == "docx"
            ):
                Docx2txtLoader = self._get_loader_class(
                    "Docx2txtLoader",
                    feature="DOCX document parsing",
                    packages=["langchain-community", "docx2txt"],
                    install_profiles=["core", "full"],
                )
                loader = Docx2txtLoader(file_path)
            elif file_content_type in [
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ] or file_ext in ["xls", "xlsx"]:
                UnstructuredExcelLoader = self._get_optional_docs_loader(
                    "UnstructuredExcelLoader",
                    feature="Excel document parsing",
                )
                loader = UnstructuredExcelLoader(file_path)
            elif file_content_type in [
                "application/vnd.ms-powerpoint",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ] or file_ext in ["ppt", "pptx"]:
                UnstructuredPowerPointLoader = self._get_optional_docs_loader(
                    "UnstructuredPowerPointLoader",
                    feature="PowerPoint document parsing",
                )
                loader = UnstructuredPowerPointLoader(file_path)
            elif file_ext == "rst":
                UnstructuredRSTLoader = self._get_optional_docs_loader(
                    "UnstructuredRSTLoader",
                    feature="RST document parsing",
                )
                loader = UnstructuredRSTLoader(file_path, mode="elements")
            elif file_ext == "xml":
                UnstructuredXMLLoader = self._get_optional_docs_loader(
                    "UnstructuredXMLLoader",
                    feature="XML document parsing",
                )
                loader = UnstructuredXMLLoader(file_path)
            elif file_content_type == "application/epub+zip":
                UnstructuredEPubLoader = self._get_optional_docs_loader(
                    "UnstructuredEPubLoader",
                    feature="EPUB document parsing",
                )
                loader = UnstructuredEPubLoader(file_path)
            elif file_ext == "msg":
                OutlookMessageLoader = self._get_optional_docs_loader(
                    "OutlookMessageLoader",
                    feature="Outlook message parsing",
                )
                loader = OutlookMessageLoader(file_path)
            elif self._is_text_file(file_ext, file_content_type):
                loader = TextLoader(file_path, autodetect_encoding=True)
            else:
                raise FileUploadDiagnosticError(
                    make_unsupported_binary_diagnostic(basename(filename))
                )

        return loader
