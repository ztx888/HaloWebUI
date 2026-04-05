import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from typing import Any, Dict, List, Literal, Optional

from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.config import get_config, save_config
from open_webui.config import BannerModel

from open_webui.utils.tools import get_tool_server_data, get_tool_servers_data
from open_webui.utils.mcp import (
    get_mcp_runtime_capabilities,
    get_mcp_runtime_profile,
    get_mcp_server_data,
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


router = APIRouter()


############################
# ImportConfig
############################


class ImportConfigForm(BaseModel):
    config: dict


@router.post("/import", response_model=dict)
async def import_config(form_data: ImportConfigForm, user=Depends(get_admin_user)):
    save_config(form_data.config)
    return get_config()


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


@router.get("/tool_servers", response_model=ToolServersConfigForm)
async def get_tool_servers_config(request: Request, user=Depends(get_verified_user)):
    return {
        "TOOL_SERVER_CONNECTIONS": get_user_tool_server_connections(request, user),
    }


@router.post("/tool_servers", response_model=ToolServersConfigForm)
async def set_tool_servers_config(
    request: Request,
    form_data: ToolServersConfigForm,
    user=Depends(get_verified_user),
):
    connections = [
        connection.model_dump() for connection in form_data.TOOL_SERVER_CONNECTIONS
    ]

    set_user_tool_server_connections(user, connections)

    return {
        "TOOL_SERVER_CONNECTIONS": connections,
    }


@router.post("/tool_servers/verify")
async def verify_tool_servers_config(
    request: Request, form_data: ToolServerConnection, user=Depends(get_verified_user)
):
    """
    Verify the connection to the tool server.
    """
    try:

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
            detail="Invalid TOOL_CALLING_MODE. Must be 'default' or 'native'.",
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
    name: Optional[str] = None
    description: Optional[str] = None
    auth_type: Optional[str] = None
    key: Optional[str] = None
    config: Optional[dict] = None
    server_info: Optional[dict] = None
    tool_count: Optional[int] = None
    verified_at: Optional[str] = None

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="after")
    def validate_transport_fields(self):
        self.transport_type = (self.transport_type or "http").lower()
        self.url = (self.url or "").strip() or None
        self.command = (self.command or "").strip() or None
        self.args = [str(item) for item in (self.args or [])]
        self.env = {str(key): str(value) for key, value in (self.env or {}).items()}

        if self.transport_type == "http":
            if not self.url:
                raise ValueError("url is required when transport_type is http")
        elif self.transport_type == "stdio":
            if not self.command:
                raise ValueError("command is required when transport_type is stdio")

        return self


def _normalize_mcp_server_connection(connection: MCPServerConnection) -> dict:
    base = {
        "transport_type": connection.transport_type,
        "name": connection.name,
        "description": connection.description,
        "config": connection.config or {},
        "server_info": connection.server_info,
        "tool_count": connection.tool_count,
        "verified_at": connection.verified_at,
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
        if connection.key:
            normalized["key"] = connection.key

    return {key: value for key, value in normalized.items() if value is not None}


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
    return _build_mcp_servers_config_response(
        get_user_mcp_server_connections(request, user)
    )


@router.post("/mcp_servers", response_model=MCPServersConfigForm)
async def set_mcp_servers_config(
    request: Request, form_data: MCPServersConfigForm, user=Depends(get_verified_user)
):
    if (
        getattr(user, "role", None) != "admin"
        and any(
            connection.transport_type == "stdio"
            for connection in form_data.MCP_SERVER_CONNECTIONS
        )
    ):
        raise HTTPException(status_code=403, detail="stdio MCP servers are admin-only")

    connections = [
        _normalize_mcp_server_connection(connection)
        for connection in form_data.MCP_SERVER_CONNECTIONS
    ]

    set_user_mcp_server_connections(user, connections)

    return _build_mcp_servers_config_response(connections)


@router.post("/mcp_servers/verify")
async def verify_mcp_server_connection(
    request: Request, form_data: MCPServerConnection, user=Depends(get_verified_user)
):
    """
    Verify the connection to an MCP server.
    """
    try:
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
