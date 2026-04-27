import pytest
import sqlalchemy as sa

from open_webui.runtime_migrations import (
    RuntimeMigrationError,
    _choose_pg_dump_binary_path,
    _cleanup_legacy_image_generation_options,
    _extract_note_content,
    _extract_oauth_sub,
    _extract_text_content,
    _extract_usage_tokens,
    _merge_meta,
    _parse_postgres_major_version,
)


def test_extract_oauth_sub_prefers_oidc():
    oauth = {
        "github": {"sub": "gh-1"},
        "oidc": {"sub": "oidc-1"},
    }
    assert _extract_oauth_sub(oauth) == "oidc@oidc-1"


def test_extract_note_content_prefers_markdown():
    data = {"content": {"md": "hello markdown"}}
    assert _extract_note_content(data) == "hello markdown"


def test_extract_text_content_flattens_nested_blocks():
    content = [
        {"type": "text", "text": "hello"},
        {"content": {"md": "world"}},
    ]
    assert _extract_text_content(content) == "hello\nworld"


def test_extract_usage_tokens_supports_multiple_shapes():
    usage = {"input_tokens": "12", "output_tokens": 34}
    assert _extract_usage_tokens(usage) == (12, 34)


def test_merge_meta_keeps_existing_and_adds_source_payload():
    merged = _merge_meta({"foo": "bar"}, {"raw_content": {"text": "hello"}})
    assert merged["foo"] == "bar"
    assert merged["halo_migrated_from_openwebui"]["raw_content"] == {"text": "hello"}


def test_parse_postgres_major_version_handles_release_and_beta_strings():
    assert _parse_postgres_major_version("17.5 (Debian 17.5-1.pgdg12+1)") == 17
    assert _parse_postgres_major_version("18beta1") == 18
    assert _parse_postgres_major_version("unknown") is None


def test_choose_pg_dump_binary_prefers_exact_server_major():
    binary, major = _choose_pg_dump_binary_path(
        server_major=17,
        versioned_binaries={16: "/pg/16", 17: "/pg/17", 18: "/pg/18"},
        fallback_binary="/usr/bin/pg_dump",
        fallback_major=18,
    )
    assert binary == "/pg/17"
    assert major == 17


def test_choose_pg_dump_binary_uses_nearest_newer_version():
    binary, major = _choose_pg_dump_binary_path(
        server_major=17,
        versioned_binaries={16: "/pg/16", 18: "/pg/18"},
        fallback_binary="/usr/bin/pg_dump",
        fallback_major=18,
    )
    assert binary == "/pg/18"
    assert major == 18


def test_choose_pg_dump_binary_uses_compatible_fallback_when_no_versioned_binary():
    binary, major = _choose_pg_dump_binary_path(
        server_major=17,
        versioned_binaries={},
        fallback_binary="/custom/pg_dump",
        fallback_major=18,
    )
    assert binary == "/custom/pg_dump"
    assert major == 18


def test_choose_pg_dump_binary_raises_when_only_older_versions_are_available():
    with pytest.raises(RuntimeMigrationError, match="服务端主版本为 17"):
        _choose_pg_dump_binary_path(
            server_major=17,
            versioned_binaries={14: "/pg/14", 15: "/pg/15", 16: "/pg/16"},
            fallback_binary="/usr/bin/pg_dump",
            fallback_major=16,
        )


def test_cleanup_legacy_image_generation_options_is_idempotent():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    config_table = sa.Table(
        "config",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("data", sa.JSON, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, default=0),
    )
    chat_table = sa.Table(
        "chat",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("chat", sa.JSON, nullable=False),
    )
    metadata.create_all(engine)

    original_chat = {
        "title": "旧会话",
        "image_generation_options": {
            "size": "900x1600",
            "model": "gpt-image-2",
            "aspect_ratio": "16:9",
            "unknown": "removed",
        },
        "imageGenerationOptions": {
            "size": "1024x1024",
            "n": 2,
            "unknown": "removed",
        },
        "composer_state": {
            "image_generation_options": {
                "size": "900x1600",
                "image_size": "1K",
                "connection_index": 0,
                "unknown": "removed",
            },
            "imageGenerationOptions": {
                "size": "900x1600",
                "resolution": "2k",
                "background": "transparent",
                "unknown": "removed",
            },
        },
        "messages": [{"role": "user", "content": "生成一张图"}],
    }

    with engine.begin() as conn:
        conn.execute(
            config_table.insert(),
            {
                "id": 1,
                "data": {
                    "image_generation": {
                        "size": "900x1600",
                        "model_filter_regex": "gpt-image",
                    },
                    "other": "keep",
                },
                "version": 0,
            },
        )
        conn.execute(chat_table.insert(), {"id": "chat-1", "chat": original_chat})

        first_result = _cleanup_legacy_image_generation_options(conn)
        config_after_first = conn.execute(sa.select(config_table.c.data)).scalar_one()
        chat_after_first = conn.execute(sa.select(chat_table.c.chat)).scalar_one()

        second_result = _cleanup_legacy_image_generation_options(conn)
        config_after_second = conn.execute(sa.select(config_table.c.data)).scalar_one()
        chat_after_second = conn.execute(sa.select(chat_table.c.chat)).scalar_one()

    assert first_result == {
        "updated_configs": 1,
        "scanned_chats": 1,
        "updated_chats": 1,
    }
    assert second_result == {
        "updated_configs": 0,
        "scanned_chats": 1,
        "updated_chats": 0,
    }
    assert config_after_first == config_after_second
    assert chat_after_first == chat_after_second
    assert config_after_first["image_generation"] == {
        "size": "auto",
        "model_filter_regex": "gpt-image",
    }
    assert chat_after_first["image_generation_options"] == {
        "model": "gpt-image-2",
        "aspect_ratio": "16:9",
    }
    assert chat_after_first["imageGenerationOptions"] == {"n": 2}
    assert chat_after_first["composer_state"]["image_generation_options"] == {
        "image_size": "1K",
        "connection_index": 0,
    }
    assert chat_after_first["composer_state"]["imageGenerationOptions"] == {
        "resolution": "2k",
        "background": "transparent",
    }
    assert chat_after_first["messages"] == original_chat["messages"]
