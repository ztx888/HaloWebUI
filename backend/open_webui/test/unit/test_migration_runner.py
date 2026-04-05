import ast
import logging
import pathlib
import sys
import types

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.migration_runner import run_alembic_migrations  # noqa: E402


_SKILL_MIGRATION = (
    _BACKEND_DIR
    / "open_webui/migrations/versions/b1c2d3e4f5a6_add_skill_table.py"
)


def _install_fake_alembic(monkeypatch, *, upgrade):
    alembic_module = types.ModuleType("alembic")
    config_module = types.ModuleType("alembic.config")

    class FakeConfig:
        def __init__(self, path):
            self.path = path
            self.options = {}

        def set_main_option(self, key, value):
            self.options[key] = value

    alembic_module.command = types.SimpleNamespace(upgrade=upgrade)
    config_module.Config = FakeConfig

    monkeypatch.setitem(sys.modules, "alembic", alembic_module)
    monkeypatch.setitem(sys.modules, "alembic.config", config_module)


def test_run_alembic_migrations_upgrades_head_with_repo_paths(monkeypatch, tmp_path):
    seen = {}

    def fake_upgrade(cfg, revision):
        seen["config_path"] = cfg.path
        seen["script_location"] = cfg.options["script_location"]
        seen["revision"] = revision

    _install_fake_alembic(monkeypatch, upgrade=fake_upgrade)

    run_alembic_migrations(tmp_path, logging.getLogger(__name__))

    assert seen == {
        "config_path": tmp_path / "alembic.ini",
        "script_location": str(tmp_path / "migrations"),
        "revision": "head",
    }


def test_run_alembic_migrations_raises_clear_error_on_failure(monkeypatch, tmp_path):
    def fake_upgrade(_cfg, _revision):
        raise ValueError("boom")

    _install_fake_alembic(monkeypatch, upgrade=fake_upgrade)

    with pytest.raises(RuntimeError, match="已停止启动"):
        run_alembic_migrations(tmp_path, logging.getLogger(__name__))


def test_skill_migration_uses_boolean_true_defaults_for_is_active():
    tree = ast.parse(_SKILL_MIGRATION.read_text(encoding="utf-8"))
    defaults = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "sa":
            continue
        if node.func.attr != "Column":
            continue
        if not node.args:
            continue
        if not isinstance(node.args[0], ast.Constant) or node.args[0].value != "is_active":
            continue

        server_default = next(
            (kw.value for kw in node.keywords if kw.arg == "server_default"),
            None,
        )
        defaults.append(server_default)

    assert len(defaults) == 2
    assert all(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sa"
        and node.func.attr == "true"
        for node in defaults
    )


def test_boolean_true_compiles_to_postgres_boolean_default():
    compiled = str(
        CreateColumn(
            sa.Column("is_active", sa.Boolean(), server_default=sa.true())
        ).compile(dialect=postgresql.dialect())
    ).lower()

    assert "default true" in compiled
    assert "default 1" not in compiled
