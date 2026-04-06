import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import ssl
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Deque, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse
from uuid import uuid4

import aiohttp

from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL,
    AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA,
    MCP_STDIO_ALLOWED_COMMANDS,
    MCP_STDIO_IDLE_TIMEOUT,
    MCP_STDIO_START_TIMEOUT,
    MCP_TOOL_CALL_TIMEOUT,
)

log = logging.getLogger(__name__)


SUPPORTED_MCP_PROTOCOL_VERSIONS = [
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
    "2024-10-07",
]
DEFAULT_MCP_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_STDIO_ALLOWED_COMMANDS = {"npx", "node", "python", "python3", "uvx", "uv", "deno"}
DEFAULT_MCP_PRESET_RUNTIME_COMMANDS = ("npx", "uvx", "git")
USER_FACING_SELECTION_ERROR = "所选 MCP 服务器当前不可用，请前往 设置 > 工具 重新验证。"
VERSION_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
HTTP_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
MCP_TOOL_ID_RE = re.compile(r"^mcp:(\d+)$")
STDIO_STDERR_TAIL_LINES = 12
STDIO_STDERR_TAIL_CHARS = 1200
STDIO_START_TRANSPORT_ERROR_MARKERS = (
    "WriteUnixTransport",
    "handler is closed",
    "process is not running",
    "closed stdout before sending a response",
)
MCP_RUNTIME_PROFILES = {"main", "slim"}
MCP_RESERVED_HTTP_HEADER_NAMES = {
    "accept",
    "connection",
    "content-length",
    "content-type",
    "host",
    "mcp-protocol-version",
    "mcp-session-id",
    "transfer-encoding",
}


class MCPHttpError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        parsed_json: Optional[Dict[str, Any]],
        raw_body: str,
    ):
        self.status_code = status_code
        self.parsed_json = parsed_json
        self.raw_body = raw_body
        super().__init__(f"MCP HTTP {status_code}: {raw_body}")


def _get_ssl_context() -> Optional[ssl.SSLContext]:
    val = (AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL or "").strip()
    if not val:
        return None
    if val.lower() in {"false", "0", "no"}:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=val)


def _strip_trailing_slash(url: str) -> str:
    return url[:-1] if url.endswith("/") else url


def _is_enabled(connection: Dict[str, Any]) -> bool:
    cfg = connection.get("config") or {}
    if isinstance(cfg, dict) and "enable" in cfg:
        return bool(cfg.get("enable"))
    if "enabled" in connection:
        return bool(connection.get("enabled"))
    return True


def _get_transport_type(connection: Dict[str, Any]) -> str:
    transport_type = str(connection.get("transport_type") or "http").lower()
    return transport_type if transport_type in {"http", "stdio"} else "http"


def _get_auth_headers(connection: Dict[str, Any], session_token: Optional[str]) -> Dict[str, str]:
    auth_type = (connection.get("auth_type") or "none").lower()
    if auth_type in {"bearer", "oauth21", "oauth2", "oauth"}:
        key = connection.get("key") or ""
        if not key:
            return {}
        return {"Authorization": f"Bearer {key}"}
    if auth_type == "session":
        if not session_token:
            return {}
        return {"Authorization": f"Bearer {session_token}"}
    return {}


def normalize_mcp_http_headers(raw_headers: Any) -> Dict[str, str]:
    if raw_headers is None:
        return {}
    if not isinstance(raw_headers, dict):
        raise ValueError("headers must be an object")

    normalized: Dict[str, str] = {}
    seen_lower: Dict[str, str] = {}

    for raw_key, raw_value in raw_headers.items():
        key = str(raw_key).strip()
        if not key:
            continue

        value = "" if raw_value is None else str(raw_value)
        if "\r" in key or "\n" in key:
            raise ValueError("自定义请求头名称不能包含换行符。")
        if "\r" in value or "\n" in value:
            raise ValueError(f"自定义请求头 {key} 的值不能包含换行符。")
        if not HTTP_HEADER_NAME_RE.fullmatch(key):
            raise ValueError(f"自定义请求头名称无效: {key}")

        lower_key = key.lower()
        if lower_key in MCP_RESERVED_HTTP_HEADER_NAMES:
            raise ValueError(f"请求头 {key} 为保留头，不能自定义。")

        if lower_key in seen_lower:
            raise ValueError(f"请求头 {key} 与 {seen_lower[lower_key]} 重复。")

        seen_lower[lower_key] = key
        normalized[key] = value

    return normalized


def _build_mcp_http_request_headers(
    connection: Dict[str, Any], session_token: Optional[str]
) -> Dict[str, str]:
    headers = _get_auth_headers(connection, session_token)
    headers.update(normalize_mcp_http_headers(connection.get("headers")))
    return headers


def _now_iso_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _coerce_supported_versions(value: Any) -> List[str]:
    versions: List[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if isinstance(item, str) and VERSION_RE.fullmatch(item):
                versions.append(item)
    return list(dict.fromkeys(versions))


def _extract_versions_from_text(text: str) -> List[str]:
    if not text:
        return []
    if "supported version" not in text.lower():
        return []
    return list(dict.fromkeys(VERSION_RE.findall(text)))


def _extract_supported_versions(payload: Any, raw_text: str = "") -> List[str]:
    versions: List[str] = []
    if isinstance(payload, dict):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
        if isinstance(error, dict):
            data = error.get("data")
            if isinstance(data, dict):
                versions.extend(_coerce_supported_versions(data.get("supported")))
            versions.extend(_extract_versions_from_text(str(error.get("message") or "")))
    versions.extend(_extract_versions_from_text(raw_text))
    return list(dict.fromkeys(versions))


def _select_best_common_version(server_versions: List[str]) -> Optional[str]:
    supported = set(server_versions)
    for version in SUPPORTED_MCP_PROTOCOL_VERSIONS:
        if version in supported:
            return version
    return None


def _format_protocol_error(
    requested_version: str,
    supported_versions: List[str],
    original_message: Optional[str] = None,
) -> str:
    if supported_versions:
        return (
            "MCP protocol version negotiation failed. "
            f"Client requested {requested_version}, "
            f"client supports {', '.join(SUPPORTED_MCP_PROTOCOL_VERSIONS)}, "
            f"server supports {', '.join(supported_versions)}."
        )
    return original_message or f"MCP initialize failed for protocol version {requested_version}."


def _finalize_protocol_version(
    result: Dict[str, Any],
    requested_version: str,
    *,
    context: str,
) -> str:
    response_version = result.get("protocolVersion")
    if not response_version:
        log.warning(
            "MCP %s initialize result missing protocolVersion; continuing with requested version %s",
            context,
            requested_version,
        )
        return requested_version

    if response_version not in SUPPORTED_MCP_PROTOCOL_VERSIONS:
        raise RuntimeError(
            f"MCP server returned unsupported protocolVersion {response_version}. "
            f"Client supports {', '.join(SUPPORTED_MCP_PROTOCOL_VERSIONS)}."
        )

    return response_version


def _friendly_missing_command_message(command_name: str) -> str:
    normalized = command_name.lower()
    if normalized in {"npx", "node"}:
        return f"{command_name} 不存在，请安装 Node.js 并确保 {command_name} 在 PATH 中。"
    if normalized in {"uv", "uvx"}:
        return f"{command_name} 不存在，请安装 Python + uv 并确保 {command_name} 在 PATH 中。"
    if normalized == "git":
        return "git 不存在，请安装 git 并确保 git 在 PATH 中。"
    if normalized in {"python", "python3"}:
        return f"{command_name} 不存在，请安装 Python 并确保 {command_name} 在 PATH 中。"
    if normalized == "deno":
        return "deno 不存在，请安装 Deno 并确保 deno 在 PATH 中。"
    return f"{command_name} 不存在，请确保该命令已安装并可在 PATH 中找到。"


def extract_selected_mcp_indices(tool_ids: List[Any]) -> Set[int]:
    indices: Set[int] = set()
    for tool_id in tool_ids:
        match = MCP_TOOL_ID_RE.match(str(tool_id))
        if match:
            indices.add(int(match.group(1)))
    return indices


def _parse_allowed_stdio_commands() -> Set[str]:
    configured = {
        item.strip().lower()
        for item in str(MCP_STDIO_ALLOWED_COMMANDS or "").split(",")
        if item.strip()
    }
    return DEFAULT_STDIO_ALLOWED_COMMANDS | configured


def _normalize_stdio_args(connection: Dict[str, Any]) -> List[str]:
    args = connection.get("args") or []
    if not isinstance(args, list):
        return []
    return [str(item) for item in args]


def _normalize_stdio_env(connection: Dict[str, Any]) -> Dict[str, str]:
    env = connection.get("env") or {}
    if not isinstance(env, dict):
        return {}
    return {str(key): str(value) for key, value in env.items()}


def _get_stdio_command_name(connection: Dict[str, Any]) -> str:
    command = str(connection.get("command") or "").strip()
    if not command:
        return ""
    return os.path.basename(command).lower()


def _stdio_uses_git_source(connection: Dict[str, Any]) -> bool:
    if _get_stdio_command_name(connection) not in {"uv", "uvx"}:
        return False

    args = [str(item).strip() for item in _normalize_stdio_args(connection)]
    for idx, arg in enumerate(args):
        if arg.startswith("git+"):
            return True
        if arg.startswith("--from=") and arg[len("--from=") :].startswith("git+"):
            return True
        if arg == "--from" and idx + 1 < len(args) and args[idx + 1].startswith("git+"):
            return True

    return False


def _get_derived_stdio_runtime_requirements(connection: Dict[str, Any]) -> List[str]:
    requirements: List[str] = []
    if _stdio_uses_git_source(connection):
        requirements.append("git")
    return requirements


def _friendly_missing_stdio_dependency_message(
    connection: Dict[str, Any], dependency: str
) -> str:
    if dependency == "git" and _stdio_uses_git_source(connection):
        return (
            "当前 MCP 通过 Git 源安装（检测到 git+...），但运行环境缺少 git。"
            "请切换到包含 git 的官方 main 镜像，或在容器内安装 git 后重新验证。"
        )
    return _friendly_missing_command_message(dependency)


def _validate_stdio_command(connection: Dict[str, Any]) -> str:
    command = str(connection.get("command") or "").strip()
    if not command:
        raise ValueError("缺少 MCP stdio command。")

    allowed = _parse_allowed_stdio_commands()
    is_path = os.path.sep in command or (os.path.altsep and os.path.altsep in command)
    command_name = os.path.basename(command) if is_path else command

    if command_name.lower() not in allowed:
        raise ValueError(
            f"命令 {command_name} 不在允许列表中。允许的命令: {', '.join(sorted(allowed))}"
        )

    if is_path:
        if not os.path.isfile(command):
            raise ValueError(f"命令路径不存在: {command}")
        if not os.access(command, os.X_OK):
            raise ValueError(f"命令不可执行: {command}")
        resolved = command
    else:
        resolved = _resolve_stdio_command(connection, command)
        if not resolved:
            raise ValueError(_friendly_missing_command_message(command_name))

    for dependency in _get_derived_stdio_runtime_requirements(connection):
        if not _resolve_stdio_command(connection, dependency):
            raise ValueError(_friendly_missing_stdio_dependency_message(connection, dependency))

    return resolved


def _resolve_stdio_command(connection: Dict[str, Any], command: str) -> Optional[str]:
    env = _build_stdio_env(connection)

    resolved = shutil.which(command, path=env.get("PATH"))
    if resolved:
        return resolved

    home = env.get("HOME")
    if not home:
        return None

    fallback = os.path.join(home, ".local", "bin", os.path.basename(command))
    if os.path.isfile(fallback) and os.access(fallback, os.X_OK):
        return fallback

    return None


def _build_mcp_runtime_command_capability(command: str) -> Dict[str, Any]:
    resolved = _resolve_stdio_command({}, command)
    return {
        "available": bool(resolved),
        "message": None if resolved else _friendly_missing_command_message(command),
    }


def get_mcp_runtime_profile() -> str:
    profile = str(os.environ.get("HALO_RUNTIME_PROFILE") or "").strip().lower()
    return profile if profile in MCP_RUNTIME_PROFILES else "custom"


def get_mcp_runtime_capabilities(
    preset_commands: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    commands: Dict[str, Dict[str, Any]] = {}
    seen: Set[str] = set()

    for raw_command in preset_commands or list(DEFAULT_MCP_PRESET_RUNTIME_COMMANDS):
        command = os.path.basename(str(raw_command or "").strip()).lower()
        if not command or command in seen:
            continue
        seen.add(command)
        commands[command] = _build_mcp_runtime_command_capability(command)

    return {"commands": commands}


def _build_stdio_env(connection: Dict[str, Any]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if os.environ.get("PATH"):
        env["PATH"] = os.environ["PATH"]
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    env.update(_normalize_stdio_env(connection))
    return env


async def _read_jsonrpc_response(
    response: aiohttp.ClientResponse,
    request_id: str,
    on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
) -> Dict[str, Any]:
    content_type = (response.headers.get("Content-Type") or "").lower()
    if "text/event-stream" not in content_type:
        return await response.json()

    buffer = ""
    async for raw_line in response.content:
        try:
            line = raw_line.decode("utf-8", errors="ignore").strip()
        except Exception:
            continue

        if not line:
            continue

        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                break

            candidate = payload if not buffer else buffer + payload
            try:
                msg = json.loads(candidate)
                buffer = ""
            except Exception:
                buffer = candidate
                continue

            if str(msg.get("id", "")) == str(request_id):
                return msg

            if "id" not in msg and on_notification is not None:
                try:
                    await on_notification(msg)
                except Exception:
                    pass

    raise RuntimeError("MCP server closed the stream before sending a response.")


class MCPStreamableHttpClient:
    def __init__(
        self,
        url: str,
        *,
        auth_headers: Optional[Dict[str, str]] = None,
        request_headers: Optional[Dict[str, str]] = None,
        protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
        timeout_s: Optional[int] = None,
    ):
        self.url = _strip_trailing_slash(url)
        merged_headers: Dict[str, str] = {}
        if auth_headers:
            merged_headers.update(auth_headers)
        if request_headers:
            merged_headers.update(request_headers)
        self.request_headers = merged_headers
        self.protocol_version = protocol_version
        self.timeout_s = (
            timeout_s if timeout_s is not None else AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA
        )
        self.session_id: Optional[str] = None

    def _build_request_headers(self) -> Dict[str, str]:
        headers = {
            **self.request_headers,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    async def _post_jsonrpc(
        self,
        payload: Dict[str, Any],
        on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        headers = self._build_request_headers()
        timeout = aiohttp.ClientTimeout(total=self.timeout_s) if self.timeout_s else None
        ssl_ctx = _get_ssl_context()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url, headers=headers, json=payload, ssl=ssl_ctx) as resp:
                if resp.status >= 400:
                    raw_body = await resp.text()
                    parsed_json = None
                    try:
                        parsed_candidate = json.loads(raw_body)
                        if isinstance(parsed_candidate, dict):
                            parsed_json = parsed_candidate
                    except Exception:
                        parsed_json = None
                    raise MCPHttpError(resp.status, parsed_json, raw_body)

                session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get(
                    "MCP-Session-Id"
                )
                msg = await _read_jsonrpc_response(
                    resp, str(payload.get("id", "")), on_notification=on_notification
                )
                return msg, session_id

    async def initialize(self) -> Dict[str, Any]:
        retried = False

        while True:
            requested_version = self.protocol_version
            request_id = str(uuid4())
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": requested_version,
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "HaloWebUI", "version": "unknown"},
                },
            }

            try:
                msg, session_id = await self._post_jsonrpc(payload)
            except MCPHttpError as exc:
                supported_versions = _extract_supported_versions(
                    exc.parsed_json, exc.raw_body
                )
                negotiated_version = _select_best_common_version(supported_versions)
                if (
                    negotiated_version
                    and negotiated_version != requested_version
                    and not retried
                ):
                    log.info(
                        "Retrying MCP HTTP initialize with negotiated version %s",
                        negotiated_version,
                    )
                    self.protocol_version = negotiated_version
                    retried = True
                    continue
                raise RuntimeError(
                    _format_protocol_error(
                        requested_version,
                        supported_versions,
                        str(exc),
                    )
                ) from exc

            if session_id:
                self.session_id = session_id

            if "error" in msg:
                supported_versions = _extract_supported_versions(
                    msg, json.dumps(msg, ensure_ascii=False)
                )
                negotiated_version = _select_best_common_version(supported_versions)
                if (
                    negotiated_version
                    and negotiated_version != requested_version
                    and not retried
                ):
                    log.info(
                        "Retrying MCP HTTP initialize with negotiated version %s",
                        negotiated_version,
                    )
                    self.protocol_version = negotiated_version
                    retried = True
                    continue
                raise RuntimeError(
                    _format_protocol_error(
                        requested_version,
                        supported_versions,
                        str(msg["error"]),
                    )
                )

            result = msg.get("result", {}) or {}
            self.protocol_version = _finalize_protocol_version(
                result,
                requested_version,
                context="HTTP",
            )
            return result

    async def notify_initialized(self) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        headers = self._build_request_headers()
        timeout = aiohttp.ClientTimeout(total=self.timeout_s) if self.timeout_s else None
        ssl_ctx = _get_ssl_context()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.url, headers=headers, json=payload, ssl=ssl_ctx) as resp:
                if resp.status >= 400:
                    raw_body = await resp.text()
                    parsed_json = None
                    try:
                        parsed_candidate = json.loads(raw_body)
                        if isinstance(parsed_candidate, dict):
                            parsed_json = parsed_candidate
                    except Exception:
                        parsed_json = None
                    raise MCPHttpError(resp.status, parsed_json, raw_body)

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            request_id = str(uuid4())
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/list",
                "params": {"cursor": cursor} if cursor else {},
            }
            msg, _ = await self._post_jsonrpc(payload)
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))

            result = msg.get("result", {}) or {}
            tools.extend(result.get("tools", []) or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break

        return tools

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
    ) -> Dict[str, Any]:
        request_id = str(uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        msg, _ = await self._post_jsonrpc(payload, on_notification=on_notification)
        if "error" in msg:
            raise RuntimeError(str(msg["error"]))
        return msg.get("result", {}) or {}


class MCPStdioClient:
    def __init__(
        self,
        connection: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
    ):
        self.connection = deepcopy(connection)
        self.user_id = user_id
        self.protocol_version = protocol_version
        self.process: Optional[asyncio.subprocess.Process] = None
        self.stderr_task: Optional[asyncio.Task] = None
        self._request_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._last_activity = time.monotonic()
        self._inflight_requests = 0
        self._tainted = False
        self._ready = False
        self._resolved_command: Optional[str] = None
        self._server_info: Dict[str, Any] = {}
        self._capabilities: Dict[str, Any] = {}
        self._cached_tools: Optional[List[Dict[str, Any]]] = None
        self._stderr_tail: Deque[str] = deque(maxlen=STDIO_STDERR_TAIL_LINES)

    @property
    def tainted(self) -> bool:
        return self._tainted

    @property
    def server_info(self) -> Dict[str, Any]:
        return self._server_info

    @property
    def capabilities(self) -> Dict[str, Any]:
        return self._capabilities

    def is_alive(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def _get_recent_stderr(self) -> str:
        if not self._stderr_tail:
            return ""

        stderr_output = "\n".join(self._stderr_tail).strip()
        if len(stderr_output) <= STDIO_STDERR_TAIL_CHARS:
            return stderr_output
        return "..." + stderr_output[-STDIO_STDERR_TAIL_CHARS:]

    async def _build_initialization_failure_message(self, exc: Exception) -> str:
        process = self.process
        if process and process.returncode is None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(process.wait(), timeout=0.05)

        await asyncio.sleep(0)

        exit_code = process.returncode if process else None
        stderr_output = self._get_recent_stderr()
        exc_text = str(exc).strip()
        is_transport_error = any(
            marker in exc_text for marker in STDIO_START_TRANSPORT_ERROR_MARKERS
        )

        if exit_code is None and not is_transport_error:
            return exc_text or "MCP stdio server failed during initialization."

        base = "MCP stdio server exited before initialization."
        if exit_code is not None:
            base = f"MCP stdio server exited before initialization (exit code {exit_code})."

        if stderr_output:
            return f"{base}\nstderr:\n{stderr_output}"

        if exc_text and not is_transport_error:
            return f"{base}\n{exc_text}"

        return f"{base}\n进程提前退出，未返回 MCP initialize 响应。"

    async def _pump_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return

        try:
            while True:
                line = await self.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore").rstrip()
                if text:
                    self._stderr_tail.append(text)
                    log.debug("MCP stdio stderr[%s]: %s", self.user_id or "unknown", text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("MCP stdio stderr reader stopped: %s", exc)

    async def _write_json_line(self, payload: Dict[str, Any]) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("MCP stdio process is not running.")

        line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"
        self.process.stdin.write(line.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read_jsonrpc_response(
        self,
        request_id: str,
        on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
    ) -> Dict[str, Any]:
        if not self.process or not self.process.stdout:
            raise RuntimeError("MCP stdio process is not running.")

        while True:
            raw_line = await self.process.stdout.readline()
            if not raw_line:
                raise RuntimeError("MCP stdio server closed stdout before sending a response.")

            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except Exception:
                log.debug("Ignoring invalid MCP stdio line: %s", line)
                continue

            if str(msg.get("id", "")) == request_id:
                return msg

            if "id" not in msg and on_notification is not None:
                try:
                    await on_notification(msg)
                except Exception:
                    pass

    async def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        await self._write_json_line(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    async def _perform_request(
        self,
        method: str,
        params: Dict[str, Any],
        *,
        timeout_s: Optional[int],
        on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
        cancel_on_timeout: bool = False,
    ) -> Dict[str, Any]:
        await self.start()

        async with self._request_lock:
            self._inflight_requests += 1
            self._last_activity = time.monotonic()
            request_id = str(uuid4())
            payload = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }

            try:
                async def _do_request() -> Dict[str, Any]:
                    await self._write_json_line(payload)
                    return await self._read_jsonrpc_response(
                        request_id,
                        on_notification=on_notification,
                    )

                if timeout_s:
                    try:
                        msg = await asyncio.wait_for(_do_request(), timeout=timeout_s)
                    except asyncio.TimeoutError as exc:
                        if cancel_on_timeout:
                            await self._handle_request_timeout(request_id)
                        raise RuntimeError("MCP stdio tool call timed out.") from exc
                else:
                    msg = await _do_request()

                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))

                return msg.get("result", {}) or {}
            finally:
                self._last_activity = time.monotonic()
                self._inflight_requests -= 1

    async def _handle_request_timeout(self, request_id: str) -> None:
        self._tainted = True
        try:
            await self._send_notification(
                "notifications/cancelled",
                {"requestId": request_id, "reason": "timeout"},
            )
        except Exception as exc:
            log.debug("Failed to send MCP stdio cancellation notification: %s", exc)

    async def initialize(self) -> Dict[str, Any]:
        retried = False

        while True:
            requested_version = self.protocol_version
            async with self._request_lock:
                self._inflight_requests += 1
                self._last_activity = time.monotonic()
                request_id = str(uuid4())
                payload = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": requested_version,
                        "capabilities": {"tools": {}},
                        "clientInfo": {"name": "HaloWebUI", "version": "unknown"},
                    },
                }

                try:
                    async def _do_initialize() -> Dict[str, Any]:
                        await self._write_json_line(payload)
                        return await self._read_jsonrpc_response(request_id)

                    msg = await asyncio.wait_for(
                        _do_initialize(),
                        timeout=MCP_STDIO_START_TIMEOUT,
                    )
                finally:
                    self._last_activity = time.monotonic()
                    self._inflight_requests -= 1

            if "error" in msg:
                supported_versions = _extract_supported_versions(
                    msg,
                    json.dumps(msg, ensure_ascii=False),
                )
                negotiated_version = _select_best_common_version(supported_versions)
                if (
                    negotiated_version
                    and negotiated_version != requested_version
                    and not retried
                ):
                    log.info(
                        "Retrying MCP stdio initialize with negotiated version %s",
                        negotiated_version,
                    )
                    self.protocol_version = negotiated_version
                    retried = True
                    continue
                raise RuntimeError(
                    _format_protocol_error(
                        requested_version,
                        supported_versions,
                        str(msg["error"]),
                    )
                )

            result = msg.get("result", {}) or {}
            self.protocol_version = _finalize_protocol_version(
                result,
                requested_version,
                context="stdio",
            )
            self._server_info = result.get("serverInfo", {}) or {}
            self._capabilities = result.get("capabilities", {}) or {}
            return result

    async def notify_initialized(self) -> None:
        async with self._request_lock:
            self._inflight_requests += 1
            self._last_activity = time.monotonic()
            try:
                await asyncio.wait_for(
                    self._send_notification("notifications/initialized", {}),
                    timeout=MCP_STDIO_START_TIMEOUT,
                )
            finally:
                self._last_activity = time.monotonic()
                self._inflight_requests -= 1

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self.is_alive() and self._ready and not self._tainted:
                return

            if self.process is not None:
                await self._stop_locked()

            self._resolved_command = _validate_stdio_command(self.connection)
            args = _normalize_stdio_args(self.connection)
            env = _build_stdio_env(self.connection)
            self._stderr_tail.clear()

            self.process = await asyncio.create_subprocess_exec(
                self._resolved_command,
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self.stderr_task = asyncio.create_task(self._pump_stderr())
            self._tainted = False
            self._ready = False
            self._server_info = {}
            self._capabilities = {}
            self._cached_tools = None
            self._last_activity = time.monotonic()

            try:
                await asyncio.wait_for(self.initialize(), timeout=MCP_STDIO_START_TIMEOUT)
                await asyncio.wait_for(
                    self.notify_initialized(), timeout=MCP_STDIO_START_TIMEOUT
                )
                self._ready = True
            except Exception as exc:
                message = await self._build_initialization_failure_message(exc)
                self._tainted = True
                await self._stop_locked()
                raise RuntimeError(message) from exc

    async def list_tools(self) -> List[Dict[str, Any]]:
        await self.start()
        if self._cached_tools is not None:
            return deepcopy(self._cached_tools)

        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            result = await self._perform_request(
                "tools/list",
                {"cursor": cursor} if cursor else {},
                timeout_s=AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA,
            )
            tools.extend(result.get("tools", []) or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break

        self._cached_tools = deepcopy(tools)
        return deepcopy(tools)

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
    ) -> Dict[str, Any]:
        await self.start()
        return await self._perform_request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout_s=MCP_TOOL_CALL_TIMEOUT,
            on_notification=on_notification,
            cancel_on_timeout=True,
        )

    async def reap_if_idle(self, idle_timeout_s: int) -> bool:
        async with self._lifecycle_lock:
            if not self.is_alive():
                return True
            if self._inflight_requests > 0:
                return False
            if time.monotonic() - self._last_activity <= idle_timeout_s:
                return False
            await self._stop_locked()
            return True

    async def _stop_locked(self) -> None:
        process = self.process
        stderr_task = self.stderr_task
        self.process = None
        self.stderr_task = None
        self._ready = False
        self._tainted = False
        self._server_info = {}
        self._capabilities = {}
        self._cached_tools = None
        self._stderr_tail.clear()
        self.protocol_version = DEFAULT_MCP_PROTOCOL_VERSION

        if process is None:
            return

        try:
            if process.stdin and not process.stdin.is_closing():
                process.stdin.close()
        except Exception:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                with contextlib.suppress(Exception):
                    await process.wait()

        if stderr_task:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await stderr_task

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_locked()


class MCPStdioProcessManager:
    _instance: Optional["MCPStdioProcessManager"] = None

    def __init__(self):
        self._clients: Dict[Tuple[Any, ...], MCPStdioClient] = {}
        self._manager_lock = asyncio.Lock()
        self._reaper_task: Optional[asyncio.Task] = None

    @classmethod
    def instance(cls) -> "MCPStdioProcessManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _fingerprint(
        self,
        connection: Dict[str, Any],
        user_id: Optional[str],
    ) -> Tuple[Any, ...]:
        return (
            user_id,
            str(connection.get("command") or "").strip(),
            tuple(_normalize_stdio_args(connection)),
            frozenset(_normalize_stdio_env(connection).items()),
        )

    def _ensure_reaper_started(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._idle_reaper())

    async def get_or_start(
        self,
        connection: Dict[str, Any],
        user_id: Optional[str],
    ) -> MCPStdioClient:
        stale_client: Optional[MCPStdioClient] = None
        fingerprint = self._fingerprint(connection, user_id)

        async with self._manager_lock:
            self._ensure_reaper_started()
            client = self._clients.get(fingerprint)
            if client and (client.tainted or not client.is_alive()):
                stale_client = client
                self._clients.pop(fingerprint, None)
                client = None
            if client is None:
                client = MCPStdioClient(connection, user_id=user_id)
                self._clients[fingerprint] = client

        if stale_client is not None:
            with contextlib.suppress(Exception):
                await stale_client.stop()

        await client.start()
        return client

    async def stop_by_fingerprint(self, fingerprint: Tuple[Any, ...]) -> None:
        async with self._manager_lock:
            client = self._clients.pop(fingerprint, None)
        if client is not None:
            await client.stop()

    async def stop_all(self) -> None:
        async with self._manager_lock:
            clients = list(self._clients.values())
            self._clients.clear()
            reaper_task = self._reaper_task
            self._reaper_task = None

        if reaper_task:
            reaper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reaper_task

        for client in clients:
            with contextlib.suppress(Exception):
                await client.stop()

    async def _idle_reaper(self) -> None:
        sleep_for = max(5, min(int(MCP_STDIO_IDLE_TIMEOUT or 180), 30))
        try:
            while True:
                await asyncio.sleep(sleep_for)
                async with self._manager_lock:
                    items = list(self._clients.items())

                stale_fingerprints: List[Tuple[Any, ...]] = []
                for fingerprint, client in items:
                    try:
                        should_remove = await client.reap_if_idle(MCP_STDIO_IDLE_TIMEOUT)
                    except Exception as exc:
                        log.debug("MCP stdio idle reap failed: %s", exc)
                        should_remove = client.tainted or not client.is_alive()
                    if should_remove:
                        stale_fingerprints.append(fingerprint)

                if not stale_fingerprints:
                    continue

                async with self._manager_lock:
                    for fingerprint in stale_fingerprints:
                        current = self._clients.get(fingerprint)
                        if current and (not current.is_alive() or current.tainted):
                            self._clients.pop(fingerprint, None)
        except asyncio.CancelledError:
            raise


async def verify_stdio_server(connection: Dict[str, Any]) -> Dict[str, Any]:
    client = MCPStdioClient(connection)
    try:
        await client.start()
        tools = await client.list_tools()
        return {
            "server_info": client.server_info or {},
            "capabilities": client.capabilities or {},
            "tools": tools,
        }
    finally:
        with contextlib.suppress(Exception):
            await client.stop()


def get_mcp_servers_cached_meta(servers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for idx, server in enumerate(servers):
        transport_type = _get_transport_type(server)
        results.append(
            {
                "idx": idx,
                "transport_type": transport_type,
                "url": _strip_trailing_slash(server.get("url") or ""),
                "command": str(server.get("command") or "").strip(),
                "server_info": deepcopy(server.get("server_info") or {}),
                "tool_count": server.get("tool_count"),
                "verified_at": server.get("verified_at"),
                "config": deepcopy(server.get("config") or {}),
                "name": server.get("name"),
                "description": server.get("description"),
                "auth_type": server.get("auth_type"),
            }
        )
    return results


def _get_mcp_http_display_target(
    server: Dict[str, Any], *, prefer_hostname: bool = False
) -> str:
    url = _strip_trailing_slash(str(server.get("url") or "").strip())
    if not url or not prefer_hostname:
        return url

    try:
        parsed = urlparse(url)
        return parsed.netloc or url
    except Exception:
        return url


def get_mcp_server_display_metadata(
    server: Dict[str, Any],
    *,
    index: Optional[int] = None,
    default_description: str = "",
    prefer_hostname_for_http: bool = False,
) -> Tuple[str, str]:
    config = server.get("config") or {}
    transport_type = _get_transport_type(server)
    server_info = server.get("server_info") or {}

    custom_name = str(server.get("name") or config.get("remark") or "").strip()
    resolved_server_name = str(server_info.get("name") or "").strip()

    if transport_type == "stdio":
        fallback_name = str(server.get("command") or "").strip()
    else:
        fallback_name = _get_mcp_http_display_target(
            server, prefer_hostname=prefer_hostname_for_http
        )

    title = (
        custom_name
        or resolved_server_name
        or fallback_name
        or (f"MCP Server {index + 1}" if index is not None else "MCP Server")
    )

    custom_description = str(server.get("description") or "").strip()
    description = custom_description or default_description

    return title, description


async def get_mcp_server_data(
    connection: Dict[str, Any],
    *,
    session_token: Optional[str] = None,
    protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
    user_id: Optional[str] = None,
    use_temp_stdio_client: bool = False,
) -> Dict[str, Any]:
    transport_type = _get_transport_type(connection)

    if transport_type == "stdio":
        if use_temp_stdio_client:
            return await verify_stdio_server(connection)

        client = await MCPStdioProcessManager.instance().get_or_start(connection, user_id)
        tools = await client.list_tools()
        return {
            "server_info": deepcopy(client.server_info),
            "capabilities": deepcopy(client.capabilities),
            "tools": deepcopy(tools),
        }

    url = connection.get("url") or ""
    if not url:
        raise ValueError("Missing MCP server URL")

    client = MCPStreamableHttpClient(
        url,
        request_headers=_build_mcp_http_request_headers(connection, session_token),
        protocol_version=protocol_version,
    )
    init_result = await client.initialize()
    await client.notify_initialized()
    tools = await client.list_tools()

    return {
        "server_info": init_result.get("serverInfo", {}) or {},
        "capabilities": init_result.get("capabilities", {}) or {},
        "tools": tools,
    }


async def get_mcp_servers_data(
    servers: List[Dict[str, Any]],
    *,
    session_token: Optional[str] = None,
    protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
    selected_indices: Optional[Set[int]] = None,
    strict_selected: bool = False,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if selected_indices is not None:
        invalid_selected = sorted(
            idx for idx in selected_indices if idx < 0 or idx >= len(servers)
        )
        if invalid_selected and strict_selected:
            raise RuntimeError(USER_FACING_SELECTION_ERROR)

        disabled_selected = sorted(
            idx
            for idx in selected_indices
            if 0 <= idx < len(servers) and not _is_enabled(servers[idx])
        )
        if disabled_selected and strict_selected:
            raise RuntimeError(USER_FACING_SELECTION_ERROR)

    server_entries = [
        (idx, server)
        for idx, server in enumerate(servers)
        if _is_enabled(server) and (selected_indices is None or idx in selected_indices)
    ]

    tasks = [
        get_mcp_server_data(
            server,
            session_token=session_token,
            protocol_version=protocol_version,
            user_id=user_id,
        )
        for _, server in server_entries
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    results: List[Dict[str, Any]] = []
    selected_indices_set = selected_indices or set()
    for (idx, server), response in zip(server_entries, responses):
        if isinstance(response, Exception):
            transport_type = _get_transport_type(server)
            identifier = (
                server.get("url")
                if transport_type == "http"
                else str(server.get("command") or "").strip()
            )
            log.warning(
                "Failed to connect to MCP server idx=%s (%s): %s",
                idx,
                identifier,
                response,
            )
            if strict_selected and idx in selected_indices_set:
                raise RuntimeError(USER_FACING_SELECTION_ERROR) from response
            continue

        results.append(
            {
                "idx": idx,
                "transport_type": _get_transport_type(server),
                "url": _strip_trailing_slash(server.get("url") or ""),
                "command": str(server.get("command") or "").strip(),
                "server_info": response.get("server_info", {}) or {},
                "capabilities": response.get("capabilities", {}) or {},
                "tools": response.get("tools", []) or [],
            }
        )

    return results


async def execute_mcp_tool(
    connection: Dict[str, Any],
    *,
    name: str,
    arguments: Dict[str, Any],
    session_token: Optional[str] = None,
    protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
    on_notification: Optional[Callable[[Dict[str, Any]], Coroutine]] = None,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    transport_type = _get_transport_type(connection)

    if transport_type == "stdio":
        client = await MCPStdioProcessManager.instance().get_or_start(connection, user_id)
        return await client.call_tool(
            name=name,
            arguments=arguments or {},
            on_notification=on_notification,
        )

    url = connection.get("url") or ""
    if not url:
        raise ValueError("Missing MCP server URL")

    client = MCPStreamableHttpClient(
        url,
        request_headers=_build_mcp_http_request_headers(connection, session_token),
        protocol_version=protocol_version,
        timeout_s=MCP_TOOL_CALL_TIMEOUT,
    )
    await client.initialize()
    await client.notify_initialized()
    return await client.call_tool(
        name=name,
        arguments=arguments or {},
        on_notification=on_notification,
    )
