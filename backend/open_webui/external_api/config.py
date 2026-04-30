from open_webui.config import PersistentConfig


ENABLE_EXTERNAL_CLIENT_GATEWAY = PersistentConfig(
    "ENABLE_EXTERNAL_CLIENT_GATEWAY",
    "external_api.enable",
    False,
)

EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI = PersistentConfig(
    "EXTERNAL_CLIENT_GATEWAY_ENABLE_OPENAI",
    "external_api.protocols.openai",
    True,
)

EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC = PersistentConfig(
    "EXTERNAL_CLIENT_GATEWAY_ENABLE_ANTHROPIC",
    "external_api.protocols.anthropic",
    True,
)

EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM = PersistentConfig(
    "EXTERNAL_CLIENT_GATEWAY_DEFAULT_RPM",
    "external_api.default_rpm_limit",
    60,
)

