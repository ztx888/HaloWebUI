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
from open_webui.utils.model_identity import (
    build_model_lookup,
    decorate_provider_model_identity,
    get_model_aliases,
    get_model_ref_from_model,
    get_model_selection_id,
    parse_selection_id,
)



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
_BASE_MODEL_CACHE_MAX_ENTRIES = 64
_BASE_MODEL_FETCH_TIMEOUT = (
    min(float(AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST), 5.0)
    if isinstance(AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST, (int, float))
    else 5.0
)


def _get_base_model_cache_key(user: Optional[UserModel]) -> str:
    return user.id if user else "anon"


def _evict_stale_base_model_cache(now: float) -> None:
    stale_keys = [
        k
        for k, (ts, _) in _base_model_cache.items()
        if now - ts > (_BASE_MODEL_CACHE_TTL * 2)
    ]
    for k in stale_keys:
        _base_model_cache.pop(k, None)

    overflow = len(_base_model_cache) - _BASE_MODEL_CACHE_MAX_ENTRIES
    if overflow <= 0:
        return

    oldest_keys = [
        cache_key
        for cache_key, _ in sorted(
            _base_model_cache.items(), key=lambda item: item[1][0]
        )[:overflow]
    ]
    for cache_key in oldest_keys:
        _base_model_cache.pop(cache_key, None)


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
        ollama_models = []
        for model in ollama_resp.get("models", []) or []:
            if not isinstance(model, dict) or not model.get("model") or not model.get("name"):
                continue
            original_model_id = model.get("original_model") or model.get("model")
            connection_index = (
                model.get("urls", [None])[0]
                if isinstance(model.get("urls"), list) and model.get("urls")
                else None
            )
            entry = {
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
            decorate_provider_model_identity(
                entry,
                provider="ollama",
                model_id=original_model_id,
                source="personal",
                connection_index=connection_index,
                legacy_ids=[model.get("model"), original_model_id],
            )
            ollama_models.append(entry)
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
    _evict_stale_base_model_cache(now)
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
    model_by_id, _ambiguous_model_aliases = build_model_lookup(
        [m for m in models if isinstance(m, dict)]
    )
    model_ids = {
        m.get("id")
        for m in models
        if isinstance(m, dict) and m.get("id")
    }

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
            from open_webui.utils.user_connections import maybe_migrate_user_connections

            owner = Users.get_user_by_id(user_id)
            if not owner:
                return []
            owner = maybe_migrate_user_connections(request, owner)
            return await get_all_base_models(request, user=owner)
        except Exception:
            return []

    owner_base_models_cache: dict[str, list[dict]] = {}

    def _find_model_like(models_list: list[dict], model_id: str) -> Optional[dict]:
        if not model_id:
            return None
        local_lookup, local_ambiguous = build_model_lookup(
            [m for m in (models_list or []) if isinstance(m, dict)]
        )
        if model_id in local_ambiguous:
            return None
        direct = local_lookup.get(model_id)
        if direct:
            return direct
        for m in models_list or []:
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if mid == model_id or model_id in get_model_aliases(m):
                return m
            # Ollama ids can vary ('llama3' vs 'llama3:7b'); match on base name.
            if m.get("owned_by") == "ollama" and isinstance(mid, str) and isinstance(model_id, str):
                if model_id == mid.split(":")[0]:
                    return m
        return None

    def _model_meta_dict(model_row) -> dict:
        meta = getattr(model_row, "meta", None)
        if not meta:
            return {}
        try:
            dumped = meta.model_dump()
            return dumped if isinstance(dumped, dict) else {}
        except Exception:
            return meta if isinstance(meta, dict) else {}

    def _clean_model_id_hint(value) -> str:
        parsed = parse_selection_id(value)
        if parsed:
            return parsed.get("model_id") or ""
        return str(value or "").strip()

    def _candidate_clean_id(model: dict) -> str:
        return str(
            model.get("model_id")
            or model.get("original_id")
            or model.get("id")
            or ""
        ).strip()

    def _model_ref_matches(candidate: dict, target_ref: dict) -> bool:
        if not isinstance(candidate, dict) or not isinstance(target_ref, dict):
            return False

        candidate_ref = get_model_ref_from_model(candidate)
        if not candidate_ref:
            return False

        target_provider = str(target_ref.get("provider") or "").strip().lower()
        candidate_provider = str(candidate_ref.get("provider") or "").strip().lower()
        if target_provider and candidate_provider and target_provider != candidate_provider:
            return False

        target_source = str(target_ref.get("source") or "").strip()
        candidate_source = str(candidate_ref.get("source") or "").strip()
        if target_source and candidate_source and target_source != candidate_source:
            return False

        target_connection_id = str(
            target_ref.get("connection_id") or target_ref.get("prefix_id") or ""
        ).strip()
        if target_connection_id:
            candidate_connection_id = str(
                candidate_ref.get("connection_id") or candidate_ref.get("prefix_id") or ""
            ).strip()
            return candidate_connection_id == target_connection_id

        target_index = target_ref.get("connection_index")
        if target_index is not None and str(target_index).strip() != "":
            candidate_index = candidate_ref.get("connection_index")
            return (
                "" if candidate_index is None else str(candidate_index).strip()
            ) == str(target_index).strip()

        return False

    def _model_ref_scope_matches(candidate: dict, target_ref: dict) -> bool:
        if not isinstance(candidate, dict) or not isinstance(target_ref, dict):
            return False

        candidate_ref = get_model_ref_from_model(candidate)
        if not candidate_ref:
            return False

        target_provider = str(target_ref.get("provider") or "").strip().lower()
        candidate_provider = str(candidate_ref.get("provider") or "").strip().lower()
        if target_provider and candidate_provider and target_provider != candidate_provider:
            return False

        target_source = str(target_ref.get("source") or "").strip()
        candidate_source = str(candidate_ref.get("source") or "").strip()
        if target_source and candidate_source and target_source != candidate_source:
            return False

        return True

    def _find_model_by_ref(
        models_list: list[dict],
        model_ref: dict,
        model_id_hint=None,
    ) -> Optional[dict]:
        clean_hint = _clean_model_id_hint(model_id_hint)
        target_connection_id = str(
            model_ref.get("connection_id") or model_ref.get("prefix_id") or ""
        ).strip()
        target_index = model_ref.get("connection_index")
        if (
            not target_connection_id
            and target_index is not None
            and str(target_index).strip() != ""
        ):
            if not clean_hint:
                return None
            scoped_matches = [
                candidate
                for candidate in models_list or []
                if _model_ref_scope_matches(candidate, model_ref)
                and (
                    _candidate_clean_id(candidate) == clean_hint
                    or clean_hint in get_model_aliases(candidate)
                )
            ]
            scoped_by_id = {
                get_model_selection_id(candidate): candidate
                for candidate in scoped_matches
                if get_model_selection_id(candidate)
            }
            if len(scoped_by_id) != 1:
                return None
            return next(iter(scoped_by_id.values()))

        matches = []
        for candidate in models_list or []:
            if not _model_ref_matches(candidate, model_ref):
                continue
            if (
                clean_hint
                and _candidate_clean_id(candidate) != clean_hint
                and clean_hint not in get_model_aliases(candidate)
            ):
                continue
            matches.append(candidate)

        return matches[0] if len(matches) == 1 else None

    def _resolve_base_model(custom_model, models_list: list[dict]) -> Optional[dict]:
        meta = _model_meta_dict(custom_model)

        base_like = _find_model_like(models_list, custom_model.base_model_id)
        if base_like:
            return base_like

        base_model_ref = meta.get("base_model_ref") or meta.get("model_ref")
        if isinstance(base_model_ref, dict):
            base_like = _find_model_by_ref(
                models_list,
                base_model_ref,
                meta.get("base_selection_id") or custom_model.base_model_id,
            )
            if base_like:
                return base_like

        return _find_model_like(models_list, meta.get("base_selection_id"))

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
        injected["legacy_ids"] = list(
            {
                *(injected.get("legacy_ids") or []),
                custom_model.id,
                get_model_selection_id(owner_base_model),
            }
        )
        injected["selection_id"] = custom_model.id
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

        base_like = _resolve_base_model(custom_model, models)  # user's own base models
        if base_like is None and custom_model.user_id:
            owner_id = custom_model.user_id
            if owner_id not in owner_base_models_cache:
                owner_base_models_cache[owner_id] = await _owner_base_models_by_user_id(owner_id)
            base_like = _resolve_base_model(
                custom_model,
                owner_base_models_cache.get(owner_id, []),
            )

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

        info_dump = custom_model.model_dump()
        base_model_ref = get_model_ref_from_model(base_like)
        if base_model_ref:
            info_dump.setdefault("meta", {})
            if isinstance(info_dump["meta"], dict):
                info_dump["meta"].setdefault("base_model_ref", base_model_ref)
                info_dump["meta"].setdefault(
                    "base_selection_id", get_model_selection_id(base_like)
                )

        models.append(
            {
                "id": f"{custom_model.id}",
                "selection_id": f"{custom_model.id}",
                "name": custom_model.name,
                "object": "model",
                "created": custom_model.created_at,
                "owned_by": owned_by,
                "info": info_dump,
                "preset": True,
                **({"model_ref": base_model_ref} if base_model_ref else {}),
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
        return function_module

    for model in models:
        action_ids = [
            action_id
            for action_id in list(set(model.pop("action_ids", []) + global_action_ids))
            if action_id in enabled_action_ids
        ]

        model["actions"] = []
        for action_id in action_ids:
            try:
                action_function = Functions.get_function_by_id(action_id)
                if action_function is None:
                    log.warning(
                        "Skipping missing action while building models list: %s",
                        action_id,
                    )
                    continue

                function_module = get_function_module_by_id(action_id)
                model["actions"].extend(
                    get_action_items_from_module(action_function, function_module)
                )
            except Exception as e:
                log.warning(
                    "Skipping action %s for model %s while building models list: %s: %s",
                    action_id,
                    model.get("id"),
                    type(e).__name__,
                    e,
                )
                continue
    log.debug(f"get_all_models() returned {len(models)} models")

    # Per-request model map (avoid leaking across users). Ambiguous legacy aliases are
    # intentionally omitted so a naked duplicate model name can never route by accident.
    request.state.MODELS, request.state.MODELS_AMBIGUOUS = build_model_lookup(
        [model for model in models if isinstance(model, dict)]
    )
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
