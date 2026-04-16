import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from typing import Any, Dict, List, Literal, Optional

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.config import get_config, save_config
from open_webui.config import BannerModel
from open_webui.constants import ERROR_MESSAGES

from open_webui.utils.tools import get_tool_server_data, get_tool_servers_data
from open_webui.utils.mcp import (
    get_mcp_runtime_capabilities,
    get_mcp_runtime_profile,
    get_mcp_server_data,
    normalize_mcp_http_headers,
)
from open_webui.utils.user_tools import (
    MAX_TOOL_CALL_ROUNDS_DEFAULT,
    MAX_TOOL_CALL_ROUNDS_MAX,
    MAX_TOOL_CALL_ROUNDS_MIN,
    TOOL_CALLING_MODE_ALLOWED,
    TOOL_CALLING_MODE_DEFAULT,
    TOOL_CALLING_MODE_KEY,
    get_user_mcp_server_connections,
    get_user_native_tools_config,
    get_user_tool_server_connections,
    normalize_max_tool_call_rounds,
    normalize_tool_calling_mode,
    set_user_mcp_server_connections,
    set_user_native_tools_config,
    set_user_tool_server_connections,
)
from open_webui.utils.data_management import deep_merge_dict
from open_webui.models.shared_tool_servers import SharedToolServers
from open_webui.utils.shared_tool_servers import (
    build_shared_mcp_display_metadata,
    build_shared_openapi_display_metadata,
    ensure_direct_tool_servers_access,
    ensure_shareable_mcp_connection,
    ensure_shareable_openapi_connection,
    get_connection_shared_id,
    strip_connection_share_runtime_fields,
)


router = APIRouter()


def _sanitize_connection_shared_fields(connection: dict) -> dict:
    payload = strip_connection_share_runtime_fields(connection)
    shared_id = get_connection_shared_id(connection)
    if shared_id:
        payload["shared_id"] = shared_id
    else:
        payload.pop("shared_id", None)
    return payload


def _get_owner_shared_records_map(owner_user_id: str, kind: str) -> dict:
    return {
        item.id: item
        for item in SharedToolServers.get_shared_tool_servers_by_owner_user_id(
            owner_user_id
        )
        if item.kind == kind
    }


def _attach_shared_connection_metadata(
    owner_user_id: str, kind: str, connections: list[dict]
) -> list[dict]:
    shared_records_map = _get_owner_shared_records_map(owner_user_id, kind)
    enriched_connections = []

    for connection in connections:
        payload = _sanitize_connection_shared_fields(connection)
        shared_id = payload.get("shared_id")
        if shared_id and shared_id in shared_records_map:
            shared = shared_records_map[shared_id]
            payload["shared_id"] = shared.id
            payload["shared_access_control"] = shared.access_control
            payload["shared_enabled"] = shared.enabled
        enriched_connections.append(payload)

    return enriched_connections


async def _sync_shared_openapi_connections(
    owner_user_id: str,
    previous_connections: list[dict],
    next_connections: list[dict],
) -> list[dict]:
    shared_records_map = _get_owner_shared_records_map(owner_user_id, "openapi")
    previous_shared_ids = {
        shared_id
        for shared_id in (
            get_connection_shared_id(connection) for connection in previous_connections
        )
        if shared_id
    }
    next_shared_ids: set[str] = set()
    synced_connections: list[dict] = []

    for connection in next_connections:
        payload = _sanitize_connection_shared_fields(connection)
        shared_id = payload.get("shared_id")
        if shared_id:
            shared = shared_records_map.get(shared_id)
            if shared is None:
                payload.pop("shared_id", None)
            else:
                next_shared_ids.add(shared.id)
                if shared.enabled:
                    ensure_shareable_openapi_connection(payload)
                    display_metadata = build_shared_openapi_display_metadata(
                        payload, shared.display_metadata
                    )
                    SharedToolServers.update_shared_tool_server_by_id(
                        shared.id,
                        {
                            "connection_payload": payload,
                            "display_metadata": display_metadata,
                        },
                    )
        synced_connections.append(payload)

    removed_shared_ids = sorted(previous_shared_ids - next_shared_ids)
    if removed_shared_ids:
        SharedToolServers.delete_shared_tool_servers_by_ids(removed_shared_ids)

    return synced_connections


async def _sync_shared_mcp_connections(
    owner_user_id: str,
    previous_connections: list[dict],
    next_connections: list[dict],
) -> list[dict]:
    shared_records_map = _get_owner_shared_records_map(owner_user_id, "mcp")
    previous_shared_ids = {
        shared_id
        for shared_id in (
            get_connection_shared_id(connection) for connection in previous_connections
        )
        if shared_id
    }
    next_shared_ids: set[str] = set()
    synced_connections: list[dict] = []

    for connection in next_connections:
        payload = _sanitize_connection_shared_fields(connection)
        shared_id = payload.get("shared_id")
        if shared_id:
            shared = shared_records_map.get(shared_id)
            if shared is None:
                payload.pop("shared_id", None)
            else:
                next_shared_ids.add(shared.id)
                if shared.enabled:
                    ensure_shareable_mcp_connection(payload)
                    display_metadata = build_shared_mcp_display_metadata(
                        payload, shared.display_metadata
                    )
                    SharedToolServers.update_shared_tool_server_by_id(
                        shared.id,
                        {
                            "connection_payload": payload,
                            "display_metadata": display_metadata,
                        },
                    )
        synced_connections.append(payload)

    removed_shared_ids = sorted(previous_shared_ids - next_shared_ids)
    if removed_shared_ids:
        SharedToolServers.delete_shared_tool_servers_by_ids(removed_shared_ids)

    return synced_connections


############################
# ImportConfig
############################


class ImportConfigForm(BaseModel):
    config: dict
    mode: Literal["merge", "replace"] = "replace"


@router.post("/import", response_model=dict)
async def import_config(form_data: ImportConfigForm, user=Depends(get_admin_user)):
    next_config = (
        deep_merge_dict(get_config(), form_data.config)
        if form_data.mode == "merge"
        else form_data.config
    )

    if not save_config(next_config):
        raise HTTPException(status_code=400, detail="Failed to import config.")

    return {"mode": form_data.mode, "config": get_config()}


############################
# ExportConfig
############################


@router.get("/export", response_model=dict)
async def export_config(user=Depends(get_admin_user)):
    return get_config()


############################
# Direct Connections Config
############################


class DirectConnectionsConfigForm(BaseModel):
    ENABLE_DIRECT_CONNECTIONS: bool


@router.get("/direct_connections", response_model=DirectConnectionsConfigForm)
async def get_direct_connections_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_DIRECT_CONNECTIONS": request.app.state.config.ENABLE_DIRECT_CONNECTIONS,
    }


@router.post("/direct_connections", response_model=DirectConnectionsConfigForm)
async def set_direct_connections_config(
    request: Request,
    form_data: DirectConnectionsConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.ENABLE_DIRECT_CONNECTIONS = (
        form_data.ENABLE_DIRECT_CONNECTIONS
    )
    return {
        "ENABLE_DIRECT_CONNECTIONS": request.app.state.config.ENABLE_DIRECT_CONNECTIONS,
    }


############################
# Connections Config (new UI)
############################


class ConnectionsConfigForm(BaseModel):
    ENABLE_DIRECT_CONNECTIONS: bool
    ENABLE_BASE_MODELS_CACHE: bool


@router.get("/connections", response_model=ConnectionsConfigForm)
async def get_connections_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_DIRECT_CONNECTIONS": request.app.state.config.ENABLE_DIRECT_CONNECTIONS,
        "ENABLE_BASE_MODELS_CACHE": request.app.state.config.ENABLE_BASE_MODELS_CACHE,
    }


@router.post("/connections", response_model=ConnectionsConfigForm)
async def set_connections_config(
    request: Request, form_data: ConnectionsConfigForm, user=Depends(get_admin_user)
):
    prev_cache_enabled = request.app.state.config.ENABLE_BASE_MODELS_CACHE

    request.app.state.config.ENABLE_DIRECT_CONNECTIONS = form_data.ENABLE_DIRECT_CONNECTIONS
    request.app.state.config.ENABLE_BASE_MODELS_CACHE = form_data.ENABLE_BASE_MODELS_CACHE

    # If the cache is (re-)enabled, warm it once at save time (in background).
    if request.app.state.config.ENABLE_BASE_MODELS_CACHE and (
        not prev_cache_enabled or getattr(request.app.state, "BASE_MODELS", None) is None
    ):
        from open_webui.utils.models import invalidate_base_model_cache

        request.app.state.BASE_MODELS = None
        invalidate_base_model_cache()
        try:
            from open_webui.utils.models import get_all_base_models

            asyncio.create_task(get_all_base_models(request, user=user))
        except Exception:
            # Cache warmup is best-effort; the next /api/models call will populate it.
            pass

    return {
        "ENABLE_DIRECT_CONNECTIONS": request.app.state.config.ENABLE_DIRECT_CONNECTIONS,
        "ENABLE_BASE_MODELS_CACHE": request.app.state.config.ENABLE_BASE_MODELS_CACHE,
    }


############################
# ToolServers Config
############################


class ToolServerConnection(BaseModel):
    url: str
    path: str
    auth_type: Optional[str]
    key: Optional[str]
    config: Optional[dict]

    model_config = ConfigDict(extra="allow")


class ToolServersConfigForm(BaseModel):
    TOOL_SERVER_CONNECTIONS: list[ToolServerConnection]


def _normalize_tool_server_connection(connection: ToolServerConnection) -> dict:
    payload = _sanitize_connection_shared_fields(connection.model_dump())
    payload["url"] = str(payload.get("url") or "").rstrip("/")
    payload["path"] = str(payload.get("path") or "openapi.json").strip() or "openapi.json"
    payload["auth_type"] = str(payload.get("auth_type") or "bearer").lower()
    if payload.get("auth_type") != "bearer":
        payload.pop("key", None)
    return payload


@router.get("/tool_servers", response_model=ToolServersConfigForm)
async def get_tool_servers_config(request: Request, user=Depends(get_verified_user)):
    ensure_direct_tool_servers_access(request, user)
    connections = get_user_tool_server_connections(request, user)
    return {
        "TOOL_SERVER_CONNECTIONS": _attach_shared_connection_metadata(
            user.id, "openapi", connections
        ),
    }


@router.post("/tool_servers", response_model=ToolServersConfigForm)
async def set_tool_servers_config(
    request: Request,
    form_data: ToolServersConfigForm,
    user=Depends(get_verified_user),
):
    ensure_direct_tool_servers_access(request, user)
    previous_connections = get_user_tool_server_connections(request, user)
    connections = [
        _normalize_tool_server_connection(connection)
        for connection in form_data.TOOL_SERVER_CONNECTIONS
    ]
    connections = await _sync_shared_openapi_connections(
        user.id, previous_connections, connections
    )

    set_user_tool_server_connections(user, connections)

    return {
        "TOOL_SERVER_CONNECTIONS": _attach_shared_connection_metadata(
            user.id, "openapi", connections
        ),
    }


@router.post("/tool_servers/verify")
async def verify_tool_servers_config(
    request: Request, form_data: ToolServerConnection, user=Depends(get_verified_user)
):
    """
    Verify the connection to the tool server.
    """
    try:
        ensure_direct_tool_servers_access(request, user)

        token = None
        if form_data.auth_type == "bearer":
            token = form_data.key
        elif form_data.auth_type == "session":
            token = request.state.token.credentials

        url = f"{form_data.url}/{form_data.path}"
        return await get_tool_server_data(token, url)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to connect to the tool server: {str(e)}",
        )


class SharedToolServerAccessForm(BaseModel):
    access_control: Optional[dict] = None


class SharedToolServerAccessResponse(BaseModel):
    id: str
    enabled: bool
    access_control: Optional[dict] = None


@router.post(
    "/tool_servers/{index}/share", response_model=SharedToolServerAccessResponse
)
async def share_tool_server_connection(
    index: int,
    request: Request,
    form_data: SharedToolServerAccessForm,
    user=Depends(get_admin_user),
):
    connections = get_user_tool_server_connections(request, user)
    if index < 0 or index >= len(connections):
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    connection = _sanitize_connection_shared_fields(connections[index])
    ensure_shareable_openapi_connection(connection)

    shared_id = get_connection_shared_id(connection)
    shared = None
    if shared_id:
        shared = SharedToolServers.get_shared_tool_server_by_id(shared_id)
        if shared and (shared.kind != "openapi" or shared.owner_user_id != user.id):
            shared = None

    display_metadata = build_shared_openapi_display_metadata(
        connection,
        shared.display_metadata if shared else None,
    )
    try:
        auth_type = str(connection.get("auth_type") or "bearer").lower()
        token = connection.get("key", "") if auth_type == "bearer" else None
        openapi_data = await get_tool_server_data(
            token,
            f"{str(connection.get('url') or '').rstrip('/')}/{str(connection.get('path') or 'openapi.json').strip() or 'openapi.json'}",
        )
        info = openapi_data.get("info", {}) or {}
        display_metadata = {
            **display_metadata,
            "title": str(info.get("title") or "").strip() or display_metadata.get("title"),
            "description": str(info.get("description") or "").strip()
            or display_metadata.get("description"),
        }
    except Exception:
        pass
    if shared:
        shared = SharedToolServers.update_shared_tool_server_by_id(
            shared.id,
            {
                "connection_payload": connection,
                "display_metadata": display_metadata,
                "access_control": form_data.access_control,
                "enabled": True,
            },
        )
        if shared is None:
            raise HTTPException(status_code=400, detail="Failed to update shared tool server")
    else:
        shared = SharedToolServers.insert_new_shared_tool_server(
            user.id,
            kind="openapi",
            connection_payload=connection,
            display_metadata=display_metadata,
            access_control=form_data.access_control,
            enabled=True,
        )
        if shared is None:
            raise HTTPException(status_code=400, detail="Failed to share tool server")

    updated_connections = [deepcopy(item) for item in connections]
    updated_connections[index] = {
        **_sanitize_connection_shared_fields(updated_connections[index]),
        "shared_id": shared.id,
    }
    set_user_tool_server_connections(user, updated_connections)

    return {
        "id": shared.id,
        "enabled": shared.enabled,
        "access_control": shared.access_control,
    }


@router.delete(
    "/tool_servers/{index}/share", response_model=SharedToolServerAccessResponse
)
async def unshare_tool_server_connection(
    index: int,
    request: Request,
    user=Depends(get_admin_user),
):
    connections = get_user_tool_server_connections(request, user)
    if index < 0 or index >= len(connections):
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    shared_id = get_connection_shared_id(connections[index])
    if not shared_id:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    shared = SharedToolServers.get_shared_tool_server_by_id(shared_id)
    if shared is None or shared.kind != "openapi" or shared.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    updated = SharedToolServers.update_shared_tool_server_by_id(
        shared.id, {"enabled": False}
    )
    if updated is None:
        raise HTTPException(status_code=400, detail="Failed to revoke shared tool server")

    return {
        "id": updated.id,
        "enabled": updated.enabled,
        "access_control": updated.access_control,
    }


############################
# Native/Builtin Tools Config (Native Mode)
############################


class NativeToolsConfigForm(BaseModel):
    TOOL_CALLING_MODE: str
    ENABLE_INTERLEAVED_THINKING: bool
    MAX_TOOL_CALL_ROUNDS: int = Field(
        MAX_TOOL_CALL_ROUNDS_DEFAULT,
        ge=MAX_TOOL_CALL_ROUNDS_MIN,
        le=MAX_TOOL_CALL_ROUNDS_MAX,
    )

    # Built-in system tools (injected in Native Mode)
    ENABLE_WEB_SEARCH_TOOL: bool
    ENABLE_URL_FETCH: bool
    ENABLE_URL_FETCH_RENDERED: bool

    ENABLE_LIST_KNOWLEDGE_BASES: bool
    ENABLE_SEARCH_KNOWLEDGE_BASES: bool
    ENABLE_QUERY_KNOWLEDGE_FILES: bool
    ENABLE_VIEW_KNOWLEDGE_FILE: bool

    ENABLE_IMAGE_GENERATION_TOOL: bool
    ENABLE_IMAGE_EDIT: bool

    ENABLE_MEMORY_TOOLS: bool
    ENABLE_NOTES: bool
    ENABLE_CHAT_HISTORY_TOOLS: bool
    ENABLE_TIME_TOOLS: bool
    ENABLE_CHANNEL_TOOLS: bool
    ENABLE_TERMINAL_TOOL: bool


@router.get("/native_tools", response_model=NativeToolsConfigForm)
async def get_native_tools_config(request: Request, user=Depends(get_verified_user)):
    return get_user_native_tools_config(request, user)


@router.post("/native_tools", response_model=NativeToolsConfigForm)
async def set_native_tools_config(
    request: Request, form_data: NativeToolsConfigForm, user=Depends(get_verified_user)
):
    mode_raw = str(getattr(form_data, TOOL_CALLING_MODE_KEY, "") or "").strip().lower()
    if mode_raw not in TOOL_CALLING_MODE_ALLOWED:
        raise HTTPException(
            status_code=400,
            detail="Invalid TOOL_CALLING_MODE. Must be 'default', 'native', or 'off'.",
        )
    mode = normalize_tool_calling_mode(mode_raw, default=TOOL_CALLING_MODE_DEFAULT)

    payload = form_data.model_dump()
    payload[TOOL_CALLING_MODE_KEY] = mode
    payload["MAX_TOOL_CALL_ROUNDS"] = normalize_max_tool_call_rounds(
        payload.get("MAX_TOOL_CALL_ROUNDS"),
        default=MAX_TOOL_CALL_ROUNDS_DEFAULT,
    )
    updated_user = set_user_native_tools_config(user, payload) or user

    return get_user_native_tools_config(request, updated_user)


############################
# MCP Servers Config
############################


class MCPServerConnection(BaseModel):
    transport_type: Literal["http", "stdio"] = "http"
    url: Optional[str] = None
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    headers: Dict[str, str] = Field(default_factory=dict)
    name: Optional[str] = None
    description: Optional[str] = None
    auth_type: Optional[str] = None
    key: Optional[str] = None
    config: Optional[dict] = None
    server_info: Optional[dict] = None
    tool_count: Optional[int] = None
    verified_at: Optional[str] = None
    tools: Optional[list[dict]] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_transport_fields(self):
        self.transport_type = (self.transport_type or "http").lower()
        self.url = (self.url or "").strip() or None
        self.command = (self.command or "").strip() or None
        self.args = [str(item) for item in (self.args or [])]
        self.env = {str(key): str(value) for key, value in (self.env or {}).items()}
        self.headers = normalize_mcp_http_headers(self.headers or {})

        if self.transport_type == "http":
            if not self.url:
                raise ValueError("url is required when transport_type is http")
        elif self.transport_type == "stdio":
            if not self.command:
                raise ValueError("command is required when transport_type is stdio")
            self.headers = {}

        return self


def _normalize_mcp_server_connection(connection: MCPServerConnection) -> dict:
    shared_id = get_connection_shared_id(connection.model_dump())
    base = {
        "transport_type": connection.transport_type,
        "name": connection.name,
        "description": connection.description,
        "config": connection.config or {},
        "server_info": connection.server_info,
        "tool_count": connection.tool_count,
        "verified_at": connection.verified_at,
        "tools": connection.tools,
    }

    if connection.transport_type == "stdio":
        normalized = {
            **base,
            "command": connection.command,
            "args": [str(item) for item in (connection.args or [])],
            "env": {str(key): str(value) for key, value in (connection.env or {}).items()},
        }
    else:
        normalized = {
            **base,
            "url": (connection.url or "").rstrip("/"),
            "auth_type": connection.auth_type,
        }
        if connection.headers:
            normalized["headers"] = connection.headers
        if connection.key:
            normalized["key"] = connection.key

    normalized = {key: value for key, value in normalized.items() if value is not None}
    normalized = _sanitize_connection_shared_fields(normalized)
    if shared_id:
        normalized["shared_id"] = shared_id
    return normalized


class MCPServersConfigForm(BaseModel):
    MCP_SERVER_CONNECTIONS: list[MCPServerConnection]
    MCP_RUNTIME_CAPABILITIES: Dict[str, Any] = Field(default_factory=dict)
    MCP_RUNTIME_PROFILE: str = "custom"


def _build_mcp_servers_config_response(connections: list[dict]) -> dict:
    return {
        "MCP_SERVER_CONNECTIONS": connections,
        "MCP_RUNTIME_CAPABILITIES": get_mcp_runtime_capabilities(),
        "MCP_RUNTIME_PROFILE": get_mcp_runtime_profile(),
    }


@router.get("/mcp_servers", response_model=MCPServersConfigForm)
async def get_mcp_servers_config(request: Request, user=Depends(get_verified_user)):
    ensure_direct_tool_servers_access(request, user)
    return _build_mcp_servers_config_response(
        _attach_shared_connection_metadata(
            user.id, "mcp", get_user_mcp_server_connections(request, user)
        )
    )


@router.post("/mcp_servers", response_model=MCPServersConfigForm)
async def set_mcp_servers_config(
    request: Request, form_data: MCPServersConfigForm, user=Depends(get_verified_user)
):
    ensure_direct_tool_servers_access(request, user)
    if (
        getattr(user, "role", None) != "admin"
        and any(
            connection.transport_type == "stdio"
            for connection in form_data.MCP_SERVER_CONNECTIONS
        )
    ):
        raise HTTPException(status_code=403, detail="stdio MCP servers are admin-only")

    previous_connections = get_user_mcp_server_connections(request, user)
    connections = [
        _normalize_mcp_server_connection(connection)
        for connection in form_data.MCP_SERVER_CONNECTIONS
    ]
    connections = await _sync_shared_mcp_connections(
        user.id, previous_connections, connections
    )

    set_user_mcp_server_connections(user, connections)

    return _build_mcp_servers_config_response(
        _attach_shared_connection_metadata(user.id, "mcp", connections)
    )


@router.post("/mcp_servers/verify")
async def verify_mcp_server_connection(
    request: Request, form_data: MCPServerConnection, user=Depends(get_verified_user)
):
    """
    Verify the connection to an MCP server.
    """
    try:
        ensure_direct_tool_servers_access(request, user)
        if form_data.transport_type == "stdio" and getattr(user, "role", None) != "admin":
            raise HTTPException(status_code=403, detail="stdio MCP servers are admin-only")

        normalized_connection = _normalize_mcp_server_connection(form_data)
        token = None
        if (
            form_data.transport_type == "http"
            and (form_data.auth_type or "none").lower() == "session"
        ):
            token = request.state.token.credentials

        data = await get_mcp_server_data(
            normalized_connection,
            session_token=token,
            use_temp_stdio_client=form_data.transport_type == "stdio",
        )

        tools = data.get("tools", []) or []
        return {
            "server_info": data.get("server_info", {}) or {},
            "tool_count": len(tools),
            "verified_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "tools": [
                {
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "inputSchema": t.get("inputSchema") or {},
                }
                for t in tools[:50]
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to connect to the MCP server: {str(e)}",
        )


@router.post("/mcp_servers/{index}/share", response_model=SharedToolServerAccessResponse)
async def share_mcp_server_connection(
    index: int,
    request: Request,
    form_data: SharedToolServerAccessForm,
    user=Depends(get_admin_user),
):
    connections = get_user_mcp_server_connections(request, user)
    if index < 0 or index >= len(connections):
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    connection = _sanitize_connection_shared_fields(connections[index])
    ensure_shareable_mcp_connection(connection)

    shared_id = get_connection_shared_id(connection)
    shared = None
    if shared_id:
        shared = SharedToolServers.get_shared_tool_server_by_id(shared_id)
        if shared and (shared.kind != "mcp" or shared.owner_user_id != user.id):
            shared = None

    display_metadata = build_shared_mcp_display_metadata(
        connection,
        shared.display_metadata if shared else None,
    )
    if shared:
        shared = SharedToolServers.update_shared_tool_server_by_id(
            shared.id,
            {
                "connection_payload": connection,
                "display_metadata": display_metadata,
                "access_control": form_data.access_control,
                "enabled": True,
            },
        )
        if shared is None:
            raise HTTPException(status_code=400, detail="Failed to update shared MCP server")
    else:
        shared = SharedToolServers.insert_new_shared_tool_server(
            user.id,
            kind="mcp",
            connection_payload=connection,
            display_metadata=display_metadata,
            access_control=form_data.access_control,
            enabled=True,
        )
        if shared is None:
            raise HTTPException(status_code=400, detail="Failed to share MCP server")

    updated_connections = [deepcopy(item) for item in connections]
    updated_connections[index] = {
        **_sanitize_connection_shared_fields(updated_connections[index]),
        "shared_id": shared.id,
    }
    set_user_mcp_server_connections(user, updated_connections)

    return {
        "id": shared.id,
        "enabled": shared.enabled,
        "access_control": shared.access_control,
    }


@router.delete(
    "/mcp_servers/{index}/share", response_model=SharedToolServerAccessResponse
)
async def unshare_mcp_server_connection(
    index: int,
    request: Request,
    user=Depends(get_admin_user),
):
    connections = get_user_mcp_server_connections(request, user)
    if index < 0 or index >= len(connections):
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    shared_id = get_connection_shared_id(connections[index])
    if not shared_id:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    shared = SharedToolServers.get_shared_tool_server_by_id(shared_id)
    if shared is None or shared.kind != "mcp" or shared.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)

    updated = SharedToolServers.update_shared_tool_server_by_id(
        shared.id, {"enabled": False}
    )
    if updated is None:
        raise HTTPException(status_code=400, detail="Failed to revoke shared MCP server")

    return {
        "id": updated.id,
        "enabled": updated.enabled,
        "access_control": updated.access_control,
    }


############################
# CodeInterpreterConfig
############################
class CodeInterpreterConfigForm(BaseModel):
    ENABLE_CODE_EXECUTION: bool
    CODE_EXECUTION_ENGINE: str
    CODE_EXECUTION_JUPYTER_URL: Optional[str]
    CODE_EXECUTION_JUPYTER_AUTH: Optional[str]
    CODE_EXECUTION_JUPYTER_AUTH_TOKEN: Optional[str]
    CODE_EXECUTION_JUPYTER_AUTH_PASSWORD: Optional[str]
    CODE_EXECUTION_JUPYTER_TIMEOUT: Optional[int]
    ENABLE_CODE_INTERPRETER: bool
    CODE_INTERPRETER_ENGINE: str
    CODE_INTERPRETER_JUPYTER_URL: Optional[str]
    CODE_INTERPRETER_JUPYTER_AUTH: Optional[str]
    CODE_INTERPRETER_JUPYTER_AUTH_TOKEN: Optional[str]
    CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD: Optional[str]
    CODE_INTERPRETER_JUPYTER_TIMEOUT: Optional[int]


@router.get("/code_execution", response_model=CodeInterpreterConfigForm)
async def get_code_execution_config(request: Request, user=Depends(get_admin_user)):
    return {
        "ENABLE_CODE_EXECUTION": request.app.state.config.ENABLE_CODE_EXECUTION,
        "CODE_EXECUTION_ENGINE": request.app.state.config.CODE_EXECUTION_ENGINE,
        "CODE_EXECUTION_JUPYTER_URL": request.app.state.config.CODE_EXECUTION_JUPYTER_URL,
        "CODE_EXECUTION_JUPYTER_AUTH": request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH,
        "CODE_EXECUTION_JUPYTER_AUTH_TOKEN": request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_TOKEN,
        "CODE_EXECUTION_JUPYTER_AUTH_PASSWORD": request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_PASSWORD,
        "CODE_EXECUTION_JUPYTER_TIMEOUT": request.app.state.config.CODE_EXECUTION_JUPYTER_TIMEOUT,
        "ENABLE_CODE_INTERPRETER": request.app.state.config.ENABLE_CODE_INTERPRETER,
        "CODE_INTERPRETER_ENGINE": request.app.state.config.CODE_INTERPRETER_ENGINE,
        "CODE_INTERPRETER_JUPYTER_URL": request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
        "CODE_INTERPRETER_JUPYTER_AUTH": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH,
        "CODE_INTERPRETER_JUPYTER_AUTH_TOKEN": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN,
        "CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD,
        "CODE_INTERPRETER_JUPYTER_TIMEOUT": request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
    }


@router.post("/code_execution", response_model=CodeInterpreterConfigForm)
async def set_code_execution_config(
    request: Request, form_data: CodeInterpreterConfigForm, user=Depends(get_admin_user)
):

    request.app.state.config.ENABLE_CODE_EXECUTION = form_data.ENABLE_CODE_EXECUTION

    request.app.state.config.CODE_EXECUTION_ENGINE = form_data.CODE_EXECUTION_ENGINE
    request.app.state.config.CODE_EXECUTION_JUPYTER_URL = (
        form_data.CODE_EXECUTION_JUPYTER_URL
    )
    request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH = (
        form_data.CODE_EXECUTION_JUPYTER_AUTH
    )
    request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_TOKEN = (
        form_data.CODE_EXECUTION_JUPYTER_AUTH_TOKEN
    )
    request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_PASSWORD = (
        form_data.CODE_EXECUTION_JUPYTER_AUTH_PASSWORD
    )
    request.app.state.config.CODE_EXECUTION_JUPYTER_TIMEOUT = (
        form_data.CODE_EXECUTION_JUPYTER_TIMEOUT
    )

    request.app.state.config.ENABLE_CODE_INTERPRETER = form_data.ENABLE_CODE_INTERPRETER
    request.app.state.config.CODE_INTERPRETER_ENGINE = form_data.CODE_INTERPRETER_ENGINE

    request.app.state.config.CODE_INTERPRETER_JUPYTER_URL = (
        form_data.CODE_INTERPRETER_JUPYTER_URL
    )

    request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH = (
        form_data.CODE_INTERPRETER_JUPYTER_AUTH
    )

    request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN = (
        form_data.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
    )
    request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD = (
        form_data.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
    )
    request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT = (
        form_data.CODE_INTERPRETER_JUPYTER_TIMEOUT
    )

    return {
        "ENABLE_CODE_EXECUTION": request.app.state.config.ENABLE_CODE_EXECUTION,
        "CODE_EXECUTION_ENGINE": request.app.state.config.CODE_EXECUTION_ENGINE,
        "CODE_EXECUTION_JUPYTER_URL": request.app.state.config.CODE_EXECUTION_JUPYTER_URL,
        "CODE_EXECUTION_JUPYTER_AUTH": request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH,
        "CODE_EXECUTION_JUPYTER_AUTH_TOKEN": request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_TOKEN,
        "CODE_EXECUTION_JUPYTER_AUTH_PASSWORD": request.app.state.config.CODE_EXECUTION_JUPYTER_AUTH_PASSWORD,
        "CODE_EXECUTION_JUPYTER_TIMEOUT": request.app.state.config.CODE_EXECUTION_JUPYTER_TIMEOUT,
        "ENABLE_CODE_INTERPRETER": request.app.state.config.ENABLE_CODE_INTERPRETER,
        "CODE_INTERPRETER_ENGINE": request.app.state.config.CODE_INTERPRETER_ENGINE,
        "CODE_INTERPRETER_JUPYTER_URL": request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
        "CODE_INTERPRETER_JUPYTER_AUTH": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH,
        "CODE_INTERPRETER_JUPYTER_AUTH_TOKEN": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN,
        "CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD,
        "CODE_INTERPRETER_JUPYTER_TIMEOUT": request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
    }


# Compatibility-only model UI config.
# DEFAULT_MODELS is deprecated and intentionally ignored; only MODEL_ORDER_LIST is mutable.
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: Optional[str]
    MODEL_ORDER_LIST: Optional[list[str]]


@router.get("/models", response_model=ModelsConfigForm)
async def get_models_config(request: Request, user=Depends(get_admin_user)):
    return {
        "DEFAULT_MODELS": "",
        "MODEL_ORDER_LIST": request.app.state.config.MODEL_ORDER_LIST or [],
    }


@router.post("/models", response_model=ModelsConfigForm)
async def set_models_config(
    request: Request, form_data: ModelsConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.DEFAULT_MODELS = ""
    request.app.state.config.MODEL_ORDER_LIST = form_data.MODEL_ORDER_LIST or []
    return {
        "DEFAULT_MODELS": "",
        "MODEL_ORDER_LIST": request.app.state.config.MODEL_ORDER_LIST or [],
    }


class PromptSuggestion(BaseModel):
    title: list[str]
    content: str


class SetDefaultSuggestionsForm(BaseModel):
    suggestions: list[PromptSuggestion]


@router.post("/suggestions", response_model=list[PromptSuggestion])
async def set_default_suggestions(
    request: Request,
    form_data: SetDefaultSuggestionsForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    request.app.state.config.DEFAULT_PROMPT_SUGGESTIONS = data["suggestions"]
    return request.app.state.config.DEFAULT_PROMPT_SUGGESTIONS


############################
# SetBanners
############################


class SetBannersForm(BaseModel):
    banners: list[BannerModel]


@router.post("/banners", response_model=list[BannerModel])
async def set_banners(
    request: Request,
    form_data: SetBannersForm,
    user=Depends(get_admin_user),
):
    data = form_data.model_dump()
    request.app.state.config.BANNERS = data["banners"]
    return request.app.state.config.BANNERS


@router.get("/banners", response_model=list[BannerModel])
async def get_banners(
    request: Request,
    user=Depends(get_verified_user),
):
    return request.app.state.config.BANNERS
