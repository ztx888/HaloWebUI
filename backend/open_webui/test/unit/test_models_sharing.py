import asyncio
import pathlib
import sys
from types import SimpleNamespace


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.utils import models as models_utils
from open_webui.models.users import Users


class _WorkspaceModel:
    def __init__(
        self,
        *,
        model_id: str,
        user_id: str,
        name: str,
        access_control,
        is_active: bool = True,
        base_model_id=None,
        created_at: int = 123,
    ):
        self.id = model_id
        self.user_id = user_id
        self.name = name
        self.access_control = access_control
        self.is_active = is_active
        self.base_model_id = base_model_id
        self.created_at = created_at
        self.meta = {}

    def model_dump(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "base_model_id": self.base_model_id,
            "name": self.name,
            "params": {},
            "meta": {},
            "access_control": self.access_control,
            "is_active": self.is_active,
            "updated_at": self.created_at,
            "created_at": self.created_at,
        }


def _make_request():
    return SimpleNamespace(state=SimpleNamespace())


def test_public_admin_shared_model_is_injected_without_user_connections(monkeypatch):
    owner = SimpleNamespace(id="admin-1", role="admin")
    user = SimpleNamespace(id="user-1", role="user")

    async def fake_get_all_base_models(_request, user=None):
        if user and user.id == owner.id:
            return [
                {
                    "id": "shared.gpt-4o",
                    "name": "Owner GPT-4o",
                    "object": "model",
                    "created": 123,
                    "owned_by": "openai",
                }
            ]
        return []

    monkeypatch.setattr(models_utils, "get_all_base_models", fake_get_all_base_models)
    monkeypatch.setattr(models_utils.Functions, "get_global_action_functions", lambda: [])
    monkeypatch.setattr(
        models_utils.Functions,
        "get_functions_by_type",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        models_utils.Models,
        "get_all_models",
        lambda: [
            _WorkspaceModel(
                model_id="shared.gpt-4o",
                user_id=owner.id,
                name="Shared GPT-4o",
                access_control=None,
            )
        ],
    )
    monkeypatch.setattr(
        models_utils,
        "has_access",
        lambda _user_id, **kwargs: kwargs.get("access_control") is None,
    )
    monkeypatch.setattr(
        Users,
        "get_user_by_id",
        lambda user_id: owner if user_id == owner.id else None,
    )

    request = _make_request()
    models = asyncio.run(models_utils.get_all_models(request, user=user))

    assert [model["id"] for model in models] == ["shared.gpt-4o"]
    assert models[0]["name"] == "Shared GPT-4o"
    assert models[0]["owned_by"] == "openai"
    assert models[0]["info"]["user_id"] == owner.id
    assert request.state.MODELS["shared.gpt-4o"]["name"] == "Shared GPT-4o"


def test_private_admin_shared_model_stays_hidden_without_explicit_access(monkeypatch):
    owner = SimpleNamespace(id="admin-1", role="admin")
    user = SimpleNamespace(id="user-1", role="user")

    async def fake_get_all_base_models(_request, user=None):
        if user and user.id == owner.id:
            return [
                {
                    "id": "shared.gpt-4o",
                    "name": "Owner GPT-4o",
                    "object": "model",
                    "created": 123,
                    "owned_by": "openai",
                }
            ]
        return []

    monkeypatch.setattr(models_utils, "get_all_base_models", fake_get_all_base_models)
    monkeypatch.setattr(models_utils.Functions, "get_global_action_functions", lambda: [])
    monkeypatch.setattr(
        models_utils.Functions,
        "get_functions_by_type",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        models_utils.Models,
        "get_all_models",
        lambda: [
            _WorkspaceModel(
                model_id="shared.gpt-4o",
                user_id=owner.id,
                name="Shared GPT-4o",
                access_control={},
            )
        ],
    )
    monkeypatch.setattr(models_utils, "has_access", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        Users,
        "get_user_by_id",
        lambda user_id: owner if user_id == owner.id else None,
    )

    request = _make_request()
    models = asyncio.run(models_utils.get_all_models(request, user=user))

    assert models == []
    assert request.state.MODELS == {}


def test_orphan_base_override_is_not_injected_into_models(monkeypatch):
    owner = SimpleNamespace(id="admin-1", role="admin")

    async def fake_get_all_base_models(_request, user=None):
        return []

    monkeypatch.setattr(models_utils, "get_all_base_models", fake_get_all_base_models)
    monkeypatch.setattr(models_utils.Functions, "get_global_action_functions", lambda: [])
    monkeypatch.setattr(
        models_utils.Functions,
        "get_functions_by_type",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        models_utils.Models,
        "get_all_models",
        lambda: [
            _WorkspaceModel(
                model_id="grok-4.1-fast",
                user_id=owner.id,
                name="Legacy Grok 4.1 Fast",
                access_control=None,
            )
        ],
    )
    monkeypatch.setattr(
        Users,
        "get_user_by_id",
        lambda user_id: owner if user_id == owner.id else None,
    )

    request = _make_request()
    models = asyncio.run(models_utils.get_all_models(request, user=owner))

    assert models == []
    assert request.state.MODELS == {}


def test_orphan_preset_model_is_not_injected_into_models(monkeypatch):
    owner = SimpleNamespace(id="admin-1", role="admin")

    async def fake_get_all_base_models(_request, user=None):
        return []

    monkeypatch.setattr(models_utils, "get_all_base_models", fake_get_all_base_models)
    monkeypatch.setattr(models_utils.Functions, "get_global_action_functions", lambda: [])
    monkeypatch.setattr(
        models_utils.Functions,
        "get_functions_by_type",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        models_utils.Models,
        "get_all_models",
        lambda: [
            _WorkspaceModel(
                model_id="HH Grok-4.1-Fast",
                user_id=owner.id,
                name="HH Grok-4.1-Fast",
                access_control=None,
                base_model_id="grok-4.1-fast",
            )
        ],
    )
    monkeypatch.setattr(
        Users,
        "get_user_by_id",
        lambda user_id: owner if user_id == owner.id else None,
    )

    request = _make_request()
    models = asyncio.run(models_utils.get_all_models(request, user=owner))

    assert models == []
    assert request.state.MODELS == {}
