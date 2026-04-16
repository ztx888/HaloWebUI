import inspect
import logging
import re
import aiohttp
import asyncio
import yaml

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from typing import (
    Any,
    Awaitable,
    Callable,
    get_type_hints,
    get_args,
    get_origin,
    Dict,
    List,
    Tuple,
    Union,
    Optional,
    Type,
)
from functools import update_wrapper, partial


from fastapi import HTTPException, Request, status
from pydantic import BaseModel, Field, create_model

from langchain_core.utils.function_calling import (
    convert_to_openai_function as convert_pydantic_model_to_openai_function_spec,
)


from open_webui.models.tools import Tools
from open_webui.models.users import UserModel
from open_webui.constants import ERROR_MESSAGES
from open_webui.utils.access_control import can_read_resource
from open_webui.utils.plugin import load_tool_module_by_id
from open_webui.env import AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA
from open_webui.utils.mcp import execute_mcp_tool
from open_webui.utils.shared_tool_servers import (
    MCP_SHARED_TOOL_PREFIX,
    OPENAPI_SHARED_TOOL_PREFIX,
    can_use_direct_tool_servers,
    validate_requested_shared_tool_ids_access,
)

import copy

log = logging.getLogger(__name__)


def validate_tool_ids_access(
    tool_ids: list[str] | None,
    user: UserModel,
    request: Optional[Request] = None,
) -> None:
    if not tool_ids:
        return

    missing_tool_ids: list[str] = []
    denied_tool_ids: list[str] = []

    if request is not None:
        validate_requested_shared_tool_ids_access(request, tool_ids, user)
        if any(
            str(tool_id or "").strip().startswith(("server:", "mcp:"))
            for tool_id in tool_ids
        ) and not can_use_direct_tool_servers(request, user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
            )

    for tool_id in tool_ids:
        tool_id = str(tool_id or "").strip()
        if not tool_id or tool_id.startswith(
            ("server:", "mcp:", OPENAPI_SHARED_TOOL_PREFIX, MCP_SHARED_TOOL_PREFIX)
        ):
            continue

        tool = Tools.get_tool_by_id(tool_id)
        if tool is None:
            missing_tool_ids.append(tool_id)
            continue

        if not can_read_resource(user, tool):
            denied_tool_ids.append(tool_id)

    if denied_tool_ids:
        log.warning(
            "[TOOLS] Access denied for user %s on workspace tools: %s",
            getattr(user, "id", None),
            denied_tool_ids,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ERROR_MESSAGES.ACCESS_PROHIBITED,
        )

    if missing_tool_ids:
        log.warning(
            "[TOOLS] Missing workspace tools requested by user %s: %s",
            getattr(user, "id", None),
            missing_tool_ids,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ERROR_MESSAGES.NOT_FOUND,
        )


def _get_shared_tool_server_runtime_entry(
    request: Request, shared_id: str
) -> tuple[Optional[dict], Optional[dict]]:
    shared_connections = (
        getattr(getattr(request, "state", None), "SHARED_TOOL_SERVER_CONNECTIONS", None)
        or {}
    )
    shared_servers = (
        getattr(getattr(request, "state", None), "SHARED_TOOL_SERVERS", None) or {}
    )
    return shared_connections.get(shared_id), shared_servers.get(shared_id)


def _get_shared_mcp_runtime_entry(
    request: Request, shared_id: str
) -> tuple[Optional[dict], Optional[dict]]:
    shared_connections = (
        getattr(getattr(request, "state", None), "SHARED_MCP_SERVER_CONNECTIONS", None)
        or {}
    )
    shared_servers = (
        getattr(getattr(request, "state", None), "SHARED_MCP_SERVERS", None) or {}
    )
    return shared_connections.get(shared_id), shared_servers.get(shared_id)


def _make_openapi_tool_runtime(
    request: Request,
    tool_id: str,
    tool_server_connection: dict,
    tool_server_data: dict,
) -> dict[str, dict]:
    tools_dict: dict[str, dict] = {}
    specs = tool_server_data.get("specs", [])

    for spec in specs:
        function_name = spec["name"]

        auth_type = tool_server_connection.get("auth_type", "bearer")
        token = None

        if auth_type == "bearer":
            token = tool_server_connection.get("key", "")
        elif auth_type == "session":
            token = request.state.token.credentials

        def make_tool_function(function_name, token, tool_server_data):
            async def tool_function(**kwargs):
                return await execute_tool_server(
                    token=token,
                    url=tool_server_data["url"],
                    name=function_name,
                    params=kwargs,
                    server_data=tool_server_data,
                )

            return tool_function

        tool_function = make_tool_function(function_name, token, tool_server_data)
        callable = get_async_tool_function_and_apply_extra_params(tool_function, {})

        tool_dict = {
            "tool_id": tool_id,
            "callable": callable,
            "spec": spec,
        }

        if function_name in tools_dict:
            log.warning(f"Tool {function_name} already exists in another tools!")
            log.warning(f"Discarding {tool_id}.{function_name}")
        else:
            tools_dict[function_name] = tool_dict

    return tools_dict


def _merge_tool_runtime_entries(
    tools_dict: dict[str, dict], next_entries: dict[str, dict], tool_id: str
) -> None:
    for function_name, tool_dict in next_entries.items():
        if function_name in tools_dict:
            log.warning(f"Tool {function_name} already exists in another tools!")
            log.warning(f"Discarding {tool_id}.{function_name}")
            continue
        tools_dict[function_name] = tool_dict


def _make_mcp_tool_runtime(
    request: Request,
    user: UserModel,
    extra_params: dict,
    tool_id: str,
    mcp_server_connection: dict,
    mcp_server_data: dict,
    *,
    metadata_server_id: Any,
) -> dict[str, dict]:
    tools_dict: dict[str, dict] = {}

    def sanitize_tool_name(name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", name or "").strip("_")
        if not sanitized:
            sanitized = "tool"
        if not re.match(r"^[a-zA-Z_]", sanitized):
            sanitized = f"tool_{sanitized}"
        return sanitized

    def make_mcp_function_name(server_identifier: Any, tool_name: str) -> str:
        identifier = sanitize_tool_name(str(server_identifier))
        base = f"mcp_{identifier}__{sanitize_tool_name(tool_name)}"
        if len(base) > 64:
            base = base[:64]
        return base

    for mcp_tool in mcp_server_data.get("tools", []) or []:
        original_tool_name = mcp_tool.get("name") or ""
        function_name = make_mcp_function_name(metadata_server_id, original_tool_name)

        input_schema = mcp_tool.get("inputSchema") or {}
        if not isinstance(input_schema, dict):
            input_schema = {}
        if input_schema.get("type") != "object":
            input_schema = {"type": "object", "properties": {}}

        spec = {
            "name": function_name,
            "description": mcp_tool.get("description")
            or original_tool_name
            or function_name,
            "parameters": input_schema,
        }

        def make_tool_function(original_tool_name, mcp_server_connection):
            async def tool_function(__event_emitter__=None, **kwargs):
                notif_cb = None
                if __event_emitter__ is not None:

                    async def notif_cb(notification):
                        method = notification.get("method", "")
                        params = notification.get("params") or {}
                        if method == "notifications/progress":
                            progress = params.get("progress")
                            total = params.get("total")
                            desc = f"MCP: {original_tool_name}"
                            if total:
                                desc = f"MCP: {original_tool_name} ({progress}/{total})"
                            await __event_emitter__(
                                {
                                    "type": "status",
                                    "data": {
                                        "action": "mcp_progress",
                                        "description": desc,
                                        "done": False,
                                    },
                                }
                            )
                        elif method == "notifications/message":
                            level = params.get("level", "info")
                            data_val = params.get("data", "")
                            await __event_emitter__(
                                {
                                    "type": "status",
                                    "data": {
                                        "action": "mcp_message",
                                        "description": str(data_val),
                                        "level": level,
                                        "done": False,
                                    },
                                }
                            )

                return await execute_mcp_tool(
                    mcp_server_connection,
                    name=original_tool_name,
                    arguments=kwargs,
                    session_token=getattr(
                        getattr(request, "state", None),
                        "token",
                        None,
                    ).credentials
                    if getattr(getattr(request, "state", None), "token", None)
                    else None,
                    user_id=getattr(user, "id", None),
                    on_notification=notif_cb,
                )

            return tool_function

        tool_function = make_tool_function(original_tool_name, mcp_server_connection)
        callable = get_async_tool_function_and_apply_extra_params(
            tool_function,
            extra_params,
        )

        tool_dict = {
            "tool_id": tool_id,
            "callable": callable,
            "spec": spec,
            "metadata": {
                "mcp": {
                    "server_idx": metadata_server_id,
                    "tool_name": original_tool_name,
                }
            },
        }

        if function_name in tools_dict:
            log.warning(f"Tool {function_name} already exists in another tools!")
            log.warning(f"Discarding {tool_id}.{function_name}")
        else:
            tools_dict[function_name] = tool_dict

    return tools_dict


def get_async_tool_function_and_apply_extra_params(
    function: Callable, extra_params: dict
) -> Callable[..., Awaitable]:
    sig = inspect.signature(function)
    extra_params = {k: v for k, v in extra_params.items() if k in sig.parameters}
    partial_func = partial(function, **extra_params)

    if inspect.iscoroutinefunction(function):
        update_wrapper(partial_func, function)
        return partial_func
    else:
        # Make it a coroutine function
        async def new_function(*args, **kwargs):
            return partial_func(*args, **kwargs)

        update_wrapper(new_function, function)
        return new_function


def get_tools(
    request: Request, tool_ids: list[str], user: UserModel, extra_params: dict
) -> dict[str, dict]:
    tools_dict = {}

    for tool_id in tool_ids:
        tool = Tools.get_tool_by_id(tool_id)
        if tool is None:
            if tool_id.startswith("server:"):
                try:
                    server_idx = int(tool_id.split(":")[1])
                except Exception:
                    log.warning(f"Invalid tool server tool_id: {tool_id}")
                    continue

                tool_server_connections = (
                    getattr(getattr(request, "state", None), "TOOL_SERVER_CONNECTIONS", None)
                    or getattr(getattr(request.app.state, "config", None), "TOOL_SERVER_CONNECTIONS", None)
                    or []
                )
                if server_idx < 0 or server_idx >= len(tool_server_connections):
                    log.warning(f"Tool server idx out of range for tool_id: {tool_id}")
                    continue

                tool_server_connection = tool_server_connections[server_idx]

                tool_servers = (
                    getattr(getattr(request, "state", None), "TOOL_SERVERS", None)
                    or getattr(request.app.state, "TOOL_SERVERS", None)
                    or []
                )

                tool_server_data = None
                for server in tool_servers:
                    if server.get("idx") == server_idx:
                        tool_server_data = server
                        break

                if tool_server_data is None:
                    log.warning(
                        f"Tool server data not loaded for idx={server_idx}; skipping {tool_id}"
                    )
                    continue
                _merge_tool_runtime_entries(
                    tools_dict,
                    _make_openapi_tool_runtime(
                        request,
                        tool_id,
                        tool_server_connection,
                        tool_server_data,
                    ),
                    tool_id,
                )
            elif tool_id.startswith(OPENAPI_SHARED_TOOL_PREFIX):
                shared_id = tool_id[len(OPENAPI_SHARED_TOOL_PREFIX) :].strip()
                tool_server_connection, tool_server_data = _get_shared_tool_server_runtime_entry(
                    request, shared_id
                )
                if tool_server_connection is None or tool_server_data is None:
                    log.warning(
                        f"Shared OpenAPI tool server data not loaded for id={shared_id}; skipping {tool_id}"
                    )
                    continue

                _merge_tool_runtime_entries(
                    tools_dict,
                    _make_openapi_tool_runtime(
                        request,
                        tool_id,
                        tool_server_connection,
                        tool_server_data,
                    ),
                    tool_id,
                )
            elif tool_id.startswith("mcp:"):
                try:
                    server_idx = int(tool_id.split(":")[1])
                except Exception:
                    log.warning(f"Invalid MCP tool_id: {tool_id}")
                    continue

                mcp_connections = (
                    getattr(getattr(request, "state", None), "MCP_SERVER_CONNECTIONS", None)
                    or getattr(getattr(request.app.state, "config", None), "MCP_SERVER_CONNECTIONS", None)
                    or []
                )
                if server_idx < 0 or server_idx >= len(mcp_connections):
                    log.warning(f"MCP server idx out of range for tool_id: {tool_id}")
                    continue

                mcp_server_connection = mcp_connections[server_idx]

                mcp_server_data = None
                mcp_servers = (
                    getattr(getattr(request, "state", None), "MCP_SERVERS", None)
                    or getattr(request.app.state, "MCP_SERVERS", None)
                    or []
                )
                for server in mcp_servers:
                    if server.get("idx") == server_idx:
                        mcp_server_data = server
                        break

                if mcp_server_data is None:
                    log.warning(
                        f"MCP server data not loaded for idx={server_idx}; skipping {tool_id}"
                    )
                    continue
                _merge_tool_runtime_entries(
                    tools_dict,
                    _make_mcp_tool_runtime(
                        request,
                        user,
                        extra_params,
                        tool_id,
                        mcp_server_connection,
                        mcp_server_data,
                        metadata_server_id=server_idx,
                    ),
                    tool_id,
                )
            elif tool_id.startswith(MCP_SHARED_TOOL_PREFIX):
                shared_id = tool_id[len(MCP_SHARED_TOOL_PREFIX) :].strip()
                mcp_server_connection, mcp_server_data = _get_shared_mcp_runtime_entry(
                    request, shared_id
                )
                if mcp_server_connection is None or mcp_server_data is None:
                    log.warning(
                        f"Shared MCP server data not loaded for id={shared_id}; skipping {tool_id}"
                    )
                    continue

                _merge_tool_runtime_entries(
                    tools_dict,
                    _make_mcp_tool_runtime(
                        request,
                        user,
                        extra_params,
                        tool_id,
                        mcp_server_connection,
                        mcp_server_data,
                        metadata_server_id=f"shared_{shared_id}",
                    ),
                    tool_id,
                )
            else:
                continue
        else:
            module = request.app.state.TOOLS.get(tool_id, None)
            if module is None:
                module, _ = load_tool_module_by_id(tool_id)
                request.app.state.TOOLS[tool_id] = module

            extra_params["__id__"] = tool_id

            # Set valves for the tool
            if hasattr(module, "valves") and hasattr(module, "Valves"):
                valves = Tools.get_tool_valves_by_id(tool_id) or {}
                module.valves = module.Valves(**valves)
            if hasattr(module, "UserValves"):
                extra_params["__user__"]["valves"] = module.UserValves(  # type: ignore
                    **Tools.get_user_valves_by_id_and_user_id(tool_id, user.id)
                )

            for spec in tool.specs:
                # TODO: Fix hack for OpenAI API
                # Some times breaks OpenAI but others don't. Leaving the comment
                for val in spec.get("parameters", {}).get("properties", {}).values():
                    if val["type"] == "str":
                        val["type"] = "string"

                # Remove internal reserved parameters (e.g. __id__, __user__)
                spec["parameters"]["properties"] = {
                    key: val
                    for key, val in spec["parameters"]["properties"].items()
                    if not key.startswith("__")
                }

                # convert to function that takes only model params and inserts custom params
                function_name = spec["name"]
                tool_function = getattr(module, function_name)
                callable = get_async_tool_function_and_apply_extra_params(
                    tool_function, extra_params
                )

                # TODO: Support Pydantic models as parameters
                if callable.__doc__ and callable.__doc__.strip() != "":
                    s = re.split(":(param|return)", callable.__doc__, 1)
                    spec["description"] = s[0]
                else:
                    spec["description"] = function_name

                tool_dict = {
                    "tool_id": tool_id,
                    "callable": callable,
                    "spec": spec,
                    # Misc info
                    "metadata": {
                        "file_handler": hasattr(module, "file_handler")
                        and module.file_handler,
                        "citation": hasattr(module, "citation") and module.citation,
                    },
                }

                # TODO: if collision, prepend toolkit name
                if function_name in tools_dict:
                    log.warning(
                        f"Tool {function_name} already exists in another tools!"
                    )
                    log.warning(f"Discarding {tool_id}.{function_name}")
                else:
                    tools_dict[function_name] = tool_dict

    return tools_dict


def parse_description(docstring: str | None) -> str:
    """
    Parse a function's docstring to extract the description.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        str: The description.
    """

    if not docstring:
        return ""

    lines = [line.strip() for line in docstring.strip().split("\n")]
    description_lines: list[str] = []

    for line in lines:
        if re.match(r":param", line) or re.match(r":return", line):
            break

        description_lines.append(line)

    return "\n".join(description_lines)


def parse_docstring(docstring):
    """
    Parse a function's docstring to extract parameter descriptions in reST format.

    Args:
        docstring (str): The docstring to parse.

    Returns:
        dict: A dictionary where keys are parameter names and values are descriptions.
    """
    if not docstring:
        return {}

    # Regex to match `:param name: description` format
    param_pattern = re.compile(r":param (\w+):\s*(.+)")
    param_descriptions = {}

    for line in docstring.splitlines():
        match = param_pattern.match(line.strip())
        if not match:
            continue
        param_name, param_description = match.groups()
        if param_name.startswith("__"):
            continue
        param_descriptions[param_name] = param_description

    return param_descriptions


def convert_function_to_pydantic_model(func: Callable) -> type[BaseModel]:
    """
    Converts a Python function's type hints and docstring to a Pydantic model,
    including support for nested types, default values, and descriptions.

    Args:
        func: The function whose type hints and docstring should be converted.
        model_name: The name of the generated Pydantic model.

    Returns:
        A Pydantic model class.
    """
    type_hints = get_type_hints(func)
    signature = inspect.signature(func)
    parameters = signature.parameters

    docstring = func.__doc__

    description = parse_description(docstring)
    function_descriptions = parse_docstring(docstring)

    field_defs = {}
    for name, param in parameters.items():

        type_hint = type_hints.get(name, Any)
        default_value = param.default if param.default is not param.empty else ...

        description = function_descriptions.get(name, None)

        if description:
            field_defs[name] = type_hint, Field(default_value, description=description)
        else:
            field_defs[name] = type_hint, default_value

    model = create_model(func.__name__, **field_defs)
    model.__doc__ = description

    return model


def get_functions_from_tool(tool: object) -> list[Callable]:
    return [
        getattr(tool, func)
        for func in dir(tool)
        if callable(
            getattr(tool, func)
        )  # checks if the attribute is callable (a method or function).
        and not func.startswith(
            "__"
        )  # filters out special (dunder) methods like init, str, etc. — these are usually built-in functions of an object that you might not need to use directly.
        and not inspect.isclass(
            getattr(tool, func)
        )  # ensures that the callable is not a class itself, just a method or function.
    ]


def get_tool_specs(tool_module: object) -> list[dict]:
    function_models = map(
        convert_function_to_pydantic_model, get_functions_from_tool(tool_module)
    )

    specs = [
        convert_pydantic_model_to_openai_function_spec(function_model)
        for function_model in function_models
    ]

    return specs


def resolve_schema(schema, components):
    """
    Recursively resolves a JSON schema using OpenAPI components.
    """
    if not schema:
        return {}

    if "$ref" in schema:
        ref_path = schema["$ref"]
        ref_parts = ref_path.strip("#/").split("/")
        resolved = components
        for part in ref_parts[1:]:  # Skip the initial 'components'
            resolved = resolved.get(part, {})
        return resolve_schema(resolved, components)

    resolved_schema = copy.deepcopy(schema)

    # Recursively resolve inner schemas
    if "properties" in resolved_schema:
        for prop, prop_schema in resolved_schema["properties"].items():
            resolved_schema["properties"][prop] = resolve_schema(
                prop_schema, components
            )

    if "items" in resolved_schema:
        resolved_schema["items"] = resolve_schema(resolved_schema["items"], components)

    return resolved_schema


def convert_openapi_to_tool_payload(openapi_spec):
    """
    Converts an OpenAPI specification into a custom tool payload structure.

    Args:
        openapi_spec (dict): The OpenAPI specification as a Python dict.

    Returns:
        list: A list of tool payloads.
    """
    tool_payload = []

    for path, methods in openapi_spec.get("paths", {}).items():
        for method, operation in methods.items():
            tool = {
                "type": "function",
                "name": operation.get("operationId"),
                "description": operation.get(
                    "description", operation.get("summary", "No description available.")
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            }

            # Extract path and query parameters
            for param in operation.get("parameters", []):
                param_name = param["name"]
                param_schema = param.get("schema", {})
                tool["parameters"]["properties"][param_name] = {
                    "type": param_schema.get("type"),
                    "description": param_schema.get("description", ""),
                }
                if param.get("required"):
                    tool["parameters"]["required"].append(param_name)

            # Extract and resolve requestBody if available
            request_body = operation.get("requestBody")
            if request_body:
                content = request_body.get("content", {})
                json_schema = content.get("application/json", {}).get("schema")
                if json_schema:
                    resolved_schema = resolve_schema(
                        json_schema, openapi_spec.get("components", {})
                    )

                    if resolved_schema.get("properties"):
                        tool["parameters"]["properties"].update(
                            resolved_schema["properties"]
                        )
                        if "required" in resolved_schema:
                            tool["parameters"]["required"] = list(
                                set(
                                    tool["parameters"]["required"]
                                    + resolved_schema["required"]
                                )
                            )
                    elif resolved_schema.get("type") == "array":
                        tool["parameters"] = resolved_schema  # special case for array

            tool_payload.append(tool)

    return tool_payload


async def get_tool_server_data(token: str, url: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    error = None
    try:
        timeout = aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as response:
                if response.status != 200:
                    error_body = await response.json()
                    raise Exception(error_body)

                # Check if URL ends with .yaml or .yml to determine format
                if url.lower().endswith((".yaml", ".yml")):
                    text_content = await response.text()
                    res = yaml.safe_load(text_content)
                else:
                    res = await response.json()
    except Exception as err:
        log.exception(f"Could not fetch tool server spec from {url}")
        if isinstance(err, dict) and "detail" in err:
            error = err["detail"]
        else:
            error = str(err)
        raise Exception(error)

    data = {
        "openapi": res,
        "info": res.get("info", {}),
        "specs": convert_openapi_to_tool_payload(res),
    }

    print("Fetched data:", data)
    return data


async def get_tool_servers_data(
    servers: List[Dict[str, Any]], session_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    # Prepare list of enabled servers along with their original index
    server_entries = []
    for idx, server in enumerate(servers):
        if server.get("config", {}).get("enable"):
            url_path = server.get("path", "openapi.json")
            full_url = f"{server.get('url', '').rstrip('/')}/{url_path}"

            auth_type = server.get("auth_type", "bearer")
            token = None

            if auth_type == "bearer":
                token = server.get("key", "")
            elif auth_type == "session":
                token = session_token
            server_entries.append((idx, server, full_url, token))

    # Create async tasks to fetch data
    tasks = [get_tool_server_data(token, url) for (_, _, url, token) in server_entries]

    # Execute tasks concurrently
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Build final results with index and server metadata
    results = []
    for (idx, server, url, _), response in zip(server_entries, responses):
        if isinstance(response, Exception):
            print(f"Failed to connect to {url} OpenAPI tool server")
            continue

        results.append(
            {
                "idx": idx,
                "url": server.get("url"),
                "openapi": response.get("openapi"),
                "info": response.get("info"),
                "specs": response.get("specs"),
            }
        )

    return results


async def execute_tool_server(
    token: str, url: str, name: str, params: Dict[str, Any], server_data: Dict[str, Any]
) -> Any:
    error = None
    try:
        openapi = server_data.get("openapi", {})
        paths = openapi.get("paths", {})

        matching_route = None
        for route_path, methods in paths.items():
            for http_method, operation in methods.items():
                if isinstance(operation, dict) and operation.get("operationId") == name:
                    matching_route = (route_path, methods)
                    break
            if matching_route:
                break

        if not matching_route:
            raise Exception(f"No matching route found for operationId: {name}")

        route_path, methods = matching_route

        method_entry = None
        for http_method, operation in methods.items():
            if operation.get("operationId") == name:
                method_entry = (http_method.lower(), operation)
                break

        if not method_entry:
            raise Exception(f"No matching method found for operationId: {name}")

        http_method, operation = method_entry

        path_params = {}
        query_params = {}
        body_params = {}

        for param in operation.get("parameters", []):
            param_name = param["name"]
            param_in = param["in"]
            if param_name in params:
                if param_in == "path":
                    path_params[param_name] = params[param_name]
                elif param_in == "query":
                    query_params[param_name] = params[param_name]

        final_url = f"{url.rstrip('/')}{route_path}"
        for key, value in path_params.items():
            final_url = final_url.replace(f"{{{key}}}", str(value))

        if query_params:
            query_string = "&".join(f"{k}={v}" for k, v in query_params.items())
            final_url = f"{final_url}?{query_string}"

        if operation.get("requestBody", {}).get("content"):
            if params:
                body_params = params
            else:
                raise Exception(
                    f"Request body expected for operation '{name}' but none found."
                )

        headers = {"Content-Type": "application/json"}

        if token:
            headers["Authorization"] = f"Bearer {token}"

        async with aiohttp.ClientSession() as session:
            request_method = getattr(session, http_method.lower())

            if http_method in ["post", "put", "patch"]:
                async with request_method(
                    final_url, json=body_params, headers=headers
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"HTTP error {response.status}: {text}")
                    return await response.json()
            else:
                async with request_method(final_url, headers=headers) as response:
                    if response.status >= 400:
                        text = await response.text()
                        raise Exception(f"HTTP error {response.status}: {text}")
                    return await response.json()

    except Exception as err:
        error = str(err)
        print("API Request Error:", error)
        return {"error": error}
