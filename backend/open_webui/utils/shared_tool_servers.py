from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.shared_tool_servers import (
    SharedToolServerModel,
    SharedToolServers,
)
from open_webui.models.users import UserModel
from open_webui.utils.access_control import has_access, has_permission
from open_webui.utils.mcp import get_mcp_server_display_metadata


MCP_SHARED_TOOL_PREFIX = "mcp_shared:"
OPENAPI_SHARED_TOOL_PREFIX = "server_shared:"
SHARED_TOOL_KINDS = {"mcp", "openapi"}


def can_use_direct_tool_servers(request: Request, user: Optional[UserModel]) -> bool:
    if getattr(user, "role", None) == "admin":
        return True
    if not user:
        return False
    return has_permission(
        user.id,
        "features.direct_tool_servers",
        request.app.state.config.USER_PERMISSIONS,
    )


def ensure_direct_tool_servers_access(
    request: Request, user: Optional[UserModel]
) -> None:
    if can_use_direct_tool_servers(request, user):
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
    )


def get_shared_tool_kind_from_tool_id(tool_id: Any) -> Optional[str]:
    value = str(tool_id or "").strip()
    if value.startswith(MCP_SHARED_TOOL_PREFIX):
        return "mcp"
    if value.startswith(OPENAPI_SHARED_TOOL_PREFIX):
        return "openapi"
    return None


def get_shared_tool_id_value(tool_id: Any) -> Optional[str]:
    value = str(tool_id or "").strip()
    if value.startswith(MCP_SHARED_TOOL_PREFIX):
        return value[len(MCP_SHARED_TOOL_PREFIX) :].strip() or None
    if value.startswith(OPENAPI_SHARED_TOOL_PREFIX):
        return value[len(OPENAPI_SHARED_TOOL_PREFIX) :].strip() or None
    return None


def build_shared_tool_id(kind: str, shared_id: str) -> str:
    if kind == "mcp":
        return f"{MCP_SHARED_TOOL_PREFIX}{shared_id}"
    if kind == "openapi":
        return f"{OPENAPI_SHARED_TOOL_PREFIX}{shared_id}"
    raise ValueError(f"Unsupported shared tool kind: {kind}")


def extract_selected_shared_tool_ids(
    tool_ids: list[Any] | None, *, kind: Optional[str] = None
) -> set[str]:
    selected: set[str] = set()
    for tool_id in tool_ids or []:
        resolved_kind = get_shared_tool_kind_from_tool_id(tool_id)
        if not resolved_kind:
            continue
        if kind and resolved_kind != kind:
            continue
        shared_id = get_shared_tool_id_value(tool_id)
        if shared_id:
            selected.add(shared_id)
    return selected


def get_shared_tool_servers_by_ids(ids: list[str]) -> dict[str, SharedToolServerModel]:
    return {
        item.id: item for item in SharedToolServers.get_shared_tool_servers_by_ids(ids)
    }


def can_read_shared_tool_server(
    request: Request, user: Optional[UserModel], shared_tool_server: SharedToolServerModel
) -> bool:
    if not user or not shared_tool_server.enabled:
        return False

    if getattr(user, "role", None) == "admin" and user.id == shared_tool_server.owner_user_id:
        return True

    if not can_use_direct_tool_servers(request, user):
        return False

    if user.id == shared_tool_server.owner_user_id:
        return True

    return has_access(user.id, "read", shared_tool_server.access_control)


def get_accessible_shared_tool_servers(
    request: Request,
    user: Optional[UserModel],
    *,
    kind: Optional[str] = None,
    ids: Optional[set[str]] = None,
) -> list[SharedToolServerModel]:
    shared_tool_servers = SharedToolServers.get_shared_tool_servers()
    accessible: list[SharedToolServerModel] = []

    for shared_tool_server in shared_tool_servers:
        if kind and shared_tool_server.kind != kind:
            continue
        if ids is not None and shared_tool_server.id not in ids:
            continue
        if can_read_shared_tool_server(request, user, shared_tool_server):
            accessible.append(SharedToolServerModel.model_validate(shared_tool_server))

    return accessible


def validate_requested_shared_tool_ids_access(
    request: Request,
    tool_ids: list[Any] | None,
    user: Optional[UserModel],
) -> None:
    shared_ids = extract_selected_shared_tool_ids(tool_ids)
    if not shared_ids:
        return

    if not can_use_direct_tool_servers(request, user) and getattr(user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    shared_tool_servers_map = get_shared_tool_servers_by_ids(list(shared_ids))
    missing_ids = sorted(shared_ids - set(shared_tool_servers_map.keys()))
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )

    denied_ids = [
        shared_id
        for shared_id in shared_ids
        if not can_read_shared_tool_server(request, user, shared_tool_servers_map[shared_id])
    ]
    if denied_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )


def ensure_shareable_openapi_connection(connection: dict) -> None:
    if str(connection.get("auth_type") or "bearer").lower() == "session":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="使用 Session 认证的 OpenAPI 工具暂不支持共享，请改用 Bearer 密钥。",
        )


def ensure_shareable_mcp_connection(connection: dict) -> None:
    transport_type = str(connection.get("transport_type") or "http").lower()
    auth_type = str(connection.get("auth_type") or "none").lower()
    if transport_type == "http" and auth_type == "session":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="使用 Session 认证的 HTTP MCP 暂不支持共享，请改用 Bearer 或其他固定认证方式。",
        )


def build_runtime_shared_connection_payload(connection: dict) -> dict:
    payload = deepcopy(connection or {})
    config = payload.get("config") or {}
    payload["config"] = {**config, "enable": True}
    return payload


def get_connection_shared_id(connection: dict) -> Optional[str]:
    value = str((connection or {}).get("shared_id") or "").strip()
    return value or None


def strip_connection_share_runtime_fields(connection: dict) -> dict:
    payload = deepcopy(connection or {})
    payload.pop("shared_access_control", None)
    payload.pop("shared_enabled", None)
    return payload


def build_shared_openapi_display_metadata(
    connection: dict, current_metadata: Optional[dict] = None
) -> dict:
    metadata = deepcopy(current_metadata or {})
    url = str(connection.get("url") or "").rstrip("/")
    path = str(connection.get("path") or "openapi.json").strip() or "openapi.json"

    hostname = ""
    if url:
        try:
            hostname = urlparse(url).netloc or url
        except Exception:
            hostname = url

    title = str(metadata.get("title") or "").strip() or hostname or url or "OpenAPI Server"
    description = str(metadata.get("description") or "").strip()

    return {
        **metadata,
        "title": title,
        "description": description,
        "url": url,
        "path": path,
    }


def build_shared_mcp_display_metadata(
    connection: dict, current_metadata: Optional[dict] = None
) -> dict:
    metadata = deepcopy(current_metadata or {})
    transport_type = str(connection.get("transport_type") or "http").lower()
    tool_count = connection.get("tool_count")
    verified_at = connection.get("verified_at")
    server_info = deepcopy(connection.get("server_info") or {})

    default_description = (
        f"MCP ({'HTTP' if transport_type == 'http' else 'stdio'})"
        f"{f' - 已验证 {verified_at}' if verified_at else ' - 未验证'}"
    )
    title, description = get_mcp_server_display_metadata(
        connection,
        default_description=default_description,
    )

    return {
        **metadata,
        "title": str(metadata.get("title") or "").strip() or title,
        "description": str(metadata.get("description") or "").strip() or description,
        "transport_type": transport_type,
        "tool_count": tool_count,
        "verified_at": verified_at,
        "server_info": server_info,
    }
