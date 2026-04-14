import asyncio
import logging
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS
from open_webui.models.skills import SkillForm, SkillModel, Skills
from open_webui.models.tools import Tools
from open_webui.utils.access_control import (
    can_read_resource,
    can_write_resource,
    ensure_requested_access_control_allowed,
    ensure_resource_acl_change_allowed,
)
from open_webui.utils.auth import get_verified_user
from open_webui.utils.mcp import (
    get_mcp_server_display_metadata,
    get_mcp_servers_cached_meta,
)
from open_webui.utils.skill_importer import (
    ImportedSkillPayload,
    SkillImportError,
    import_skill_from_github,
    import_skill_from_url,
    import_skill_from_zip,
)
from open_webui.utils.tools import get_tool_server_data
from open_webui.utils.user_tools import (
    get_user_mcp_server_connections,
    get_user_native_tools_config,
    get_user_tool_server_connections,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])

router = APIRouter()


BUILTIN_SKILLS = [
    {
        "config_key": "ENABLE_WEB_SEARCH_TOOL",
        "description": "Search the web directly from native tools.",
        "source_badge": "Built-in",
        "title": "Web Search",
    },
    {
        "config_key": "ENABLE_URL_FETCH",
        "description": "Fetch and extract page content from URLs.",
        "source_badge": "Built-in",
        "title": "URL Fetch",
    },
    {
        "config_key": "ENABLE_URL_FETCH_RENDERED",
        "description": "Fetch JavaScript-rendered pages through the rendered fetcher.",
        "source_badge": "Built-in",
        "title": "Rendered URL Fetch",
    },
    {
        "config_key": "ENABLE_LIST_KNOWLEDGE_BASES",
        "description": "List available knowledge bases.",
        "source_badge": "Built-in",
        "title": "Knowledge Bases",
    },
    {
        "config_key": "ENABLE_SEARCH_KNOWLEDGE_BASES",
        "description": "Search across knowledge bases.",
        "source_badge": "Built-in",
        "title": "Knowledge Search",
    },
    {
        "config_key": "ENABLE_QUERY_KNOWLEDGE_FILES",
        "description": "Query indexed knowledge files.",
        "source_badge": "Built-in",
        "title": "Knowledge File Query",
    },
    {
        "config_key": "ENABLE_VIEW_KNOWLEDGE_FILE",
        "description": "View the contents of knowledge files.",
        "source_badge": "Built-in",
        "title": "Knowledge File Viewer",
    },
    {
        "config_key": "ENABLE_IMAGE_GENERATION_TOOL",
        "description": "Generate images from native tools.",
        "source_badge": "Built-in",
        "title": "Image Generation",
    },
    {
        "config_key": "ENABLE_IMAGE_EDIT",
        "description": "Edit generated images when available.",
        "source_badge": "Built-in",
        "title": "Image Edit",
    },
    {
        "config_key": "ENABLE_MEMORY_TOOLS",
        "description": "Read and write persistent memory items.",
        "source_badge": "Built-in",
        "title": "Memory",
    },
    {
        "config_key": "ENABLE_NOTES",
        "description": "Create and manage notes from native tools.",
        "source_badge": "Built-in",
        "title": "Notes",
    },
    {
        "config_key": "ENABLE_CHAT_HISTORY_TOOLS",
        "description": "Search chat history from native tools.",
        "source_badge": "Built-in",
        "title": "Chat History",
    },
    {
        "config_key": "ENABLE_TIME_TOOLS",
        "description": "Get current date and time information.",
        "source_badge": "Built-in",
        "title": "Time",
    },
    {
        "config_key": "ENABLE_CHANNEL_TOOLS",
        "description": "Use channel-aware native tools.",
        "source_badge": "Built-in",
        "title": "Channels",
    },
    {
        "config_key": "ENABLE_TERMINAL_TOOL",
        "description": "Use terminal access from native tools when enabled.",
        "source_badge": "Built-in",
        "title": "Terminal",
    },
]


class SkillCatalogItem(BaseModel):
    id: str
    kind: str
    source: str
    title: str
    description: str = ""
    status: str
    editable: bool = False
    manage_href: Optional[str] = None
    source_badge: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class SkillImportResult(BaseModel):
    skill: SkillModel
    status: str


class SkillImportForm(BaseModel):
    name: str
    description: str = ""
    content: str = ""


class SkillUrlImportForm(BaseModel):
    url: str


class SkillGitHubImportForm(BaseModel):
    url: str


class SkillListResponse(BaseModel):
    items: list[SkillModel]
    total: int


def _filter_visible_skills(skills: list[SkillModel], user) -> list[SkillModel]:
    if user.role == "admin":
        return skills

    return [skill for skill in skills if can_read_resource(user, skill)]


def _is_enabled_connection(connection: dict) -> bool:
    config = connection.get("config") or {}
    if isinstance(config, dict) and "enable" in config:
        return bool(config.get("enable"))
    if "enabled" in connection:
        return bool(connection.get("enabled"))
    return True


def _hostname(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


def _prompt_skill_source(skill: SkillModel) -> str:
    return "imported" if (skill.source or "manual") in {"url", "github", "zip"} else "custom"


def _prompt_skill_badge(skill: SkillModel) -> str:
    source = skill.source or "manual"
    if source == "github":
        return "GitHub"
    if source == "url":
        return "URL"
    if source == "zip":
        return "ZIP"
    return "Custom"


async def _build_tool_server_items(request: Request, user) -> list[SkillCatalogItem]:
    connections = get_user_tool_server_connections(request, user)
    session_token = getattr(getattr(request.state, "token", None), "credentials", None)
    tasks = []

    for connection in connections:
        if not _is_enabled_connection(connection):
            tasks.append(None)
            continue

        auth_type = connection.get("auth_type", "bearer")
        token = None
        if auth_type == "bearer":
            token = connection.get("key", "")
        elif auth_type == "session":
            token = session_token

        path = (connection.get("path") or "openapi.json").lstrip("/")
        base_url = (connection.get("url") or "").rstrip("/")
        full_url = f"{base_url}/{path}" if path else base_url
        tasks.append(get_tool_server_data(token, full_url))

    results = await asyncio.gather(
        *[task for task in tasks if task is not None], return_exceptions=True
    )

    resolved: list[Any] = []
    result_idx = 0
    for task in tasks:
        if task is None:
            resolved.append(None)
        else:
            resolved.append(results[result_idx])
            result_idx += 1

    items = []
    for idx, (connection, result) in enumerate(zip(connections, resolved)):
        config = connection.get("config") or {}
        title = (
            config.get("remark")
            or connection.get("name")
            or _hostname(connection.get("url") or "")
            or f"OpenAPI Server {idx + 1}"
        )

        status_value = "disabled"
        description = "Manage your OpenAPI tool server connection."
        if _is_enabled_connection(connection):
            if isinstance(result, Exception):
                status_value = "error"
                description = str(result)
            elif isinstance(result, dict):
                status_value = "connected"
                description = (
                    result.get("info", {}).get("description")
                    or description
                )

        items.append(
            SkillCatalogItem(
                id=f"server:{idx}",
                kind="tool_server",
                source="custom",
                title=title,
                description=description,
                status=status_value,
                editable=True,
                manage_href="/settings/tools",
                source_badge="OpenAPI",
                meta={
                    "auth_type": connection.get("auth_type"),
                    "path": connection.get("path"),
                    "server_index": idx,
                    "url": connection.get("url"),
                },
            )
        )

    return items


async def _build_mcp_server_items(request: Request, user) -> list[SkillCatalogItem]:
    connections = get_user_mcp_server_connections(request, user)
    cached_meta = get_mcp_servers_cached_meta(connections)

    items = []
    for idx, (connection, cached) in enumerate(zip(connections, cached_meta)):
        config = connection.get("config") or {}
        transport_type = str(cached.get("transport_type") or "http").lower()
        server_info = cached.get("server_info") or {}
        verified_at = cached.get("verified_at")
        tool_count = int(cached.get("tool_count") or 0)

        status_value = "disabled"
        description = "Manage your MCP server connection."
        if _is_enabled_connection(connection):
            status_value = "connected"
            version = server_info.get("version")
            if verified_at:
                description = (
                    f"MCP server with {tool_count} tools"
                    + (f" (v{version})" if version else "")
                )
            else:
                description = "MCP server needs verification."

        title, description = get_mcp_server_display_metadata(
            {**cached, "config": config},
            index=idx,
            default_description=description,
            prefer_hostname_for_http=True,
        )

        items.append(
            SkillCatalogItem(
                id=f"mcp:{idx}",
                kind="mcp_server",
                source="custom",
                title=title,
                description=description,
                status=status_value,
                editable=True,
                manage_href="/settings/tools",
                source_badge="MCP",
                meta={
                    "server_index": idx,
                    "tool_count": tool_count,
                    "transport_type": transport_type,
                    "url": connection.get("url"),
                    "command": connection.get("command"),
                    "verified_at": verified_at,
                },
            )
        )

    return items


def _build_builtin_items(request: Request, user) -> list[SkillCatalogItem]:
    native_cfg = get_user_native_tools_config(request, user)
    items = []

    for builtin in BUILTIN_SKILLS:
        config_key = builtin["config_key"]
        enabled = bool(native_cfg.get(config_key, False))
        items.append(
            SkillCatalogItem(
                id=config_key,
                kind="builtin",
                source="official",
                title=builtin["title"],
                description=builtin["description"],
                status="enabled" if enabled else "disabled",
                editable=True,
                manage_href="/settings/tools",
                source_badge=builtin["source_badge"],
                meta={"config_key": config_key},
            )
        )

    return items


def _build_workspace_tool_items(user) -> list[SkillCatalogItem]:
    items = []
    for tool in Tools.get_tools_list_by_user_id(user.id, "read"):
        description = ""
        if tool.meta and getattr(tool.meta, "description", None):
            description = tool.meta.description or ""
        items.append(
            SkillCatalogItem(
                id=tool.id,
                kind="workspace_tool",
                source="custom",
                title=tool.name,
                description=description,
                status="installed",
                editable=True,
                manage_href=f"/workspace/tools/edit?id={tool.id}",
                source_badge="Tool",
                meta={
                    "tool_id": tool.id,
                    "description": description,
                },
            )
        )
    return items


def _build_prompt_skill_items(skills: list[SkillModel]) -> list[SkillCatalogItem]:
    items = []
    for skill in skills:
        tags = []
        if isinstance(skill.meta, dict):
            tags = skill.meta.get("tags") or []
        items.append(
            SkillCatalogItem(
                id=skill.id,
                kind="prompt_skill",
                source=_prompt_skill_source(skill),
                title=skill.name,
                description=skill.description or "",
                status="installed",
                editable=True,
                manage_href="/workspace/skills",
                source_badge=_prompt_skill_badge(skill),
                meta={
                    "identifier": skill.identifier,
                    "skill_id": skill.id,
                    "source": skill.source or "manual",
                    "source_url": skill.source_url,
                    "tags": tags,
                },
            )
        )
    return items


async def _upsert_imported_skill(user, payload: ImportedSkillPayload) -> SkillImportResult:
    existing = Skills.get_skill_by_identifier_and_user_id(user.id, payload.identifier)

    if existing:
        existing_meta = existing.meta or {}
        same_hash = existing_meta.get("import_hash") == payload.meta.get("import_hash")
        same_content = (
            existing.name == payload.name
            and existing.description == payload.description
            and existing.content == payload.content
            and (existing.source or "manual") == payload.source
            and (existing.source_url or None) == payload.source_url
        )
        if same_hash and same_content:
            return SkillImportResult(skill=existing, status="unchanged")

        updated = Skills.update_skill_by_id(
            existing.id,
            SkillForm(
                name=payload.name,
                description=payload.description,
                content=payload.content,
                source=payload.source,
                identifier=payload.identifier,
                source_url=payload.source_url,
                meta=payload.meta,
                access_control=existing.access_control,
                is_active=existing.is_active,
            ),
        )
        if not updated:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=ERROR_MESSAGES.DEFAULT("Error updating imported skill"),
            )
        return SkillImportResult(skill=updated, status="updated")

    created = Skills.insert_new_skill(
        user.id,
        SkillForm(
            name=payload.name,
            description=payload.description,
            content=payload.content,
            source=payload.source,
            identifier=payload.identifier,
            source_url=payload.source_url,
            meta=payload.meta,
            access_control={},
            is_active=True,
        ),
    )
    if not created:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT("Error creating imported skill"),
        )
    return SkillImportResult(skill=created, status="created")


############################
# GetSkills
############################


@router.get("/", response_model=list[SkillModel])
async def get_skills(request: Request, user=Depends(get_verified_user)):
    return _filter_visible_skills(Skills.get_skills(), user)


@router.get("/list", response_model=SkillListResponse)
async def get_skill_list(
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=30, ge=1, le=100),
    user=Depends(get_verified_user),
):
    items = _filter_visible_skills(Skills.get_skills(), user)

    if view_option == "created":
        items = [item for item in items if item.user_id == user.id]
    elif view_option == "shared":
        items = [item for item in items if item.user_id != user.id]

    if query:
        query_lower = query.strip().lower()
        items = [
            item
            for item in items
            if query_lower in (item.name or "").lower()
            or query_lower in (item.description or "").lower()
            or query_lower in (item.id or "").lower()
        ]

    total = len(items)
    offset = (page - 1) * limit
    return SkillListResponse(items=items[offset : offset + limit], total=total)


############################
# GetSkillCatalog
############################


@router.get("/catalog", response_model=list[SkillCatalogItem])
async def get_skill_catalog(request: Request, user=Depends(get_verified_user)):
    visible_skills = _filter_visible_skills(Skills.get_skills(), user)
    builtin_items = _build_builtin_items(request, user)
    tool_server_items, mcp_server_items = await asyncio.gather(
        _build_tool_server_items(request, user),
        _build_mcp_server_items(request, user),
    )
    workspace_tool_items = _build_workspace_tool_items(user)
    prompt_skill_items = _build_prompt_skill_items(visible_skills)

    return (
        sorted(builtin_items, key=lambda item: (item.status != "enabled", item.title.lower()))
        + sorted(
            tool_server_items,
            key=lambda item: (item.status not in {"connected", "enabled"}, item.title.lower()),
        )
        + sorted(
            mcp_server_items,
            key=lambda item: (item.status not in {"connected", "enabled"}, item.title.lower()),
        )
        + sorted(workspace_tool_items, key=lambda item: item.title.lower())
        + sorted(prompt_skill_items, key=lambda item: item.title.lower())
    )


############################
# CreateSkill
############################


@router.post("/create", response_model=SkillModel)
async def create_skill(
    request: Request,
    form_data: SkillForm,
    user=Depends(get_verified_user),
):
    ensure_requested_access_control_allowed(
        request,
        user,
        form_data.access_control,
        public_permission_key=None,
    )
    skill = Skills.insert_new_skill(user.id, form_data)
    if not skill:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT("Error creating skill"),
        )
    return skill


############################
# ImportSkill (legacy)
############################


@router.post("/import", response_model=SkillModel)
async def import_skill(
    request: Request,
    form_data: SkillImportForm,
    user=Depends(get_verified_user),
):
    skill_form = SkillForm(
        name=form_data.name,
        description=form_data.description,
        content=form_data.content,
        source="manual",
        access_control={},
    )
    skill = Skills.insert_new_skill(user.id, skill_form)
    if not skill:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT("Error importing skill"),
        )
    return skill


############################
# ImportSkillFromUrl
############################


@router.post("/import/url", response_model=SkillImportResult)
async def import_skill_from_url_route(
    request: Request,
    form_data: SkillUrlImportForm,
    user=Depends(get_verified_user),
):
    try:
        payload = await import_skill_from_url(form_data.url)
        return await _upsert_imported_skill(user, payload)
    except SkillImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


############################
# ImportSkillFromGitHub
############################


@router.post("/import/github", response_model=SkillImportResult)
async def import_skill_from_github_route(
    request: Request,
    form_data: SkillGitHubImportForm,
    user=Depends(get_verified_user),
):
    try:
        payload = await import_skill_from_github(form_data.url)
        return await _upsert_imported_skill(user, payload)
    except SkillImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


############################
# ImportSkillFromZip
############################


@router.post("/import/zip", response_model=SkillImportResult)
async def import_skill_from_zip_route(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_verified_user),
):
    try:
        buffer = await file.read()
        payload = await import_skill_from_zip(file.filename or "skill.zip", buffer)
        return await _upsert_imported_skill(user, payload)
    except SkillImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


############################
# GetSkillById
############################


@router.get("/{skill_id}", response_model=SkillModel)
async def get_skill_by_id(skill_id: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not can_read_resource(user, skill):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
        )

    return skill


############################
# UpdateSkillById
############################


@router.post("/{skill_id}/update", response_model=SkillModel)
async def update_skill_by_id(
    request: Request,
    skill_id: str,
    form_data: SkillForm,
    user=Depends(get_verified_user),
):
    skill = Skills.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not can_write_resource(user, skill):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
        )

    if "access_control" not in getattr(form_data, "model_fields_set", set()):
        form_data.access_control = skill.access_control

    ensure_resource_acl_change_allowed(
        request,
        user,
        skill,
        form_data.access_control,
        public_permission_key=None,
    )

    updated = Skills.update_skill_by_id(skill_id, form_data)
    if not updated:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ERROR_MESSAGES.DEFAULT("Error updating skill"),
        )
    return updated


############################
# DeleteSkillById
############################


@router.delete("/{skill_id}/delete", response_model=bool)
async def delete_skill_by_id(skill_id: str, user=Depends(get_verified_user)):
    skill = Skills.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.NOT_FOUND
        )

    if not can_write_resource(user, skill):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=ERROR_MESSAGES.ACCESS_PROHIBITED
        )

    return Skills.delete_skill_by_id(skill_id)
