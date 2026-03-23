import asyncio
import copy
import time
import logging
import sys
from typing import Optional

from fastapi import Request

from open_webui.routers import openai, ollama, gemini, anthropic
from open_webui.functions import get_function_models


from open_webui.models.functions import Functions
from open_webui.models.models import Models


from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.access_control import has_access



from open_webui.env import (
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST,
    SRC_LOG_LEVELS,
    GLOBAL_LOG_LEVEL,
)
from open_webui.models.users import UserModel


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


# Per-user base model cache: {user_id: (timestamp, models)}
_base_model_cache: dict[str, tuple[float, list]] = {}
_base_model_refresh_tasks: dict[str, asyncio.Task[list]] = {}
_BASE_MODEL_CACHE_TTL = 5 * 60  # seconds
_BASE_MODEL_FETCH_TIMEOUT = (
    min(float(AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST), 5.0)
    if isinstance(AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST, (int, float))
    else 5.0
)


def _get_base_model_cache_key(user: Optional[UserModel]) -> str:
    return user.id if user else "anon"


def _evict_stale_base_model_cache(now: float) -> None:
    stale_keys = [k for k, (ts, _) in _base_model_cache.items() if now - ts > (_BASE_MODEL_CACHE_TTL * 2)]
    for k in stale_keys:
        _base_model_cache.pop(k, None)


def invalidate_base_model_cache(user_id: Optional[str] = None) -> None:
    cache_keys = (
        [user_id]
        if user_id
        else list(set(_base_model_cache.keys()) | set(_base_model_refresh_tasks.keys()))
    )
    for cache_key in cache_keys:
        _base_model_cache.pop(cache_key, None)
        task = _base_model_refresh_tasks.pop(cache_key, None)
        if task and not task.done():
            task.cancel()


async def _fetch_source_models(name: str, fetch_coro):
    try:
        if _BASE_MODEL_FETCH_TIMEOUT > 0:
            return await asyncio.wait_for(fetch_coro, timeout=_BASE_MODEL_FETCH_TIMEOUT)
        return await fetch_coro
    except asyncio.TimeoutError:
        log.warning(
            f"Base models fetch timed out: {name} after {_BASE_MODEL_FETCH_TIMEOUT:.1f}s"
        )
    except Exception as e:
        log.warning(f"Base models fetch failed: {name}: {type(e).__name__}: {e}")
    return None


def _schedule_base_model_refresh(
    cache_key: str, request: Request, user: Optional[UserModel]
) -> asyncio.Task[list]:
    existing = _base_model_refresh_tasks.get(cache_key)
    if existing and not existing.done():
        return existing

    async def _runner() -> list:
        models = await _fetch_all_base_models(request, user=user)
        now = time.time()
        _base_model_cache[cache_key] = (now, models)
        _evict_stale_base_model_cache(now)
        return models

    task = asyncio.create_task(_runner())
    _base_model_refresh_tasks[cache_key] = task

    def _finalize(done: asyncio.Task[list]) -> None:
        if _base_model_refresh_tasks.get(cache_key) is done:
            _base_model_refresh_tasks.pop(cache_key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning(
                f"Base models background refresh failed: {cache_key}: {type(e).__name__}: {e}"
            )

    task.add_done_callback(_finalize)
    return task


async def _fetch_all_base_models(request: Request, user: UserModel = None):
    # Base models are now user-scoped (per-user connections). Provider routers return [] when
    # the user has no configured connections for that provider.
    # Fetch all providers in parallel and cap individual sources so one slow upstream
    # does not block the entire settings / model-management UI.
    openai_resp, ollama_resp, gemini_resp, anthropic_resp, function_models_resp = (
        await asyncio.gather(
            _fetch_source_models("openai", openai.get_all_models(request, user=user)),
            _fetch_source_models("ollama", ollama.get_all_models(request, user=user)),
            _fetch_source_models("gemini", gemini.get_all_models(request, user=user)),
            _fetch_source_models(
                "anthropic", anthropic.get_all_models(request, user=user)
            ),
            _fetch_source_models("functions", get_function_models(request)),
        )
    )

    # Process openai
    if not isinstance(openai_resp, dict):
        openai_models = []
    else:
        openai_models = openai_resp.get("data", []) if isinstance(openai_resp, dict) else []

    # Process ollama
    if isinstance(ollama_resp, dict) and "models" in ollama_resp:
        ollama_models = [
            {
                "id": model["model"],
                "name": model["name"],
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ollama",
                "ollama": model,
                **(
                    {"connection_name": model.get("connection_name")}
                    if model.get("connection_name")
                    else {}
                ),
                **(
                    {"connection_icon": model.get("connection_icon")}
                    if model.get("connection_icon")
                    else {}
                ),
                "tags": model.get("tags", []),
            }
            for model in ollama_resp.get("models", []) or []
            if isinstance(model, dict) and model.get("model") and model.get("name")
        ]
    else:
        ollama_models = []

    # Process gemini
    if not isinstance(gemini_resp, dict):
        gemini_models = []
    else:
        gemini_models = gemini_resp.get("data", []) if isinstance(gemini_resp, dict) else []

    # Process anthropic
    if not isinstance(anthropic_resp, dict):
        anthropic_models = []
    else:
        anthropic_models = anthropic_resp.get("data", []) if isinstance(anthropic_resp, dict) else []

    function_models = (
        function_models_resp if isinstance(function_models_resp, list) else []
    )
    models = function_models + openai_models + ollama_models + gemini_models + anthropic_models

    return models


async def get_all_base_models(request: Request, user: UserModel = None):
    # Per-user cache + stale-while-revalidate keeps model-aware pages responsive even when
    # one upstream connection is temporarily slow.
    cache_enabled = bool(
        getattr(getattr(request.app.state, "config", None), "ENABLE_BASE_MODELS_CACHE", True)
    )
    if not cache_enabled:
        return await _fetch_all_base_models(request, user=user)

    cache_key = _get_base_model_cache_key(user)
    now = time.time()
    cached = _base_model_cache.get(cache_key)
    if cached:
        ts, models = cached
        if now - ts < _BASE_MODEL_CACHE_TTL:
            return models
        _schedule_base_model_refresh(cache_key, request, user)
        return models

    return await _schedule_base_model_refresh(cache_key, request, user)


async def get_all_models(request, user: UserModel = None):
    """
    Return the effective model list for a given user.

    This is user-scoped: it merges the user's provider base models (from their own connections)
    with workspace models stored in the DB (Models table), including shared models owned by
    other users that the caller has access to.

    NOTE: This function intentionally does not write user-scoped model lists into app.state
    to avoid cross-user leakage. Callers can use request.state.MODELS as a per-request cache.
    """
    base_models = await get_all_base_models(request, user=user)
    models = copy.deepcopy(base_models)

    # Do not return early when the caller has no provider-backed base models.
    # Admin-shared workspace models are injected below and must still be visible
    # to users who have not configured their own connections.

    global_action_ids = [
        function.id for function in Functions.get_global_action_functions()
    ]
    enabled_action_ids = [
        function.id
        for function in Functions.get_functions_by_type("action", active_only=True)
    ]

    # Build quick indexes for matching.
    model_by_id: dict[str, dict] = {m.get("id"): m for m in models if isinstance(m, dict) and m.get("id")}
    model_ids = set(model_by_id.keys())

    def _can_read_workspace_model(model_row) -> bool:
        if not user:
            return False
        if user.role == "admin":
            return True
        if user.id == model_row.user_id:
            return True
        return has_access(user.id, type="read", access_control=model_row.access_control)

    # For shared models (owned by other users), we may need to fetch a small subset of
    # provider base model metadata from that owner's connections so we can route correctly.
    async def _owner_base_models_by_user_id(user_id: str) -> list[dict]:
        try:
            from open_webui.models.users import Users  # local import to avoid heavy coupling

            owner = Users.get_user_by_id(user_id)
            if not owner:
                return []
            return await get_all_base_models(request, user=owner)
        except Exception:
            return []

    owner_base_models_cache: dict[str, list[dict]] = {}

    def _find_model_like(models_list: list[dict], model_id: str) -> Optional[dict]:
        if not model_id:
            return None
        for m in models_list or []:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if mid == model_id:
                return m
            # Ollama ids can vary ('llama3' vs 'llama3:7b'); match on base name.
            if m.get("owned_by") == "ollama" and isinstance(mid, str) and isinstance(model_id, str):
                if model_id == mid.split(":")[0]:
                    return m
        return None

    custom_models = Models.get_all_models()

    # 1) Apply base model overrides (base_model_id == None).
    for custom_model in custom_models:
        if custom_model.base_model_id is not None:
            continue

        # Skip entries the caller can't see (for users).
        if user and user.role == "user" and not _can_read_workspace_model(custom_model):
            continue

        existing = model_by_id.get(custom_model.id) or _find_model_like(models, custom_model.id)
        if existing:
            if custom_model.is_active:
                existing["name"] = custom_model.name
                existing["info"] = custom_model.model_dump()

                action_ids = []
                if "info" in existing and "meta" in existing["info"]:
                    action_ids.extend(existing["info"]["meta"].get("actionIds", []))
                existing["action_ids"] = action_ids
            else:
                try:
                    models.remove(existing)
                except Exception:
                    pass
                model_by_id.pop(custom_model.id, None)
                model_ids.discard(custom_model.id)
            continue

        # Shared base model not present in this user's base model list. Inject it if active.
        if not custom_model.is_active:
            continue

        owner_id = custom_model.user_id
        if owner_id not in owner_base_models_cache:
            owner_base_models_cache[owner_id] = await _owner_base_models_by_user_id(owner_id)

        owner_base_model = _find_model_like(
            owner_base_models_cache.get(owner_id, []), custom_model.id
        )
        if not owner_base_model:
            # Skip orphaned overrides: after upgrades or connection changes, the DB can still
            # contain legacy override rows for base models that are no longer available.
            continue

        injected = copy.deepcopy(owner_base_model)
        injected["id"] = custom_model.id
        injected["name"] = custom_model.name
        injected["info"] = custom_model.model_dump()
        injected["preset"] = True
        models.append(injected)
        model_by_id[injected["id"]] = injected
        model_ids.add(injected["id"])

    # 2) Append custom/preset models (base_model_id != None).
    for custom_model in custom_models:
        if custom_model.base_model_id is None or not custom_model.is_active:
            continue

        # Skip entries the caller can't see (for users).
        if user and user.role == "user" and not _can_read_workspace_model(custom_model):
            continue

        if custom_model.id in model_ids:
            continue

        owned_by = "openai"
        pipe = None
        action_ids = []

        base_like = _find_model_like(models, custom_model.base_model_id)  # user's own base models
        if base_like is None and custom_model.user_id:
            owner_id = custom_model.user_id
            if owner_id not in owner_base_models_cache:
                owner_base_models_cache[owner_id] = await _owner_base_models_by_user_id(owner_id)
            base_like = _find_model_like(owner_base_models_cache.get(owner_id, []), custom_model.base_model_id)

        if not base_like:
            # Skip orphaned preset models whose upstream/base model no longer exists for the
            # owner. Leaving them in the chat model list causes stale, unusable entries.
            continue

        owned_by = base_like.get("owned_by", owned_by)
        if "pipe" in base_like:
            pipe = base_like.get("pipe")

        if custom_model.meta:
            meta = custom_model.meta.model_dump()
            if "actionIds" in meta:
                action_ids.extend(meta["actionIds"])

        models.append(
            {
                "id": f"{custom_model.id}",
                "name": custom_model.name,
                "object": "model",
                "created": custom_model.created_at,
                "owned_by": owned_by,
                "info": custom_model.model_dump(),
                "preset": True,
                **({"pipe": pipe} if pipe is not None else {}),
                "action_ids": action_ids,
            }
        )

    # Process action_ids to get the actions
    def get_action_items_from_module(function, module):
        actions = []
        if hasattr(module, "actions"):
            actions = module.actions
            return [
                {
                    "id": f"{function.id}.{action['id']}",
                    "name": action.get("name", f"{function.name} ({action['id']})"),
                    "description": function.meta.description,
                    "icon_url": action.get(
                        "icon_url", function.meta.manifest.get("icon_url", None)
                    ),
                }
                for action in actions
            ]
        else:
            return [
                {
                    "id": function.id,
                    "name": function.name,
                    "description": function.meta.description,
                    "icon_url": function.meta.manifest.get("icon_url", None),
                }
            ]

    def get_function_module_by_id(function_id):
        if function_id in request.app.state.FUNCTIONS:
            function_module = request.app.state.FUNCTIONS[function_id]
        else:
            function_module, _, _ = load_function_module_by_id(function_id)
            request.app.state.FUNCTIONS[function_id] = function_module

    for model in models:
        action_ids = [
            action_id
            for action_id in list(set(model.pop("action_ids", []) + global_action_ids))
            if action_id in enabled_action_ids
        ]

        model["actions"] = []
        for action_id in action_ids:
            action_function = Functions.get_function_by_id(action_id)
            if action_function is None:
                raise Exception(f"Action not found: {action_id}")

            function_module = get_function_module_by_id(action_id)
            model["actions"].extend(
                get_action_items_from_module(action_function, function_module)
            )
    log.debug(f"get_all_models() returned {len(models)} models")

    # Per-request model map (avoid leaking across users).
    request.state.MODELS = {model["id"]: model for model in models if isinstance(model, dict) and model.get("id")}
    return models


def check_model_access(user, model):
    model_info = Models.get_model_by_id(model.get("id"))
    # Base models coming from a user's own external connections may not have a DB row.
    # In that case, access is implicitly granted because the model list is already user-scoped.
    if not model_info:
        return True

    if not (
        user.id == model_info.user_id
        or has_access(user.id, type="read", access_control=model_info.access_control)
    ):
        raise Exception("Model not found")
    return True
