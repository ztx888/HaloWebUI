from typing import Optional
from urllib.parse import urlparse


NATIVE_WEB_SEARCH_STATUS_SUPPORTED = "supported"
NATIVE_WEB_SEARCH_STATUS_UNKNOWN = "unknown"
NATIVE_WEB_SEARCH_STATUS_UNSUPPORTED = "unsupported"


def _get_hostname(value: str) -> str:
    try:
        return (urlparse(value).hostname or "").strip().lower()
    except Exception:
        return ""


def is_official_openai_connection(url: str) -> bool:
    host = _get_hostname(url)
    return host == "api.openai.com" or host.endswith(".openai.com")


def is_official_gemini_connection(url: str) -> bool:
    host = _get_hostname(url)
    return host == "generativelanguage.googleapis.com"


def build_native_web_search_support(
    provider: str,
    *,
    url: str = "",
    api_config: Optional[dict] = None,
    connection_name: Optional[str] = None,
) -> dict:
    normalized_provider = str(provider or "").strip().lower()
    config = api_config if isinstance(api_config, dict) else {}
    explicit = config.get("native_web_search_enabled")
    configured = explicit if isinstance(explicit, bool) else None

    if normalized_provider == "openai":
        official = is_official_openai_connection(url)
        if configured is True:
            status = NATIVE_WEB_SEARCH_STATUS_SUPPORTED
            reason = "connection_enabled"
            source = "connection_config"
        elif configured is False:
            status = NATIVE_WEB_SEARCH_STATUS_UNSUPPORTED
            reason = "connection_disabled"
            source = "connection_config"
        elif official:
            status = NATIVE_WEB_SEARCH_STATUS_SUPPORTED
            reason = "official_connection"
            source = "official_connection"
        else:
            status = NATIVE_WEB_SEARCH_STATUS_UNKNOWN
            reason = "compat_connection_unverified"
            source = "connection_inference"

        return {
            "provider": normalized_provider,
            "status": status,
            "reason": reason,
            "source": source,
            "official": official,
            "configured": configured,
            "supported": status == NATIVE_WEB_SEARCH_STATUS_SUPPORTED,
            "can_attempt": status
            in {
                NATIVE_WEB_SEARCH_STATUS_SUPPORTED,
                NATIVE_WEB_SEARCH_STATUS_UNKNOWN,
            },
            **({"connection_name": connection_name} if connection_name else {}),
        }

    if normalized_provider in {"google", "gemini"}:
        official = is_official_gemini_connection(url)
        if configured is True:
            status = NATIVE_WEB_SEARCH_STATUS_SUPPORTED
            reason = "connection_enabled"
            source = "connection_config"
        elif configured is False:
            status = NATIVE_WEB_SEARCH_STATUS_UNSUPPORTED
            reason = "connection_disabled"
            source = "connection_config"
        elif official:
            status = NATIVE_WEB_SEARCH_STATUS_SUPPORTED
            reason = "official_connection"
            source = "official_connection"
        else:
            status = NATIVE_WEB_SEARCH_STATUS_UNKNOWN
            reason = "compat_connection_unverified"
            source = "connection_inference"

        return {
            "provider": "gemini",
            "status": status,
            "reason": reason,
            "source": source,
            "official": official,
            "configured": configured,
            "supported": status == NATIVE_WEB_SEARCH_STATUS_SUPPORTED,
            "can_attempt": status
            in {
                NATIVE_WEB_SEARCH_STATUS_SUPPORTED,
                NATIVE_WEB_SEARCH_STATUS_UNKNOWN,
            },
            **({"connection_name": connection_name} if connection_name else {}),
        }

    return {
        "provider": normalized_provider or "unknown",
        "status": NATIVE_WEB_SEARCH_STATUS_UNSUPPORTED,
        "reason": "provider_not_supported",
        "source": "provider_capability",
        "official": False,
        "configured": configured,
        "supported": False,
        "can_attempt": False,
        **({"connection_name": connection_name} if connection_name else {}),
    }
