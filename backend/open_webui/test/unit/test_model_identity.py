import pytest
from fastapi import HTTPException

from open_webui.utils.model_identity import (
    build_model_lookup,
    decorate_provider_model_identity,
    resolve_model_from_lookup,
    resolve_provider_connection_by_model_id,
)


def _provider_model(provider: str, model_id: str, idx: int, connection_id: str = ""):
    model = {
        "id": f"{connection_id}.{model_id}" if connection_id else model_id,
        "name": model_id,
        "owned_by": provider,
    }
    return decorate_provider_model_identity(
        model,
        provider=provider,
        model_id=model_id,
        connection_index=idx,
        connection_id=connection_id or None,
        legacy_ids=[model["id"], model_id],
    )


def test_duplicate_provider_models_keep_unique_selection_ids_and_ambiguous_legacy_name():
    official = _provider_model("openai", "gpt-image-2", 0)
    relay = _provider_model("openai", "gpt-image-2", 1, "7ad57b3e")

    lookup, ambiguous = build_model_lookup([official, relay])

    assert official["selection_id"] in lookup
    assert relay["selection_id"] in lookup
    assert "7ad57b3e.gpt-image-2" in lookup
    assert "gpt-image-2" in ambiguous

    with pytest.raises(HTTPException):
        resolve_model_from_lookup(lookup, ambiguous, "gpt-image-2")


def test_selection_id_resolves_exact_connection_and_clean_upstream_model():
    relay = _provider_model("openai", "gpt-image-2", 1, "7ad57b3e")

    idx, url, key, api_config = resolve_provider_connection_by_model_id(
        provider="openai",
        model_id=relay["selection_id"],
        base_urls=["https://api.openai.com/v1", "https://relay.example/v1"],
        keys=["official-key", "relay-key"],
        cfgs={"0": {}, "1": {"prefix_id": "7ad57b3e"}},
    )

    assert idx == 1
    assert url == "https://relay.example/v1"
    assert key == "relay-key"
    assert api_config["_resolved_model_id"] == "gpt-image-2"


def test_selection_id_without_connection_id_does_not_use_connection_index():
    model = _provider_model("openai", "gpt-image-2", 0)

    assert "::idx:" not in model["selection_id"]
    assert "::none::" in model["selection_id"]


def test_legacy_index_selection_uses_unique_current_model_ref_instead_of_index():
    relay = _provider_model("openai", "gpt-image-2", 1, "7ad57b3e")

    idx, url, key, api_config = resolve_provider_connection_by_model_id(
        provider="openai",
        model_id="modelref::openai::personal::idx:0::gpt-image-2",
        base_urls=["https://api.openai.com/v1", "https://relay.example/v1"],
        keys=["official-key", "relay-key"],
        cfgs={"0": {"prefix_id": "11111111"}, "1": {"prefix_id": "7ad57b3e"}},
        request_models=[relay],
    )

    assert idx == 1
    assert url == "https://relay.example/v1"
    assert key == "relay-key"
    assert api_config["_resolved_model_id"] == "gpt-image-2"


def test_legacy_index_selection_is_ambiguous_for_duplicate_current_models():
    official = _provider_model("openai", "gpt-image-2", 0, "11111111")
    relay = _provider_model("openai", "gpt-image-2", 1, "7ad57b3e")

    with pytest.raises(HTTPException):
        resolve_provider_connection_by_model_id(
            provider="openai",
            model_id="modelref::openai::personal::idx:0::gpt-image-2",
            base_urls=["https://api.openai.com/v1", "https://relay.example/v1"],
            keys=["official-key", "relay-key"],
            cfgs={"0": {"prefix_id": "11111111"}, "1": {"prefix_id": "7ad57b3e"}},
            request_models=[official, relay],
        )


def test_legacy_prefixed_id_resolves_without_falling_back_to_first_connection():
    idx, url, key, api_config = resolve_provider_connection_by_model_id(
        provider="openai",
        model_id="7ad57b3e.gpt-image-2",
        base_urls=["https://api.openai.com/v1", "https://relay.example/v1"],
        keys=["official-key", "relay-key"],
        cfgs={"0": {}, "1": {"prefix_id": "7ad57b3e"}},
    )

    assert idx == 1
    assert url == "https://relay.example/v1"
    assert key == "relay-key"
    assert api_config["_resolved_model_id"] == "gpt-image-2"


def test_naked_duplicate_model_id_is_rejected_when_connection_is_not_unique():
    official = _provider_model("openai", "gpt-image-2", 0)
    relay = _provider_model("openai", "gpt-image-2", 1, "7ad57b3e")

    with pytest.raises(HTTPException):
        resolve_provider_connection_by_model_id(
            provider="openai",
            model_id="gpt-image-2",
            base_urls=["https://api.openai.com/v1", "https://relay.example/v1"],
            keys=["official-key", "relay-key"],
            cfgs={"0": {}, "1": {"prefix_id": "7ad57b3e"}},
            request_models=[official, relay],
        )


def test_naked_model_id_with_multiple_enabled_connections_is_rejected_even_without_keys():
    with pytest.raises(HTTPException):
        resolve_provider_connection_by_model_id(
            provider="openai",
            model_id="gpt-image-2",
            base_urls=["https://first.example/v1", "https://second.example/v1"],
            keys=["", ""],
            cfgs={"0": {"enable": True}, "1": {"enable": True}},
            request_models=[],
        )
