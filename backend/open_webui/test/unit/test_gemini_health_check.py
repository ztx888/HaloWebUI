import asyncio
import pathlib
import sys


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.models.users import UserModel  # noqa: E402
from open_webui.routers import gemini as gemini_router  # noqa: E402


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

    def post(self, url, headers=None, json=None):
        self._requests_log.append(("POST", url, headers, json))
        return self._responses.pop(0)


def test_gemini_health_check_discovers_generate_content_model_and_uses_query_auth(monkeypatch):
    requests_log = []

    async def fake_send_get_request(url: str, key: str = None, config: dict = None):
        return {
            "models": [
                {
                    "name": "models/text-embedding-004",
                    "supportedGenerationMethods": ["embedContent"],
                },
                {
                    "name": "models/gemini-2.5-flash",
                    "supportedGenerationMethods": ["generateContent"],
                },
            ]
        }

    monkeypatch.setattr(gemini_router, "send_get_request", fake_send_get_request)
    monkeypatch.setattr(
        gemini_router.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _FakeSession(
            [_FakeResponse(200, {"candidates": []})], requests_log
        ),
    )

    result = asyncio.run(
        gemini_router.health_check_connection(
            gemini_router.HealthCheckForm(
                url="https://generativelanguage.googleapis.com/v1beta",
                key="test-key",
                config={"auth_type": "query"},
            ),
            user=_make_user(),
        )
    )

    assert result["ok"] is True
    assert result["model"] == "gemini-2.5-flash"
    assert result["response_time_ms"] >= 1

    assert requests_log[0][0] == "POST"
    assert (
        requests_log[0][1]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=test-key"
    )
    assert requests_log[0][3]["generationConfig"]["maxOutputTokens"] == 16
