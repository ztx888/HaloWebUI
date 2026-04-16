from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import Request

from open_webui.models.users import UserModel
from open_webui.utils.mcp import get_mcp_server_data
from open_webui.utils.shared_tool_servers import (
    build_runtime_shared_connection_payload,
    extract_selected_shared_tool_ids,
    get_accessible_shared_tool_servers,
)
from open_webui.utils.tools import get_tool_server_data


async def ensure_selected_shared_tool_runtime_loaded(
    request: Request,
    user: UserModel,
    tool_ids: list[Any] | None,
) -> None:
    selected_openapi_ids = extract_selected_shared_tool_ids(tool_ids, kind="openapi")
    if selected_openapi_ids and not getattr(
        getattr(request, "state", None), "SHARED_TOOL_SERVERS", None
    ):
        shared_tool_servers = get_accessible_shared_tool_servers(
            request, user, kind="openapi", ids=selected_openapi_ids
        )
        request.state.SHARED_TOOL_SERVER_CONNECTIONS = {}
        request.state.SHARED_TOOL_SERVERS = {}

        for shared_tool_server in shared_tool_servers:
            connection_payload = build_runtime_shared_connection_payload(
                shared_tool_server.connection_payload
            )
            request.state.SHARED_TOOL_SERVER_CONNECTIONS[shared_tool_server.id] = (
                deepcopy(connection_payload)
            )

            try:
                auth_type = str(connection_payload.get("auth_type") or "bearer").lower()
                token = connection_payload.get("key", "") if auth_type == "bearer" else None
                url = (
                    f"{str(connection_payload.get('url') or '').rstrip('/')}/"
                    f"{str(connection_payload.get('path') or 'openapi.json').strip() or 'openapi.json'}"
                )
                data = await get_tool_server_data(token, url)
                request.state.SHARED_TOOL_SERVERS[shared_tool_server.id] = {
                    "shared_id": shared_tool_server.id,
                    "url": connection_payload.get("url"),
                    "openapi": data.get("openapi"),
                    "info": data.get("info"),
                    "specs": data.get("specs"),
                }
            except Exception:
                continue

    selected_mcp_ids = extract_selected_shared_tool_ids(tool_ids, kind="mcp")
    if selected_mcp_ids and not getattr(
        getattr(request, "state", None), "SHARED_MCP_SERVERS", None
    ):
        shared_mcp_servers = get_accessible_shared_tool_servers(
            request, user, kind="mcp", ids=selected_mcp_ids
        )
        request.state.SHARED_MCP_SERVER_CONNECTIONS = {}
        request.state.SHARED_MCP_SERVERS = {}

        for shared_tool_server in shared_mcp_servers:
            connection_payload = build_runtime_shared_connection_payload(
                shared_tool_server.connection_payload
            )
            request.state.SHARED_MCP_SERVER_CONNECTIONS[shared_tool_server.id] = (
                deepcopy(connection_payload)
            )
            try:
                data = await get_mcp_server_data(
                    connection_payload,
                    session_token=None,
                    user_id=user.id,
                )
                request.state.SHARED_MCP_SERVERS[shared_tool_server.id] = {
                    "shared_id": shared_tool_server.id,
                    "transport_type": connection_payload.get("transport_type", "http"),
                    "url": connection_payload.get("url", ""),
                    "command": connection_payload.get("command", ""),
                    "server_info": data.get("server_info", {}) or {},
                    "capabilities": data.get("capabilities", {}) or {},
                    "tools": data.get("tools", []) or [],
                }
            except Exception:
                cached_tools = deepcopy(connection_payload.get("tools") or [])
                if not cached_tools:
                    continue
                request.state.SHARED_MCP_SERVERS[shared_tool_server.id] = {
                    "shared_id": shared_tool_server.id,
                    "transport_type": connection_payload.get("transport_type", "http"),
                    "url": connection_payload.get("url", ""),
                    "command": connection_payload.get("command", ""),
                    "server_info": deepcopy(connection_payload.get("server_info") or {}),
                    "capabilities": {},
                    "tools": cached_tools,
                }
