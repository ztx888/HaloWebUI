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
from typing import Any, Optional

from open_webui.models.users import Users, UserModel


UI_KEY = "ui"
CONNECTIONS_KEY = "connections"


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


def maybe_migrate_user_connections(request, user: UserModel) -> UserModel:
    """
    Ensure user.settings.ui.connections exists and migrate legacy data into it.

    Migration rules:
    - Never delete legacy fields (e.g. ui.directConnections) or global configs.
    - Only fill missing provider configs in ui.connections.
    - Admin users: if no per-user provider configs exist yet, seed them from global app.state.config.
    - All users: seed OpenAI-compatible connections from legacy ui.directConnections if present.
    """

    ui = _get_ui_settings(user)
    connections = _as_dict(ui.get(CONNECTIONS_KEY))

    changed = False

    # 1) Migrate legacy per-user OpenAI-compatible directConnections -> ui.connections.openai
    legacy_direct = ui.get("directConnections")
    if isinstance(legacy_direct, dict):
        if "openai" not in connections and _has_provider_values(
            legacy_direct, "OPENAI_API_BASE_URLS", "OPENAI_API_KEYS", "OPENAI_API_CONFIGS"
        ):
            connections["openai"] = deepcopy(legacy_direct)
            changed = True

    # 2) Admin seeding from global configs (one-time, only when provider key is missing)
    if getattr(user, "role", None) == "admin":
        cfg = getattr(getattr(request, "app", None), "state", None)
        cfg = getattr(cfg, "config", None)
        if cfg is not None:
            global_openai = {
                "OPENAI_API_BASE_URLS": deepcopy(getattr(cfg, "OPENAI_API_BASE_URLS", []) or []),
                "OPENAI_API_KEYS": deepcopy(getattr(cfg, "OPENAI_API_KEYS", []) or []),
                "OPENAI_API_CONFIGS": deepcopy(getattr(cfg, "OPENAI_API_CONFIGS", {}) or {}),
            }
            global_gemini = {
                "GEMINI_API_BASE_URLS": deepcopy(getattr(cfg, "GEMINI_API_BASE_URLS", []) or []),
                "GEMINI_API_KEYS": deepcopy(getattr(cfg, "GEMINI_API_KEYS", []) or []),
                "GEMINI_API_CONFIGS": deepcopy(getattr(cfg, "GEMINI_API_CONFIGS", {}) or {}),
            }
            global_anthropic = {
                "ANTHROPIC_API_BASE_URLS": deepcopy(getattr(cfg, "ANTHROPIC_API_BASE_URLS", []) or []),
                "ANTHROPIC_API_KEYS": deepcopy(getattr(cfg, "ANTHROPIC_API_KEYS", []) or []),
                "ANTHROPIC_API_CONFIGS": deepcopy(getattr(cfg, "ANTHROPIC_API_CONFIGS", {}) or {}),
            }
            global_ollama = {
                "OLLAMA_BASE_URLS": deepcopy(getattr(cfg, "OLLAMA_BASE_URLS", []) or []),
                "OLLAMA_API_CONFIGS": deepcopy(getattr(cfg, "OLLAMA_API_CONFIGS", {}) or {}),
            }

            # Only seed missing keys. If openai already came from legacy_direct, keep it.
            if "openai" not in connections and _has_provider_values(
                global_openai, "OPENAI_API_BASE_URLS", "OPENAI_API_KEYS", "OPENAI_API_CONFIGS"
            ):
                connections["openai"] = global_openai
                changed = True
            if "gemini" not in connections and _has_provider_values(
                global_gemini, "GEMINI_API_BASE_URLS", "GEMINI_API_KEYS", "GEMINI_API_CONFIGS"
            ):
                connections["gemini"] = global_gemini
                changed = True
            if "anthropic" not in connections and _has_provider_values(
                global_anthropic, "ANTHROPIC_API_BASE_URLS", "ANTHROPIC_API_KEYS", "ANTHROPIC_API_CONFIGS"
            ):
                connections["anthropic"] = global_anthropic
                changed = True
            if "ollama" not in connections and _has_provider_values(
                global_ollama, "OLLAMA_BASE_URLS", None, "OLLAMA_API_CONFIGS"
            ):
                connections["ollama"] = global_ollama
                changed = True

    if not changed:
        return user

    next_ui = dict(ui)
    next_ui[CONNECTIONS_KEY] = connections

    # Preserve any other ui keys.
    next_settings = {UI_KEY: next_ui}
    updated = Users.update_user_settings_by_id(user.id, next_settings)
    return updated or user


def get_user_connections(user: Optional[UserModel]) -> dict:
    """
    Return ui.connections dict. Call maybe_migrate_user_connections() earlier to ensure it's present.
    """
    ui = _get_ui_settings(user)
    return _as_dict(ui.get(CONNECTIONS_KEY))


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

    connections[provider] = deepcopy(_as_dict(provider_config))
    ui[CONNECTIONS_KEY] = connections

    updated = Users.update_user_settings_by_id(user_id, {UI_KEY: ui})
    return updated or user
