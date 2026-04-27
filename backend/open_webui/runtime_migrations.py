from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, URL, make_url
from sqlalchemy.pool import NullPool

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data")).resolve()
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR}/webui.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

HALO_AUTO_MIGRATE = os.environ.get("HALO_AUTO_MIGRATE", "true").lower() == "true"
HALO_BACKUP_DIR = Path(
    os.environ.get(
        "HALO_MIGRATION_BACKUP_DIR", str((DATA_DIR / "migration-backups").resolve())
    )
).resolve()
HALO_STATE_TABLE = "halowebui_migration_state"
HALO_DATA_MIGRATION_TABLE = "halowebui_data_migrations"
HALO_TARGET_HEAD = "3d4e5f6a7b8c"
HALO_CONNECTION_METADATA_BACKFILL_KEY = "connection_metadata_backfill_v2"
HALO_IMAGE_GENERATION_OPTIONS_CLEANUP_KEY = "image_generation_options_cleanup_v1"
HALO_SOURCE_FAMILIES = {
    "c440947495f3": "owui_070_family",
    "a1b2c3d4e5f6": "owui_080_family",
    "b2c3d4e5f6a7": "owui_081_0810_family",
}
HALO_PEEWEE_MIGRATIONS = [
    "001_initial_schema",
    "002_add_local_sharing",
    "003_add_auth_api_key",
    "004_add_archived",
    "005_add_updated_at",
    "006_migrate_timestamps_and_charfields",
    "007_add_user_last_active_at",
    "008_add_memory",
    "009_add_models",
    "010_migrate_modelfiles_to_models",
    "011_add_user_settings",
    "012_add_tools",
    "013_add_user_info",
    "014_add_files",
    "015_add_functions",
    "016_add_valves_and_is_active",
    "017_add_user_oauth_sub",
    "018_add_function_is_global",
    "019_add_notes",
    "020_upgrade_prompt_v2",
]
RESOURCE_TYPES_WITH_ACCESS = (
    "channel",
    "file",
    "knowledge",
    "model",
    "note",
    "prompt",
    "skill",
    "tool",
)
CHAT_META_KEYS = frozenset(
    {
        "output",
        "files",
        "sources",
        "embeds",
        "done",
        "status_history",
        "statusHistory",
        "error",
        "usage",
        "model_id",
        "model_ref",
        "modelName",
        "modelIdx",
        "completedAt",
        "childrenIds",
        "info",
        "code_executions",
        "originalContent",
    }
)
PG_ADVISORY_LOCK_KEY = 431709370217493001


class RuntimeMigrationError(RuntimeError):
    pass


@dataclass(slots=True)
class DetectionResult:
    family: str
    revision: Optional[str]
    backend: str
    tables: set[str]


def ensure_runtime_migrated() -> Optional[DetectionResult]:
    if os.environ.get("HALO_RUNTIME_MIGRATION_DONE") == "true":
        return None

    with _locked_connection() as (_engine, conn, url):
        detection = _detect_database(conn, url)
        if detection.family in {"fresh", "already_halo"}:
            _run_post_halo_data_migrations(conn)
            conn.commit()
            os.environ["HALO_RUNTIME_MIGRATION_DONE"] = "true"
            return detection

        if detection.family == "unknown":
            raise RuntimeMigrationError(
                _format_unknown_database_error(detection.revision, detection.backend)
            )

        if not HALO_AUTO_MIGRATE:
            raise RuntimeMigrationError(
                "检测到受支持的 OpenWebUI 数据库，但 HALO_AUTO_MIGRATE=false，已阻止自动迁移。"
            )

        _ensure_no_incomplete_state(conn)
        backup = _backup_database(conn, url, detection)
        manifest = {
            "source_family": detection.family,
            "source_revision": detection.revision,
            "target_revision": HALO_TARGET_HEAD,
            "backend": detection.backend,
            "backup_path": str(backup),
            "database_url_driver": url.drivername,
            "created_at": _now(),
        }
        _write_manifest(backup, manifest)

        try:
            _ensure_state_table(conn)
            _write_state(
                conn,
                source_family=detection.family,
                source_revision=detection.revision,
                target_revision=HALO_TARGET_HEAD,
                status="started",
                backup_path=str(backup),
                details=manifest,
            )
            conn.commit()

            if detection.family == "owui_070_family":
                _migrate_070_family(conn, detection.backend)
            elif detection.family == "owui_080_family":
                _migrate_080_family(conn, detection.backend)
            elif detection.family == "owui_081_0810_family":
                _migrate_081_0810_family(conn, detection.backend)
            else:
                raise RuntimeMigrationError(f"未知迁移家族: {detection.family}")

            _seed_migratehistory(conn)
            _stamp_halo_head(conn)
            _run_post_halo_data_migrations(conn)

            _write_state(
                conn,
                source_family=detection.family,
                source_revision=detection.revision,
                target_revision=HALO_TARGET_HEAD,
                status="completed",
                backup_path=str(backup),
                details=manifest,
            )
            conn.commit()
            os.environ["HALO_RUNTIME_MIGRATION_DONE"] = "true"
            log.warning(
                "数据库已从 %s 自动迁移到当前 HaloWebUI 版本。备份: %s",
                detection.family,
                backup,
            )
            return detection
        except Exception as exc:
            conn.rollback()
            _write_state(
                conn,
                source_family=detection.family,
                source_revision=detection.revision,
                target_revision=HALO_TARGET_HEAD,
                status="failed",
                backup_path=str(backup),
                details={**manifest, "error": str(exc)},
            )
            conn.commit()
            raise RuntimeMigrationError(
                "自动迁移失败，已阻止启动。"
                f" 备份位于: {backup}. 可先恢复备份，再用 CLI 手动排查。"
            ) from exc


def migrate_auto(
    *, dry_run: bool = False, backup_only: bool = False, force_family: Optional[str] = None
) -> dict[str, Any]:
    with _locked_connection() as (_engine, conn, url):
        detection = _detect_database(conn, url)
        if force_family:
            detection.family = force_family

        result = {
            "family": detection.family,
            "revision": detection.revision,
            "backend": detection.backend,
            "tables": sorted(detection.tables),
            "backup_path": None,
            "action": "none",
        }

        if detection.family == "fresh":
            if not dry_run and not backup_only:
                _run_post_halo_data_migrations(conn)
                conn.commit()
            result["action"] = "fresh_db"
            return result

        if detection.family == "already_halo":
            if not dry_run and not backup_only:
                _run_post_halo_data_migrations(conn)
                conn.commit()
            result["action"] = "already_halo"
            return result

        if detection.family == "unknown":
            raise RuntimeMigrationError(
                _format_unknown_database_error(detection.revision, detection.backend)
            )

        backup = _backup_database(conn, url, detection)
        result["backup_path"] = str(backup)

        if backup_only:
            result["action"] = "backup_only"
            return result

        if dry_run:
            result["action"] = "dry_run"
            return result

        _ensure_no_incomplete_state(conn)
        _ensure_state_table(conn)
        manifest = {
            "source_family": detection.family,
            "source_revision": detection.revision,
            "target_revision": HALO_TARGET_HEAD,
            "backend": detection.backend,
            "backup_path": str(backup),
            "database_url_driver": url.drivername,
            "created_at": _now(),
        }
        _write_manifest(backup, manifest)
        try:
            _write_state(
                conn,
                source_family=detection.family,
                source_revision=detection.revision,
                target_revision=HALO_TARGET_HEAD,
                status="started",
                backup_path=str(backup),
                details=manifest,
            )
            conn.commit()
            if detection.family == "owui_070_family":
                _migrate_070_family(conn, detection.backend)
            elif detection.family == "owui_080_family":
                _migrate_080_family(conn, detection.backend)
            elif detection.family == "owui_081_0810_family":
                _migrate_081_0810_family(conn, detection.backend)
            _seed_migratehistory(conn)
            _stamp_halo_head(conn)
            _write_state(
                conn,
                source_family=detection.family,
                source_revision=detection.revision,
                target_revision=HALO_TARGET_HEAD,
                status="completed",
                backup_path=str(backup),
                details=manifest,
            )
            conn.commit()
            result["action"] = "migrated"
            return result
        except Exception as exc:
            conn.rollback()
            _write_state(
                conn,
                source_family=detection.family,
                source_revision=detection.revision,
                target_revision=HALO_TARGET_HEAD,
                status="failed",
                backup_path=str(backup),
                details={**manifest, "error": str(exc)},
            )
            conn.commit()
            raise


@contextlib.contextmanager
def _locked_connection():
    engine = _create_engine()
    url = make_url(DATABASE_URL)
    backend = _backend_name(url)

    if backend == "sqlite":
        db_path = Path(url.database).resolve()
        lock_path = db_path.with_suffix(db_path.suffix + ".halo-migration.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            with engine.connect() as conn:
                yield engine, conn, url
            engine.dispose()
        return

    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": PG_ADVISORY_LOCK_KEY})
        try:
            yield engine, conn, url
        finally:
            conn.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": PG_ADVISORY_LOCK_KEY},
            )
            engine.dispose()


def _create_engine() -> Engine:
    url = make_url(DATABASE_URL)
    backend = _backend_name(url)
    if backend == "sqlite":
        return create_engine(
            DATABASE_URL,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )

    return create_engine(DATABASE_URL, poolclass=NullPool, pool_pre_ping=True)


def _backend_name(url: URL) -> str:
    return "sqlite" if url.drivername.startswith("sqlite") else "postgresql"


def _detect_database(conn: Connection, url: URL) -> DetectionResult:
    tables = set(inspect(conn).get_table_names())
    if not tables:
        return DetectionResult(
            family="fresh",
            revision=None,
            backend=_backend_name(url),
            tables=tables,
        )

    revision = _get_alembic_revision(conn) if "alembic_version" in tables else None
    if revision == HALO_TARGET_HEAD:
        return DetectionResult(
            family="already_halo",
            revision=revision,
            backend=_backend_name(url),
            tables=tables,
        )

    if "migratehistory" in tables and _migratehistory_complete(conn):
        return DetectionResult(
            family="already_halo",
            revision=revision,
            backend=_backend_name(url),
            tables=tables,
        )

    family = HALO_SOURCE_FAMILIES.get(revision)
    if family and _verify_family(conn, family, tables):
        return DetectionResult(
            family=family,
            revision=revision,
            backend=_backend_name(url),
            tables=tables,
        )

    fingerprint_matches = [
        family_name
        for family_name in (
            "owui_070_family",
            "owui_080_family",
            "owui_081_0810_family",
        )
        if _verify_family(conn, family_name, tables)
    ]
    if len(fingerprint_matches) == 1:
        return DetectionResult(
            family=fingerprint_matches[0],
            revision=revision,
            backend=_backend_name(url),
            tables=tables,
        )

    return DetectionResult(
        family="unknown",
        revision=revision,
        backend=_backend_name(url),
        tables=tables,
    )


def _verify_family(conn: Connection, family: str, tables: set[str]) -> bool:
    if family == "owui_070_family":
        required = {
            "auth",
            "user",
            "group",
            "group_member",
            "prompt",
            "chat",
            "api_key",
            "knowledge_file",
        }
        forbidden = {"access_grant", "skill", "chat_message", "prompt_history"}
        return required.issubset(tables) and not (tables & forbidden)

    if family == "owui_080_family":
        required = {
            "auth",
            "user",
            "group",
            "group_member",
            "prompt",
            "chat",
            "access_grant",
            "skill",
            "chat_message",
            "prompt_history",
        }
        return required.issubset(tables) and not _column_exists(conn, "user", "scim")

    if family == "owui_081_0810_family":
        required = {
            "auth",
            "user",
            "group",
            "group_member",
            "prompt",
            "chat",
            "access_grant",
            "skill",
            "chat_message",
            "prompt_history",
        }
        return required.issubset(tables) and _column_exists(conn, "user", "scim")

    return False


def _format_unknown_database_error(
    revision: Optional[str], backend: str
) -> str:
    revision_text = revision or "None"
    return (
        "检测到未知数据库形态，已阻止启动以避免误迁移。"
        f" backend={backend}, alembic_version={revision_text}. "
        "仅支持官方 OpenWebUI 0.7.0~0.7.2、官方 0.8.0、官方 0.8.1~0.8.10、"
        "以及 ztx888/HaloWebUI 0.7.3-7/0.7.3-8/当前 main。"
    )


def _parse_postgres_major_version(raw_version: Any) -> Optional[int]:
    text = str(raw_version).strip()
    digits: list[str] = []
    for char in text:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    if not digits:
        return None
    return int("".join(digits))


def _get_postgres_server_major(conn: Connection) -> Optional[int]:
    raw_version = conn.execute(text("SHOW server_version")).scalar()
    if raw_version is None:
        return None
    return _parse_postgres_major_version(raw_version)


def _discover_versioned_pg_dump_binaries() -> dict[int, str]:
    versioned_binaries: dict[int, str] = {}
    pg_root = Path("/usr/lib/postgresql")
    if not pg_root.exists():
        return versioned_binaries

    for child in pg_root.iterdir():
        if not child.is_dir():
            continue
        major = _parse_postgres_major_version(child.name)
        if major is None:
            continue
        binary = child / "bin" / "pg_dump"
        if binary.is_file():
            versioned_binaries[major] = str(binary)
    return dict(sorted(versioned_binaries.items()))


def _get_pg_dump_major(binary: Optional[str]) -> Optional[int]:
    if not binary:
        return None

    try:
        result = subprocess.run(
            [binary, "--version"],
            check=True,
            env=os.environ.copy(),
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    output = (result.stdout or result.stderr).decode("utf-8", errors="ignore").strip()
    return _parse_postgres_major_version(output)


def _choose_pg_dump_binary_path(
    *,
    server_major: Optional[int],
    versioned_binaries: dict[int, str],
    fallback_binary: Optional[str],
    fallback_major: Optional[int],
) -> tuple[str, Optional[int]]:
    available_majors = sorted(versioned_binaries)

    if server_major is not None:
        exact_match = versioned_binaries.get(server_major)
        if exact_match:
            return exact_match, server_major

        for major in available_majors:
            if major > server_major:
                return versioned_binaries[major], major

        if fallback_binary and fallback_major is not None and fallback_major >= server_major:
            return fallback_binary, fallback_major

        compatible_majors = sorted(
            {
                *available_majors,
                *([fallback_major] if fallback_major is not None else []),
            }
        )
        if compatible_majors:
            raise RuntimeMigrationError(
                f"检测到 PostgreSQL 服务端主版本为 {server_major}，"
                "但当前镜像中可用的 `pg_dump` 主版本只有 "
                f"{', '.join(str(major) for major in compatible_majors)}。"
                " `pg_dump` 必须与服务端同版本或更高版本。"
                "请改用包含更高版本 PostgreSQL client 的镜像后重试。"
            )

    if fallback_binary:
        return fallback_binary, fallback_major

    if available_majors:
        newest = available_majors[-1]
        return versioned_binaries[newest], newest

    return "pg_dump", None


def _select_pg_dump_binary(
    conn: Connection,
) -> tuple[str, Optional[int], Optional[int], list[int]]:
    override_binary = os.environ.get("HALO_PG_DUMP_BIN", "").strip()
    server_major = _get_postgres_server_major(conn)
    if override_binary:
        return override_binary, server_major, _get_pg_dump_major(override_binary), []

    versioned_binaries = _discover_versioned_pg_dump_binaries()
    fallback_binary = shutil.which("pg_dump")
    fallback_major = _get_pg_dump_major(fallback_binary)
    binary, selected_major = _choose_pg_dump_binary_path(
        server_major=server_major,
        versioned_binaries=versioned_binaries,
        fallback_binary=fallback_binary,
        fallback_major=fallback_major,
    )
    return binary, server_major, selected_major, sorted(versioned_binaries)


def _backup_database(conn: Connection, url: URL, detection: DetectionResult) -> Path:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    family_slug = detection.family.replace("_family", "")
    HALO_BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    if detection.backend == "sqlite":
        source_path = Path(url.database).resolve()
        backup_path = HALO_BACKUP_DIR / f"{timestamp}-{family_slug}.sqlite3"
        source = sqlite3.connect(source_path)
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return backup_path

    backup_path = HALO_BACKUP_DIR / f"{timestamp}-{family_slug}.dump"
    pg_dump_binary, server_major, selected_major, available_majors = _select_pg_dump_binary(
        conn
    )
    cmd = [
        pg_dump_binary,
        "-Fc",
        "-f",
        str(backup_path),
        url.render_as_string(hide_password=False),
    ]
    try:
        subprocess.run(cmd, check=True, env=os.environ.copy(), capture_output=True)
    except FileNotFoundError as exc:
        raise RuntimeMigrationError(
            "检测到 PostgreSQL 数据库并准备自动迁移，但当前镜像缺少 `pg_dump`，"
            "无法先创建安全备份。请改用包含 PostgreSQL client 的镜像后重试。"
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="ignore").strip()
        detail = f" pg_dump stderr: {stderr}" if stderr else ""
        version_detail = ""
        if server_major is not None:
            version_detail += f" PostgreSQL 服务端主版本: {server_major}."
        if selected_major is not None:
            version_detail += f" 选中的 pg_dump 主版本: {selected_major}."
        if available_majors:
            version_detail += (
                " 镜像内发现的 versioned pg_dump 主版本: "
                f"{', '.join(str(major) for major in available_majors)}."
            )
        raise RuntimeMigrationError(
            "检测到 PostgreSQL 数据库并准备自动迁移，但执行 `pg_dump` 备份失败，"
            "已阻止启动以避免无备份迁移。常见原因是数据库权限不足，"
            "或容器内 `pg_dump` 版本低于 PostgreSQL 服务端主版本。"
            f"{version_detail}{detail}"
        ) from exc
    return backup_path


def _write_manifest(backup_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _ensure_no_incomplete_state(conn: Connection) -> None:
    tables = set(inspect(conn).get_table_names())
    if HALO_STATE_TABLE not in tables:
        return
    row = conn.execute(
        text(
            f'SELECT status, backup_path FROM "{HALO_STATE_TABLE}" '
            "ORDER BY started_at DESC LIMIT 1"
        )
    ).mappings().first()
    if row and row["status"] != "completed":
        raise RuntimeMigrationError(
            "检测到上次迁移未完成，已阻止继续启动。"
            f" 最近一次备份: {row.get('backup_path')}"
        )


def _ensure_state_table(conn: Connection) -> None:
    tables = set(inspect(conn).get_table_names())
    if HALO_STATE_TABLE in tables:
        return
    conn.execute(
        text(
            f"""
            CREATE TABLE "{HALO_STATE_TABLE}" (
                id TEXT PRIMARY KEY,
                source_family TEXT NOT NULL,
                source_revision TEXT NULL,
                target_revision TEXT NOT NULL,
                status TEXT NOT NULL,
                backup_path TEXT NULL,
                details TEXT NULL,
                started_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            )
            """
        )
    )


def _ensure_data_migration_table(conn: Connection) -> None:
    tables = set(inspect(conn).get_table_names())
    if HALO_DATA_MIGRATION_TABLE in tables:
        return
    conn.execute(
        text(
            f"""
            CREATE TABLE "{HALO_DATA_MIGRATION_TABLE}" (
                key TEXT PRIMARY KEY,
                details TEXT NULL,
                completed_at BIGINT NOT NULL
            )
            """
        )
    )


def _has_completed_data_migration(conn: Connection, key: str) -> bool:
    _ensure_data_migration_table(conn)
    row = conn.execute(
        text(
            f'SELECT 1 FROM "{HALO_DATA_MIGRATION_TABLE}" '
            "WHERE key = :key LIMIT 1"
        ),
        {"key": key},
    ).first()
    return row is not None


def _mark_data_migration_completed(
    conn: Connection, key: str, details: Optional[dict[str, Any]] = None
) -> None:
    _ensure_data_migration_table(conn)
    payload = {
        "key": key,
        "details": json.dumps(details or {}, ensure_ascii=False),
        "completed_at": _now(),
    }
    if _has_completed_data_migration(conn, key):
        conn.execute(
            text(
                f"""
                UPDATE "{HALO_DATA_MIGRATION_TABLE}"
                SET details = :details, completed_at = :completed_at
                WHERE key = :key
                """
            ),
            payload,
        )
        return

    conn.execute(
        text(
            f"""
            INSERT INTO "{HALO_DATA_MIGRATION_TABLE}" (key, details, completed_at)
            VALUES (:key, :details, :completed_at)
            """
        ),
        payload,
    )


def _settings_payload_to_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return json.loads(json.dumps(value, ensure_ascii=False))
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _backfill_user_connection_metadata(conn: Connection) -> dict[str, int]:
    from open_webui.models.users import User
    from open_webui.utils.user_connections import build_migrated_user_settings

    scanned_users = 0
    updated_users = 0

    if not _table_exists(conn, "user"):
        return {"scanned_users": 0, "updated_users": 0}

    rows = conn.execute(text('SELECT id, role, settings FROM "user"')).mappings().all()
    for row in rows:
        scanned_users += 1
        settings_dict = _settings_payload_to_dict(row.get("settings"))
        next_settings, changed = build_migrated_user_settings(
            settings_dict,
            is_admin=str(row.get("role") or "").strip().lower() == "admin",
            global_provider_defaults=None,
            id_strategy="derived",
        )
        if not changed or next_settings == settings_dict:
            continue

        values = {"settings": next_settings}
        if "updated_at" in User.__table__.c:
            values["updated_at"] = _now()

        conn.execute(
            User.__table__.update().where(User.__table__.c.id == row["id"]).values(**values)
        )
        updated_users += 1

    return {"scanned_users": scanned_users, "updated_users": updated_users}


def _cleanup_legacy_image_generation_options(conn: Connection) -> dict[str, int]:
    from open_webui.utils.image_generation_options import (
        sanitize_chat_payload_image_generation_options,
    )

    updated_configs = 0
    scanned_chats = 0
    updated_chats = 0

    if _table_exists(conn, "config"):
        config_table = sa.table(
            "config",
            sa.column("id", sa.Integer()),
            sa.column("data", sa.JSON()),
        )
        rows = conn.execute(
            sa.select(config_table.c.id, config_table.c.data)
        ).mappings().all()
        for row in rows:
            config_data = _settings_payload_to_dict(row.get("data"))
            image_generation = config_data.get("image_generation")
            if not isinstance(image_generation, dict):
                continue

            if image_generation.get("size") == "auto":
                continue

            image_generation["size"] = "auto"
            conn.execute(
                config_table.update()
                .where(config_table.c.id == row["id"])
                .values(data=config_data)
            )
            updated_configs += 1

    if not _table_exists(conn, "chat"):
        return {
            "updated_configs": updated_configs,
            "scanned_chats": scanned_chats,
            "updated_chats": updated_chats,
        }

    chat_table = sa.table(
        "chat",
        sa.column("id", sa.String()),
        sa.column("chat", sa.JSON()),
    )
    batch_size = 1000
    offset = 0
    base_query = sa.select(chat_table.c.id, chat_table.c.chat).order_by(chat_table.c.id)

    while True:
        rows = conn.execute(base_query.limit(batch_size).offset(offset)).mappings().all()
        if not rows:
            break

        offset += len(rows)
        for row in rows:
            scanned_chats += 1
            chat_payload = row.get("chat")
            if isinstance(chat_payload, str):
                try:
                    chat_payload = json.loads(chat_payload)
                except Exception:
                    continue

            cleaned_chat, changed = sanitize_chat_payload_image_generation_options(
                chat_payload
            )
            if not changed:
                continue

            conn.execute(
                chat_table.update()
                .where(chat_table.c.id == row["id"])
                .values(chat=cleaned_chat)
            )
            updated_chats += 1

    return {
        "updated_configs": updated_configs,
        "scanned_chats": scanned_chats,
        "updated_chats": updated_chats,
    }


def _run_post_halo_data_migrations(conn: Connection) -> None:
    if not _has_completed_data_migration(conn, HALO_CONNECTION_METADATA_BACKFILL_KEY):
        details = _backfill_user_connection_metadata(conn)
        _mark_data_migration_completed(
            conn,
            HALO_CONNECTION_METADATA_BACKFILL_KEY,
            details,
        )
    if not _has_completed_data_migration(conn, HALO_IMAGE_GENERATION_OPTIONS_CLEANUP_KEY):
        details = _cleanup_legacy_image_generation_options(conn)
        _mark_data_migration_completed(
            conn,
            HALO_IMAGE_GENERATION_OPTIONS_CLEANUP_KEY,
            details,
        )


def _write_state(
    conn: Connection,
    *,
    source_family: str,
    source_revision: Optional[str],
    target_revision: str,
    status: str,
    backup_path: Optional[str],
    details: dict[str, Any],
) -> None:
    _ensure_state_table(conn)
    now = _now()
    conn.execute(
        text(
            f"""
            INSERT INTO "{HALO_STATE_TABLE}"
            (id, source_family, source_revision, target_revision, status, backup_path, details, started_at, updated_at)
            VALUES
            (:id, :source_family, :source_revision, :target_revision, :status, :backup_path, :details, :started_at, :updated_at)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "source_family": source_family,
            "source_revision": source_revision,
            "target_revision": target_revision,
            "status": status,
            "backup_path": backup_path,
            "details": json.dumps(details, ensure_ascii=False),
            "started_at": now,
            "updated_at": now,
        },
    )


def _migrate_070_family(conn: Connection, backend: str) -> None:
    _ensure_group_user_ids(conn, backend)
    _ensure_user_legacy_auth_columns(conn, backend)
    _ensure_prompt_legacy_and_v2_columns(conn, backend, source_has_access_grants=False)
    _ensure_note_columns(conn, backend, source_has_access_grants=False)
    _ensure_knowledge_columns(conn, backend, source_has_access_grants=False)
    _ensure_folder_system_prompt(conn)
    _ensure_chat_assistant_id(conn)
    _ensure_access_control_columns(conn, backend, use_access_grants=False)
    _ensure_skill_table(conn, backend, source_has_access_grants=False)
    _rebuild_chat_message_table(conn, backend, source_has_chat_message=False)
    _ensure_halo_extra_tables(conn, backend)


def _migrate_080_family(conn: Connection, backend: str) -> None:
    _ensure_group_user_ids(conn, backend)
    _ensure_user_legacy_auth_columns(conn, backend)
    _ensure_prompt_legacy_and_v2_columns(conn, backend, source_has_access_grants=True)
    _ensure_note_columns(conn, backend, source_has_access_grants=True)
    _ensure_knowledge_columns(conn, backend, source_has_access_grants=True)
    _ensure_folder_system_prompt(conn)
    _ensure_chat_assistant_id(conn)
    _ensure_access_control_columns(conn, backend, use_access_grants=True)
    _ensure_skill_table(conn, backend, source_has_access_grants=True)
    _rebuild_chat_message_table(conn, backend, source_has_chat_message=True)
    _ensure_halo_extra_tables(conn, backend)


def _migrate_081_0810_family(conn: Connection, backend: str) -> None:
    _migrate_080_family(conn, backend)


def _ensure_group_user_ids(conn: Connection, backend: str) -> None:
    if not _table_exists(conn, "group"):
        return
    _ensure_column(conn, "group", "user_ids", "JSON")
    _ensure_column(conn, "group", "updated_at", "BIGINT")
    members_by_group: dict[str, list[str]] = {}
    if _table_exists(conn, "group_member"):
        rows = conn.execute(
            text('SELECT group_id, user_id FROM "group_member" ORDER BY created_at ASC')
        ).mappings()
        for row in rows:
            members_by_group.setdefault(str(row["group_id"]), [])
            user_id = str(row["user_id"])
            if user_id not in members_by_group[str(row["group_id"])]:
                members_by_group[str(row["group_id"])].append(user_id)

    groups = conn.execute(
        text('SELECT id, created_at, updated_at, user_ids FROM "group"')
    ).mappings()
    for group in groups:
        group_id = str(group["id"])
        user_ids = members_by_group.get(group_id, [])
        current = _json_value(group.get("user_ids"))
        if isinstance(current, list):
            for user_id in current:
                if user_id not in user_ids:
                    user_ids.append(user_id)
        _update_json_column_by_id(conn, backend, "group", "user_ids", group_id, user_ids)
        if group.get("updated_at") is None and group.get("created_at") is not None:
            conn.execute(
                text('UPDATE "group" SET updated_at = :updated_at WHERE id = :id'),
                {"updated_at": int(group["created_at"]), "id": group_id},
            )


def _ensure_user_legacy_auth_columns(conn: Connection, backend: str) -> None:
    if not _table_exists(conn, "user"):
        return

    _ensure_column(conn, "user", "api_key", "TEXT")
    _ensure_column(conn, "user", "oauth_sub", "TEXT")
    _ensure_column(conn, "user", "note", "TEXT")

    latest_keys: dict[str, str] = {}
    if _table_exists(conn, "api_key"):
        rows = conn.execute(
            text(
                'SELECT user_id, key FROM "api_key" '
                "ORDER BY COALESCE(updated_at, created_at, 0) DESC"
            )
        ).mappings()
        for row in rows:
            user_id = str(row["user_id"])
            if user_id not in latest_keys and row.get("key"):
                latest_keys[user_id] = str(row["key"])

    users = conn.execute(
        text('SELECT id, api_key, oauth, oauth_sub FROM "user"')
    ).mappings()
    for user in users:
        user_id = str(user["id"])
        if not user.get("api_key") and user_id in latest_keys:
            conn.execute(
                text('UPDATE "user" SET api_key = :api_key WHERE id = :id'),
                {"api_key": latest_keys[user_id], "id": user_id},
            )

        if not user.get("oauth_sub"):
            oauth_sub = _extract_oauth_sub(_json_value(user.get("oauth")))
            if oauth_sub:
                conn.execute(
                    text('UPDATE "user" SET oauth_sub = :oauth_sub WHERE id = :id'),
                    {"oauth_sub": oauth_sub, "id": user_id},
                )


def _ensure_prompt_legacy_and_v2_columns(
    conn: Connection, backend: str, *, source_has_access_grants: bool
) -> None:
    if not _table_exists(conn, "prompt"):
        return

    columns = _column_names(conn, "prompt")
    if "id" not in columns:
        _ensure_column(conn, "prompt", "id", "TEXT")
    _ensure_column(conn, "prompt", "name", "TEXT")
    _ensure_column(conn, "prompt", "data", "JSON")
    _ensure_column(conn, "prompt", "meta", "JSON")
    _ensure_column(conn, "prompt", "tags", "JSON")
    _ensure_column(conn, "prompt", "version_id", "TEXT")
    _ensure_column(conn, "prompt", "created_at", "BIGINT")
    _ensure_column(conn, "prompt", "updated_at", "BIGINT")
    _ensure_column(conn, "prompt", "is_active", "BOOLEAN DEFAULT TRUE")
    _ensure_column(conn, "prompt", "title", "TEXT")
    _ensure_column(conn, "prompt", "timestamp", "BIGINT")
    _ensure_column(conn, "prompt", "access_control", "JSON")

    rows = conn.execute(text('SELECT * FROM "prompt"')).mappings()
    access_map = (
        _build_access_control_map(conn) if source_has_access_grants else {}
    )
    for row in rows:
        prompt_id = row.get("id") or str(uuid.uuid4())
        created_at = row.get("created_at") or row.get("timestamp") or _now()
        updated_at = row.get("updated_at") or created_at
        name = row.get("name") or row.get("title") or row.get("command") or ""
        title = row.get("title") or row.get("name") or row.get("command") or ""
        timestamp = row.get("timestamp") or created_at
        if row.get("id") is None:
            conn.execute(
                text('UPDATE "prompt" SET id = :id WHERE command = :command'),
                {"id": prompt_id, "command": row["command"]},
            )
        conn.execute(
            text(
                'UPDATE "prompt" SET name = :name, title = :title, '
                "created_at = COALESCE(created_at, :created_at), "
                "updated_at = COALESCE(updated_at, :updated_at), "
                "timestamp = COALESCE(timestamp, :timestamp), "
                "is_active = COALESCE(is_active, :is_active) "
                'WHERE command = :command'
            ),
            {
                "name": name,
                "title": title,
                "created_at": int(created_at),
                "updated_at": int(updated_at),
                "timestamp": int(timestamp),
                "is_active": True,
                "command": row["command"],
            },
        )
        if source_has_access_grants:
            access = access_map.get(
                ("prompt", prompt_id), {"read": {"group_ids": [], "user_ids": []}, "write": {"group_ids": [], "user_ids": []}}
            )
            _update_json_column_by_id(
                conn,
                backend,
                "prompt",
                "access_control",
                prompt_id,
                access,
            )


def _ensure_note_columns(
    conn: Connection, backend: str, *, source_has_access_grants: bool
) -> None:
    if not _table_exists(conn, "note"):
        return

    _ensure_column(conn, "note", "content", "TEXT")
    _ensure_column(conn, "note", "access_control", "JSON")
    access_map = _build_access_control_map(conn) if source_has_access_grants else {}

    rows = conn.execute(text('SELECT * FROM "note"')).mappings()
    for row in rows:
        note_id = str(row["id"])
        content = row.get("content") or _extract_note_content(row.get("data"))
        conn.execute(
            text('UPDATE "note" SET content = :content WHERE id = :id'),
            {"content": content, "id": note_id},
        )
        if source_has_access_grants:
            access = access_map.get(
                ("note", note_id),
                {"read": {"group_ids": [], "user_ids": []}, "write": {"group_ids": [], "user_ids": []}},
            )
            _update_json_column_by_id(
                conn, backend, "note", "access_control", note_id, access
            )


def _ensure_knowledge_columns(
    conn: Connection, backend: str, *, source_has_access_grants: bool
) -> None:
    if not _table_exists(conn, "knowledge"):
        return

    _ensure_column(conn, "knowledge", "data", "JSON")
    _ensure_column(conn, "knowledge", "access_control", "JSON")
    _ensure_column(conn, "knowledge", "updated_at", "BIGINT")
    access_map = _build_access_control_map(conn) if source_has_access_grants else {}

    file_ids_by_knowledge: dict[str, list[str]] = {}
    if _table_exists(conn, "knowledge_file"):
        rows = conn.execute(
            text(
                'SELECT knowledge_id, file_id FROM "knowledge_file" '
                "ORDER BY created_at ASC"
            )
        ).mappings()
        for row in rows:
            knowledge_id = str(row["knowledge_id"])
            file_ids_by_knowledge.setdefault(knowledge_id, [])
            file_id = str(row["file_id"])
            if file_id not in file_ids_by_knowledge[knowledge_id]:
                file_ids_by_knowledge[knowledge_id].append(file_id)

    rows = conn.execute(text('SELECT * FROM "knowledge"')).mappings()
    for row in rows:
        knowledge_id = str(row["id"])
        data = _json_object(row.get("data"))
        if file_ids_by_knowledge.get(knowledge_id):
            current_ids = list(data.get("file_ids") or [])
            for file_id in file_ids_by_knowledge[knowledge_id]:
                if file_id not in current_ids:
                    current_ids.append(file_id)
            data["file_ids"] = current_ids
        _update_json_column_by_id(conn, backend, "knowledge", "data", knowledge_id, data)

        created_at = row.get("created_at") or _now()
        if row.get("updated_at") is None:
            conn.execute(
                text('UPDATE "knowledge" SET updated_at = :updated_at WHERE id = :id'),
                {"updated_at": int(created_at), "id": knowledge_id},
            )

        if source_has_access_grants:
            access = access_map.get(
                ("knowledge", knowledge_id),
                {"read": {"group_ids": [], "user_ids": []}, "write": {"group_ids": [], "user_ids": []}},
            )
            _update_json_column_by_id(
                conn, backend, "knowledge", "access_control", knowledge_id, access
            )


def _ensure_folder_system_prompt(conn: Connection) -> None:
    if not _table_exists(conn, "folder"):
        return
    _ensure_column(conn, "folder", "system_prompt", "TEXT")
    rows = conn.execute(text('SELECT id, data, system_prompt FROM "folder"')).mappings()
    for row in rows:
        if row.get("system_prompt"):
            continue
        data = _json_value(row.get("data"))
        system_prompt = None
        if isinstance(data, dict):
            system_prompt = (
                data.get("system_prompt")
                or (data.get("config") or {}).get("system_prompt")
                or (data.get("prompt") or {}).get("system")
            )
        if system_prompt:
            conn.execute(
                text('UPDATE "folder" SET system_prompt = :system_prompt WHERE id = :id'),
                {"system_prompt": str(system_prompt), "id": row["id"]},
            )


def _ensure_chat_assistant_id(conn: Connection) -> None:
    if not _table_exists(conn, "chat"):
        return

    _ensure_column(conn, "chat", "assistant_id", "TEXT")
    conn.execute(
        text(
            'CREATE INDEX IF NOT EXISTS "ix_chat_assistant_id" ON "chat" ("assistant_id")'
        )
    )


def _ensure_access_control_columns(
    conn: Connection, backend: str, *, use_access_grants: bool
) -> None:
    access_map = _build_access_control_map(conn) if use_access_grants else {}
    for table_name in ("channel", "file", "model", "tool"):
        if not _table_exists(conn, table_name):
            continue
        _ensure_column(conn, table_name, "access_control", "JSON")
        if not use_access_grants:
            continue
        rows = conn.execute(text(f'SELECT id FROM "{table_name}"')).mappings()
        for row in rows:
            resource_id = str(row["id"])
            access = access_map.get(
                (table_name, resource_id),
                {"read": {"group_ids": [], "user_ids": []}, "write": {"group_ids": [], "user_ids": []}},
            )
            _update_json_column_by_id(
                conn, backend, table_name, "access_control", resource_id, access
            )


def _ensure_skill_table(
    conn: Connection, backend: str, *, source_has_access_grants: bool
) -> None:
    if not _table_exists(conn, "skill"):
        conn.execute(
            text(
                """
                CREATE TABLE "skill" (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    source TEXT DEFAULT 'manual',
                    identifier TEXT NULL,
                    source_url TEXT NULL,
                    meta JSON NULL,
                    access_control JSON NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    updated_at BIGINT NOT NULL,
                    created_at BIGINT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text('CREATE INDEX IF NOT EXISTS "ix_skill_identifier" ON "skill" ("identifier")')
        )
        return

    _ensure_column(conn, "skill", "source", "TEXT DEFAULT 'manual'")
    _ensure_column(conn, "skill", "identifier", "TEXT")
    _ensure_column(conn, "skill", "source_url", "TEXT")
    _ensure_column(conn, "skill", "access_control", "JSON")
    access_map = _build_access_control_map(conn) if source_has_access_grants else {}
    if not source_has_access_grants:
        return
    rows = conn.execute(text('SELECT id FROM "skill"')).mappings()
    for row in rows:
        skill_id = str(row["id"])
        access = access_map.get(
            ("skill", skill_id),
            {"read": {"group_ids": [], "user_ids": []}, "write": {"group_ids": [], "user_ids": []}},
        )
        _update_json_column_by_id(
            conn, backend, "skill", "access_control", skill_id, access
        )


def _rebuild_chat_message_table(
    conn: Connection, backend: str, *, source_has_chat_message: bool
) -> None:
    temp_table = "chat_message_halo_tmp"
    if _table_exists(conn, temp_table):
        conn.execute(text(f'DROP TABLE "{temp_table}"'))

    conn.execute(
        text(
            f"""
            CREATE TABLE "{temp_table}" (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NULL,
                parent_id TEXT NULL,
                model TEXT NULL,
                prompt_tokens INTEGER NULL,
                completion_tokens INTEGER NULL,
                meta JSON NULL,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL
            )
            """
        )
    )
    conn.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "ix_{temp_table}_chat_id" ON "{temp_table}" ("chat_id")'
        )
    )
    conn.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "ix_{temp_table}_user_id" ON "{temp_table}" ("user_id")'
        )
    )
    conn.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS "ix_{temp_table}_model" ON "{temp_table}" ("model")'
        )
    )

    if source_has_chat_message and _table_exists(conn, "chat_message"):
        _copy_chat_messages_from_source(conn, backend, temp_table)
        conn.execute(text('DROP TABLE "chat_message"'))
    else:
        _backfill_chat_messages_from_chat_blob(conn, backend, temp_table)

    conn.execute(text(f'ALTER TABLE "{temp_table}" RENAME TO "chat_message"'))


def _copy_chat_messages_from_source(
    conn: Connection, backend: str, temp_table: str
) -> None:
    chat_owner_map = {
        str(row["id"]): str(row["user_id"])
        for row in conn.execute(text('SELECT id, user_id FROM "chat"')).mappings()
        if row.get("id") is not None
    }
    rows = conn.execute(text('SELECT * FROM "chat_message"')).mappings()
    for row in rows:
        raw_content = row.get("content")
        content_text = _extract_text_content(raw_content)
        usage = _json_object(row.get("usage"))
        prompt_tokens, completion_tokens = _extract_usage_tokens(usage)
        meta = _json_object(row.get("meta"))
        legacy_meta = {
            "raw_content": _json_value(raw_content),
            "output": _json_value(row.get("output")),
            "files": _json_value(row.get("files")),
            "sources": _json_value(row.get("sources")),
            "embeds": _json_value(row.get("embeds")),
            "done": row.get("done"),
            "status_history": _json_value(row.get("status_history"))
            or _json_value(row.get("statusHistory")),
            "error": _json_value(row.get("error")),
            "usage": usage or None,
        }
        meta = _merge_meta(meta, legacy_meta)
        user_id = row.get("user_id") or chat_owner_map.get(str(row["chat_id"])) or ""
        conn.execute(
            text(
                f"""
                INSERT INTO "{temp_table}"
                (id, chat_id, user_id, role, content, parent_id, model, prompt_tokens, completion_tokens, meta, created_at, updated_at)
                VALUES
                (:id, :chat_id, :user_id, :role, :content, :parent_id, :model, :prompt_tokens, :completion_tokens, { _json_bind_expr('meta_json', backend) }, :created_at, :updated_at)
                """
            ),
            {
                "id": str(row["id"]),
                "chat_id": str(row["chat_id"]),
                "user_id": str(user_id),
                "role": str(row.get("role") or "assistant"),
                "content": content_text,
                "parent_id": row.get("parent_id"),
                "model": row.get("model") or row.get("model_id"),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "meta_json": _json_dump(meta),
                "created_at": int(row.get("created_at") or _now()),
                "updated_at": int(row.get("updated_at") or row.get("created_at") or _now()),
            },
        )


def _backfill_chat_messages_from_chat_blob(
    conn: Connection, backend: str, temp_table: str
) -> None:
    rows = conn.execute(
        text('SELECT id, user_id, chat, created_at FROM "chat" ORDER BY id ASC')
    ).mappings()
    seen_ids: set[str] = set()
    for row in rows:
        chat_id = str(row["id"])
        user_id = str(row["user_id"])
        chat_created_at = int(row.get("created_at") or _now())
        chat_data = _json_value(row.get("chat"))
        if not isinstance(chat_data, dict):
            continue
        history = chat_data.get("history")
        if not isinstance(history, dict):
            continue
        messages = history.get("messages")
        if not isinstance(messages, dict):
            continue

        for message_id, message in messages.items():
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if not role:
                continue
            row_id = str(message.get("id") or message_id)
            if row_id in seen_ids:
                row_id = f"{chat_id}:{row_id}"
            seen_ids.add(row_id)
            content_text = _extract_text_content(message.get("content"))
            usage = _json_object(message.get("usage"))
            prompt_tokens, completion_tokens = _extract_usage_tokens(usage)
            legacy_meta = {
                key: _json_value(value)
                for key, value in message.items()
                if key in CHAT_META_KEYS and value is not None
            }
            meta = _merge_meta({}, {"raw_content": _json_value(message.get("content"))})
            meta = _merge_meta(meta, legacy_meta)
            created_at = message.get("timestamp") or chat_created_at
            conn.execute(
                text(
                    f"""
                    INSERT INTO "{temp_table}"
                    (id, chat_id, user_id, role, content, parent_id, model, prompt_tokens, completion_tokens, meta, created_at, updated_at)
                    VALUES
                    (:id, :chat_id, :user_id, :role, :content, :parent_id, :model, :prompt_tokens, :completion_tokens, { _json_bind_expr('meta_json', backend) }, :created_at, :updated_at)
                    """
                ),
                {
                    "id": row_id,
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": str(role),
                    "content": content_text,
                    "parent_id": message.get("parentId") or message.get("parent_id"),
                    "model": message.get("model") or message.get("model_id"),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "meta_json": _json_dump(meta),
                    "created_at": int(created_at),
                    "updated_at": int(chat_created_at),
                },
            )


def _ensure_halo_extra_tables(conn: Connection, backend: str) -> None:
    if not _table_exists(conn, "chat_reaction"):
        conn.execute(
            text(
                """
                CREATE TABLE "chat_reaction" (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    created_at BIGINT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                'CREATE INDEX IF NOT EXISTS "ix_chat_reaction_chat_message" '
                'ON "chat_reaction" ("chat_id", "message_id")'
            )
        )

    if not _table_exists(conn, "haloclaw_gateway"):
        conn.execute(
            text(
                """
                CREATE TABLE "haloclaw_gateway" (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    name TEXT NOT NULL,
                    config TEXT NULL,
                    default_model_id TEXT NULL,
                    system_prompt TEXT NULL,
                    access_policy TEXT NULL,
                    enabled BOOLEAN DEFAULT FALSE,
                    meta TEXT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                )
                """
            )
        )

    if not _table_exists(conn, "haloclaw_external_user"):
        conn.execute(
            text(
                """
                CREATE TABLE "haloclaw_external_user" (
                    id TEXT PRIMARY KEY,
                    gateway_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    platform_user_id TEXT NOT NULL,
                    platform_username TEXT NULL,
                    platform_display_name TEXT NULL,
                    halo_user_id TEXT NULL,
                    model_override TEXT NULL,
                    is_blocked BOOLEAN DEFAULT FALSE,
                    meta TEXT NULL,
                    created_at BIGINT NOT NULL,
                    updated_at BIGINT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                'CREATE UNIQUE INDEX IF NOT EXISTS "ix_haloclaw_ext_user_lookup" '
                'ON "haloclaw_external_user" ("gateway_id", "platform", "platform_user_id")'
            )
        )

    if not _table_exists(conn, "haloclaw_message_log"):
        conn.execute(
            text(
                """
                CREATE TABLE "haloclaw_message_log" (
                    id TEXT PRIMARY KEY,
                    gateway_id TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    platform_chat_id TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    platform_message_id TEXT NULL,
                    model_id TEXT NULL,
                    prompt_tokens INTEGER NULL,
                    completion_tokens INTEGER NULL,
                    meta TEXT NULL,
                    created_at BIGINT NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                'CREATE INDEX IF NOT EXISTS "ix_haloclaw_msg_log_chat" '
                'ON "haloclaw_message_log" ("gateway_id", "platform_chat_id", "created_at")'
            )
        )


def _build_access_control_map(conn: Connection) -> dict[tuple[str, str], dict[str, Any]]:
    if not _table_exists(conn, "access_grant"):
        return {}

    rows = conn.execute(
        text(
            'SELECT resource_type, resource_id, principal_type, principal_id, permission '
            'FROM "access_grant"'
        )
    ).mappings()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["resource_type"]), str(row["resource_id"]))
        grouped.setdefault(
            key,
            {"read": {"group_ids": [], "user_ids": []}, "write": {"group_ids": [], "user_ids": []}},
        )
        permission = str(row["permission"])
        if permission not in ("read", "write"):
            continue
        principal_type = str(row["principal_type"])
        principal_id = str(row["principal_id"])
        if principal_type == "group":
            if principal_id not in grouped[key][permission]["group_ids"]:
                grouped[key][permission]["group_ids"].append(principal_id)
        elif principal_type == "user":
            if principal_id not in grouped[key][permission]["user_ids"]:
                grouped[key][permission]["user_ids"].append(principal_id)
    return grouped


def _seed_migratehistory(conn: Connection) -> None:
    if not _table_exists(conn, "migratehistory"):
        conn.execute(
            text(
                """
                CREATE TABLE "migratehistory" (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    migrated_at DATETIME NOT NULL
                )
                """
            )
        )

    existing = {
        row["name"]
        for row in conn.execute(
            text('SELECT name FROM "migratehistory"')
        ).mappings()
    }
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    for index, migration_name in enumerate(HALO_PEEWEE_MIGRATIONS, start=1):
        if migration_name in existing:
            continue
        conn.execute(
            text(
                'INSERT INTO "migratehistory" (id, name, migrated_at) '
                "VALUES (:id, :name, :migrated_at)"
            ),
            {"id": index, "name": migration_name, "migrated_at": now},
        )


def _stamp_halo_head(conn: Connection) -> None:
    if not _table_exists(conn, "alembic_version"):
        conn.execute(
            text(
                """
                CREATE TABLE "alembic_version" (
                    version_num VARCHAR(32) NOT NULL PRIMARY KEY
                )
                """
            )
        )
        conn.execute(
            text('INSERT INTO "alembic_version" (version_num) VALUES (:version_num)'),
            {"version_num": HALO_TARGET_HEAD},
        )
        return

    count = conn.execute(text('SELECT COUNT(*) FROM "alembic_version"')).scalar() or 0
    if count == 0:
        conn.execute(
            text('INSERT INTO "alembic_version" (version_num) VALUES (:version_num)'),
            {"version_num": HALO_TARGET_HEAD},
        )
    else:
        conn.execute(
            text('UPDATE "alembic_version" SET version_num = :version_num'),
            {"version_num": HALO_TARGET_HEAD},
        )


def _migratehistory_complete(conn: Connection) -> bool:
    count = conn.execute(text('SELECT COUNT(*) FROM "migratehistory"')).scalar() or 0
    return int(count) >= len(HALO_PEEWEE_MIGRATIONS)


def _get_alembic_revision(conn: Connection) -> Optional[str]:
    row = conn.execute(
        text('SELECT version_num FROM "alembic_version" LIMIT 1')
    ).mappings().first()
    return str(row["version_num"]) if row and row.get("version_num") else None


def _table_exists(conn: Connection, table_name: str) -> bool:
    return table_name in set(inspect(conn).get_table_names())


def _column_exists(conn: Connection, table_name: str, column_name: str) -> bool:
    return column_name in _column_names(conn, table_name)


def _column_names(conn: Connection, table_name: str) -> set[str]:
    try:
        return {column["name"] for column in inspect(conn).get_columns(table_name)}
    except Exception:
        return set()


def _ensure_column(
    conn: Connection, table_name: str, column_name: str, column_sql: str
) -> None:
    if _column_exists(conn, table_name, column_name):
        return
    conn.execute(
        text(
            f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_sql}'
        )
    )


def _update_json_column_by_id(
    conn: Connection,
    backend: str,
    table_name: str,
    column_name: str,
    row_id: str,
    value: Any,
) -> None:
    if value is None:
        conn.execute(
            text(
                f'UPDATE "{table_name}" SET "{column_name}" = NULL WHERE id = :id'
            ),
            {"id": row_id},
        )
        return
    conn.execute(
        text(
            f'UPDATE "{table_name}" SET "{column_name}" = { _json_placeholder(backend) } WHERE id = :id'
        ),
        {"val": _json_dump(value), "id": row_id},
    )


def _json_placeholder(backend: str) -> str:
    return "CAST(:val AS JSON)" if backend == "postgresql" else ":val"


def _json_bind_expr(param_name: str, backend: str) -> str:
    return (
        f"CAST(:{param_name} AS JSON)"
        if backend == "postgresql"
        else f":{param_name}"
    )


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _json_object(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _extract_oauth_sub(oauth: Any) -> Optional[str]:
    if not isinstance(oauth, dict) or not oauth:
        return None
    provider_order = list(oauth.keys())
    if "oidc" in oauth:
        provider_order.remove("oidc")
        provider_order.insert(0, "oidc")
    for provider in provider_order:
        provider_data = oauth.get(provider)
        if isinstance(provider_data, dict) and provider_data.get("sub"):
            return f"{provider}@{provider_data['sub']}"
    return None


def _extract_note_content(data: Any) -> str:
    parsed = _json_value(data)
    if isinstance(parsed, dict):
        content = parsed.get("content")
        if isinstance(content, dict):
            markdown = content.get("md")
            if isinstance(markdown, str):
                return markdown
        if isinstance(content, str):
            return content
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed
    return _extract_text_content(parsed)


def _extract_text_content(content: Any) -> str:
    parsed = _json_value(content)
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed
    if isinstance(parsed, dict):
        for key in ("md", "text", "content", "value"):
            if key in parsed:
                extracted = _extract_text_content(parsed[key])
                if extracted:
                    return extracted
        parts = [_extract_text_content(value) for value in parsed.values()]
        parts = [part for part in parts if part]
        if parts:
            return "\n".join(parts)
        return json.dumps(parsed, ensure_ascii=False)
    if isinstance(parsed, list):
        parts = [_extract_text_content(item) for item in parsed]
        parts = [part for part in parts if part]
        if parts:
            return "\n".join(parts)
        return json.dumps(parsed, ensure_ascii=False)
    return str(parsed)


def _extract_usage_tokens(usage: Any) -> tuple[Optional[int], Optional[int]]:
    usage = _json_object(usage)
    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens")
    if prompt_tokens is None:
        prompt_tokens = usage.get("input_tokens") or usage.get("promptTokenCount")
    if completion_tokens is None:
        completion_tokens = usage.get("output_tokens") or usage.get(
            "candidatesTokenCount"
        )

    def _coerce(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    return _coerce(prompt_tokens), _coerce(completion_tokens)


def _merge_meta(existing: Any, extra: dict[str, Any]) -> dict[str, Any]:
    meta = _json_object(existing)
    legacy = meta.get("halo_migrated_from_openwebui")
    if not isinstance(legacy, dict):
        legacy = {}
    for key, value in extra.items():
        if value is not None:
            legacy[key] = value
    meta["halo_migrated_from_openwebui"] = legacy
    return meta


def _now() -> int:
    return int(time.time())
