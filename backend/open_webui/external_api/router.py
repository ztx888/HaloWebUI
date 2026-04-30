import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.env import SRC_LOG_LEVELS
from open_webui.external_api.config import (
    ENABLE_EXTERNAL_CLIENT_GATEWAY,
    EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM,
    EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC,
    EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI,
)
from open_webui.external_api.models import (
    ExternalApiAuditLogs,
    ExternalApiClientCreateForm,
    ExternalApiClientResponse,
    ExternalApiClientUpdateForm,
    ExternalApiClients,
)
from open_webui.models.users import Users
from open_webui.routers import anthropic as anthropic_router
from open_webui.routers import openai as openai_router
from open_webui.utils.auth import get_admin_user, get_http_authorization_cred
from open_webui.utils.model_identity import get_model_aliases, get_model_selection_id
from open_webui.utils.models import get_all_models

log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["OPENAI"])

router = APIRouter()

_RUNTIME_RATE_BUCKET: dict[tuple[str, int], int] = {}


class ExternalApiGatewayConfigResponse(BaseModel):
    enabled: bool
    protocols: dict
    default_rpm_limit: int


class ExternalApiGatewayConfigForm(BaseModel):
    enabled: Optional[bool] = None
    openai: Optional[bool] = None
    anthropic: Optional[bool] = None
    default_rpm_limit: Optional[int] = None


class ExternalApiClientCreateResponse(BaseModel):
    client: ExternalApiClientResponse
    api_key: str


def _is_enabled() -> bool:
    return bool(ENABLE_EXTERNAL_CLIENT_GATEWAY.value)


def _protocol_enabled(protocol: str) -> bool:
    if protocol == "openai":
        return bool(EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI.value)
    if protocol == "anthropic":
        return bool(EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC.value)
    return False


def _client_to_response(client) -> ExternalApiClientResponse:
    return ExternalApiClientResponse(
        id=client.id,
        name=client.name,
        owner_user_id=client.owner_user_id,
        key_prefix=client.key_prefix,
        enabled=client.enabled,
        allowed_protocols=list(client.allowed_protocols or []),
        allowed_model_ids=list(client.allowed_model_ids or []),
        allow_tools=client.allow_tools,
        rpm_limit=client.rpm_limit,
        note=client.note,
        created_at=client.created_at,
        updated_at=client.updated_at,
        last_used_at=client.last_used_at,
    )


def _extract_gateway_key(request: Request) -> Optional[str]:
    auth = get_http_authorization_cred(request.headers.get("Authorization"))
    if not auth:
        return None
    if str(auth.scheme).lower() != "bearer":
        return None
    return auth.credentials


def _coerce_usage(data: object) -> tuple[Optional[int], Optional[int]]:
    if not isinstance(data, dict):
        return None, None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None, None
    prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
    completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens")
    try:
        prompt_tokens = int(prompt_tokens) if prompt_tokens is not None else None
    except Exception:
        prompt_tokens = None
    try:
        completion_tokens = int(completion_tokens) if completion_tokens is not None else None
    except Exception:
        completion_tokens = None
    return prompt_tokens, completion_tokens


def _tools_requested(payload: dict) -> bool:
    tools = payload.get("tools")
    if isinstance(tools, list) and len(tools) > 0:
        return True
    return bool(payload.get("tool_choice"))


async def _authenticate_gateway_request(request: Request, protocol: str):
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="External client gateway is disabled")
    if not _protocol_enabled(protocol):
        raise HTTPException(status_code=404, detail=f"{protocol} gateway is disabled")

    raw_key = _extract_gateway_key(request)
    if not raw_key:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if raw_key.startswith("sk-"):
        raise HTTPException(status_code=403, detail="Personal API keys are not allowed on the external gateway")

    client = ExternalApiClients.get_by_api_key(raw_key)
    if not client or not client.enabled:
        raise HTTPException(status_code=401, detail="Invalid external client key")
    if protocol not in set(client.allowed_protocols or []):
        raise HTTPException(status_code=403, detail=f"{protocol} protocol is not allowed for this client")

    owner_user = Users.get_user_by_id(client.owner_user_id)
    if not owner_user:
        raise HTTPException(status_code=403, detail="Gateway owner user not found")

    rpm_limit = client.rpm_limit if client.rpm_limit is not None else int(EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM.value)
    if rpm_limit > 0:
        minute_bucket = int(time.time() // 60)
        bucket_key = (client.id, minute_bucket)
        current = _RUNTIME_RATE_BUCKET.get(bucket_key, 0) + 1
        _RUNTIME_RATE_BUCKET[bucket_key] = current
        if current > rpm_limit:
            raise HTTPException(status_code=429, detail="Gateway rate limit exceeded")

    request.state.external_api_client = client
    request.state.connection_user = owner_user
    ExternalApiClients.touch_last_used(client.id)
    return client, owner_user


async def _load_gateway_models(request: Request, owner_user):
    await get_all_models(request, user=owner_user)
    return getattr(request.state, "MODELS", {}) or {}


def _ensure_model_allowed(client, request_models: dict, requested_model: str) -> dict:
    model_entry = request_models.get(requested_model)
    if not model_entry:
        raise HTTPException(status_code=404, detail="Model not found")

    allowed = set(client.allowed_model_ids or [])
    candidate_ids = {
        str(value)
        for value in (
            requested_model,
            model_entry.get("id"),
            model_entry.get("base_model_id"),
            model_entry.get("model"),
            get_model_selection_id(model_entry),
            ((model_entry.get("info") or {}).get("base_model_id") if isinstance(model_entry.get("info"), dict) else None),
        )
        if value
    }
    candidate_ids.update({str(value) for value in get_model_aliases(model_entry) if value})
    model_ref = model_entry.get("info") if isinstance(model_entry.get("info"), dict) else {}
    if isinstance(model_ref, dict):
        for key in ("model_ref", "base_model_id", "id"):
            value = model_ref.get(key)
            if value:
                candidate_ids.add(str(value))

    if not (candidate_ids & allowed):
        raise HTTPException(status_code=403, detail="Model is not allowed for this external client")
    return model_entry


def _build_gateway_metadata(client, owner_user, protocol: str, endpoint: str, payload: dict, model: dict) -> dict:
    return {
        "user_id": owner_user.id,
        "chat_id": payload.get("chat_id"),
        "message_id": payload.get("id"),
        "session_id": payload.get("session_id"),
        "tool_ids": payload.get("tool_ids"),
        "skill_ids": payload.get("skill_ids"),
        "files": payload.get("files"),
        "features": payload.get("features"),
        "variables": payload.get("variables"),
        "external_gateway": {
            "client_id": client.id,
            "protocol": protocol,
            "endpoint": endpoint,
            "allow_tools": client.allow_tools,
        },
        "model": model,
    }


def _strip_disallowed_tools(payload: dict, allow_tools: bool) -> dict:
    if allow_tools:
        return payload
    next_payload = dict(payload)
    next_payload.pop("tools", None)
    next_payload.pop("tool_choice", None)
    if isinstance(next_payload.get("params"), dict):
        params = dict(next_payload["params"])
        params.pop("function_calling", None)
        next_payload["params"] = params
    return next_payload


def _build_audit_payload(data: object, status_code: int, error: Optional[str] = None) -> tuple[Optional[int], Optional[int], bool]:
    prompt_tokens, completion_tokens = _coerce_usage(data)
    tools_used = False
    if isinstance(data, dict):
        choices = data.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                message = choice.get("message") if isinstance(choice, dict) else None
                if isinstance(message, dict) and message.get("tool_calls"):
                    tools_used = True
                    break
    return prompt_tokens, completion_tokens, tools_used


def _audit_log(
    *,
    request: Request,
    client,
    owner_user,
    protocol: str,
    endpoint: str,
    model: Optional[str],
    status_code: int,
    latency_ms: int,
    data: object = None,
    error: Optional[str] = None,
):
    prompt_tokens, completion_tokens, tools_used = _build_audit_payload(data, status_code, error)
    ExternalApiAuditLogs.create(
        client_id=client.id,
        owner_user_id=owner_user.id,
        protocol=protocol,
        endpoint=endpoint,
        model=model,
        status_code=status_code,
        tools_used=tools_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        ip_address=(request.client.host if request.client else None),
        error=error,
        meta=None,
    )


@router.get("/config", response_model=ExternalApiGatewayConfigResponse)
async def get_external_api_gateway_config(user=Depends(get_admin_user)):
    return ExternalApiGatewayConfigResponse(
        enabled=bool(ENABLE_EXTERNAL_CLIENT_GATEWAY.value),
        protocols={
            "openai": bool(EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI.value),
            "anthropic": bool(EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC.value),
        },
        default_rpm_limit=int(EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM.value),
    )


@router.post("/config", response_model=ExternalApiGatewayConfigResponse)
async def update_external_api_gateway_config(
    form_data: ExternalApiGatewayConfigForm, user=Depends(get_admin_user)
):
    if form_data.enabled is not None:
        ENABLE_EXTERNAL_CLIENT_GATEWAY.value = form_data.enabled
        ENABLE_EXTERNAL_CLIENT_GATEWAY.save()
    if form_data.openai is not None:
        EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI.value = form_data.openai
        EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI.save()
    if form_data.anthropic is not None:
        EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC.value = form_data.anthropic
        EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC.save()
    if form_data.default_rpm_limit is not None:
        EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM.value = int(form_data.default_rpm_limit)
        EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM.save()
    return await get_external_api_gateway_config(user)


@router.get("/clients", response_model=list[ExternalApiClientResponse])
async def list_external_api_clients(user=Depends(get_admin_user)):
    return [_client_to_response(client) for client in ExternalApiClients.list()]


@router.post("/clients", response_model=ExternalApiClientCreateResponse)
async def create_external_api_client(form_data: ExternalApiClientCreateForm, user=Depends(get_admin_user)):
    client, raw_key = ExternalApiClients.create(form_data)
    return ExternalApiClientCreateResponse(client=_client_to_response(client), api_key=raw_key)


@router.post("/clients/{client_id}", response_model=ExternalApiClientResponse)
async def update_external_api_client(
    client_id: str, form_data: ExternalApiClientUpdateForm, user=Depends(get_admin_user)
):
    client = ExternalApiClients.update(client_id, form_data)
    if not client:
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    return _client_to_response(client)


@router.delete("/clients/{client_id}", response_model=bool)
async def delete_external_api_client(client_id: str, user=Depends(get_admin_user)):
    if not ExternalApiClients.delete(client_id):
        raise HTTPException(status_code=404, detail=ERROR_MESSAGES.NOT_FOUND)
    return True


@router.get("/clients/{client_id}/logs")
async def list_external_api_client_logs(
    client_id: str, limit: int = 100, user=Depends(get_admin_user)
):
    return ExternalApiAuditLogs.list_by_client(client_id, limit=limit)


@router.get("/logs")
async def list_external_api_logs(limit: int = 100, user=Depends(get_admin_user)):
    return ExternalApiAuditLogs.list(limit=limit)


@router.get("/gateway/openai/v1/models")
async def gateway_openai_models(request: Request):
    start = time.time()
    client = None
    owner_user = None
    try:
        client, owner_user = await _authenticate_gateway_request(request, "openai")
        request_models = await _load_gateway_models(request, owner_user)
        allowed = set(client.allowed_model_ids or [])
        visible = []
        for model_id, model in request_models.items():
            candidate_ids = {
                str(model_id),
                str(model.get("id") or ""),
                str(model.get("base_model_id") or ""),
                str(get_model_selection_id(model) or ""),
            }
            candidate_ids.update({str(value) for value in get_model_aliases(model) if value})
            if candidate_ids & allowed:
                visible.append(model)
        response = {"data": visible}
        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="openai",
            endpoint="/models",
            model=None,
            status_code=200,
            latency_ms=int((time.time() - start) * 1000),
            data=response,
        )
        return response
    except HTTPException as exc:
        if client and owner_user:
            _audit_log(
                request=request,
                client=client,
                owner_user=owner_user,
                protocol="openai",
                endpoint="/models",
                model=None,
                status_code=exc.status_code,
                latency_ms=int((time.time() - start) * 1000),
                error=str(exc.detail),
            )
        raise


@router.post("/gateway/openai/v1/chat/completions")
async def gateway_openai_chat_completions(request: Request, form_data: dict):
    start = time.time()
    client, owner_user = await _authenticate_gateway_request(request, "openai")
    request_models = await _load_gateway_models(request, owner_user)
    requested_model = str(form_data.get("model") or "")
    model_entry = _ensure_model_allowed(client, request_models, requested_model)
    if _tools_requested(form_data) and not client.allow_tools:
        raise HTTPException(status_code=403, detail="Tool calling is disabled for this external client")

    form_data = _strip_disallowed_tools(form_data, client.allow_tools)
    request.state.MODELS = request_models
    request.state.MODELS_AMBIGUOUS = set()
    request.state.model = model_entry
    request.state.connection_user = owner_user
    request.state.metadata = _build_gateway_metadata(
        client, owner_user, "openai", "/chat/completions", form_data, model_entry
    )
    form_data["metadata"] = request.state.metadata

    try:
        response = await openai_router.generate_chat_completion(
            request=request,
            form_data=form_data,
            user=owner_user,
            bypass_filter=False,
        )
        latency_ms = int((time.time() - start) * 1000)
        if isinstance(response, StreamingResponse):
            _audit_log(
                request=request,
                client=client,
                owner_user=owner_user,
                protocol="openai",
                endpoint="/chat/completions",
                model=requested_model,
                status_code=200,
                latency_ms=latency_ms,
                data={"choices": [{"message": {"tool_calls": form_data.get("tools") or []}}]},
            )
            return response
        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="openai",
            endpoint="/chat/completions",
            model=requested_model,
            status_code=200,
            latency_ms=latency_ms,
            data=response,
        )
        return response
    except HTTPException as exc:
        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="openai",
            endpoint="/chat/completions",
            model=requested_model,
            status_code=exc.status_code,
            latency_ms=int((time.time() - start) * 1000),
            error=str(exc.detail),
        )
        raise


@router.post("/gateway/openai/v1/responses")
async def gateway_openai_responses(request: Request, form_data: dict):
    start = time.time()
    client, owner_user = await _authenticate_gateway_request(request, "openai")
    requested_model = str(form_data.get("model") or "")
    request_models = await _load_gateway_models(request, owner_user)
    model_entry = _ensure_model_allowed(client, request_models, requested_model)
    if _tools_requested(form_data) and not client.allow_tools:
        raise HTTPException(status_code=403, detail="Tool calling is disabled for this external client")

    connection_user = owner_user
    base_urls, keys, cfgs = openai_router._get_openai_user_config(connection_user)
    if not base_urls:
        raise HTTPException(status_code=404, detail="No connections configured")

    model_ref = openai_router.get_model_ref_from_model(model_entry)
    idx, url, key, api_config = openai_router._resolve_openai_connection_by_model_id(
        requested_model,
        base_urls,
        keys,
        cfgs,
        model_ref=model_ref,
        request_models=request_models,
    )
    if not url:
        raise HTTPException(status_code=404, detail="Connection not found")

    if api_config.get("_resolved_model_id"):
        form_data["model"] = api_config["_resolved_model_id"]

    form_data = _strip_disallowed_tools(form_data, client.allow_tools)
    payload = dict(form_data)
    payload = openai_router.merge_additive_payload_fields(
        payload,
        payload.pop("custom_params", None),
        forbidden_keys=openai_router._CUSTOM_PARAM_FORBIDDEN_KEYS,
    )
    headers = openai_router._build_upstream_headers(url, key or "", api_config, user=owner_user)
    request_url = f"{(url or '').rstrip('/')}/responses"
    payload_json = json.dumps(payload, ensure_ascii=False, default=str)

    try:
        import aiohttp
        from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL, AIOHTTP_CLIENT_TIMEOUT

        async with aiohttp.ClientSession(
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
        ) as session:
            async with session.post(
                request_url,
                data=payload_json,
                headers=headers,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as upstream:
                if payload.get("stream"):
                    async def passthrough():
                        async for chunk in upstream.content.iter_any():
                            if chunk:
                                yield chunk

                    response = StreamingResponse(
                        passthrough(),
                        media_type=upstream.headers.get("Content-Type", "text/event-stream"),
                        status_code=upstream.status,
                    )
                    _audit_log(
                        request=request,
                        client=client,
                        owner_user=owner_user,
                        protocol="openai",
                        endpoint="/responses",
                        model=requested_model,
                        status_code=upstream.status,
                        latency_ms=int((time.time() - start) * 1000),
                        data={"choices": [{"message": {"tool_calls": payload.get("tools") or []}}]},
                    )
                    return response

                body = await openai_router._safe_read_upstream_body(upstream)
                if upstream.status >= 400:
                    message = openai_router._format_responses_upstream_error(
                        request_url=request_url,
                        status=upstream.status,
                        body=body,
                    )
                    raise HTTPException(status_code=upstream.status, detail=message)

                _audit_log(
                    request=request,
                    client=client,
                    owner_user=owner_user,
                    protocol="openai",
                    endpoint="/responses",
                    model=requested_model,
                    status_code=200,
                    latency_ms=int((time.time() - start) * 1000),
                    data=body,
                )
                return JSONResponse(content=body, status_code=200)
    except HTTPException as exc:
        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="openai",
            endpoint="/responses",
            model=requested_model,
            status_code=exc.status_code,
            latency_ms=int((time.time() - start) * 1000),
            error=str(exc.detail),
        )
        raise


@router.get("/gateway/anthropic/v1/models")
async def gateway_anthropic_models(request: Request):
    start = time.time()
    client = None
    owner_user = None
    try:
        client, owner_user = await _authenticate_gateway_request(request, "anthropic")
        request_models = await _load_gateway_models(request, owner_user)
        allowed = set(client.allowed_model_ids or [])
        visible = []
        for model_id, model in request_models.items():
            candidate_ids = {
                str(model_id),
                str(model.get("id") or ""),
                str(model.get("base_model_id") or ""),
                str(get_model_selection_id(model) or ""),
            }
            candidate_ids.update({str(value) for value in get_model_aliases(model) if value})
            owned_by = str(model.get("owned_by") or "").lower()
            if not (owned_by.startswith("anthropic") or owned_by == "claude"):
                continue
            if candidate_ids & allowed:
                visible.append(model)
        response = {"data": visible}
        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="anthropic",
            endpoint="/models",
            model=None,
            status_code=200,
            latency_ms=int((time.time() - start) * 1000),
            data=response,
        )
        return response
    except HTTPException as exc:
        if client and owner_user:
            _audit_log(
                request=request,
                client=client,
                owner_user=owner_user,
                protocol="anthropic",
                endpoint="/models",
                model=None,
                status_code=exc.status_code,
                latency_ms=int((time.time() - start) * 1000),
                error=str(exc.detail),
            )
        raise


@router.post("/gateway/anthropic/v1/messages")
async def gateway_anthropic_messages(request: Request, form_data: dict):
    start = time.time()
    client, owner_user = await _authenticate_gateway_request(request, "anthropic")
    request_models = await _load_gateway_models(request, owner_user)
    requested_model = str(form_data.get("model") or "")
    model_entry = _ensure_model_allowed(client, request_models, requested_model)

    anthropic_tools = form_data.get("tools")
    if isinstance(anthropic_tools, list) and anthropic_tools and not client.allow_tools:
        raise HTTPException(status_code=403, detail="Tool calling is disabled for this external client")

    payload = {
        "model": requested_model,
        "messages": form_data.get("messages", []),
        "stream": bool(form_data.get("stream", False)),
        "max_tokens": form_data.get("max_tokens"),
    }
    if isinstance(anthropic_tools, list) and client.allow_tools:
        payload["tools"] = anthropic_tools
    if form_data.get("tool_choice") is not None and client.allow_tools:
        payload["tool_choice"] = form_data.get("tool_choice")
    if form_data.get("system") is not None:
        payload["system"] = form_data.get("system")
    if form_data.get("temperature") is not None:
        payload["temperature"] = form_data.get("temperature")
    if form_data.get("metadata") is not None:
        payload["metadata"] = form_data.get("metadata")

    openai_messages = []
    for msg in payload.get("messages", []) or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            openai_messages.append({"role": "user", "content": content})
        elif role == "assistant":
            tool_calls = []
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text") or "")
                    elif block.get("type") == "tool_use":
                        tool_calls.append(
                            {
                                "id": block.get("id") or f"toolu_{int(time.time() * 1000)}",
                                "type": "function",
                                "function": {
                                    "name": block.get("name") or "",
                                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                                },
                            }
                        )
                message = {"role": "assistant", "content": "".join(text_parts)}
                if tool_calls:
                    message["tool_calls"] = tool_calls
                openai_messages.append(message)
            else:
                openai_messages.append({"role": "assistant", "content": content})
        else:
            content_blocks = content if isinstance(content, list) else []
            tool_result_blocks = [b for b in content_blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if tool_result_blocks:
                for block in tool_result_blocks:
                    openai_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id") or "",
                            "content": block.get("content") or "",
                        }
                    )

    openai_payload = {
        "model": requested_model,
        "messages": openai_messages,
        "stream": payload.get("stream", False),
    }
    if "system" in payload:
        system = payload["system"]
        if isinstance(system, str):
            openai_payload["messages"] = [{"role": "system", "content": system}] + openai_payload["messages"]
        elif isinstance(system, list):
            system_text = "".join(
                block.get("text") or ""
                for block in system
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if system_text:
                openai_payload["messages"] = [{"role": "system", "content": system_text}] + openai_payload["messages"]
    if payload.get("tools"):
        converted_tools = []
        for tool in payload["tools"]:
            if not isinstance(tool, dict):
                continue
            converted_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name") or "",
                        "description": tool.get("description") or "",
                        "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
                    },
                }
            )
        openai_payload["tools"] = converted_tools
    if payload.get("tool_choice") is not None:
        openai_payload["tool_choice"] = payload.get("tool_choice")
    if payload.get("temperature") is not None:
        openai_payload["temperature"] = payload.get("temperature")
    if payload.get("max_tokens") is not None:
        openai_payload["max_tokens"] = payload.get("max_tokens")

    request.state.MODELS = request_models
    request.state.MODELS_AMBIGUOUS = set()
    request.state.connection_user = owner_user
    request.state.metadata = _build_gateway_metadata(
        client, owner_user, "anthropic", "/messages", openai_payload, model_entry
    )
    openai_payload["metadata"] = request.state.metadata

    try:
        response = await anthropic_router.generate_chat_completion(
            request=request,
            form_data=openai_payload,
            user=owner_user,
            bypass_filter=False,
        )
        latency_ms = int((time.time() - start) * 1000)
        if isinstance(response, StreamingResponse):
            _audit_log(
                request=request,
                client=client,
                owner_user=owner_user,
                protocol="anthropic",
                endpoint="/messages",
                model=requested_model,
                status_code=200,
                latency_ms=latency_ms,
                data={"choices": [{"message": {"tool_calls": openai_payload.get("tools") or []}}]},
            )
            return response

        if isinstance(response, dict):
            choices = response.get("choices") or []
            message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
            content_blocks = []
            if message.get("content"):
                content_blocks.append({"type": "text", "text": message.get("content")})
            for tc in message.get("tool_calls") or []:
                fn = tc.get("function") or {}
                arguments = fn.get("arguments") or "{}"
                try:
                    parsed_arguments = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
                except Exception:
                    parsed_arguments = {"_raw": arguments}
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "input": parsed_arguments,
                    }
                )
            anthropic_response = {
                "id": response.get("id") or "",
                "type": "message",
                "role": "assistant",
                "model": requested_model,
                "content": content_blocks,
                "stop_reason": (
                    "tool_use"
                    if any(block.get("type") == "tool_use" for block in content_blocks)
                    else "end_turn"
                ),
                "usage": response.get("usage") or {},
            }
            _audit_log(
                request=request,
                client=client,
                owner_user=owner_user,
                protocol="anthropic",
                endpoint="/messages",
                model=requested_model,
                status_code=200,
                latency_ms=latency_ms,
                data=response,
            )
            return JSONResponse(content=anthropic_response, status_code=200)

        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="anthropic",
            endpoint="/messages",
            model=requested_model,
            status_code=200,
            latency_ms=latency_ms,
            data=None,
        )
        return response
    except HTTPException as exc:
        _audit_log(
            request=request,
            client=client,
            owner_user=owner_user,
            protocol="anthropic",
            endpoint="/messages",
            model=requested_model,
            status_code=exc.status_code,
            latency_ms=int((time.time() - start) * 1000),
            error=str(exc.detail),
        )
        raise
