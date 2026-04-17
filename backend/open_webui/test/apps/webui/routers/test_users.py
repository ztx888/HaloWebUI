from test.util.abstract_integration_test import AbstractPostgresTest
from test.util.mock_user import mock_webui_user
import time


def _get_user_by_id(data, param):
    return next((item for item in data if item["id"] == param), None)


def _assert_user(data, id, **kwargs):
    user = _get_user_by_id(data, id)
    assert user is not None
    comparison_data = {
        "name": f"user {id}",
        "email": f"user{id}@openwebui.com",
        "profile_image_url": f"/user{id}.png",
        "role": "user",
        **kwargs,
    }
    for key, value in comparison_data.items():
        assert user[key] == value


class TestUsers(AbstractPostgresTest):
    BASE_PATH = "/api/v1/users"

    def setup_class(cls):
        super().setup_class()
        from open_webui.models.users import Users

        cls.users = Users

    def setup_method(self):
        super().setup_method()
        self.users.insert_new_user(
            id="1",
            name="user 1",
            email="user1@openwebui.com",
            profile_image_url="/user1.png",
            role="user",
        )
        self.users.insert_new_user(
            id="2",
            name="user 2",
            email="user2@openwebui.com",
            profile_image_url="/user2.png",
            role="user",
        )

    def test_users(self):
        # Get all users
        with mock_webui_user(id="3"):
            response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200
        assert len(response.json()) == 2
        data = response.json()
        _assert_user(data, "1")
        _assert_user(data, "2")

        # update role
        with mock_webui_user(id="3"):
            response = self.fast_api_client.post(
                self.create_url("/update/role"), json={"id": "2", "role": "admin"}
            )
        assert response.status_code == 200
        _assert_user([response.json()], "2", role="admin")

        # Get all users
        with mock_webui_user(id="3"):
            response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200
        assert len(response.json()) == 2
        data = response.json()
        _assert_user(data, "1")
        _assert_user(data, "2", role="admin")

        # Get (empty) user settings
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/user/settings"))
        assert response.status_code == 200
        assert response.json() is None

        # Update user settings
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "ui": {"attr1": "value1", "attr2": "value2"},
                    "model_config": {"attr3": "value3", "attr4": "value4"},
                },
            )
        assert response.status_code == 200
        assert response.json() == {
            "ui": {"attr1": "value1", "attr2": "value2"},
            "revision": 1,
            "model_config": {"attr3": "value3", "attr4": "value4"},
        }

        # Get user settings
        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/user/settings"))
        assert response.status_code == 200
        assert response.json() == {
            "ui": {"attr1": "value1", "attr2": "value2"},
            "revision": 1,
            "model_config": {"attr3": "value3", "attr4": "value4"},
        }

        # Get (empty) user info
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/user/info"))
        assert response.status_code == 200
        assert response.json() is None

        # Update user info
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(
                self.create_url("/user/info/update"),
                json={"attr1": "value1", "attr2": "value2"},
            )
        assert response.status_code == 200

        # Get user info
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/user/info"))
        assert response.status_code == 200
        assert response.json() == {"attr1": "value1", "attr2": "value2"}

        # Get user by id
        with mock_webui_user(id="1"):
            response = self.fast_api_client.get(self.create_url("/2"))
        assert response.status_code == 200
        assert response.json() == {"name": "user 2", "profile_image_url": "/user2.png"}

        # Update user by id
        with mock_webui_user(id="1"):
            response = self.fast_api_client.post(
                self.create_url("/2/update"),
                json={
                    "name": "user 2 updated",
                    "email": "user2-updated@openwebui.com",
                    "profile_image_url": "/user2-updated.png",
                },
            )
        assert response.status_code == 200

        # Get all users
        with mock_webui_user(id="3"):
            response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200
        assert len(response.json()) == 2
        data = response.json()
        _assert_user(data, "1")
        _assert_user(
            data,
            "2",
            role="admin",
            name="user 2 updated",
            email="user2-updated@openwebui.com",
            profile_image_url="/user2-updated.png",
        )

        # Delete user by id
        with mock_webui_user(id="1"):
            response = self.fast_api_client.delete(self.create_url("/2"))
        assert response.status_code == 200

        # Get all users
        with mock_webui_user(id="3"):
            response = self.fast_api_client.get(self.create_url(""))
        assert response.status_code == 200
        assert len(response.json()) == 1
        data = response.json()
        _assert_user(data, "1")

    def test_update_user_settings_invalidates_model_cache_when_connections_change(
        self, monkeypatch
    ):
        from main import app
        from open_webui.utils import auth as auth_utils

        invalidated = []

        monkeypatch.setattr(
            "open_webui.utils.models.invalidate_base_model_cache",
            lambda user_id=None: invalidated.append(user_id),
        )

        app.state.BASE_MODELS = ["stale"]
        app.state.MODELS = {"stale": {"id": "stale"}}
        auth_utils._user_cache["2"] = (time.monotonic(), {"id": "2", "stale": True})

        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "ui": {
                        "connections": {
                            "openai": {
                                "OPENAI_API_BASE_URLS": ["https://wzw.pp.ua/v1"],
                                "OPENAI_API_KEYS": ["sk-test"],
                                "OPENAI_API_CONFIGS": {
                                    "0": {
                                        "remark": "Wong",
                                    }
                                },
                            }
                        }
                    }
                },
            )

        assert response.status_code == 200
        assert invalidated == ["2"]
        assert app.state.BASE_MODELS is None
        assert app.state.MODELS == {}
        assert "2" not in auth_utils._user_cache

    def test_update_user_settings_merges_ui_patch_and_tracks_revision(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "ui": {
                        "connections": {
                            "openai": {
                                "OPENAI_API_BASE_URLS": ["https://api.example.com/v1"],
                            }
                        }
                    }
                },
            )

        assert response.status_code == 200
        assert response.json()["revision"] == 1

        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "revision": 1,
                    "ui": {
                        "autoFollowUps": False,
                    },
                },
            )

        assert response.status_code == 200
        assert response.json() == {
            "ui": {
                "connections": {
                    "openai": {
                        "OPENAI_API_BASE_URLS": ["https://api.example.com/v1"],
                    }
                },
                "autoFollowUps": False,
            },
            "revision": 2,
        }

    def test_update_user_settings_replaces_connections_subtree_without_leaking_old_flags(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "ui": {
                        "connections": {
                            "openai": {
                                "OPENAI_API_BASE_URLS": [
                                    "https://api.example.com/v1",
                                    "https://old.example.com/v1",
                                ],
                                "OPENAI_API_KEYS": ["sk-old", "sk-delete"],
                                "OPENAI_API_CONFIGS": {
                                    "0": {
                                        "remark": "Primary",
                                        "azure": True,
                                        "api_version": "2025-01-01-preview",
                                        "use_responses_api": True,
                                        "force_mode": True,
                                        "native_file_inputs_enabled": True,
                                        "headers": {"X-Test": "1"},
                                        "model_ids": ["gpt-4.1"],
                                    },
                                    "1": {
                                        "remark": "Secondary",
                                        "use_responses_api": True,
                                    },
                                },
                            },
                            "anthropic": {
                                "ANTHROPIC_API_BASE_URLS": ["https://api.anthropic.com/v1"],
                                "ANTHROPIC_API_KEYS": ["sk-anthropic"],
                                "ANTHROPIC_API_CONFIGS": {
                                    "0": {
                                        "remark": "Anthropic",
                                        "anthropic_extra_body": {
                                            "thinking": {"type": "enabled"}
                                        },
                                    }
                                },
                            },
                        },
                        "autoFollowUps": False,
                    }
                },
            )

        assert response.status_code == 200
        assert response.json()["revision"] == 1

        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "revision": 1,
                    "ui": {
                        "connections": {
                            "openai": {
                                "OPENAI_API_BASE_URLS": ["https://api.example.com/v1"],
                                "OPENAI_API_KEYS": ["sk-new"],
                                "OPENAI_API_CONFIGS": {
                                    "0": {
                                        "remark": "Primary",
                                        "auth_type": "bearer",
                                        "model_ids": ["gpt-4.1"],
                                    }
                                },
                            }
                        }
                    },
                },
            )

        assert response.status_code == 200

        payload = response.json()
        assert payload["revision"] == 2
        assert payload["ui"]["autoFollowUps"] is False
        assert "anthropic" not in payload["ui"]["connections"]
        assert payload["ui"]["connections"]["openai"]["OPENAI_API_BASE_URLS"] == [
            "https://api.example.com/v1"
        ]
        assert payload["ui"]["connections"]["openai"]["OPENAI_API_KEYS"] == ["sk-new"]
        assert payload["ui"]["connections"]["openai"]["OPENAI_API_CONFIGS"] == {
            "0": {
                "remark": "Primary",
                "auth_type": "bearer",
                "model_ids": ["gpt-4.1"],
            }
        }

    def test_update_user_settings_rejects_stale_revision(self):
        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={"ui": {"theme": "dark"}},
            )

        assert response.status_code == 200
        assert response.json()["revision"] == 1

        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "revision": 0,
                    "ui": {"theme": "light"},
                },
            )

        assert response.status_code == 409
        assert response.json()["detail"] == (
            "User settings were updated elsewhere. Please retry with the latest settings."
        )

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/user/settings"))

        assert response.status_code == 200
        assert response.json() == {
            "ui": {"theme": "dark"},
            "revision": 1,
        }

    def test_get_user_settings_backfills_missing_admin_provider_connections_once(
        self, monkeypatch
    ):
        from main import app

        self.users.update_user_role_by_id("2", "admin")
        self.users.update_user_by_id(
            "2",
            {
                "settings": {
                    "ui": {
                        "connections": {
                            "gemini": {
                                "GEMINI_API_BASE_URLS": ["https://gemini.example.com/v1beta"],
                                "GEMINI_API_KEYS": ["gem-key"],
                                "GEMINI_API_CONFIGS": {"0": {"remark": "Gemini"}},
                            }
                        }
                    }
                }
            },
        )

        monkeypatch.setattr(
            app.state.config,
            "OPENAI_API_BASE_URLS",
            ["https://api.example.com/v1"],
            raising=False,
        )
        monkeypatch.setattr(
            app.state.config,
            "OPENAI_API_KEYS",
            ["sk-openai"],
            raising=False,
        )
        monkeypatch.setattr(
            app.state.config,
            "OPENAI_API_CONFIGS",
            {"0": {"remark": "OpenAI"}},
            raising=False,
        )

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/user/settings"))

        assert response.status_code == 200
        data = response.json()
        assert data["ui"]["connections"]["gemini"]["GEMINI_API_BASE_URLS"] == [
            "https://gemini.example.com/v1beta"
        ]
        assert data["ui"]["connections"]["openai"]["OPENAI_API_BASE_URLS"] == [
            "https://api.example.com/v1"
        ]
        assert data["ui"]["_legacy_global_connections_seeded_v1"] is True

        # Once the one-time backfill marker exists, removed providers should stay removed.
        self.users.update_user_by_id(
            "2",
            {
                "settings": {
                    "ui": {
                        "connections": {
                            "gemini": {
                                "GEMINI_API_BASE_URLS": ["https://gemini.example.com/v1beta"],
                                "GEMINI_API_KEYS": ["gem-key"],
                                "GEMINI_API_CONFIGS": {"0": {"remark": "Gemini"}},
                            }
                        },
                        "_legacy_global_connections_seeded_v1": True,
                    }
                }
            },
        )

        with mock_webui_user(id="2"):
            response = self.fast_api_client.get(self.create_url("/user/settings"))

        assert response.status_code == 200
        data = response.json()
        assert "openai" not in data["ui"]["connections"]

    def test_update_user_settings_does_not_invalidate_model_cache_for_unrelated_ui_changes(
        self, monkeypatch
    ):
        from main import app
        from open_webui.utils import auth as auth_utils

        invalidated = []

        monkeypatch.setattr(
            "open_webui.utils.models.invalidate_base_model_cache",
            lambda user_id=None: invalidated.append(user_id),
        )

        app.state.BASE_MODELS = ["keep"]
        app.state.MODELS = {"keep": {"id": "keep"}}
        auth_utils._user_cache["2"] = (time.monotonic(), {"id": "2", "stale": True})

        with mock_webui_user(id="2"):
            response = self.fast_api_client.post(
                self.create_url("/user/settings/update"),
                json={
                    "ui": {"theme": "dark"},
                },
            )

        assert response.status_code == 200
        assert invalidated == []
        assert app.state.BASE_MODELS == ["keep"]
        assert app.state.MODELS == {"keep": {"id": "keep"}}
        assert "2" not in auth_utils._user_cache
