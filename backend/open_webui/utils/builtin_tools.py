"""Built-in system tools injected in Native Mode.

This module provides a small set of server-side tools that are automatically exposed to
models when `function_calling == "native"` is enabled. Tools are gated by:
- Per-account configuration (`/api/configs/native_tools`)
- Chat feature flags (web_search, image_generation)
- Server-side permission checks (group permissions)
"""

import asyncio
import logging
import json
import os
import re
import subprocess
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import Request

from open_webui.models.chats import Chats
from open_webui.models.channels import Channels
from open_webui.models.files import Files
from open_webui.models.knowledge import Knowledges
from open_webui.models.memories import Memories
from open_webui.retrieval.runtime import ensure_reranking_runtime
from open_webui.models.messages import Messages
from open_webui.models.users import UserModel
from open_webui.retrieval.vector.connector import VECTOR_DB_CLIENT
from open_webui.retrieval.web.utils import get_web_loader
from open_webui.routers.images import (
    GenerateImageForm,
    get_automatic1111_api_auth,
    image_generations,
    load_b64_image_data,
    load_url_image_data,
    upload_image,
)
from open_webui.routers.retrieval import search_web as _search_web
from open_webui.utils.access_control import has_access, has_permission
from open_webui.utils.user_tools import get_user_native_tools_config
from open_webui.config import ENABLE_TERMINAL, TERMINAL_COMMAND_TIMEOUT, TERMINAL_MAX_OUTPUT_CHARS
from open_webui.routers.terminal import _get_workspace_root


log = logging.getLogger(__name__)


def _can_use_feature(request: Request, user: UserModel, key: str) -> bool:
    if user.role == "admin":
        return True
    return has_permission(user.id, key, request.app.state.config.USER_PERMISSIONS)


def _tool_spec(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": name, "description": description, "parameters": parameters}


def get_builtin_tools(
    request: Request, user: UserModel, metadata: dict
) -> Dict[str, Dict[str, Any]]:
    """Return built-in system tools for Native Mode.

    Each entry matches open_webui.utils.tools.get_tools output:
      {tool_name: {"tool_id": "...", "callable": async_fn, "spec": {...}}}
    """

    tools: Dict[str, Dict[str, Any]] = {}
    features = metadata.get("features") or {}
    native_cfg = get_user_native_tools_config(request, user)

    # Per-model built-in tool overrides (stored in model.meta.builtin_tool_config).
    # Model-level overrides win over user-level config for shared keys.
    model_data = metadata.get("model") or {}
    model_meta = model_data.get("meta", {}) if isinstance(model_data, dict) else {}
    model_tool_cfg = model_meta.get("builtin_tool_config") or {}
    if isinstance(model_tool_cfg, dict):
        for k, v in model_tool_cfg.items():
            if k in native_cfg:
                native_cfg[k] = v

    # -------------------------
    # Web tools (networked)
    # -------------------------
    if (
        bool((native_cfg or {}).get("ENABLE_WEB_SEARCH_TOOL", False))
        and request.app.state.config.ENABLE_WEB_SEARCH
    ):

        async def search_web(query: str, k: Optional[int] = None) -> str:
            if not query or not str(query).strip():
                return "[]"

            limit = (
                int(k)
                if k is not None
                else int(request.app.state.config.WEB_SEARCH_RESULT_COUNT)
            )
            limit = max(1, min(limit, 10))

            def _run():
                results = _search_web(
                    request,
                    request.app.state.config.WEB_SEARCH_ENGINE,
                    str(query),
                )
                return [r.model_dump() for r in results[:limit]]

            results = await asyncio.to_thread(_run)
            return json.dumps(results, ensure_ascii=False)

        tools["search_web"] = {
            "tool_id": "builtin:web",
            "callable": search_web,
            "spec": _tool_spec(
                "search_web",
                "Search the web and return top results (title, link, snippet).",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "k": {
                            "type": "integer",
                            "description": "Maximum number of results (1-10).",
                        },
                    },
                    "required": ["query"],
                },
            ),
        }

        if bool((native_cfg or {}).get("ENABLE_URL_FETCH", False)):

            async def fetch_url(url: str, max_chars: int = 50000) -> str:
                if not url or not str(url).strip():
                    raise ValueError("url is required")

                parsed = urlparse(str(url).strip())
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError(f"Invalid URL: {url}")

                loader = get_web_loader(
                    str(url),
                    verify_ssl=request.app.state.config.ENABLE_WEB_LOADER_SSL_VERIFICATION,
                    requests_per_second=request.app.state.config.WEB_SEARCH_CONCURRENT_REQUESTS,
                    trust_env=request.app.state.config.WEB_SEARCH_TRUST_ENV,
                )

                docs = await asyncio.to_thread(loader.load)
                content = " ".join(
                    [doc.page_content for doc in docs if doc and doc.page_content]
                ).strip()

                title = ""
                for doc in docs:
                    if not doc:
                        continue
                    metadata = getattr(doc, "metadata", None)
                    if isinstance(metadata, dict):
                        title = str(metadata.get("title") or "").strip()
                        if title:
                            break

                try:
                    max_chars_int = int(max_chars)
                except Exception:
                    max_chars_int = 50000

                if max_chars_int > 0 and len(content) > max_chars_int:
                    content = content[:max_chars_int]

                lowered = content.lower()
                signals: list[str] = []

                blocked_markers = [
                    "oops, something went wrong",
                    "just a moment",
                    "enable javascript",
                    "verify you are human",
                    "captcha",
                    "access denied",
                ]
                if any(marker in lowered for marker in blocked_markers):
                    signals.append("anti_bot_or_challenge")

                text_len = len(content)
                if text_len < 120:
                    signals.append("too_short")

                number_hits = len(re.findall(r"\d", content)) if content else 0
                number_density = float(number_hits) / float(text_len) if text_len > 0 else 0.0
                if number_density < 0.01:
                    signals.append("low_numeric_density")

                status = "ok"
                quality_score = 0.75
                if "anti_bot_or_challenge" in signals:
                    status = "blocked"
                    quality_score = 0.05
                    if len(content) > 1200:
                        content = content[:1200]
                elif "too_short" in signals:
                    status = "thin"
                    quality_score = 0.20
                elif text_len < 400:
                    quality_score = 0.45

                domain = parsed.netloc.lower()
                payload = {
                    "url": str(url),
                    "domain": domain,
                    "title": title,
                    "content": content,
                    "status": status,
                    "quality_score": round(quality_score, 3),
                    "signals": signals,
                    "content_length": len(content),
                }

                return json.dumps(payload, ensure_ascii=False)

            tools["fetch_url"] = {
                "tool_id": "builtin:web",
                "callable": fetch_url,
                "spec": _tool_spec(
                    "fetch_url",
                    "Fetch and extract the textual content of a URL.",
                    {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "HTTP(S) URL to fetch.",
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Maximum characters to return.",
                            },
                        },
                        "required": ["url"],
                    },
                ),
            }

        if bool((native_cfg or {}).get("ENABLE_URL_FETCH_RENDERED", False)):

            async def fetch_url_rendered(url: str, max_chars: int = 50000) -> str:
                """Fetch a URL via Jina Reader (headless browser, JS rendering) and return Markdown."""
                if not url or not str(url).strip():
                    raise ValueError("url is required")

                parsed = urlparse(str(url).strip())
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    raise ValueError(f"Invalid URL: {url}")

                try:
                    max_chars_int = int(max_chars)
                except Exception:
                    max_chars_int = 50000

                domain = parsed.netloc.lower()
                jina_url = f"https://r.jina.ai/{str(url).strip()}"
                headers = {
                    "Accept": "text/markdown",
                    "X-No-Cache": "true",
                    "X-Timeout": "15",
                }

                import aiohttp

                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            jina_url,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=20),
                        ) as resp:
                            if resp.status != 200:
                                return json.dumps(
                                    {
                                        "url": str(url),
                                        "domain": domain,
                                        "title": "",
                                        "content": f"Jina Reader returned HTTP {resp.status}",
                                        "status": "error",
                                        "quality_score": 0.0,
                                        "signals": ["jina_error"],
                                        "content_length": 0,
                                    },
                                    ensure_ascii=False,
                                )
                            content = await resp.text()
                except Exception as e:
                    return json.dumps(
                        {
                            "url": str(url),
                            "domain": domain,
                            "title": "",
                            "content": f"Jina Reader error: {type(e).__name__}: {e}",
                            "status": "error",
                            "quality_score": 0.0,
                            "signals": ["jina_error"],
                            "content_length": 0,
                        },
                        ensure_ascii=False,
                    )

                if max_chars_int > 0 and len(content) > max_chars_int:
                    content = content[:max_chars_int]

                # Extract title from first Markdown heading
                title = ""
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("# "):
                        title = stripped[2:].strip()
                        break

                # Quality signals (reuse fetch_url logic)
                lowered = content.lower()
                signals: list[str] = []
                blocked_markers = [
                    "oops, something went wrong",
                    "just a moment",
                    "enable javascript",
                    "verify you are human",
                    "captcha",
                    "access denied",
                ]
                if any(marker in lowered for marker in blocked_markers):
                    signals.append("anti_bot_or_challenge")

                text_len = len(content)
                if text_len < 120:
                    signals.append("too_short")

                status = "ok"
                quality_score = 0.80
                if "anti_bot_or_challenge" in signals:
                    status = "blocked"
                    quality_score = 0.05
                elif "too_short" in signals:
                    status = "thin"
                    quality_score = 0.20

                payload = {
                    "url": str(url),
                    "domain": domain,
                    "title": title,
                    "content": content,
                    "status": status,
                    "quality_score": round(quality_score, 3),
                    "signals": signals,
                    "content_length": len(content),
                }
                return json.dumps(payload, ensure_ascii=False)

            tools["fetch_url_rendered"] = {
                "tool_id": "builtin:web",
                "callable": fetch_url_rendered,
                "spec": _tool_spec(
                    "fetch_url_rendered",
                    "Fetch a URL using a headless browser that renders JavaScript, and return the page content as clean Markdown. Use this when fetch_url fails due to anti-bot protection or JavaScript-heavy pages.",
                    {
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "HTTP(S) URL to fetch with JavaScript rendering.",
                            },
                            "max_chars": {
                                "type": "integer",
                                "description": "Maximum characters to return.",
                            },
                        },
                        "required": ["url"],
                    },
                ),
            }

    # -------------------------
    # Knowledge tools
    # -------------------------
    if bool((native_cfg or {}).get("ENABLE_LIST_KNOWLEDGE_BASES", False)):

        async def list_knowledge_bases() -> List[dict]:
            if user.role == "admin":
                bases = Knowledges.get_knowledge_bases()
            else:
                bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "read")

            return [
                {"id": kb.id, "name": kb.name, "description": kb.description}
                for kb in bases
            ]

        tools["list_knowledge_bases"] = {
            "tool_id": "builtin:knowledge",
            "callable": list_knowledge_bases,
            "spec": _tool_spec(
                "list_knowledge_bases",
                "List knowledge bases the user can access.",
                {"type": "object", "properties": {}},
            ),
        }

    if bool((native_cfg or {}).get("ENABLE_SEARCH_KNOWLEDGE_BASES", False)):

        async def search_knowledge_bases(query: str) -> List[dict]:
            q = (query or "").strip().lower()
            if not q:
                return []

            if user.role == "admin":
                bases = Knowledges.get_knowledge_bases()
            else:
                bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "read")

            matched: List[dict] = []
            for kb in bases:
                hay = f"{kb.name}\n{kb.description}".lower()
                if q in hay:
                    matched.append(
                        {"id": kb.id, "name": kb.name, "description": kb.description}
                    )
            return matched

        tools["search_knowledge_bases"] = {
            "tool_id": "builtin:knowledge",
            "callable": search_knowledge_bases,
            "spec": _tool_spec(
                "search_knowledge_bases",
                "Search knowledge bases by name/description.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."}
                    },
                    "required": ["query"],
                },
            ),
        }

    if bool((native_cfg or {}).get("ENABLE_QUERY_KNOWLEDGE_FILES", False)):

        async def query_knowledge_bases(
            query: str,
            knowledge_base_ids: Optional[List[str]] = None,
            k: int = 3,
        ) -> dict:
            from open_webui.retrieval.utils import (
                query_collection,
                query_collection_with_hybrid_search,
            )

            q = (query or "").strip()
            if not q:
                return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

            if user.role == "admin":
                bases = Knowledges.get_knowledge_bases()
            else:
                bases = Knowledges.get_knowledge_bases_by_user_id(user.id, "read")

            allowed_ids = {kb.id for kb in bases}

            if knowledge_base_ids:
                collection_names = [
                    kb_id for kb_id in knowledge_base_ids if kb_id in allowed_ids
                ]
            else:
                collection_names = list(allowed_ids)

            limit = max(1, min(int(k or 3), 10))

            def embed(text: str, prefix: Optional[str] = None):
                return request.app.state.EMBEDDING_FUNCTION(text, prefix=prefix, user=user)

            if request.app.state.config.ENABLE_RAG_HYBRID_SEARCH:
                return query_collection_with_hybrid_search(
                    collection_names=collection_names,
                    queries=[q],
                    embedding_function=lambda text, prefix: embed(text, prefix=prefix),
                    k=limit,
                    reranking_function=ensure_reranking_runtime(request.app),
                    k_reranker=request.app.state.config.TOP_K_RERANKER,
                    r=request.app.state.config.RELEVANCE_THRESHOLD,
                )

            return query_collection(
                collection_names=collection_names,
                queries=[q],
                embedding_function=lambda text, prefix: embed(text, prefix=prefix),
                k=limit,
            )

        tools["query_knowledge_bases"] = {
            "tool_id": "builtin:knowledge",
            "callable": query_knowledge_bases,
            "spec": _tool_spec(
                "query_knowledge_bases",
                "Query one or more knowledge bases and return the most relevant chunks.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "User query."},
                        "knowledge_base_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional knowledge base IDs to search; defaults to all accessible.",
                        },
                        "k": {"type": "integer", "description": "Top results (1-10)."},
                    },
                    "required": ["query"],
                },
            ),
        }

        async def search_knowledge_files(
            query: str,
            knowledge_base_ids: Optional[List[str]] = None,
            k: int = 5,
        ) -> List[dict]:
            results = await query_knowledge_bases(
                query=query,
                knowledge_base_ids=knowledge_base_ids,
                k=max(1, min(int(k or 5), 10)),
            )
            metadatas = (results or {}).get("metadatas", [[]])[0] or []

            file_hits: Dict[str, dict] = {}
            for md in metadatas:
                file_id = (md or {}).get("file_id")
                if not file_id:
                    continue
                if file_id not in file_hits:
                    file_hits[file_id] = {
                        "file_id": file_id,
                        "name": (md or {}).get("name") or (md or {}).get("source"),
                        "source": (md or {}).get("source"),
                        "collection_name": (md or {}).get("collection_name"),
                    }

            return list(file_hits.values())

        tools["search_knowledge_files"] = {
            "tool_id": "builtin:knowledge",
            "callable": search_knowledge_files,
            "spec": _tool_spec(
                "search_knowledge_files",
                "Search knowledge files and return matching file IDs.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "User query."},
                        "knowledge_base_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional knowledge base IDs to search; defaults to all accessible.",
                        },
                        "k": {"type": "integer", "description": "Top results (1-10)."},
                    },
                    "required": ["query"],
                },
            ),
        }

    if bool((native_cfg or {}).get("ENABLE_VIEW_KNOWLEDGE_FILE", False)):

        async def view_knowledge_file(file_id: str, max_chars: int = 50000) -> dict:
            file = Files.get_file_by_id(file_id)
            if not file:
                raise ValueError("File not found")

            allowed = user.role == "admin" or file.user_id == user.id
            kb_id = (file.meta or {}).get("collection_name")
            if not allowed and kb_id:
                for kb in Knowledges.get_knowledge_bases_by_user_id(user.id, "read"):
                    if kb.id == kb_id:
                        allowed = True
                        break

            if not allowed:
                raise PermissionError("Access denied")

            content = (file.data or {}).get("content", "")
            content = content or ""

            try:
                max_chars_int = int(max_chars)
            except Exception:
                max_chars_int = 50000

            if max_chars_int > 0 and len(content) > max_chars_int:
                content = content[:max_chars_int]

            return {"file_id": file.id, "name": file.filename, "content": content}

        tools["view_knowledge_file"] = {
            "tool_id": "builtin:knowledge",
            "callable": view_knowledge_file,
            "spec": _tool_spec(
                "view_knowledge_file",
                "View the extracted textual content of a knowledge file by file_id.",
                {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "string", "description": "File ID."},
                        "max_chars": {
                            "type": "integer",
                            "description": "Maximum characters to return.",
                        },
                    },
                    "required": ["file_id"],
                },
            ),
        }

    # -------------------------
    # Image tools (networked)
    # -------------------------
    if (
        bool((native_cfg or {}).get("ENABLE_IMAGE_GENERATION_TOOL", False))
        and bool(features.get("image_generation"))
        and request.app.state.config.ENABLE_IMAGE_GENERATION
        and _can_use_feature(request, user, "features.image_generation")
    ):

        async def generate_image(
            prompt: str,
            n: int = 1,
            size: Optional[str] = None,
            negative_prompt: Optional[str] = None,
        ):
            form = GenerateImageForm(
                prompt=str(prompt),
                n=int(n or 1),
                size=size,
                negative_prompt=negative_prompt,
            )
            return await image_generations(request, form, user=user)

        tools["generate_image"] = {
            "tool_id": "builtin:images",
            "callable": generate_image,
            "spec": _tool_spec(
                "generate_image",
                "Generate an image and return URLs.",
                {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string", "description": "Image prompt."},
                        "n": {"type": "integer", "description": "Number of images."},
                        "size": {
                            "type": "string",
                            "description": "Image size, e.g. 1024x1024.",
                        },
                        "negative_prompt": {
                            "type": "string",
                            "description": "Negative prompt (if supported).",
                        },
                    },
                    "required": ["prompt"],
                },
            ),
        }

    if (
        bool((native_cfg or {}).get("ENABLE_IMAGE_EDIT", False))
        and bool(features.get("image_generation"))
        and request.app.state.config.ENABLE_IMAGE_GENERATION
        and _can_use_feature(request, user, "features.image_generation")
    ):

        async def edit_image(
            image_url: str,
            prompt: str,
            n: int = 1,
            size: Optional[str] = None,
            negative_prompt: Optional[str] = None,
            strength: Optional[float] = None,
            mask_url: Optional[str] = None,
        ) -> List[dict]:
            """
            Edit an image using the configured image engine (best-effort).

            Supported engines:
              - automatic1111: /sdapi/v1/img2img
              - openai: /images/edits (DALL·E 2 compatible)
            """

            if not image_url or not str(image_url).strip():
                raise ValueError("image_url is required")
            if not prompt or not str(prompt).strip():
                raise ValueError("prompt is required")

            def absolutize(url: str) -> str:
                u = str(url or "").strip()
                if not u:
                    return u
                if u.startswith("/"):
                    return str(request.base_url).rstrip("/") + u
                return u

            token_obj = getattr(getattr(request, "state", None), "token", None)
            session_token = getattr(token_obj, "credentials", None) if token_obj else None
            auth_headers = (
                {"Authorization": f"Bearer {session_token}"} if session_token else None
            )

            resolved_image_url = absolutize(image_url)
            resolved_mask_url = absolutize(mask_url) if mask_url else None

            image_loaded = await asyncio.to_thread(
                load_url_image_data, resolved_image_url, auth_headers
            )
            if not image_loaded:
                raise ValueError("Failed to load image from image_url")
            image_bytes, image_mime = image_loaded

            mask_bytes = None
            if resolved_mask_url:
                mask_loaded = await asyncio.to_thread(
                    load_url_image_data, resolved_mask_url, auth_headers
                )
                if not mask_loaded:
                    raise ValueError("Failed to load mask from mask_url")
                mask_bytes, _mask_mime = mask_loaded

            engine = (request.app.state.config.IMAGE_GENERATION_ENGINE or "").strip().lower()

            def parse_size(value: Optional[str]) -> tuple[int, int]:
                raw = (value or "").strip()
                if raw:
                    try:
                        w, h = tuple(map(int, raw.lower().split("x")))
                        if w > 0 and h > 0:
                            return w, h
                    except Exception:
                        pass
                return tuple(map(int, request.app.state.config.IMAGE_SIZE.split("x")))

            width, height = parse_size(size)

            try:
                requested_n = int(n or 1)
            except Exception:
                requested_n = 1
            requested_n = max(1, min(requested_n, 4))

            try:
                denoise = float(strength) if strength is not None else 0.75
            except Exception:
                denoise = 0.75
            denoise = max(0.0, min(denoise, 1.0))

            if engine in {"automatic1111", ""}:
                import base64
                import requests

                init_b64 = base64.b64encode(image_bytes).decode("utf-8")

                data: Dict[str, Any] = {
                    "prompt": str(prompt),
                    "batch_size": requested_n,
                    "width": width,
                    "height": height,
                    "init_images": [init_b64],
                    "denoising_strength": denoise,
                }

                if negative_prompt is not None:
                    data["negative_prompt"] = str(negative_prompt)

                if mask_bytes is not None:
                    data["mask"] = base64.b64encode(mask_bytes).decode("utf-8")

                if request.app.state.config.IMAGE_STEPS is not None:
                    data["steps"] = request.app.state.config.IMAGE_STEPS

                if request.app.state.config.AUTOMATIC1111_CFG_SCALE:
                    data["cfg_scale"] = request.app.state.config.AUTOMATIC1111_CFG_SCALE
                if request.app.state.config.AUTOMATIC1111_SAMPLER:
                    data["sampler_name"] = request.app.state.config.AUTOMATIC1111_SAMPLER
                if request.app.state.config.AUTOMATIC1111_SCHEDULER:
                    data["scheduler"] = request.app.state.config.AUTOMATIC1111_SCHEDULER

                r = await asyncio.to_thread(
                    requests.post,
                    url=f"{request.app.state.config.AUTOMATIC1111_BASE_URL}/sdapi/v1/img2img",
                    json=data,
                    headers={"authorization": get_automatic1111_api_auth(request)},
                )
                r.raise_for_status()
                res = r.json() or {}

                images: List[dict] = []
                for image_b64 in res.get("images", []) or []:
                    loaded = load_b64_image_data(image_b64)
                    if not loaded:
                        continue
                    img_data, content_type = loaded
                    url = upload_image(request, data, img_data, content_type, user)
                    images.append({"url": url})
                return images

            if engine == "openai":
                import requests

                headers: Dict[str, str] = {
                    "Authorization": f"Bearer {request.app.state.config.IMAGES_OPENAI_API_KEY}"
                }

                data = {
                    "model": (
                        request.app.state.config.IMAGE_GENERATION_MODEL
                        if request.app.state.config.IMAGE_GENERATION_MODEL != ""
                        else "dall-e-2"
                    ),
                    "prompt": str(prompt),
                    "n": requested_n,
                    "size": f"{width}x{height}",
                    "response_format": "b64_json",
                }

                files = {"image": ("image", image_bytes, image_mime or "image/png")}
                if mask_bytes is not None:
                    files["mask"] = ("mask", mask_bytes, "image/png")

                r = await asyncio.to_thread(
                    requests.post,
                    url=f"{request.app.state.config.IMAGES_OPENAI_API_BASE_URL}/images/edits",
                    data=data,
                    files=files,
                    headers=headers,
                )
                r.raise_for_status()
                res = r.json() or {}

                images: List[dict] = []
                for image in res.get("data", []) or []:
                    if image_url := image.get("url", None):
                        loaded = load_url_image_data(image_url, headers)
                        if not loaded:
                            continue
                        img_data, content_type = loaded
                    else:
                        loaded = load_b64_image_data(image.get("b64_json", ""))
                        if not loaded:
                            continue
                        img_data, content_type = loaded

                    url = upload_image(request, data, img_data, content_type, user)
                    images.append({"url": url})
                return images

            if engine == "gemini":
                import base64
                import requests

                init_b64 = base64.b64encode(image_bytes).decode("utf-8")
                model = (
                    request.app.state.config.IMAGE_GENERATION_MODEL
                    if request.app.state.config.IMAGE_GENERATION_MODEL != ""
                    else "imagen-3.0-capability-001"
                )

                headers_g: Dict[str, str] = {
                    "Content-Type": "application/json",
                    "x-goog-api-key": request.app.state.config.IMAGES_GEMINI_API_KEY,
                }

                data = {
                    "instances": {
                        "prompt": str(prompt),
                        "image": {"bytesBase64Encoded": init_b64},
                    },
                    "parameters": {
                        "sampleCount": requested_n,
                        "editConfig": {
                            "editMode": "INPAINTING" if mask_bytes else "OUTPAINTING",
                        },
                        "outputOptions": {"mimeType": "image/png"},
                    },
                }

                if mask_bytes is not None:
                    data["instances"]["mask"] = {
                        "bytesBase64Encoded": base64.b64encode(mask_bytes).decode("utf-8")
                    }

                r = await asyncio.to_thread(
                    requests.post,
                    url=f"{request.app.state.config.IMAGES_GEMINI_API_BASE_URL}/models/{model}:predict",
                    json=data,
                    headers=headers_g,
                )
                r.raise_for_status()
                res = r.json() or {}

                images: List[dict] = []
                for pred in res.get("predictions", []) or []:
                    loaded = load_b64_image_data(pred.get("bytesBase64Encoded", ""))
                    if not loaded:
                        continue
                    img_data, content_type = loaded
                    url = upload_image(request, data, img_data, content_type, user)
                    images.append({"url": url})
                return images

            raise NotImplementedError(f"edit_image is not supported for engine '{engine}'")

        tools["edit_image"] = {
            "tool_id": "builtin:images",
            "callable": edit_image,
            "spec": _tool_spec(
                "edit_image",
                "Edit an image (best-effort).",
                {
                    "type": "object",
                    "properties": {
                        "image_url": {
                            "type": "string",
                            "description": "Image URL to edit (supports local file URLs).",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Edit instruction / prompt.",
                        },
                        "n": {
                            "type": "integer",
                            "description": "Number of images (1-4).",
                        },
                        "size": {
                            "type": "string",
                            "description": "Output size, e.g. 1024x1024.",
                        },
                        "negative_prompt": {
                            "type": "string",
                            "description": "Negative prompt (if supported by your engine).",
                        },
                        "strength": {
                            "type": "number",
                            "description": "Edit strength / denoising (0-1).",
                        },
                        "mask_url": {
                            "type": "string",
                            "description": "Optional mask image URL (engine dependent).",
                        },
                    },
                    "required": ["image_url", "prompt"],
                },
            ),
        }

    # -------------------------
    # Memory tools
    # -------------------------
    if bool((native_cfg or {}).get("ENABLE_MEMORY_TOOLS", False)) and bool(
        features.get("memory")
    ):

        async def add_memory(content: str) -> dict:
            memory = Memories.insert_new_memory(user.id, str(content))
            if not memory:
                raise RuntimeError("Failed to create memory")

            VECTOR_DB_CLIENT.upsert(
                collection_name=f"user-memory-{user.id}",
                items=[
                    {
                        "id": memory.id,
                        "text": memory.content,
                        "vector": request.app.state.EMBEDDING_FUNCTION(
                            memory.content, user=user
                        ),
                        "metadata": {"created_at": memory.created_at},
                    }
                ],
            )

            return memory.model_dump()

        async def search_memories(query: str, k: int = 3) -> dict:
            limit = max(1, min(int(k or 3), 10))
            result = VECTOR_DB_CLIENT.search(
                collection_name=f"user-memory-{user.id}",
                vectors=[request.app.state.EMBEDDING_FUNCTION(str(query), user=user)],
                limit=limit,
            )
            if result is None:
                return {
                    "ids": [[]],
                    "documents": [[]],
                    "metadatas": [[]],
                    "distances": [[]],
                }
            if hasattr(result, "model_dump"):
                return result.model_dump()
            if hasattr(result, "dict"):
                return result.dict()
            return result

        async def forget_memory(memory_id: str) -> bool:
            ok = Memories.delete_memory_by_id_and_user_id(memory_id, user.id)
            if ok:
                try:
                    VECTOR_DB_CLIENT.delete(
                        collection_name=f"user-memory-{user.id}",
                        ids=[memory_id],
                    )
                except Exception:
                    pass
            return bool(ok)

        tools["add_memory"] = {
            "tool_id": "builtin:memory",
            "callable": add_memory,
            "spec": _tool_spec(
                "add_memory",
                "Save a durable user memory such as preferences, profile facts, likes/dislikes, or recurring personal context.",
                {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "Memory content."}
                    },
                    "required": ["content"],
                },
            ),
        }

        tools["search_memories"] = {
            "tool_id": "builtin:memory",
            "callable": search_memories,
            "spec": _tool_spec(
                "search_memories",
                "Search durable user memories such as preferences, profile facts, and recurring personal context.",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "k": {"type": "integer", "description": "Top results (1-10)."},
                    },
                    "required": ["query"],
                },
            ),
        }

        tools["forget_memory"] = {
            "tool_id": "builtin:memory",
            "callable": forget_memory,
            "spec": _tool_spec(
                "forget_memory",
                "Delete a memory by id.",
                {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string", "description": "Memory ID."}
                    },
                    "required": ["memory_id"],
                },
            ),
        }

    # -------------------------
    # Notes tools (experimental)
    # -------------------------
    if bool((native_cfg or {}).get("ENABLE_NOTES", False)):
        try:
            from open_webui.models.notes import Notes, NoteForm  # type: ignore
        except Exception:
            Notes = None

        if Notes:

            async def add_note(title: str, content: str) -> dict:
                form = NoteForm(title=str(title), content=str(content))
                note = Notes.insert_new_note(user.id, form)
                if not note:
                    raise RuntimeError("Failed to create note")

                VECTOR_DB_CLIENT.upsert(
                    collection_name=f"user-notes-{user.id}",
                    items=[
                        {
                            "id": note.id,
                            "text": f"{note.title}\n{note.content}",
                            "vector": request.app.state.EMBEDDING_FUNCTION(
                                f"{note.title}\n{note.content}", user=user
                            ),
                            "metadata": {"created_at": note.created_at},
                        }
                    ],
                )

                return note.model_dump()

            async def search_notes(query: str, k: int = 3) -> dict:
                limit = max(1, min(int(k or 3), 10))
                result = VECTOR_DB_CLIENT.search(
                    collection_name=f"user-notes-{user.id}",
                    vectors=[request.app.state.EMBEDDING_FUNCTION(str(query), user=user)],
                    limit=limit,
                )
                if result is None:
                    return {
                        "ids": [[]],
                        "documents": [[]],
                        "metadatas": [[]],
                        "distances": [[]],
                    }
                if hasattr(result, "model_dump"):
                    return result.model_dump()
                if hasattr(result, "dict"):
                    return result.dict()
                return result

            tools["add_note"] = {
                "tool_id": "builtin:notes",
                "callable": add_note,
                "spec": _tool_spec(
                    "add_note",
                    "Write a titled user note for note-taking or longer saved content, not for simple personal preferences or profile memories.",
                    {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "description": "Note title."},
                            "content": {"type": "string", "description": "Note content."},
                        },
                        "required": ["title", "content"],
                    },
                ),
            }

            tools["search_notes"] = {
                "tool_id": "builtin:notes",
                "callable": search_notes,
                "spec": _tool_spec(
                    "search_notes",
                    "Search the user's saved notes and note-like content.",
                    {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query."},
                            "k": {"type": "integer", "description": "Top results (1-10)."},
                        },
                        "required": ["query"],
                    },
                ),
            }

    # -------------------------
    # Chat history tools
    # -------------------------
    if bool((native_cfg or {}).get("ENABLE_CHAT_HISTORY_TOOLS", False)):

        async def search_chats(
            query: str, include_archived: bool = False, limit: int = 20
        ) -> List[dict]:
            q = (query or "").strip()
            if not q:
                return []

            chats = Chats.get_chats_by_user_id_and_search_text(
                user.id,
                q,
                include_archived=bool(include_archived),
                skip=0,
                limit=max(1, min(int(limit or 20), 60)),
            )

            return [
                {"id": c.id, "title": c.title, "updated_at": c.updated_at} for c in chats
            ]

        tools["search_chats"] = {
            "tool_id": "builtin:chats",
            "callable": search_chats,
            "spec": _tool_spec(
                "search_chats",
                "Search the user's chat history (titles and message content).",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "include_archived": {
                            "type": "boolean",
                            "description": "Include archived chats.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max chats to return (1-60).",
                        },
                    },
                    "required": ["query"],
                },
            ),
        }

    # -------------------------
    # Time tools
    # -------------------------
    if bool((native_cfg or {}).get("ENABLE_TIME_TOOLS", False)):

        async def get_current_time() -> str:
            return datetime.now().strftime("%H:%M:%S")

        async def get_current_date() -> str:
            return date.today().isoformat()

        async def get_current_timestamp() -> int:
            return int(datetime.now(tz=timezone.utc).timestamp())

        tools["get_current_time"] = {
            "tool_id": "builtin:time",
            "callable": get_current_time,
            "spec": _tool_spec(
                "get_current_time",
                "Get the current local time (HH:MM:SS).",
                {"type": "object", "properties": {}},
            ),
        }

        tools["get_current_date"] = {
            "tool_id": "builtin:time",
            "callable": get_current_date,
            "spec": _tool_spec(
                "get_current_date",
                "Get the current local date (YYYY-MM-DD).",
                {"type": "object", "properties": {}},
            ),
        }

        tools["get_current_timestamp"] = {
            "tool_id": "builtin:time",
            "callable": get_current_timestamp,
            "spec": _tool_spec(
                "get_current_timestamp",
                "Get the current Unix timestamp (UTC).",
                {"type": "object", "properties": {}},
            ),
        }

    # -------------------------
    # Channels tools
    # -------------------------
    if (
        bool((native_cfg or {}).get("ENABLE_CHANNEL_TOOLS", False))
        and request.app.state.config.ENABLE_CHANNELS
    ):

        async def search_channels(query: Optional[str] = None, limit: int = 20) -> List[dict]:
            q = (query or "").strip().lower()
            channels = Channels.get_channels_by_user_id(user.id, permission="read")
            if q:
                channels = [
                    c
                    for c in channels
                    if q in (c.name or "").lower()
                    or q in (c.description or "").lower()
                ]
            channels = channels[: max(1, min(int(limit or 20), 60))]
            return [{"id": c.id, "name": c.name, "description": c.description} for c in channels]

        async def search_channel_messages(channel_id: str, query: str, limit: int = 20) -> List[dict]:
            channel = Channels.get_channel_by_id(channel_id)
            if not channel:
                raise ValueError("Channel not found")

            if user.role != "admin" and not has_access(
                user.id, "read", channel.access_control
            ):
                raise PermissionError("Access denied")

            q = (query or "").strip().lower()
            if not q:
                return []

            messages = Messages.get_messages_by_channel_id(channel_id, skip=0, limit=200)
            hits: List[dict] = []
            for m in messages:
                if q in (m.content or "").lower():
                    hits.append(
                        {"id": m.id, "content": m.content, "created_at": m.created_at}
                    )
                if len(hits) >= max(1, min(int(limit or 20), 60)):
                    break
            return hits

        async def view_channel_message(channel_id: str, message_id: str) -> dict:
            channel = Channels.get_channel_by_id(channel_id)
            if not channel:
                raise ValueError("Channel not found")

            if user.role != "admin" and not has_access(
                user.id, "read", channel.access_control
            ):
                raise PermissionError("Access denied")

            msgs = Messages.get_messages_by_parent_id(
                channel_id, parent_id=message_id, skip=0, limit=100
            )
            if not msgs:
                raise ValueError("Message not found")

            return {"messages": [m.model_dump() for m in msgs]}

        tools["search_channels"] = {
            "tool_id": "builtin:channels",
            "callable": search_channels,
            "spec": _tool_spec(
                "search_channels",
                "Search channels the user can access.",
                {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (optional).",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (1-60).",
                        },
                    },
                },
            ),
        }

        tools["search_channel_messages"] = {
            "tool_id": "builtin:channels",
            "callable": search_channel_messages,
            "spec": _tool_spec(
                "search_channel_messages",
                "Search recent messages in a channel (best-effort).",
                {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string", "description": "Channel ID."},
                        "query": {"type": "string", "description": "Search query."},
                        "limit": {
                            "type": "integer",
                            "description": "Max results (1-60).",
                        },
                    },
                    "required": ["channel_id", "query"],
                },
            ),
        }

        tools["view_channel_message"] = {
            "tool_id": "builtin:channels",
            "callable": view_channel_message,
            "spec": _tool_spec(
                "view_channel_message",
                "View a channel message and its thread (replies).",
                {
                    "type": "object",
                    "properties": {
                        "channel_id": {"type": "string", "description": "Channel ID."},
                        "message_id": {
                            "type": "string",
                            "description": "Message ID (thread root).",
                        },
                    },
                    "required": ["channel_id", "message_id"],
                },
            ),
        }

    # -------------------------
    # Terminal tool
    # -------------------------
    if (
        bool((native_cfg or {}).get("ENABLE_TERMINAL_TOOL", False))
        and ENABLE_TERMINAL.value
    ):

        def _terminal_env_desc() -> str:
            """Build environment description dynamically for the tool prompt."""
            import platform, shutil
            sys_name = platform.system()
            home = os.path.expanduser("~")
            lines = [
                "Execute a shell command on the user's computer. Returns stdout, stderr, exit_code.",
                "ALWAYS use this tool directly — never suggest commands for the user to run manually.",
            ]
            is_wsl = sys_name == "Linux" and "microsoft" in platform.release().lower()
            has_ps = bool(shutil.which("powershell.exe") or shutil.which("pwsh"))
            if is_wsl:
                # Detect Windows user home via /mnt/c/Users
                win_home = ""
                try:
                    users_dir = "/mnt/c/Users"
                    if os.path.isdir(users_dir):
                        candidates = [d for d in os.listdir(users_dir)
                                      if d not in ("Public", "Default", "Default User", "All Users")
                                      and os.path.isdir(os.path.join(users_dir, d))]
                        if candidates:
                            win_home = f"/mnt/c/Users/{candidates[0]}"
                except OSError:
                    pass
                lines.append(f"Environment: WSL2 Linux (bash), with full access to Windows host.")
                lines.append(f"Linux commands run natively. For Windows: use cmd.exe /c or powershell.exe.")
                lines.append(f"Windows filesystem: /mnt/c/.")
                if win_home:
                    lines.append(f"Windows user home: {win_home}/.")
                lines.append(f"Linux home: {home}.")
            elif sys_name == "Linux":
                lines.append(f"Environment: Linux (bash). Home: {home}.")
            elif sys_name == "Darwin":
                lines.append(f"Environment: macOS. Home: {home}.")
            else:
                lines.append(f"Environment: {sys_name}. Home: {home}.")

            if has_ps:
                lines.append("Tips: (1) For simple Windows tasks, prefer cmd.exe (e.g. cmd.exe /c tasklist).")
                lines.append("(2) For complex PowerShell scripts (multi-line, Add-Type, .NET), use the powershell_script parameter "
                             "instead of command — it base64-encodes the script and runs via -EncodedCommand, avoiding all quoting/escaping/path issues.")
            else:
                lines.append("PowerShell is NOT available in this environment. Do not use the powershell_script parameter.")
            lines.append("(3) If a command fails, try a simpler approach instead of retrying the same thing.")
            return " ".join(lines)

        async def execute_command(command: str = "", timeout: int = 30, powershell_script: str = "") -> str:
            max_timeout = int(TERMINAL_COMMAND_TIMEOUT.value)
            timeout = max(1, min(int(timeout), max_timeout))
            max_chars = int(TERMINAL_MAX_OUTPUT_CHARS.value)
            half = max_chars // 2
            workspace = str(_get_workspace_root())

            def _decode(raw: bytes) -> str:
                """Decode bytes trying UTF-8 first, then GBK (Chinese Windows)."""
                if not raw:
                    return ""
                for enc in ("utf-8", "gbk", "latin-1"):
                    try:
                        return raw.decode(enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
                return raw.decode("utf-8", errors="replace")

            def _run():
                actual_cmd = command
                try:
                    # If powershell_script is provided, encode it and pass via -EncodedCommand.
                    # This avoids ALL temp-file / path / quoting issues across every environment.
                    if powershell_script.strip():
                        import base64
                        encoded = base64.b64encode(
                            powershell_script.encode("utf-16-le")
                        ).decode("ascii")
                        actual_cmd = (
                            f"powershell.exe -ExecutionPolicy Bypass "
                            f"-EncodedCommand {encoded}"
                        )

                    result = subprocess.run(
                        ["/bin/bash", "-c", actual_cmd],
                        cwd=workspace,
                        capture_output=True,
                        timeout=timeout,
                    )
                    return {
                        "exit_code": result.returncode,
                        "stdout": _decode(result.stdout)[:half],
                        "stderr": _decode(result.stderr)[:half],
                        "timed_out": False,
                        "cwd": workspace,
                    }
                except subprocess.TimeoutExpired as e:
                    return {
                        "exit_code": -1,
                        "stdout": _decode(e.stdout or b"")[:half],
                        "stderr": _decode(e.stderr or b"")[:half],
                        "timed_out": True,
                        "cwd": workspace,
                    }

            log.info("Terminal tool: user=%s command=%s", user.id, command[:200])
            result = await asyncio.to_thread(_run)
            return json.dumps(result, ensure_ascii=False)

        tools["execute_command"] = {
            "tool_id": "builtin:terminal",
            "callable": execute_command,
            "spec": _tool_spec(
                "execute_command",
                _terminal_env_desc(),
                {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Bash command to execute. For simple Windows tasks: cmd.exe /c ... or powershell.exe -Command '...'",
                        },
                        "powershell_script": {
                            "type": "string",
                            "description": "Full PowerShell script content (multi-line OK). Encoded as base64 and executed via powershell.exe -EncodedCommand — no temp files or path issues. Use this for complex scripts with Add-Type, .NET, special characters. When set, 'command' is ignored.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Timeout in seconds (1-30, default 30).",
                        },
                    },
                    "required": [],
                },
            ),
        }

    return tools
