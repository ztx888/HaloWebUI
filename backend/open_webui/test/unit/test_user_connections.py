import re

from open_webui.utils.user_connections import (
    CONNECTION_ID_TOMBSTONES_KEY,
    build_migrated_user_settings,
    normalize_connections_payload,
)


def test_build_migrated_user_settings_backfills_legacy_direct_connections_with_stable_derived_ids():
    settings = {
        "ui": {
            "directConnections": {
                "OPENAI_API_BASE_URLS": [
                    "https://api.openai.com/v1",
                    "https://relay.example.com/v1",
                ],
                "OPENAI_API_KEYS": ["sk-official", "sk-relay"],
                "OPENAI_API_CONFIGS": {
                    "0": {"remark": "Official"},
                    "1": {"remark": "Relay"},
                },
            }
        }
    }

    migrated_once, changed_once = build_migrated_user_settings(
        settings,
        id_strategy="derived",
    )
    migrated_twice, changed_twice = build_migrated_user_settings(
        settings,
        id_strategy="derived",
    )

    assert changed_once is True
    assert changed_twice is True

    openai = migrated_once["ui"]["connections"]["openai"]
    assert re.fullmatch(r"[0-9a-f]{8}", openai["OPENAI_API_CONFIGS"]["0"]["prefix_id"])
    assert re.fullmatch(r"[0-9a-f]{8}", openai["OPENAI_API_CONFIGS"]["1"]["prefix_id"])
    assert openai["OPENAI_API_CONFIGS"]["0"]["name"] == "Official"
    assert openai["OPENAI_API_CONFIGS"]["1"]["name"] == "Relay"
    assert migrated_once == migrated_twice


def test_normalize_connections_payload_generates_persistent_prefix_ids_and_preserves_existing_ones():
    existing_connections = {
        "openai": {
            "OPENAI_API_BASE_URLS": [
                "https://api.openai.com/v1",
                "https://relay.example.com/v1",
            ],
            "OPENAI_API_KEYS": ["sk-old-official", "sk-old-relay"],
            "OPENAI_API_CONFIGS": {
                "0": {"remark": "Official", "prefix_id": ""},
                "1": {"remark": "Relay", "prefix_id": "7ad57b3e"},
            },
        }
    }
    next_connections = {
        "openai": {
            "OPENAI_API_BASE_URLS": [
                "https://api.openai.com/v1",
                "https://relay.example.com/v1",
                "https://third.example.com/v1",
            ],
            "OPENAI_API_KEYS": ["sk-new-official", "sk-new-relay", "sk-third"],
            "OPENAI_API_CONFIGS": {
                "0": {"remark": "Official"},
                "1": {"remark": "Relay"},
                "2": {"remark": "Third"},
            },
        }
    }

    normalized = normalize_connections_payload(
        next_connections,
        existing_connections=existing_connections,
        id_strategy="generated",
    )

    openai = normalized["openai"]
    assert re.fullmatch(r"[0-9a-f]{8}", openai["OPENAI_API_CONFIGS"]["0"]["prefix_id"])
    assert openai["OPENAI_API_CONFIGS"]["1"]["prefix_id"] == "7ad57b3e"
    assert re.fullmatch(r"[0-9a-f]{8}", openai["OPENAI_API_CONFIGS"]["2"]["prefix_id"])
    assert openai["OPENAI_API_CONFIGS"]["2"]["name"] == "Third"


def test_normalize_connections_payload_reuses_unique_tombstone_match_only():
    existing_connections = {
        "openai": {
            "OPENAI_API_BASE_URLS": ["https://relay.example.com/v1"],
            "OPENAI_API_KEYS": ["sk-old"],
            "OPENAI_API_CONFIGS": {
                "0": {
                    "remark": "Relay",
                    "auth_type": "bearer",
                    "prefix_id": "7ad57b3e",
                },
            },
            CONNECTION_ID_TOMBSTONES_KEY: [
                {
                    "provider": "openai",
                    "url": "https://ambiguous.example.com/v1",
                    "auth_type": "bearer",
                    "name": "Relay",
                    "prefix_id": "aaaaaaaa",
                },
                {
                    "provider": "openai",
                    "url": "https://ambiguous.example.com/v1",
                    "auth_type": "bearer",
                    "name": "Relay",
                    "prefix_id": "bbbbbbbb",
                },
            ],
        }
    }

    normalized = normalize_connections_payload(
        {
            "openai": {
                "OPENAI_API_BASE_URLS": [
                    "https://relay.example.com/v1",
                    "https://ambiguous.example.com/v1",
                ],
                "OPENAI_API_KEYS": ["sk-new", "sk-newer"],
                "OPENAI_API_CONFIGS": {
                    "0": {"remark": "Relay", "auth_type": "bearer"},
                    "1": {"remark": "Relay", "auth_type": "bearer"},
                },
            }
        },
        existing_connections=existing_connections,
        id_strategy="generated",
        update_tombstones=True,
    )

    openai = normalized["openai"]
    assert openai["OPENAI_API_CONFIGS"]["0"]["prefix_id"] == "7ad57b3e"
    assert openai["OPENAI_API_CONFIGS"]["1"]["prefix_id"] not in {"aaaaaaaa", "bbbbbbbb"}
    assert len(openai[CONNECTION_ID_TOMBSTONES_KEY]) <= 50


def test_normalize_connections_payload_does_not_reuse_index_prefix_when_name_changes():
    existing_connections = {
        "openai": {
            "OPENAI_API_BASE_URLS": ["https://relay.example.com/v1"],
            "OPENAI_API_KEYS": ["sk-old"],
            "OPENAI_API_CONFIGS": {
                "0": {
                    "remark": "Relay A",
                    "auth_type": "bearer",
                    "prefix_id": "aaaaaaaa",
                },
            },
        }
    }

    normalized = normalize_connections_payload(
        {
            "openai": {
                "OPENAI_API_BASE_URLS": ["https://relay.example.com/v1"],
                "OPENAI_API_KEYS": ["sk-new"],
                "OPENAI_API_CONFIGS": {
                    "0": {"remark": "Relay B", "auth_type": "bearer"},
                },
            }
        },
        existing_connections=existing_connections,
        id_strategy="derived",
    )

    openai = normalized["openai"]
    assert openai["OPENAI_API_CONFIGS"]["0"]["prefix_id"] != "aaaaaaaa"
    assert re.fullmatch(r"[0-9a-f]{8}", openai["OPENAI_API_CONFIGS"]["0"]["prefix_id"])
