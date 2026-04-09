import asyncio
import pathlib
import sys
from contextlib import asynccontextmanager


_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.models.users import UserModel  # noqa: E402
from open_webui.routers import anthropic as anthropic_router  # noqa: E402


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
        self.closed = False

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return str(self._body)

    def release(self):
        self.closed = True


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_anthropic_health_check_strips_prefix_and_merges_extra_body(monkeypatch):
    captured = {}

    @asynccontextmanager
    async def fake_post_preserve_method(session, url, *, json_data, headers, max_redirects=5):
        captured["url"] = url
        captured["json_data"] = json_data
        captured["headers"] = headers
        yield _FakeResponse(200, {"id": "msg_123", "content": []})

    monkeypatch.setattr(anthropic_router, "_post_preserve_method", fake_post_preserve_method)
    monkeypatch.setattr(
        anthropic_router.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _FakeSession(),
    )

    result = asyncio.run(
        anthropic_router.health_check_connection(
            anthropic_router.HealthCheckForm(
                url="https://api.anthropic.com/v1",
                key="test-key",
                config={
                    "prefix_id": "pref",
                    "anthropic_version": "2023-06-01",
                    "anthropic_extra_body": {
                        "metadata": {"source": "health-check"},
                        "model": "should-not-override",
                    },
                },
                model="pref.claude-sonnet-4-5",
            ),
            user=_make_user(),
        )
    )

    assert result["ok"] is True
    assert result["model"] == "claude-sonnet-4-5"
    assert result["response_time_ms"] >= 1
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["json_data"]["model"] == "claude-sonnet-4-5"
    assert captured["json_data"]["max_tokens"] == 16
    assert captured["json_data"]["metadata"] == {"source": "health-check"}
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
