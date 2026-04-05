from __future__ import annotations

import logging
from pathlib import Path


def run_alembic_migrations(open_webui_dir: Path, logger: logging.Logger) -> None:
    logger.info("Running migrations")
    try:
        from alembic import command
        from alembic.config import Config

        alembic_cfg = Config(open_webui_dir / "alembic.ini")
        migrations_path = open_webui_dir / "migrations"
        alembic_cfg.set_main_option("script_location", str(migrations_path))

        command.upgrade(alembic_cfg, "head")
    except Exception as exc:
        logger.exception("Error running migrations: %s", exc)
        raise RuntimeError(
            "数据库 schema 迁移失败，已停止启动，避免应用进入半初始化状态。"
            " 请先查看上方 Alembic 错误日志并修复数据库后重试。"
        ) from exc
