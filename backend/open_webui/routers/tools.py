import logging
from pathlib import Path
from typing import Optional
import time
import re

from open_webui.models.tools import (
    ToolForm,
    ToolModel,
    ToolResponse,
    ToolMeta,
    ToolUserResponse,
    Tools,
)
from open_webui.utils.plugin import load_tool_module_by_id, replace_imports
from open_webui.config import CACHE_DIR
from open_webui.constants import ERROR_MESSAGES
from fastapi import APIRouter, Depends, HTTPException, Request, status
from open_webui.utils.tools import get_tool_specs
from open_webui.utils.auth import get_verified_user
from open_webui.utils.access_control import (
    can_read_resource,
    can_write_resource,
    ensure_requested_access_control_allowed,
    ensure_resource_acl_change_allowed,
    has_permission,
)
from open_webui.env import SRC_LOG_LEVELS

from open_webui.utils.tools import get_tool_servers_data
from open_webui.utils.mcp import (
    get_mcp_server_display_metadata,
    get_mcp_servers_cached_meta,
)
from open_webui.utils.user_tools import (
    get_user_mcp_server_connections,
    get_user_tool_server_connections,
)
from open_webui.models.users import Users, UserResponse
from open_webui.utils.shared_tool_servers import (
    MCP_SHARED_TOOL_PREFIX,
    OPENAPI_SHARED_TOOL_PREFIX,
    build_runtime_shared_connection_payload,
    can_use_direct_tool_servers,
    get_accessible_shared_tool_servers,
)

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


def enrich_schema_with_dynamic_options(module, schema: dict) -> dict:
    """Enrich a valve JSON schema with dynamic enum options.

    If the module defines a ``get_valves_options`` callable, call it and merge
    the returned mapping ``{property_name: [option, ...]}`` into the schema's
    ``enum`` values.  This lets tool/function authors provide dynamic dropdown
    options (e.g. available models) at spec-request time without extra endpoints.
    """
    getter = getattr(module, "get_valves_options", None)
    if not callable(getter):
        return schema
    try:
        options = getter()
        if isinstance(options, dict):
            props = schema.get("properties", {})
            for prop, opts in options.items():
                if prop in props and isinstance(opts, list):
                    props[prop]["enum"] = opts
    except Exception as e:
        log.warning("Failed to get dynamic valve options: %s", e)
    return schema


router = APIRouter()
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

############################
# GetTools
############################


@router.get("/", response_model=list[ToolUserResponse])
async def get_tools(request: Request, user=Depends(get_verified_user)):
    # Workspace tools are already scoped via access_control and/or ownership.
    tools = Tools.get_tools_list_by_user_id(user.id, permission="read")
    direct_tool_servers_enabled = can_use_direct_tool_servers(request, user)

    tool_server_connections = []
    mcp_server_connections = []
    tool_servers_data = []
    mcp_servers_data = []
    shared_tool_servers = []

    if direct_tool_servers_enabled:
        tool_server_connections = get_user_tool_server_connections(request, user)
        mcp_server_connections = get_user_mcp_server_connections(request, user)

        # Resolve per-user server-side tools (OpenAPI / MCP). Do NOT store these on app.state,
        # otherwise configs leak across accounts.
        tool_servers_data = await get_tool_servers_data(
            tool_server_connections,
            session_token=request.state.token.credentials,
        )
        mcp_servers_data = [
            server
            for server in get_mcp_servers_cached_meta(mcp_server_connections)
            if (server.get("config") or {}).get("enable", True)
        ]
        shared_tool_servers = get_accessible_shared_tool_servers(request, user)

    for server in tool_servers_data:
        tools.append(
            ToolUserResponse(
                **{
                    "id": f"server:{server['idx']}",
                    "user_id": f"server:{server['idx']}",
                    "name": server["openapi"]
                    .get("info", {})
                    .get("title", "Tool Server"),
                    "meta": {
                        "description": server["openapi"]
                        .get("info", {})
                        .get("description", ""),
                    },
                    "access_control": None,
                    "updated_at": int(time.time()),
                    "created_at": int(time.time()),
                }
            )
        )

    for server in mcp_servers_data:
        transport_type = str(server.get("transport_type") or "http").lower()
        server_info = server.get("server_info", {}) or {}
        server_version = server_info.get("version")
        verified_at = server.get("verified_at")
        transport_label = "HTTP" if transport_type == "http" else "stdio"
        status_label = (
            f"已验证 {verified_at}" if verified_at else "未验证"
        )
        server_name, server_description = get_mcp_server_display_metadata(
            server,
            index=server["idx"],
            default_description=(
                f"MCP ({transport_label})"
                f"{' - v' + str(server_version) if server_version else ''}"
                f" - {status_label}"
            ),
        )

        tools.append(
            ToolUserResponse(
                **{
                    "id": f"mcp:{server['idx']}",
                    "user_id": f"mcp:{server['idx']}",
                    "name": server_name,
                    "meta": {
                        "description": server_description,
                    },
                    "access_control": None,
                    "updated_at": int(time.time()),
                    "created_at": int(time.time()),
                }
            )
        )

    if shared_tool_servers:
        owner_ids = list({shared_tool_server.owner_user_id for shared_tool_server in shared_tool_servers})
        owners_map = Users.get_users_map_by_ids(owner_ids) if owner_ids else {}
        now = int(time.time())

        for shared_tool_server in shared_tool_servers:
            owner = owners_map.get(shared_tool_server.owner_user_id)
            owner_name = getattr(owner, "name", "") if owner else ""

            if shared_tool_server.kind == "openapi":
                display_metadata = shared_tool_server.display_metadata or {}
                title = (
                    str(display_metadata.get("title") or "").strip()
                    or str(display_metadata.get("url") or "").strip()
                    or "OpenAPI Server"
                )
                description = str(display_metadata.get("description") or "").strip()

                tools.append(
                    ToolUserResponse(
                        id=f"{OPENAPI_SHARED_TOOL_PREFIX}{shared_tool_server.id}",
                        user_id=shared_tool_server.owner_user_id,
                        name=title,
                        meta=ToolMeta(
                            description=description,
                            source="shared",
                            owner_name=owner_name,
                            shared_kind="openapi",
                        ),
                        access_control=shared_tool_server.access_control,
                        updated_at=shared_tool_server.updated_at or now,
                        created_at=shared_tool_server.created_at or now,
                        user=UserResponse(**owner.model_dump()) if owner else None,
                    )
                )
                continue

            if shared_tool_server.kind == "mcp":
                connection_payload = build_runtime_shared_connection_payload(
                    shared_tool_server.connection_payload
                )
                cached_meta_list = get_mcp_servers_cached_meta([connection_payload])
                server = cached_meta_list[0] if cached_meta_list else {}
                display_metadata = shared_tool_server.display_metadata or {}
                transport_type = str(server.get("transport_type") or display_metadata.get("transport_type") or "http").lower()
                server_info = server.get("server_info", {}) or display_metadata.get("server_info") or {}
                server_version = server_info.get("version")
                verified_at = server.get("verified_at") or display_metadata.get("verified_at")
                transport_label = "HTTP" if transport_type == "http" else "stdio"
                status_label = f"已验证 {verified_at}" if verified_at else "未验证"
                server_name, server_description = get_mcp_server_display_metadata(
                    {
                        **server,
                        "name": display_metadata.get("title"),
                        "description": display_metadata.get("description"),
                    },
                    default_description=(
                        f"MCP ({transport_label})"
                        f"{' - v' + str(server_version) if server_version else ''}"
                        f" - {status_label}"
                    ),
                )

                tools.append(
                    ToolUserResponse(
                        id=f"{MCP_SHARED_TOOL_PREFIX}{shared_tool_server.id}",
                        user_id=shared_tool_server.owner_user_id,
                        name=server_name,
                        meta=ToolMeta(
                            description=server_description,
                            source="shared",
                            owner_name=owner_name,
                            shared_kind="mcp",
                        ),
                        access_control=shared_tool_server.access_control,
                        updated_at=shared_tool_server.updated_at or now,
                        created_at=shared_tool_server.created_at or now,
                        user=UserResponse(**owner.model_dump()) if owner else None,
                    )
                )

    return tools


############################
# GetToolList
############################


@router.get("/list", response_model=list[ToolUserResponse])
async def get_tool_list(user=Depends(get_verified_user)):
    # Return the tools the current account can write to. Keep admin scoped to their
    # account as well to avoid cross-account leakage in multi-user installs.
    return Tools.get_tools_list_by_user_id(user.id, "write")


############################
# ExportTools
############################


@router.get("/export", response_model=list[ToolModel])
async def export_tools(user=Depends(get_verified_user)):
    # Export tools owned by (or shared with write access to) the current account.
    return Tools.get_tools_by_user_id(user.id, "write")


############################
# CreateNewTools
############################


@router.post("/create", response_model=Optional[ToolResponse])
async def create_new_tools(
    request: Request,
    form_data: ToolForm,
    user=Depends(get_verified_user),
):
    if user.role != "admin" and not has_permission(
        user.id, "workspace.tools", request.app.state.config.USER_PERMISSIONS
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.UNAUTHORIZED,
        )

    ensure_requested_access_control_allowed(
        request,
        user,
        form_data.access_control,
        public_permission_key="sharing.public_tools",
    )

    if not IDENTIFIER_RE.fullmatch(form_data.id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The id must start with a letter or underscore, and may contain only letters, numbers, and underscores.",
        )

    form_data.id = form_data.id.lower()

    tools = Tools.get_tool_by_id(form_data.id)
    if tools is None:
        try:
            form_data.content = replace_imports(form_data.content)
            tool_module, frontmatter = load_tool_module_by_id(
                form_data.id, content=form_data.content
            )
            form_data.meta.manifest = frontmatter

            TOOLS = request.app.state.TOOLS
            TOOLS[form_data.id] = tool_module

            specs = get_tool_specs(TOOLS[form_data.id])
            tools = Tools.insert_new_tool(user.id, form_data, specs)

            tool_cache_dir = CACHE_DIR / "tools" / form_data.id
            tool_cache_dir.mkdir(parents=True, exist_ok=True)

            if tools:
                return tools
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT("Error creating tools"),
                )
        except Exception as e:
            log.exception(f"Failed to load the tool by id {form_data.id}: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.ID_TAKEN,
        )


############################
# GetToolsById
############################


@router.get("/id/{id}", response_model=Optional[ToolModel])
async def get_tools_by_id(id: str, user=Depends(get_verified_user)):
    tools = Tools.get_tool_by_id(id)

    if tools:
        if can_read_resource(user, tools):
            return tools
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateToolsById
############################


@router.post("/id/{id}/update", response_model=Optional[ToolModel])
async def update_tools_by_id(
    request: Request,
    id: str,
    form_data: ToolForm,
    user=Depends(get_verified_user),
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, tools):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if "access_control" not in getattr(form_data, "model_fields_set", set()):
        form_data.access_control = tools.access_control

    ensure_resource_acl_change_allowed(
        request,
        user,
        tools,
        form_data.access_control,
        public_permission_key="sharing.public_tools",
    )

    try:
        form_data.content = replace_imports(form_data.content)
        tool_module, frontmatter = load_tool_module_by_id(id, content=form_data.content)
        form_data.meta.manifest = frontmatter

        TOOLS = request.app.state.TOOLS
        TOOLS[id] = tool_module

        specs = get_tool_specs(TOOLS[id])

        updated = {
            **form_data.model_dump(exclude={"id"}),
            "specs": specs,
        }

        log.debug(updated)
        tools = Tools.update_tool_by_id(id, updated)

        if tools:
            return tools
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT("Error updating tools"),
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# DeleteToolsById
############################


@router.delete("/id/{id}/delete", response_model=bool)
async def delete_tools_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, tools):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    result = Tools.delete_tool_by_id(id)
    if result:
        TOOLS = request.app.state.TOOLS
        if id in TOOLS:
            del TOOLS[id]

    return result


############################
# GetToolValves
############################


@router.get("/id/{id}/valves", response_model=Optional[dict])
async def get_tools_valves_by_id(id: str, user=Depends(get_verified_user)):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if not can_write_resource(user, tools):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        try:
            valves = Tools.get_tool_valves_by_id(id)
            return valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# GetToolValvesSpec
############################


@router.get("/id/{id}/valves/spec", response_model=Optional[dict])
async def get_tools_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if not can_write_resource(user, tools):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if id in request.app.state.TOOLS:
            tools_module = request.app.state.TOOLS[id]
        else:
            tools_module, _ = load_tool_module_by_id(id)
            request.app.state.TOOLS[id] = tools_module

        if hasattr(tools_module, "Valves"):
            Valves = tools_module.Valves
            schema = Valves.schema()
            return enrich_schema_with_dynamic_options(tools_module, schema)
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


############################
# UpdateToolValves
############################


@router.post("/id/{id}/valves/update", response_model=Optional[dict])
async def update_tools_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if not tools:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    if not can_write_resource(user, tools):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if id in request.app.state.TOOLS:
        tools_module = request.app.state.TOOLS[id]
    else:
        tools_module, _ = load_tool_module_by_id(id)
        request.app.state.TOOLS[id] = tools_module

    if not hasattr(tools_module, "Valves"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
    Valves = tools_module.Valves

    try:
        form_data = {k: v for k, v in form_data.items() if v is not None}
        valves = Valves(**form_data)
        Tools.update_tool_valves_by_id(id, valves.model_dump())
        return valves.model_dump()
    except Exception as e:
        log.exception(f"Failed to update tool valves by id {id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ERROR_MESSAGES.DEFAULT(str(e)),
        )


############################
# ToolUserValves
############################


@router.get("/id/{id}/valves/user", response_model=Optional[dict])
async def get_tools_user_valves_by_id(id: str, user=Depends(get_verified_user)):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if not can_read_resource(user, tools):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        try:
            user_valves = Tools.get_user_valves_by_id_and_user_id(id, user.id)
            return user_valves
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ERROR_MESSAGES.DEFAULT(str(e)),
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.get("/id/{id}/valves/user/spec", response_model=Optional[dict])
async def get_tools_user_valves_spec_by_id(
    request: Request, id: str, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)
    if tools:
        if not can_read_resource(user, tools):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if id in request.app.state.TOOLS:
            tools_module = request.app.state.TOOLS[id]
        else:
            tools_module, _ = load_tool_module_by_id(id)
            request.app.state.TOOLS[id] = tools_module

        if hasattr(tools_module, "UserValves"):
            UserValves = tools_module.UserValves
            schema = UserValves.schema()
            return enrich_schema_with_dynamic_options(tools_module, schema)
        return None
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


@router.post("/id/{id}/valves/user/update", response_model=Optional[dict])
async def update_tools_user_valves_by_id(
    request: Request, id: str, form_data: dict, user=Depends(get_verified_user)
):
    tools = Tools.get_tool_by_id(id)

    if tools:
        if not can_read_resource(user, tools):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )
        if id in request.app.state.TOOLS:
            tools_module = request.app.state.TOOLS[id]
        else:
            tools_module, _ = load_tool_module_by_id(id)
            request.app.state.TOOLS[id] = tools_module

        if hasattr(tools_module, "UserValves"):
            UserValves = tools_module.UserValves

            try:
                form_data = {k: v for k, v in form_data.items() if v is not None}
                user_valves = UserValves(**form_data)
                Tools.update_user_valves_by_id_and_user_id(
                    id, user.id, user_valves.model_dump()
                )
                return user_valves.model_dump()
            except Exception as e:
                log.exception(f"Failed to update user valves by id {id}: {e}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ERROR_MESSAGES.DEFAULT(str(e)),
                )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ERROR_MESSAGES.NOT_FOUND,
            )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )
