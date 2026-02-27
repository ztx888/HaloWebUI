import logging
import copy
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import aiohttp

from typing import Optional, Literal

from open_webui.env import AIOHTTP_CLIENT_TIMEOUT
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.config import get_config, save_config
from open_webui.config import BannerModel

from open_webui.utils.tools import (
    get_tool_server_data,
    get_tool_server_url,
    set_tool_servers,
)
from open_webui.utils.mcp.client import MCPClient
from open_webui.models.oauth_sessions import OAuthSessions


from open_webui.utils.oauth import (
    get_discovery_urls,
    get_oauth_client_info_with_dynamic_client_registration,
    encrypt_data,
    decrypt_data,
    OAuthClientInformationFull,
)
from mcp.shared.auth import OAuthMetadata

router = APIRouter()

log = logging.getLogger(__name__)


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
# Connections Config
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
    request: Request,
    form_data: ConnectionsConfigForm,
    user=Depends(get_admin_user),
):
    request.app.state.config.ENABLE_DIRECT_CONNECTIONS = (
        form_data.ENABLE_DIRECT_CONNECTIONS
    )
    request.app.state.config.ENABLE_BASE_MODELS_CACHE = (
        form_data.ENABLE_BASE_MODELS_CACHE
    )

    return {
        "ENABLE_DIRECT_CONNECTIONS": request.app.state.config.ENABLE_DIRECT_CONNECTIONS,
        "ENABLE_BASE_MODELS_CACHE": request.app.state.config.ENABLE_BASE_MODELS_CACHE,
    }


class OAuthClientRegistrationForm(BaseModel):
    url: str
    client_id: str
    client_name: Optional[str] = None


@router.post("/oauth/clients/register")
async def register_oauth_client(
    request: Request,
    form_data: OAuthClientRegistrationForm,
    type: Optional[str] = None,
    user=Depends(get_admin_user),
):
    try:
        oauth_client_id = form_data.client_id
        if type:
            oauth_client_id = f"{type}:{form_data.client_id}"

        oauth_client_info = (
            await get_oauth_client_info_with_dynamic_client_registration(
                request, oauth_client_id, form_data.url
            )
        )
        return {
            "status": True,
            "oauth_client_info": encrypt_data(
                oauth_client_info.model_dump(mode="json")
            ),
        }
    except Exception as e:
        log.debug(f"Failed to register OAuth client: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to register OAuth client",
        )


############################
# ToolServers Config
############################


class ToolServerConnection(BaseModel):
    url: str
    path: str
    type: Optional[str] = "openapi"  # openapi, mcp
    auth_type: Optional[str]
    headers: Optional[dict | str] = None
    key: Optional[str]
    config: Optional[dict]

    model_config = ConfigDict(extra="allow")


class ToolServersConfigForm(BaseModel):
    TOOL_SERVER_CONNECTIONS: list[ToolServerConnection]


@router.get("/tool_servers", response_model=ToolServersConfigForm)
async def get_tool_servers_config(request: Request, user=Depends(get_admin_user)):
    return {
        "TOOL_SERVER_CONNECTIONS": request.app.state.config.TOOL_SERVER_CONNECTIONS,
    }


@router.post("/tool_servers", response_model=ToolServersConfigForm)
async def set_tool_servers_config(
    request: Request,
    form_data: ToolServersConfigForm,
    user=Depends(get_admin_user),
):
    for connection in request.app.state.config.TOOL_SERVER_CONNECTIONS:
        server_type = connection.get("type", "openapi")
        auth_type = connection.get("auth_type", "none")

        if auth_type == "oauth_2.1":
            # Remove existing OAuth clients for tool servers
            server_id = connection.get("info", {}).get("id")
            client_key = f"{server_type}:{server_id}"

            try:
                request.app.state.oauth_client_manager.remove_client(client_key)
            except:
                pass

    # Set new tool server connections
    request.app.state.config.TOOL_SERVER_CONNECTIONS = [
        connection.model_dump() for connection in form_data.TOOL_SERVER_CONNECTIONS
    ]

    await set_tool_servers(request)

    for connection in request.app.state.config.TOOL_SERVER_CONNECTIONS:
        server_type = connection.get("type", "openapi")
        if server_type == "mcp":
            server_id = connection.get("info", {}).get("id")
            auth_type = connection.get("auth_type", "none")

            if auth_type == "oauth_2.1" and server_id:
                try:
                    oauth_client_info = connection.get("info", {}).get(
                        "oauth_client_info", ""
                    )
                    oauth_client_info = decrypt_data(oauth_client_info)

                    request.app.state.oauth_client_manager.add_client(
                        f"{server_type}:{server_id}",
                        OAuthClientInformationFull(**oauth_client_info),
                    )
                except Exception as e:
                    log.debug(f"Failed to add OAuth client for MCP tool server: {e}")
                    continue

    return {
        "TOOL_SERVER_CONNECTIONS": request.app.state.config.TOOL_SERVER_CONNECTIONS,
    }


@router.post("/tool_servers/verify")
async def verify_tool_servers_config(
    request: Request, form_data: ToolServerConnection, user=Depends(get_admin_user)
):
    """
    Verify the connection to the tool server.
    """
    try:
        if form_data.type == "mcp":
            if form_data.auth_type == "oauth_2.1":
                discovery_urls = await get_discovery_urls(form_data.url)
                for discovery_url in discovery_urls:
                    log.debug(
                        f"Trying to fetch OAuth 2.1 discovery document from {discovery_url}"
                    )
                    async with aiohttp.ClientSession(
                        trust_env=True,
                        timeout=aiohttp.ClientTimeout(total=AIOHTTP_CLIENT_TIMEOUT),
                    ) as session:
                        async with session.get(
                            discovery_url
                        ) as oauth_server_metadata_response:
                            if oauth_server_metadata_response.status == 200:
                                try:
                                    oauth_server_metadata = (
                                        OAuthMetadata.model_validate(
                                            await oauth_server_metadata_response.json()
                                        )
                                    )
                                    return {
                                        "status": True,
                                        "oauth_server_metadata": oauth_server_metadata.model_dump(
                                            mode="json"
                                        ),
                                    }
                                except Exception as e:
                                    log.info(
                                        f"Failed to parse OAuth 2.1 discovery document: {e}"
                                    )
                                    raise HTTPException(
                                        status_code=400,
                                        detail=f"Failed to parse OAuth 2.1 discovery document from {discovery_url}",
                                    )

                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to fetch OAuth 2.1 discovery document from {discovery_urls}",
                )
            else:
                try:
                    client = MCPClient()
                    headers = None

                    token = None
                    if form_data.auth_type == "bearer":
                        token = form_data.key
                    elif form_data.auth_type == "session":
                        token = request.state.token.credentials
                    elif form_data.auth_type == "system_oauth":
                        oauth_token = None
                        try:
                            if request.cookies.get("oauth_session_id", None):
                                oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                                    user.id,
                                    request.cookies.get("oauth_session_id", None),
                                )

                                if oauth_token:
                                    token = oauth_token.get("access_token", "")
                        except Exception as e:
                            pass
                    if token:
                        headers = {"Authorization": f"Bearer {token}"}

                    if form_data.headers and isinstance(form_data.headers, dict):
                        if headers is None:
                            headers = {}
                        headers.update(form_data.headers)

                    await client.connect(form_data.url, headers=headers)
                    specs = await client.list_tool_specs()
                    return {
                        "status": True,
                        "specs": specs,
                    }
                except Exception as e:
                    log.debug(f"Failed to create MCP client: {e}")
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to create MCP client",
                    )
                finally:
                    if client:
                        await client.disconnect()
        else:  # openapi
            token = None
            headers = None
            if form_data.auth_type == "bearer":
                token = form_data.key
            elif form_data.auth_type == "session":
                token = request.state.token.credentials
            elif form_data.auth_type == "system_oauth":
                try:
                    if request.cookies.get("oauth_session_id", None):
                        oauth_token = (
                            await request.app.state.oauth_manager.get_oauth_token(
                                user.id,
                                request.cookies.get("oauth_session_id", None),
                            )
                        )

                        if oauth_token:
                            token = oauth_token.get("access_token", "")

                except Exception as e:
                    pass

            if token:
                headers = {"Authorization": f"Bearer {token}"}

            if form_data.headers and isinstance(form_data.headers, dict):
                if headers is None:
                    headers = {}
                headers.update(form_data.headers)

            url = get_tool_server_url(form_data.url, form_data.path)
            return await get_tool_server_data(url, headers=headers)
    except HTTPException as e:
        raise e
    except Exception as e:
        log.debug(f"Failed to connect to the tool server: {e}")
        raise HTTPException(
            status_code=400,
            detail=f"Failed to connect to the tool server",
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
    CODE_INTERPRETER_PROMPT_TEMPLATE: Optional[str]
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
        "CODE_INTERPRETER_PROMPT_TEMPLATE": request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE,
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
    request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE = (
        form_data.CODE_INTERPRETER_PROMPT_TEMPLATE
    )

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
        "CODE_INTERPRETER_PROMPT_TEMPLATE": request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE,
        "CODE_INTERPRETER_JUPYTER_URL": request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
        "CODE_INTERPRETER_JUPYTER_AUTH": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH,
        "CODE_INTERPRETER_JUPYTER_AUTH_TOKEN": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN,
        "CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD": request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD,
        "CODE_INTERPRETER_JUPYTER_TIMEOUT": request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
    }


############################
# SetDefaultModels
############################
class ModelsConfigForm(BaseModel):
    DEFAULT_MODELS: Optional[str]
    DEFAULT_PINNED_MODELS: Optional[str]
    MODEL_ORDER_LIST: Optional[list[str]]
    DEFAULT_MODEL_METADATA: Optional[dict] = None
    DEFAULT_MODEL_PARAMS: Optional[dict] = None


@router.get("/models", response_model=ModelsConfigForm)
async def get_models_config(request: Request, user=Depends(get_admin_user)):
    return {
        "DEFAULT_MODELS": request.app.state.config.DEFAULT_MODELS,
        "DEFAULT_PINNED_MODELS": request.app.state.config.DEFAULT_PINNED_MODELS,
        "MODEL_ORDER_LIST": request.app.state.config.MODEL_ORDER_LIST,
        "DEFAULT_MODEL_METADATA": request.app.state.config.DEFAULT_MODEL_METADATA,
        "DEFAULT_MODEL_PARAMS": request.app.state.config.DEFAULT_MODEL_PARAMS,
    }


@router.post("/models", response_model=ModelsConfigForm)
async def set_models_config(
    request: Request, form_data: ModelsConfigForm, user=Depends(get_admin_user)
):
    request.app.state.config.DEFAULT_MODELS = form_data.DEFAULT_MODELS
    request.app.state.config.DEFAULT_PINNED_MODELS = form_data.DEFAULT_PINNED_MODELS
    request.app.state.config.MODEL_ORDER_LIST = form_data.MODEL_ORDER_LIST
    request.app.state.config.DEFAULT_MODEL_METADATA = form_data.DEFAULT_MODEL_METADATA
    request.app.state.config.DEFAULT_MODEL_PARAMS = form_data.DEFAULT_MODEL_PARAMS
    return {
        "DEFAULT_MODELS": request.app.state.config.DEFAULT_MODELS,
        "DEFAULT_PINNED_MODELS": request.app.state.config.DEFAULT_PINNED_MODELS,
        "MODEL_ORDER_LIST": request.app.state.config.MODEL_ORDER_LIST,
        "DEFAULT_MODEL_METADATA": request.app.state.config.DEFAULT_MODEL_METADATA,
        "DEFAULT_MODEL_PARAMS": request.app.state.config.DEFAULT_MODEL_PARAMS,
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


############################
# Splash Notification
############################


class SplashNotificationConfigForm(BaseModel):
    SPLASH_NOTIFICATION_ENABLED: bool = Field(default=False)
    SPLASH_NOTIFICATION_TITLE: str = Field(default="公告通知")
    SPLASH_NOTIFICATION_CONTENT: str = Field(default="")


@router.get("/splash-notification")
async def get_splash_notification(request: Request):
    """Public endpoint - no auth required so notification shows before login"""
    return {
        "enabled": request.app.state.config.SPLASH_NOTIFICATION_ENABLED,
        "title": request.app.state.config.SPLASH_NOTIFICATION_TITLE,
        "content": request.app.state.config.SPLASH_NOTIFICATION_CONTENT,
    }


@router.get("/splash-notification/admin", response_model=SplashNotificationConfigForm)
async def get_splash_notification_admin(
    request: Request, _=Depends(get_admin_user)
):
    return {
        "SPLASH_NOTIFICATION_ENABLED": request.app.state.config.SPLASH_NOTIFICATION_ENABLED,
        "SPLASH_NOTIFICATION_TITLE": request.app.state.config.SPLASH_NOTIFICATION_TITLE,
        "SPLASH_NOTIFICATION_CONTENT": request.app.state.config.SPLASH_NOTIFICATION_CONTENT,
    }


@router.post("/splash-notification/admin", response_model=SplashNotificationConfigForm)
async def set_splash_notification_admin(
    request: Request,
    form_data: SplashNotificationConfigForm,
    _=Depends(get_admin_user),
):
    request.app.state.config.SPLASH_NOTIFICATION_ENABLED = (
        form_data.SPLASH_NOTIFICATION_ENABLED
    )
    request.app.state.config.SPLASH_NOTIFICATION_TITLE = (
        form_data.SPLASH_NOTIFICATION_TITLE
    )
    request.app.state.config.SPLASH_NOTIFICATION_CONTENT = (
        form_data.SPLASH_NOTIFICATION_CONTENT
    )
    return {
        "SPLASH_NOTIFICATION_ENABLED": request.app.state.config.SPLASH_NOTIFICATION_ENABLED,
        "SPLASH_NOTIFICATION_TITLE": request.app.state.config.SPLASH_NOTIFICATION_TITLE,
        "SPLASH_NOTIFICATION_CONTENT": request.app.state.config.SPLASH_NOTIFICATION_CONTENT,
    }


############################
# Usage
############################


class UsageConfigForm(BaseModel):
    CREDIT_NO_CHARGE_EMPTY_RESPONSE: bool = Field(default=False)
    CREDIT_NO_CREDIT_MSG: str = Field(default="余额不足，请前往 设置-积分 充值")
    CREDIT_EXCHANGE_RATIO: float = Field(default=1, gt=0)
    CREDIT_DEFAULT_CREDIT: float = Field(default=0, ge=0)
    USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE: str = Field(default="")
    USAGE_DEFAULT_ENCODING_MODEL: str = Field(default="gpt-4o")
    USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE: float = Field(default=0, ge=0)
    USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE: float = Field(default=0, ge=0)
    USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE: float = Field(default=0, ge=0)
    USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE: float = Field(default=0, ge=0)
    USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE: float = Field(default=0, ge=0)
    USAGE_CALCULATE_MINIMUM_COST: float = Field(default=0, ge=0)
    USAGE_CUSTOM_PRICE_CONFIG: str = Field(default="[]")
    EZFP_PAY_PRIORITY: Literal["qrcode", "link"] = Field(default="qrcode")
    EZFP_ENDPOINT: Optional[str] = None
    EZFP_PID: Optional[str] = None
    EZFP_KEY: Optional[str] = None
    EZFP_CALLBACK_HOST: Optional[str] = None
    EZFP_AMOUNT_CONTROL: Optional[str] = None
    ALIPAY_SERVER_URL: Optional[str] = None
    ALIPAY_APP_ID: Optional[str] = None
    ALIPAY_APP_PRIVATE_KEY: Optional[str] = None
    ALIPAY_ALIPAY_PUBLIC_KEY: Optional[str] = None
    ALIPAY_CALLBACK_HOST: Optional[str] = None
    ALIPAY_AMOUNT_CONTROL: Optional[str] = None
    ALIPAY_PRODUCT_CODE: Optional[str] = None


@router.get("/usage", response_model=UsageConfigForm)
async def get_usage_config(request: Request, _=Depends(get_admin_user)):
    return {
        "CREDIT_NO_CHARGE_EMPTY_RESPONSE": request.app.state.config.CREDIT_NO_CHARGE_EMPTY_RESPONSE,
        "CREDIT_NO_CREDIT_MSG": request.app.state.config.CREDIT_NO_CREDIT_MSG,
        "CREDIT_EXCHANGE_RATIO": request.app.state.config.CREDIT_EXCHANGE_RATIO,
        "CREDIT_DEFAULT_CREDIT": request.app.state.config.CREDIT_DEFAULT_CREDIT,
        "USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE": request.app.state.config.USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE,
        "USAGE_DEFAULT_ENCODING_MODEL": request.app.state.config.USAGE_DEFAULT_ENCODING_MODEL,
        "USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE": request.app.state.config.USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE,
        "USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE,
        "USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE,
        "USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE,
        "USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE,
        "USAGE_CALCULATE_MINIMUM_COST": request.app.state.config.USAGE_CALCULATE_MINIMUM_COST,
        "USAGE_CUSTOM_PRICE_CONFIG": request.app.state.config.USAGE_CUSTOM_PRICE_CONFIG,
        "EZFP_PAY_PRIORITY": request.app.state.config.EZFP_PAY_PRIORITY,
        "EZFP_ENDPOINT": request.app.state.config.EZFP_ENDPOINT,
        "EZFP_PID": request.app.state.config.EZFP_PID,
        "EZFP_KEY": request.app.state.config.EZFP_KEY,
        "EZFP_CALLBACK_HOST": request.app.state.config.EZFP_CALLBACK_HOST,
        "EZFP_AMOUNT_CONTROL": request.app.state.config.EZFP_AMOUNT_CONTROL,
        "ALIPAY_SERVER_URL": request.app.state.config.ALIPAY_SERVER_URL,
        "ALIPAY_APP_ID": request.app.state.config.ALIPAY_APP_ID,
        "ALIPAY_APP_PRIVATE_KEY": request.app.state.config.ALIPAY_APP_PRIVATE_KEY,
        "ALIPAY_ALIPAY_PUBLIC_KEY": request.app.state.config.ALIPAY_ALIPAY_PUBLIC_KEY,
        "ALIPAY_CALLBACK_HOST": request.app.state.config.ALIPAY_CALLBACK_HOST,
        "ALIPAY_AMOUNT_CONTROL": request.app.state.config.ALIPAY_AMOUNT_CONTROL,
        "ALIPAY_PRODUCT_CODE": request.app.state.config.ALIPAY_PRODUCT_CODE,
    }


@router.post("/usage", response_model=UsageConfigForm)
async def set_usage_config(
    request: Request, form_data: UsageConfigForm, _=Depends(get_admin_user)
):
    request.app.state.config.CREDIT_NO_CHARGE_EMPTY_RESPONSE = (
        form_data.CREDIT_NO_CHARGE_EMPTY_RESPONSE
    )
    request.app.state.config.CREDIT_NO_CREDIT_MSG = form_data.CREDIT_NO_CREDIT_MSG
    request.app.state.config.CREDIT_EXCHANGE_RATIO = form_data.CREDIT_EXCHANGE_RATIO
    request.app.state.config.CREDIT_DEFAULT_CREDIT = form_data.CREDIT_DEFAULT_CREDIT
    request.app.state.config.USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE = (
        form_data.USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE
    )
    request.app.state.config.USAGE_DEFAULT_ENCODING_MODEL = (
        form_data.USAGE_DEFAULT_ENCODING_MODEL
    )
    request.app.state.config.USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE = (
        form_data.USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE
    )
    request.app.state.config.USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE = (
        form_data.USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE
    )
    request.app.state.config.USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE = (
        form_data.USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE
    )
    request.app.state.config.USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE = (
        form_data.USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE
    )
    request.app.state.config.USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE = (
        form_data.USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE
    )
    request.app.state.config.USAGE_CALCULATE_MINIMUM_COST = (
        form_data.USAGE_CALCULATE_MINIMUM_COST
    )
    request.app.state.config.USAGE_CUSTOM_PRICE_CONFIG = (
        form_data.USAGE_CUSTOM_PRICE_CONFIG
    )
    request.app.state.config.EZFP_PAY_PRIORITY = form_data.EZFP_PAY_PRIORITY
    request.app.state.config.EZFP_ENDPOINT = form_data.EZFP_ENDPOINT
    request.app.state.config.EZFP_PID = form_data.EZFP_PID
    request.app.state.config.EZFP_KEY = form_data.EZFP_KEY
    request.app.state.config.EZFP_CALLBACK_HOST = form_data.EZFP_CALLBACK_HOST
    request.app.state.config.EZFP_AMOUNT_CONTROL = form_data.EZFP_AMOUNT_CONTROL
    request.app.state.config.ALIPAY_SERVER_URL = form_data.ALIPAY_SERVER_URL
    request.app.state.config.ALIPAY_APP_ID = form_data.ALIPAY_APP_ID
    request.app.state.config.ALIPAY_APP_PRIVATE_KEY = form_data.ALIPAY_APP_PRIVATE_KEY
    request.app.state.config.ALIPAY_ALIPAY_PUBLIC_KEY = (
        form_data.ALIPAY_ALIPAY_PUBLIC_KEY
    )
    request.app.state.config.ALIPAY_CALLBACK_HOST = form_data.ALIPAY_CALLBACK_HOST
    request.app.state.config.ALIPAY_AMOUNT_CONTROL = form_data.ALIPAY_AMOUNT_CONTROL
    request.app.state.config.ALIPAY_PRODUCT_CODE = form_data.ALIPAY_PRODUCT_CODE

    return {
        "CREDIT_NO_CHARGE_EMPTY_RESPONSE": request.app.state.config.CREDIT_NO_CHARGE_EMPTY_RESPONSE,
        "CREDIT_NO_CREDIT_MSG": request.app.state.config.CREDIT_NO_CREDIT_MSG,
        "CREDIT_EXCHANGE_RATIO": request.app.state.config.CREDIT_EXCHANGE_RATIO,
        "CREDIT_DEFAULT_CREDIT": request.app.state.config.CREDIT_DEFAULT_CREDIT,
        "USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE": request.app.state.config.USAGE_CALCULATE_MODEL_PREFIX_TO_REMOVE,
        "USAGE_DEFAULT_ENCODING_MODEL": request.app.state.config.USAGE_DEFAULT_ENCODING_MODEL,
        "USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE": request.app.state.config.USAGE_CALCULATE_DEFAULT_EMBEDDING_PRICE,
        "USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_IMAGE_GEN_PRICE,
        "USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_CODE_EXECUTE_PRICE,
        "USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_WEB_SEARCH_PRICE,
        "USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE": request.app.state.config.USAGE_CALCULATE_FEATURE_TOOL_SERVER_PRICE,
        "USAGE_CALCULATE_MINIMUM_COST": request.app.state.config.USAGE_CALCULATE_MINIMUM_COST,
        "USAGE_CUSTOM_PRICE_CONFIG": request.app.state.config.USAGE_CUSTOM_PRICE_CONFIG,
        "EZFP_PAY_PRIORITY": request.app.state.config.EZFP_PAY_PRIORITY,
        "EZFP_ENDPOINT": request.app.state.config.EZFP_ENDPOINT,
        "EZFP_PID": request.app.state.config.EZFP_PID,
        "EZFP_KEY": request.app.state.config.EZFP_KEY,
        "EZFP_CALLBACK_HOST": request.app.state.config.EZFP_CALLBACK_HOST,
        "EZFP_AMOUNT_CONTROL": request.app.state.config.EZFP_AMOUNT_CONTROL,
        "ALIPAY_SERVER_URL": request.app.state.config.ALIPAY_SERVER_URL,
        "ALIPAY_APP_ID": request.app.state.config.ALIPAY_APP_ID,
        "ALIPAY_APP_PRIVATE_KEY": request.app.state.config.ALIPAY_APP_PRIVATE_KEY,
        "ALIPAY_ALIPAY_PUBLIC_KEY": request.app.state.config.ALIPAY_ALIPAY_PUBLIC_KEY,
        "ALIPAY_CALLBACK_HOST": request.app.state.config.ALIPAY_CALLBACK_HOST,
        "ALIPAY_AMOUNT_CONTROL": request.app.state.config.ALIPAY_AMOUNT_CONTROL,
        "ALIPAY_PRODUCT_CODE": request.app.state.config.ALIPAY_PRODUCT_CODE,
    }
