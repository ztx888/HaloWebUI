import pathlib
import sys


# Ensure `open_webui` is importable when running tests from repo root.
_BACKEND_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from open_webui.routers import anthropic


def _profile(model_id: str) -> dict:
    return anthropic._build_anthropic_model_profile(model_id)


def test_resolve_thinking_payload_requires_explicit_opt_in():
    thinking, output_config, budget, enabled = anthropic._resolve_thinking_payload(
        {}, model_profile=_profile("claude-sonnet-4-6")
    )
    assert thinking is None
    assert output_config is None
    assert budget is None
    assert enabled is False

    thinking, output_config, budget, enabled = anthropic._resolve_thinking_payload(
        {"thinking": {"type": "enabled"}}, model_profile=_profile("claude-sonnet-4-6")
    )
    assert thinking == {"type": "enabled", "budget_tokens": 8192}
    assert output_config is None
    assert budget == 8192
    assert enabled is True


def test_resolve_thinking_payload_uses_effort_for_46_models():
    thinking, output_config, budget, enabled = anthropic._resolve_thinking_payload(
        {"reasoning_effort": "high"}, model_profile=_profile("claude-sonnet-4-6")
    )
    assert thinking == {"type": "adaptive"}
    assert output_config == {"effort": "high"}
    assert budget is None
    assert enabled is True


def test_resolve_thinking_payload_uses_budget_for_legacy_models():
    thinking, output_config, budget, enabled = anthropic._resolve_thinking_payload(
        {"reasoning_effort": "minimal"}, model_profile=_profile("claude-3-7-sonnet")
    )
    assert thinking == {"type": "enabled", "budget_tokens": 1024}
    assert output_config is None
    assert budget == 1024
    assert enabled is True


def test_is_anyrouter_url():
    assert anthropic._is_anyrouter_url("https://anyrouter.top/v1") is True
    assert anthropic._is_anyrouter_url("https://api.anthropic.com/v1") is False


def test_needs_cc_format_for_anyrouter_premium_models():
    assert anthropic._needs_cc_format("claude-opus-4-6", "https://anyrouter.top/v1") is True
    assert anthropic._needs_cc_format("claude-sonnet-4-6", "https://anyrouter.top/v1") is True
    assert anthropic._needs_cc_format("claude-haiku-4-5", "https://anyrouter.top/v1") is False
    assert anthropic._needs_cc_format("claude-opus-4-6", "https://api.anthropic.com/v1") is False


def test_resolve_proxy_model_alias_keeps_anyrouter_opus_short_alias():
    assert (
        anthropic._resolve_proxy_model_alias(
            "claude-opus-4-6", "https://anyrouter.top/v1"
        )
        == "claude-opus-4-6"
    )


def test_resolve_proxy_model_alias_keeps_other_proxies_unchanged():
    assert (
        anthropic._resolve_proxy_model_alias(
            "claude-opus-4-6", "https://proxy.example.com/v1"
        )
        == "claude-opus-4-6"
    )


def test_apply_cc_format_preserves_display_mode():
    headers = {}
    payload = {
        "model": "claude-opus-4-6",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
        "thinking": {"type": "enabled", "display": "summarized", "budget_tokens": 2048},
        "metadata": {"user_id": "tester"},
    }

    url = anthropic._apply_cc_format(headers, payload, "https://anyrouter.top/v1/messages")

    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["max_tokens"] == 32000
    assert url.endswith("?beta=true")
    assert headers["x-app"] == "cli"
    assert headers["User-Agent"].startswith("claude-cli/")
