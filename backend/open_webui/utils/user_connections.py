"""
Per-user external connections (account-level).

This repo historically had two parallel concepts:
1) Admin-only *global* provider configs stored in app.state.config (OpenAI/Gemini/Anthropic/Ollama).
2) User "directConnections" stored in user.settings.ui that were used by the browser to call
   OpenAI-compatible endpoints directly.

We are converging to a simpler model:
- Every user (including admins) owns their own connections and keys.
- Admins can still share *models* via access_control (public/private), but the key remains private.

This module provides:
- A single canonical location in user settings: user.settings.ui.connections
- Safe, automatic migration from legacy settings/global configs (without deleting legacy data).
"""

from __future__ import annotations

from copy import deepcopy
import secrets
from typing import Any, Optional
from urllib.parse import urlparse

from open_webui.utils.model_identity import derive_connection_id
from open_webui.models.users import UserModel, UserSettings, Users


UI_KEY = "ui"
CONNECTIONS_KEY = "connections"
LEGACY_GLOBAL_CONNECTIONS_SEEDED_KEY = "_legacy_global_connections_seeded_v1"
CONNECTION_ID_TOMBSTONES_KEY = "_HALO_CONNECTION_ID_TOMBSTONES"
CONNECTION_ID_TOMBSTONES_LIMIT = 50
CONNECTION_PROVIDER_SPECS = {
    "openai": {
        "urls_key": "OPENAI_API_BASE_URLS",
        "keys_key": "OPENAI_API_KEYS",
        "configs_key": "OPENAI_API_CONFIGS",
    },
    "gemini": {
        "urls_key": "GEMINI_API_BASE_URLS",
        "keys_key": "GEMINI_API_KEYS",
        "configs_key": "GEMINI_API_CONFIGS",
    },
    "grok": {
        "urls_key": "GROK_API_BASE_URLS",
        "keys_key": "GROK_API_KEYS",
        "configs_key": "GROK_API_CONFIGS",
    },
    "anthropic": {
        "urls_key": "ANTHROPIC_API_BASE_URLS",
        "keys_key": "ANTHROPIC_API_KEYS",
        "configs_key": "ANTHROPIC_API_CONFIGS",
    },
    "ollama": {
        "urls_key": "OLLAMA_BASE_URLS",
        "keys_key": None,
        "configs_key": "OLLAMA_API_CONFIGS",
    },
}


def _as_dict(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    return {}


def _get_ui_settings(user: Optional[UserModel]) -> dict:
    if not user or not getattr(user, "settings", None):
        return {}
    settings = user.settings
    try:
        # Pydantic model
        ui = getattr(settings, "ui", None)
    except Exception:
        ui = None
    return _as_dict(ui)


def _get_settings_dict(user: Optional[UserModel]) -> dict:
    if not user or not getattr(user, "settings", None):
        return {}
    try:
        # Pydantic model
        return _as_dict(user.settings.model_dump())
    except Exception:
        # Best-effort fallback
        return _as_dict(getattr(user, "settings", None))


def _with_settings(user: UserModel, settings_dict: dict) -> UserModel:
    settings_model = UserSettings.model_validate(settings_dict)
    return user.model_copy(update={"settings": settings_model})


def _merge_missing(dst: dict, src: dict) -> tuple[dict, bool]:
    """
    Shallow-merge only missing keys from src into dst.
    """
    changed = False
    out = dict(dst)
    for k, v in src.items():
        if k not in out:
            out[k] = v
            changed = True
    return out, changed


def _has_provider_values(cfg: Optional[dict], urls_key: str, keys_key: Optional[str], configs_key: str) -> bool:
    if not isinstance(cfg, dict):
        return False
    urls = cfg.get(urls_key) or []
    keys = cfg.get(keys_key) or [] if keys_key else []
    configs = cfg.get(configs_key) or {}

    if isinstance(urls, list) and any(str(u).strip() for u in urls):
        return True
    if isinstance(keys, list) and any(str(k).strip() for k in keys):
        return True
    if isinstance(configs, dict) and len(configs.keys()) > 0:
        return True
    return False


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _normalize_connection_url(url: Any) -> str:
    return _clean_str(url).rstrip("/")


def _default_connection_name(url: str, idx: int) -> str:
    try:
        return urlparse(url).hostname or f"Connection {idx + 1}"
    except Exception:
        return f"Connection {idx + 1}"


def _connection_name(cfg: dict, url: str, idx: int) -> str:
    name = _clean_str(cfg.get("name") or cfg.get("remark"))
    return name or _default_connection_name(url, idx)


def _connection_signature(
    provider: str,
    url: Any,
    cfg: Optional[dict],
    *,
    name: Optional[str] = None,
) -> tuple[str, str, str, str]:
    cfg = _as_dict(cfg)
    return (
        _clean_str(provider).lower(),
        _normalize_connection_url(url),
        _clean_str(cfg.get("auth_type")).lower(),
        _clean_str(name if name is not None else cfg.get("name") or cfg.get("remark")),
    )


def _add_candidate(
    candidates: dict[tuple[str, str, str, str], set[str]],
    signature: tuple[str, str, str, str],
    prefix_id: Any,
) -> None:
    prefix = _clean_str(prefix_id)
    if not prefix:
        return
    candidates.setdefault(signature, set()).add(prefix)


def _unique_candidate(
    candidates: dict[tuple[str, str, str, str], set[str]],
    signature: tuple[str, str, str, str],
) -> str:
    values = candidates.get(signature) or set()
    return next(iter(values)) if len(values) == 1 else ""


def _iter_active_connection_records(provider: str, provider_config: Optional[dict]):
    spec = CONNECTION_PROVIDER_SPECS.get(provider)
    provider_config = _as_dict(provider_config)
    if spec is None:
        return

    urls = [_normalize_connection_url(url) for url in list(provider_config.get(spec["urls_key"]) or [])]
    cfgs = _as_dict(provider_config.get(spec["configs_key"]))

    for idx, url in enumerate(urls):
        if not url:
            continue
        cfg = _as_dict(cfgs.get(str(idx), cfgs.get(url, {})))
        prefix_id = _clean_str(cfg.get("prefix_id"))
        if not prefix_id:
            continue
        name = _connection_name(cfg, url, idx)
        signature = _connection_signature(provider, url, cfg, name=name)
        yield {
            "provider": signature[0],
            "url": signature[1],
            "auth_type": signature[2],
            "name": signature[3],
            "prefix_id": prefix_id,
        }


def _iter_tombstone_records(provider: str, provider_config: Optional[dict]):
    for item in _as_dict(provider_config).get(CONNECTION_ID_TOMBSTONES_KEY) or []:
        if not isinstance(item, dict):
            continue
        prefix_id = _clean_str(item.get("prefix_id"))
        url = _normalize_connection_url(item.get("url"))
        if not prefix_id or not url:
            continue
        yield {
            "provider": _clean_str(item.get("provider") or provider).lower(),
            "url": url,
            "auth_type": _clean_str(item.get("auth_type")).lower(),
            "name": _clean_str(item.get("name")),
            "prefix_id": prefix_id,
        }


def _record_signature(record: dict) -> tuple[str, str, str, str]:
    return (
        _clean_str(record.get("provider")).lower(),
        _normalize_connection_url(record.get("url")),
        _clean_str(record.get("auth_type")).lower(),
        _clean_str(record.get("name")),
    )


def _build_connection_tombstones(
    provider: str,
    *,
    existing_provider_config: Optional[dict],
    normalized_provider_config: Optional[dict],
) -> list[dict]:
    records = [
        *list(_iter_tombstone_records(provider, existing_provider_config)),
        *list(_iter_active_connection_records(provider, existing_provider_config)),
        *list(_iter_active_connection_records(provider, normalized_provider_config)),
    ]

    deduped: list[dict] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for record in reversed(records):
        signature = (*_record_signature(record), _clean_str(record.get("prefix_id")))
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(record)

    deduped.reverse()
    return deduped[-CONNECTION_ID_TOMBSTONES_LIMIT:]


def _next_connection_id(
    *,
    id_strategy: str,
    provider: str,
    url: str,
    api_key: Any = None,
    auth_type: Any = None,
    duplicate_seed: str = "",
) -> str:
    if id_strategy == "generated":
        return secrets.token_hex(4)

    source = "personal" if not duplicate_seed else f"personal:{duplicate_seed}"
    return derive_connection_id(
        provider=provider,
        source=source,
        url=url,
        api_key=api_key,
        auth_type=auth_type,
    )


def normalize_provider_connection_config(
    provider: str,
    provider_config: Optional[dict],
    *,
    existing_provider_config: Optional[dict] = None,
    id_strategy: str = "generated",
    update_tombstones: bool = False,
) -> dict:
    spec = CONNECTION_PROVIDER_SPECS.get(provider)
    if spec is None:
        return deepcopy(_as_dict(provider_config))

    current_provider = _as_dict(existing_provider_config)
    next_provider = deepcopy(_as_dict(provider_config))

    urls_key = spec["urls_key"]
    keys_key = spec["keys_key"]
    configs_key = spec["configs_key"]

    urls = [_normalize_connection_url(url) for url in list(next_provider.get(urls_key) or [])]

    if keys_key:
        keys = list(next_provider.get(keys_key) or [])
        if len(keys) > len(urls):
            keys = keys[: len(urls)]
        elif len(keys) < len(urls):
            keys = keys + [""] * (len(urls) - len(keys))
    else:
        keys = []

    next_cfgs = _as_dict(next_provider.get(configs_key))
    current_urls = list(current_provider.get(urls_key) or [])
    current_cfgs = _as_dict(current_provider.get(configs_key))

    prev_prefix_by_index: dict[int, tuple[str, str, str, str]] = {}
    prev_prefix_by_signature: dict[tuple[str, str, str, str], set[str]] = {}
    for idx, prev_url in enumerate(current_urls):
        url_key = _normalize_connection_url(prev_url)
        if not url_key:
            continue
        cfg = _as_dict(current_cfgs.get(str(idx), current_cfgs.get(prev_url, {})))
        prefix_id = _clean_str(cfg.get("prefix_id"))
        if prefix_id:
            auth_type = _clean_str(cfg.get("auth_type")).lower()
            name = _connection_name(cfg, url_key, idx)
            prev_prefix_by_index[idx] = (url_key, auth_type, name, prefix_id)
            _add_candidate(
                prev_prefix_by_signature,
                _connection_signature(provider, url_key, cfg, name=name),
                prefix_id,
            )

    tombstone_prefix_by_signature: dict[tuple[str, str, str, str], set[str]] = {}
    for record in _iter_tombstone_records(provider, current_provider):
        _add_candidate(
            tombstone_prefix_by_signature,
            _record_signature(record),
            record.get("prefix_id"),
        )

    used_prefix_ids: set[str] = set()
    normalized_cfgs: dict[str, dict] = {}
    for idx, url in enumerate(urls):
        cfg = deepcopy(_as_dict(next_cfgs.get(str(idx), next_cfgs.get(url, {}))))
        url_key = _normalize_connection_url(url)

        name = _clean_str(cfg.get("name") or cfg.get("remark"))
        if not name:
            name = _default_connection_name(url, idx)

        prefix_id = _clean_str(cfg.get("prefix_id"))
        auth_type = _clean_str(cfg.get("auth_type")).lower()
        prev_index_match = prev_prefix_by_index.get(idx)
        if (
            not prefix_id
            and prev_index_match
            and prev_index_match[0] == url_key
            and prev_index_match[1] == auth_type
            and prev_index_match[2] == name
        ):
            prefix_id = prev_index_match[3]

        signature = _connection_signature(provider, url_key, cfg, name=name)
        if not prefix_id:
            prefix_id = _unique_candidate(prev_prefix_by_signature, signature)
        if not prefix_id:
            prefix_id = _unique_candidate(tombstone_prefix_by_signature, signature)
        if not prefix_id:
            key_value = (
                keys[idx]
                if keys_key and idx < len(keys)
                else cfg.get("key", None)
            )
            prefix_id = _next_connection_id(
                id_strategy=id_strategy,
                provider=provider,
                url=url,
                api_key=key_value,
                auth_type=cfg.get("auth_type"),
            )

        duplicate_seed = idx
        while not prefix_id or prefix_id in used_prefix_ids:
            prefix_id = _next_connection_id(
                id_strategy=id_strategy,
                provider=provider,
                url=url,
                api_key=(keys[idx] if keys_key and idx < len(keys) else cfg.get("key", None)),
                auth_type=cfg.get("auth_type"),
                duplicate_seed=str(duplicate_seed),
            )
            duplicate_seed += 1
        used_prefix_ids.add(prefix_id)

        normalized_cfg = {**cfg, "name": name}
        normalized_cfg["prefix_id"] = prefix_id

        normalized_cfgs[str(idx)] = normalized_cfg

    next_provider[urls_key] = urls
    if keys_key:
        next_provider[keys_key] = keys
    next_provider[configs_key] = normalized_cfgs
    if update_tombstones:
        next_provider[CONNECTION_ID_TOMBSTONES_KEY] = _build_connection_tombstones(
            provider,
            existing_provider_config=current_provider,
            normalized_provider_config=next_provider,
        )
    return next_provider


def normalize_connections_payload(
    connections: Optional[dict],
    *,
    existing_connections: Optional[dict] = None,
    id_strategy: str = "generated",
    update_tombstones: bool = False,
) -> dict:
    current_connections = _as_dict(existing_connections)
    next_connections = deepcopy(_as_dict(connections))

    for provider, spec in CONNECTION_PROVIDER_SPECS.items():
        provider_config = next_connections.get(provider)
        if not _has_provider_values(
            provider_config,
            spec["urls_key"],
            spec["keys_key"],
            spec["configs_key"],
        ):
            continue

        next_connections[provider] = normalize_provider_connection_config(
            provider,
            provider_config,
            existing_provider_config=current_connections.get(provider),
            id_strategy=id_strategy,
            update_tombstones=update_tombstones,
        )

    return next_connections


def build_migrated_user_settings(
    settings_dict: Optional[dict],
    *,
    is_admin: bool = False,
    global_provider_defaults: Optional[dict[str, dict]] = None,
    id_strategy: str = "derived",
) -> tuple[dict, bool]:
    next_settings = deepcopy(_as_dict(settings_dict))
    ui = _as_dict(next_settings.get(UI_KEY))
    connections = _as_dict(ui.get(CONNECTIONS_KEY))
    legacy_global_seeded = bool(ui.get(LEGACY_GLOBAL_CONNECTIONS_SEEDED_KEY))
    changed = False

    legacy_direct = ui.get("directConnections")
    if isinstance(legacy_direct, dict) and "openai" not in connections:
        if _has_provider_values(
            legacy_direct, "OPENAI_API_BASE_URLS", "OPENAI_API_KEYS", "OPENAI_API_CONFIGS"
        ):
            connections["openai"] = deepcopy(legacy_direct)
            changed = True

    if is_admin and not legacy_global_seeded and global_provider_defaults:
        for provider, provider_config in global_provider_defaults.items():
            spec = CONNECTION_PROVIDER_SPECS.get(provider)
            if spec is None or provider in connections:
                continue
            if _has_provider_values(
                provider_config,
                spec["urls_key"],
                spec["keys_key"],
                spec["configs_key"],
            ):
                connections[provider] = deepcopy(provider_config)
                changed = True

        legacy_global_seeded = True
        changed = True

    normalized_connections = normalize_connections_payload(
        connections,
        existing_connections=connections,
        id_strategy=id_strategy,
    )
    if normalized_connections != connections:
        changed = True

    if not changed:
        return next_settings, False

    ui[CONNECTIONS_KEY] = normalized_connections
    if legacy_global_seeded:
        ui[LEGACY_GLOBAL_CONNECTIONS_SEEDED_KEY] = True
    next_settings[UI_KEY] = ui
    return next_settings, True


def maybe_migrate_user_connections(request, user: UserModel) -> UserModel:
    """
    Ensure user.settings.ui.connections exists and migrate legacy data into it.

    Migration rules:
    - Never delete legacy fields (e.g. ui.directConnections) or global configs.
    - Only fill missing provider configs from legacy ui.directConnections.
    - Admin users: backfill missing legacy global provider configs once, then stop auto-seeding.
    - All users: seed OpenAI-compatible connections from legacy ui.directConnections if present.
    - Read paths must stay read-only: return an in-memory migrated view instead of mutating DB.
    """

    cfg = getattr(getattr(request, "app", None), "state", None)
    cfg = getattr(cfg, "config", None)
    global_provider_defaults = None
    if cfg is not None:
        global_provider_defaults = {
            "openai": {
                "OPENAI_API_BASE_URLS": deepcopy(getattr(cfg, "OPENAI_API_BASE_URLS", []) or []),
                "OPENAI_API_KEYS": deepcopy(getattr(cfg, "OPENAI_API_KEYS", []) or []),
                "OPENAI_API_CONFIGS": deepcopy(getattr(cfg, "OPENAI_API_CONFIGS", {}) or {}),
            },
            "gemini": {
                "GEMINI_API_BASE_URLS": deepcopy(getattr(cfg, "GEMINI_API_BASE_URLS", []) or []),
                "GEMINI_API_KEYS": deepcopy(getattr(cfg, "GEMINI_API_KEYS", []) or []),
                "GEMINI_API_CONFIGS": deepcopy(getattr(cfg, "GEMINI_API_CONFIGS", {}) or {}),
            },
            "grok": {
                "GROK_API_BASE_URLS": deepcopy(getattr(cfg, "GROK_API_BASE_URLS", []) or []),
                "GROK_API_KEYS": deepcopy(getattr(cfg, "GROK_API_KEYS", []) or []),
                "GROK_API_CONFIGS": deepcopy(getattr(cfg, "GROK_API_CONFIGS", {}) or {}),
            },
            "anthropic": {
                "ANTHROPIC_API_BASE_URLS": deepcopy(getattr(cfg, "ANTHROPIC_API_BASE_URLS", []) or []),
                "ANTHROPIC_API_KEYS": deepcopy(getattr(cfg, "ANTHROPIC_API_KEYS", []) or []),
                "ANTHROPIC_API_CONFIGS": deepcopy(getattr(cfg, "ANTHROPIC_API_CONFIGS", {}) or {}),
            },
            "ollama": {
                "OLLAMA_BASE_URLS": deepcopy(getattr(cfg, "OLLAMA_BASE_URLS", []) or []),
                "OLLAMA_API_CONFIGS": deepcopy(getattr(cfg, "OLLAMA_API_CONFIGS", {}) or {}),
            },
        }

    next_settings, changed = build_migrated_user_settings(
        _get_settings_dict(user),
        is_admin=getattr(user, "role", None) == "admin",
        global_provider_defaults=global_provider_defaults,
        id_strategy="derived",
    )
    if not changed:
        return user
    return _with_settings(user, next_settings)


def get_user_connections(user: Optional[UserModel]) -> dict:
    """
    Return ui.connections with read-only compatibility normalization.
    """
    ui = _get_ui_settings(user)
    connections = _as_dict(ui.get(CONNECTIONS_KEY))
    return normalize_connections_payload(
        connections,
        existing_connections=connections,
        id_strategy="derived",
    )


def set_user_connection_provider_config(
    user_id: str, provider: str, provider_config: Optional[dict]
) -> Optional[UserModel]:
    """
    Safely update a single provider subtree under user.settings.ui.connections.

    This reads the latest settings from DB, merges only the requested provider,
    and preserves all other ui/settings keys.
    """
    if not user_id or not provider:
        return None

    user = Users.get_user_by_id(user_id)
    if not user:
        return None

    settings = _get_settings_dict(user)
    ui = _as_dict(settings.get(UI_KEY))
    connections = _as_dict(ui.get(CONNECTIONS_KEY))

    connections[provider] = normalize_provider_connection_config(
        provider,
        provider_config,
        existing_provider_config=connections.get(provider),
        id_strategy="generated",
        update_tombstones=True,
    )
    ui[CONNECTIONS_KEY] = connections

    updated = Users.update_user_settings_by_id(user_id, {UI_KEY: ui})
    return updated or user
