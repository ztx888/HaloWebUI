import asyncio
import json
import pathlib
import sys


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.models.users import UserModel  # noqa: E402
from open_webui.routers import ollama as ollama_router  # noqa: E402


def _make_user() -> UserModel:
    return UserModel(
        id="user-1",
        name="Test User",
        email="user@example.com",
        role="user",
        profile_image_url="/user.png",
        last_active_at=0,
        updated_at=0,
        created_at=0,
    )


class _FakeResponse:
    def __init__(self, status: int, body):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return str(self._body)


class _FakeSession:
    def __init__(self, responses, requests_log):
        self._responses = list(responses)
        self._requests_log = requests_log

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get(self, url, headers=None):
        self._requests_log.append(("GET", url, headers, None))
        return self._responses.pop(0)

    def post(self, url, data=None, headers=None):
        self._requests_log.append(("POST", url, headers, data))
        return self._responses.pop(0)


def test_ollama_health_check_discovers_model_and_appends_latest(monkeypatch):
    requests_log = []
    monkeypatch.setattr(
        ollama_router.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _FakeSession(
            [
                _FakeResponse(200, {"models": [{"model": "llama3.2"}]}),
                _FakeResponse(200, {"message": {"role": "assistant", "content": "ok"}}),
            ],
            requests_log,
        ),
    )

    result = asyncio.run(
        ollama_router.health_check_connection(
            ollama_router.HealthCheckForm(
                url="http://localhost:11434",
                key="",
            ),
            user=_make_user(),
        )
    )

    assert result["ok"] is True
    assert result["model"] == "llama3.2:latest"
    assert result["response_time_ms"] >= 1
    assert requests_log[0][1] == "http://localhost:11434/api/tags"
    assert requests_log[1][1] == "http://localhost:11434/api/chat"

    payload = json.loads(requests_log[1][3])
    assert payload["model"] == "llama3.2:latest"
    assert payload["options"]["num_predict"] == 1
    assert payload["stream"] is False
