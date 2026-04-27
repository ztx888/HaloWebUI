from __future__ import annotations

import hashlib
import json
from typing import Any, Optional
from urllib.parse import quote, unquote

from fastapi import HTTPException


SELECTION_ID_PREFIX = "modelref"
AMBIGUOUS_MODEL_DETAIL = "模型连接不明确，请重新选择模型。"
STALE_MODEL_REF_DETAIL = "模型连接已失效，请重新选择模型。"
AMBIGUOUS_MODEL_CODE = "model_connection_ambiguous"
STALE_MODEL_REF_CODE = "model_connection_stale"


def _clean_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _encode(value: Any) -> str:
    return quote(_clean_str(value), safe="")


def _decode(value: str) -> str:
    return unquote(_clean_str(value))


def normalize_model_id(model_id: Any) -> str:
    return _clean_str(model_id)


def build_model_resolution_error(
    *,
    code: str,
    detail: str,
    requested_model_id: Any = None,
    candidates: Optional[list[Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": detail,
        "next_action": "reselect_model",
    }
    normalized_requested_model_id = normalize_model_id(requested_model_id)
    if normalized_requested_model_id:
        payload["requested_model_id"] = normalized_requested_model_id
    if candidates is not None:
        payload["candidates"] = [
            _clean_str(candidate)
            for candidate in candidates
            if _clean_str(candidate)
        ]
    return payload


def derive_connection_id(
    *,
    provider: str,
    url: Any = None,
    api_key: Any = None,
    auth_type: Any = None,
    source: str = "personal",
) -> str:
    normalized_url = _clean_str(url).rstrip("/")
    normalized_key = _clean_str(api_key)
    normalized_auth_type = _clean_str(auth_type).lower()
    normalized_provider = _clean_str(provider).lower()
    normalized_source = _clean_str(source) or "personal"

    if not normalized_url and not normalized_key:
        return ""

    payload = json.dumps(
        {
            "provider": normalized_provider,
            "source": normalized_source,
            "url": normalized_url,
            "api_key": normalized_key,
            "auth_type": normalized_auth_type,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def build_model_ref(
    *,
    provider: str,
    source: str = "personal",
    connection_id: Optional[Any] = None,
    connection_index: Optional[Any] = None,
) -> dict[str, Any]:
    model_ref: dict[str, Any] = {
        "provider": _clean_str(provider).lower(),
        "source": _clean_str(source) or "personal",
    }

    if connection_index is not None and _clean_str(connection_index) != "":
        try:
            model_ref["connection_index"] = int(connection_index)
        except (TypeError, ValueError):
            model_ref["connection_index"] = _clean_str(connection_index)

    normalized_connection_id = _clean_str(connection_id)
    if normalized_connection_id:
        model_ref["connection_id"] = normalized_connection_id

    return model_ref


def build_selection_id(
    *,
    provider: str,
    model_id: str,
    source: str = "personal",
    connection_id: Optional[Any] = None,
    connection_index: Optional[Any] = None,
) -> str:
    normalized_provider = _clean_str(provider).lower()
    normalized_source = _clean_str(source) or "personal"
    normalized_model_id = normalize_model_id(model_id)
    normalized_connection_id = _clean_str(connection_id)

    if normalized_connection_id:
        connection_token = f"id:{_encode(normalized_connection_id)}"
    else:
        connection_token = "none"

    return "::".join(
        [
            SELECTION_ID_PREFIX,
            _encode(normalized_provider),
            _encode(normalized_source),
            connection_token,
            _encode(normalized_model_id),
        ]
    )


def parse_selection_id(value: Any) -> Optional[dict[str, Any]]:
    raw = _clean_str(value)
    if not raw:
        return None

    parts = raw.split("::", 4)
    if len(parts) != 5 or parts[0] != SELECTION_ID_PREFIX:
        return None

    provider = _decode(parts[1]).lower()
    source = _decode(parts[2]) or "personal"
    connection_token = parts[3]
    model_id = _decode(parts[4])

    if not provider or not model_id:
        return None

    model_ref = build_model_ref(provider=provider, source=source)
    if connection_token.startswith("id:"):
        model_ref["connection_id"] = _decode(connection_token[3:])
    elif connection_token.startswith("idx:"):
        raw_index = _decode(connection_token[4:])
        try:
            model_ref["connection_index"] = int(raw_index)
        except (TypeError, ValueError):
            model_ref["connection_index"] = raw_index
    elif connection_token != "none":
        return None

    return {
        "provider": provider,
        "source": source,
        "model_id": model_id,
        "model_ref": model_ref,
    }


def unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = _clean_str(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def decorate_provider_model_identity(
    model: dict[str, Any],
    *,
    provider: str,
    model_id: Any,
    source: str = "personal",
    connection_index: Optional[Any] = None,
    connection_id: Optional[Any] = None,
    legacy_ids: Optional[list[Any]] = None,
) -> dict[str, Any]:
    upstream_model_id = normalize_model_id(model_id)
    model_ref = build_model_ref(
        provider=provider,
        source=source,
        connection_id=connection_id,
        connection_index=connection_index,
    )
    selection_id = build_selection_id(
        provider=provider,
        source=model_ref.get("source") or source,
        connection_id=model_ref.get("connection_id"),
        connection_index=model_ref.get("connection_index"),
        model_id=upstream_model_id,
    )

    legacy_candidates = [
        *(legacy_ids or []),
        model.get("legacy_id"),
        model.get("id"),
        upstream_model_id,
    ]
    if model_ref.get("connection_id"):
        legacy_candidates.append(f"{model_ref['connection_id']}.{upstream_model_id}")

    model["model_id"] = upstream_model_id
    model["original_id"] = upstream_model_id
    model["selection_id"] = selection_id
    model["model_ref"] = model_ref
    model["legacy_ids"] = unique_strings(legacy_candidates)
    return model


def get_model_selection_id(model: dict[str, Any]) -> str:
    return _clean_str(model.get("selection_id") or model.get("id"))


def get_model_aliases(model: dict[str, Any]) -> list[str]:
    aliases = [
        model.get("selection_id"),
        model.get("id"),
        model.get("model_id"),
        model.get("original_id"),
        *(model.get("legacy_ids") or []),
    ]
    return unique_strings(aliases)


def build_model_lookup(
    models: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    lookup: dict[str, dict[str, Any]] = {}
    ambiguous: set[str] = set()

    def _model_identity(model: dict[str, Any]) -> str:
        return get_model_selection_id(model) or _clean_str(model.get("id"))

    for model in models:
        if not isinstance(model, dict):
            continue

        current_identity = _model_identity(model)
        for alias in get_model_aliases(model):
            if alias in ambiguous:
                continue

            existing = lookup.get(alias)
            if existing is None:
                lookup[alias] = model
                continue

            if _model_identity(existing) == current_identity:
                continue

            lookup.pop(alias, None)
            ambiguous.add(alias)

    return lookup, ambiguous


def resolve_model_from_lookup(
    models_map: dict[str, dict[str, Any]],
    ambiguous_aliases: set[str],
    model_id: Any,
) -> Optional[dict[str, Any]]:
    normalized_model_id = _clean_str(model_id)
    if not normalized_model_id:
        return None

    if normalized_model_id in ambiguous_aliases:
        raise HTTPException(
            status_code=400,
            detail=build_model_resolution_error(
                code=AMBIGUOUS_MODEL_CODE,
                detail=AMBIGUOUS_MODEL_DETAIL,
                requested_model_id=normalized_model_id,
            ),
        )

    return models_map.get(normalized_model_id)


def get_model_ref_from_model(model: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(model, dict):
        return {}
    model_ref = model.get("model_ref")
    return dict(model_ref) if isinstance(model_ref, dict) else {}


def get_base_model_ref_from_model_info(model_info: Any) -> dict[str, Any]:
    if not model_info:
        return {}
    try:
        meta = model_info.meta.model_dump()
    except Exception:
        meta = {}
    if not isinstance(meta, dict):
        return {}
    model_ref = meta.get("base_model_ref") or meta.get("model_ref")
    return dict(model_ref) if isinstance(model_ref, dict) else {}


def _get_connection_cfg(
    cfgs: dict,
    base_urls: list[str],
    idx: int,
) -> dict[str, Any]:
    url = base_urls[idx] if idx < len(base_urls) else ""
    cfg = cfgs.get(str(idx), cfgs.get(url, {})) if isinstance(cfgs, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


def _connection_prefix(cfg: dict[str, Any]) -> str:
    return _clean_str(cfg.get("prefix_id") or cfg.get("_resolved_prefix_id"))


def _connection_matches_ref(
    *,
    provider: str,
    idx: int,
    cfg: dict[str, Any],
    model_ref: dict[str, Any],
) -> bool:
    ref_provider = _clean_str(model_ref.get("provider")).lower()
    if ref_provider and ref_provider != _clean_str(provider).lower():
        return False

    connection_id = _clean_str(model_ref.get("connection_id") or model_ref.get("prefix_id"))
    if connection_id:
        return _connection_prefix(cfg) == connection_id

    if model_ref.get("connection_index") is not None and _clean_str(
        model_ref.get("connection_index")
    ) != "":
        return _clean_str(idx) == _clean_str(model_ref.get("connection_index"))

    return False


def _strip_matching_connection_prefix(model_id: str, cfg: dict[str, Any]) -> str:
    prefix_id = _connection_prefix(cfg)
    if not prefix_id:
        return model_id
    prefix = f"{prefix_id}."
    return model_id[len(prefix) :] if model_id.startswith(prefix) else model_id


def _model_provider_matches(model: dict[str, Any], provider: str) -> bool:
    model_ref = model.get("model_ref")
    ref_provider = (
        _clean_str(model_ref.get("provider")).lower()
        if isinstance(model_ref, dict)
        else ""
    )
    if ref_provider:
        return ref_provider == provider

    owned_by = _clean_str(model.get("owned_by")).lower()
    if provider == "gemini":
        return owned_by in {"google", "gemini"}
    if provider == "anthropic":
        return owned_by in {"anthropic", "claude"}
    return owned_by == provider


def _model_clean_id(model: dict[str, Any]) -> str:
    for key in ("model_id", "original_id"):
        value = normalize_model_id(model.get(key))
        if value:
            return value
    return normalize_model_id(model.get("id"))


def _iter_unique_models(request_models: Any) -> list[dict[str, Any]]:
    if isinstance(request_models, dict):
        candidates = list(request_models.values())
    elif isinstance(request_models, list):
        candidates = request_models
    else:
        candidates = []

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for model in candidates:
        if not isinstance(model, dict):
            continue
        identity = get_model_selection_id(model) or normalize_model_id(model.get("id"))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(model)
    return result


def _find_request_model_candidates(
    *,
    provider: str,
    upstream_model_id: str,
    request_models: Any,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for model in _iter_unique_models(request_models):
        if not _model_provider_matches(model, provider):
            continue
        if _model_clean_id(model) != upstream_model_id:
            continue
        candidates.append(model)
    return candidates


def resolve_provider_connection_by_model_id(
    *,
    provider: str,
    model_id: Any,
    base_urls: list[str],
    keys: list[str],
    cfgs: dict,
    model_ref: Optional[dict[str, Any]] = None,
    request_models: Any = None,
) -> tuple[int, str, str, dict[str, Any]]:
    requested_model_id = normalize_model_id(model_id)
    parsed_selection = parse_selection_id(requested_model_id)
    effective_model_ref = dict(model_ref) if isinstance(model_ref, dict) else {}
    upstream_model_id = requested_model_id

    if parsed_selection:
        if parsed_selection["provider"] != _clean_str(provider).lower():
            raise HTTPException(
                status_code=400,
                detail=build_model_resolution_error(
                    code=STALE_MODEL_REF_CODE,
                    detail=STALE_MODEL_REF_DETAIL,
                    requested_model_id=requested_model_id,
                ),
            )
        upstream_model_id = parsed_selection["model_id"]
        effective_model_ref = parsed_selection["model_ref"]
    elif effective_model_ref:
        upstream_model_id = requested_model_id

    if effective_model_ref:
        ref_connection_id = _clean_str(
            effective_model_ref.get("connection_id") or effective_model_ref.get("prefix_id")
        )
        ref_connection_index = effective_model_ref.get("connection_index")
        if (
            not ref_connection_id
            and ref_connection_index is not None
            and _clean_str(ref_connection_index) != ""
        ):
            request_candidates = _find_request_model_candidates(
                provider=_clean_str(provider).lower(),
                upstream_model_id=upstream_model_id,
                request_models=request_models,
            )
            if len(request_candidates) > 1:
                raise HTTPException(
                    status_code=400,
                    detail=build_model_resolution_error(
                        code=AMBIGUOUS_MODEL_CODE,
                        detail=AMBIGUOUS_MODEL_DETAIL,
                        requested_model_id=requested_model_id,
                        candidates=[
                            model.get("selection_id") or model.get("id")
                            for model in request_candidates
                            if isinstance(model, dict)
                        ],
                    ),
                )
            if len(request_candidates) == 1:
                candidate_ref = get_model_ref_from_model(request_candidates[0])
                candidate_connection_id = _clean_str(
                    candidate_ref.get("connection_id") or candidate_ref.get("prefix_id")
                )
                if candidate_connection_id:
                    return resolve_provider_connection_by_model_id(
                        provider=provider,
                        model_id=upstream_model_id,
                        base_urls=base_urls,
                        keys=keys,
                        cfgs=cfgs,
                        model_ref=candidate_ref,
                        request_models=request_models,
                    )

            usable_indices = []
            for idx, url in enumerate(base_urls):
                if not _clean_str(url):
                    continue

                cfg = _get_connection_cfg(cfgs, base_urls, idx)
                if cfg.get("enable", True) is False:
                    continue

                usable_indices.append(idx)
            if len(usable_indices) > 1:
                raise HTTPException(
                    status_code=400,
                    detail=build_model_resolution_error(
                        code=AMBIGUOUS_MODEL_CODE,
                        detail=AMBIGUOUS_MODEL_DETAIL,
                        requested_model_id=requested_model_id,
                    ),
                )

        for idx, _url in enumerate(base_urls):
            cfg = _get_connection_cfg(cfgs, base_urls, idx)
            if _connection_matches_ref(
                provider=provider,
                idx=idx,
                cfg=cfg,
                model_ref=effective_model_ref,
            ):
                url = (base_urls[idx] if idx < len(base_urls) else "").rstrip("/")
                key = keys[idx] if idx < len(keys) else ""
                api_config = {**cfg, "_resolved_prefix_id": _connection_prefix(cfg)}
                api_config["_resolved_model_id"] = _strip_matching_connection_prefix(
                    upstream_model_id, api_config
                )
                return idx, url, key, api_config
        raise HTTPException(
            status_code=400,
            detail=build_model_resolution_error(
                code=STALE_MODEL_REF_CODE,
                detail=STALE_MODEL_REF_DETAIL,
                requested_model_id=requested_model_id,
            ),
        )

    if isinstance(cfgs, dict) and "." in requested_model_id:
        maybe_prefix, rest = requested_model_id.split(".", 1)
        for idx, _url in enumerate(base_urls):
            cfg = _get_connection_cfg(cfgs, base_urls, idx)
            prefix_id = _connection_prefix(cfg)
            if prefix_id and prefix_id == maybe_prefix:
                url = (base_urls[idx] if idx < len(base_urls) else "").rstrip("/")
                key = keys[idx] if idx < len(keys) else ""
                api_config = {**cfg, "_resolved_prefix_id": prefix_id}
                api_config["_resolved_model_id"] = rest
                return idx, url, key, api_config

    request_candidates = _find_request_model_candidates(
        provider=_clean_str(provider).lower(),
        upstream_model_id=requested_model_id,
        request_models=request_models,
    )
    if len(request_candidates) > 1:
        raise HTTPException(
            status_code=400,
            detail=build_model_resolution_error(
                code=AMBIGUOUS_MODEL_CODE,
                detail=AMBIGUOUS_MODEL_DETAIL,
                requested_model_id=requested_model_id,
                candidates=[
                    model.get("selection_id") or model.get("id")
                    for model in request_candidates
                    if isinstance(model, dict)
                ],
            ),
        )
    if len(request_candidates) == 1:
        candidate_ref = get_model_ref_from_model(request_candidates[0])
        if candidate_ref:
            return resolve_provider_connection_by_model_id(
                provider=provider,
                model_id=requested_model_id,
                base_urls=base_urls,
                keys=keys,
                cfgs=cfgs,
                model_ref=candidate_ref,
                request_models=request_models,
            )

    usable_indices = []
    for idx, url in enumerate(base_urls):
        if not _clean_str(url):
            continue

        cfg = _get_connection_cfg(cfgs, base_urls, idx)
        if cfg.get("enable", True) is False:
            continue

        usable_indices.append(idx)
    if len(usable_indices) > 1:
        raise HTTPException(
            status_code=400,
            detail=build_model_resolution_error(
                code=AMBIGUOUS_MODEL_CODE,
                detail=AMBIGUOUS_MODEL_DETAIL,
                requested_model_id=requested_model_id,
            ),
        )

    chosen_idx = usable_indices[0] if usable_indices else 0
    cfg = _get_connection_cfg(cfgs, base_urls, chosen_idx)
    url = (base_urls[chosen_idx] if chosen_idx < len(base_urls) else "").rstrip("/")
    key = keys[chosen_idx] if chosen_idx < len(keys) else ""
    api_config = {**cfg, "_resolved_prefix_id": _connection_prefix(cfg)}
    api_config["_resolved_model_id"] = requested_model_id
    return chosen_idx, url, key, api_config
