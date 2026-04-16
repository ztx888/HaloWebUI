import importlib.metadata
import json
import logging
import os
import pkgutil
import sys
import shutil
from pathlib import Path

import markdown
from bs4 import BeautifulSoup
from open_webui.constants import ERROR_MESSAGES

####################################
# Load .env file
####################################

OPEN_WEBUI_DIR = Path(__file__).parent  # the path containing this file
print(OPEN_WEBUI_DIR)

BACKEND_DIR = OPEN_WEBUI_DIR.parent  # the path containing this file
BASE_DIR = BACKEND_DIR.parent  # the path containing the backend/

print(BACKEND_DIR)
print(BASE_DIR)

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(str(BASE_DIR / ".env")))
except ImportError:
    print("dotenv not installed, skipping...")

DOCKER = os.environ.get("DOCKER", "False").lower() == "true"
ENABLE_LOCAL_MODEL_RUNTIME = (
    os.environ.get("ENABLE_LOCAL_MODEL_RUNTIME", "False").lower() == "true"
)

# device type embedding models - "cpu" (default), "cuda" (nvidia gpu required) or "mps" (apple silicon) - choosing this right can lead to better performance
USE_CUDA = os.environ.get("USE_CUDA_DOCKER", "false")

if ENABLE_LOCAL_MODEL_RUNTIME and USE_CUDA.lower() == "true":
    try:
        import torch

        assert torch.cuda.is_available(), "CUDA not available"
        DEVICE_TYPE = "cuda"
    except Exception as e:
        cuda_error = (
            "Error when testing CUDA but USE_CUDA_DOCKER is true. "
            f"Resetting USE_CUDA_DOCKER to false: {e}"
        )
        os.environ["USE_CUDA_DOCKER"] = "false"
        USE_CUDA = "false"
        DEVICE_TYPE = "cpu"
else:
    DEVICE_TYPE = "cpu"

if ENABLE_LOCAL_MODEL_RUNTIME:
    try:
        import torch

        if torch.backends.mps.is_available() and torch.backends.mps.is_built():
            DEVICE_TYPE = "mps"
    except Exception:
        pass

####################################
# LOGGING
####################################

GLOBAL_LOG_LEVEL = os.environ.get("GLOBAL_LOG_LEVEL", "").upper()

# LOG_FORMAT: "text" (default) or "json" for structured JSON logging
LOG_FORMAT = os.environ.get("LOG_FORMAT", "text").lower()

if GLOBAL_LOG_LEVEL in logging.getLevelNamesMapping():
    if LOG_FORMAT == "json":
        import json as _json

        class _JsonFormatter(logging.Formatter):
            def format(self, record):
                return _json.dumps({
                    "ts": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": record.getMessage(),
                })

        _handler = logging.StreamHandler(sys.stdout)
        _handler.setFormatter(_JsonFormatter())
        logging.basicConfig(handlers=[_handler], level=GLOBAL_LOG_LEVEL, force=True)
    else:
        logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL, force=True)
else:
    GLOBAL_LOG_LEVEL = "INFO"

log = logging.getLogger(__name__)
log.info(f"GLOBAL_LOG_LEVEL: {GLOBAL_LOG_LEVEL}")

if "cuda_error" in locals():
    log.exception(cuda_error)
    del cuda_error

log_sources = [
    "AUDIO",
    "COMFYUI",
    "CONFIG",
    "DB",
    "IMAGES",
    "MAIN",
    "MODELS",
    "OLLAMA",
    "OPENAI",
    "RAG",
    "WEBHOOK",
    "SOCKET",
    "OAUTH",
]

SRC_LOG_LEVELS = {}

for source in log_sources:
    log_env_var = source + "_LOG_LEVEL"
    SRC_LOG_LEVELS[source] = os.environ.get(log_env_var, "").upper()
    if SRC_LOG_LEVELS[source] not in logging.getLevelNamesMapping():
        SRC_LOG_LEVELS[source] = GLOBAL_LOG_LEVEL
    log.info(f"{log_env_var}: {SRC_LOG_LEVELS[source]}")

log.setLevel(SRC_LOG_LEVELS["CONFIG"])

WEBUI_NAME = os.environ.get("WEBUI_NAME", "Halo WebUI")

WEBUI_FAVICON_URL = "https://openwebui.com/favicon.png"

TRUSTED_SIGNATURE_KEY = os.environ.get("TRUSTED_SIGNATURE_KEY", "")

####################################
# ENV (dev,test,prod)
####################################

ENV = os.environ.get("ENV", "dev")

FROM_INIT_PY = os.environ.get("FROM_INIT_PY", "False").lower() == "true"

try:
    PACKAGE_DATA = json.loads((BASE_DIR / "package.json").read_text())
except Exception:
    try:
        PACKAGE_DATA = {"version": importlib.metadata.version("open-webui")}
    except Exception:
        PACKAGE_DATA = {"version": "0.0.0"}

VERSION = PACKAGE_DATA["version"]


# Function to parse each section
def parse_section(section):
    items = []
    for li in section.find_all("li"):
        # Extract raw HTML string
        raw_html = str(li)

        # Extract text without HTML tags
        text = li.get_text(separator=" ", strip=True)

        # Split into title and content
        parts = text.split(": ", 1)
        title = parts[0].strip() if len(parts) > 1 else ""
        content = parts[1].strip() if len(parts) > 1 else text

        items.append({"title": title, "content": content, "raw": raw_html})
    return items


def load_changelog():
    try:
        changelog_path = BASE_DIR / "CHANGELOG.md"
        with open(str(changelog_path.absolute()), "r", encoding="utf8") as file:
            changelog_content = file.read()
    except Exception:
        changelog_content = (
            pkgutil.get_data("open_webui", "CHANGELOG.md") or b""
        ).decode()

    html_content = markdown.markdown(changelog_content)
    soup = BeautifulSoup(html_content, "html.parser")
    changelog_json = {}

    for version in soup.find_all("h2"):
        version_number = version.get_text().strip().split(" - ")[0][1:-1]
        date = version.get_text().strip().split(" - ")[1]
        version_data = {"date": date}
        current = version.find_next_sibling()

        while current and current.name != "h2":
            if current.name == "h3":
                section_title = current.get_text().lower()
                section_items = parse_section(current.find_next_sibling("ul"))
                version_data[section_title] = section_items

            current = current.find_next_sibling()

        changelog_json[version_number] = version_data

    return changelog_json


CHANGELOG = load_changelog()

####################################
# SAFE_MODE
####################################

SAFE_MODE = os.environ.get("SAFE_MODE", "false").lower() == "true"

####################################
# ENABLE_FORWARD_USER_INFO_HEADERS
####################################

ENABLE_FORWARD_USER_INFO_HEADERS = (
    os.environ.get("ENABLE_FORWARD_USER_INFO_HEADERS", "False").lower() == "true"
)

####################################
# WEBUI_BUILD_HASH
####################################

WEBUI_BUILD_HASH = os.environ.get("WEBUI_BUILD_HASH", "dev-build")

####################################
# DATA/FRONTEND BUILD DIR
####################################

DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data")).resolve()

if FROM_INIT_PY:
    NEW_DATA_DIR = Path(os.getenv("DATA_DIR", OPEN_WEBUI_DIR / "data")).resolve()
    NEW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check if the data directory exists in the package directory
    if DATA_DIR.exists() and DATA_DIR != NEW_DATA_DIR:
        log.info(f"Moving {DATA_DIR} to {NEW_DATA_DIR}")
        for item in DATA_DIR.iterdir():
            dest = NEW_DATA_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

        # Zip the data directory
        shutil.make_archive(DATA_DIR.parent / "open_webui_data", "zip", DATA_DIR)

        # Remove the old data directory
        shutil.rmtree(DATA_DIR)

    DATA_DIR = Path(os.getenv("DATA_DIR", OPEN_WEBUI_DIR / "data"))

STATIC_DIR = Path(os.getenv("STATIC_DIR", OPEN_WEBUI_DIR / "static"))

FONTS_DIR = Path(os.getenv("FONTS_DIR", OPEN_WEBUI_DIR / "static" / "fonts"))

FRONTEND_BUILD_DIR = Path(os.getenv("FRONTEND_BUILD_DIR", BASE_DIR / "build")).resolve()

if FROM_INIT_PY:
    FRONTEND_BUILD_DIR = Path(
        os.getenv("FRONTEND_BUILD_DIR", OPEN_WEBUI_DIR / "frontend")
    ).resolve()

####################################
# Database
####################################

# Check if the file exists
if os.path.exists(f"{DATA_DIR}/ollama.db"):
    # Rename the file
    os.rename(f"{DATA_DIR}/ollama.db", f"{DATA_DIR}/webui.db")
    log.info("Database migrated from Ollama-WebUI successfully.")
else:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR}/webui.db")

# Replace the postgres:// with postgresql://
if "postgres://" in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://")

# SQLCipher: encrypted SQLite database
DATABASE_ENCRYPTION_KEY = os.environ.get("DATABASE_ENCRYPTION_KEY", "")

DATABASE_SCHEMA = os.environ.get("DATABASE_SCHEMA", None)

DATABASE_POOL_SIZE = os.environ.get("DATABASE_POOL_SIZE", None)

if DATABASE_POOL_SIZE is not None:
    try:
        DATABASE_POOL_SIZE = int(DATABASE_POOL_SIZE)
    except Exception:
        DATABASE_POOL_SIZE = None

DATABASE_POOL_MAX_OVERFLOW = os.environ.get("DATABASE_POOL_MAX_OVERFLOW", 0)

if DATABASE_POOL_MAX_OVERFLOW == "":
    DATABASE_POOL_MAX_OVERFLOW = 0
else:
    try:
        DATABASE_POOL_MAX_OVERFLOW = int(DATABASE_POOL_MAX_OVERFLOW)
    except Exception:
        DATABASE_POOL_MAX_OVERFLOW = 0

DATABASE_POOL_TIMEOUT = os.environ.get("DATABASE_POOL_TIMEOUT", 30)

if DATABASE_POOL_TIMEOUT == "":
    DATABASE_POOL_TIMEOUT = 30
else:
    try:
        DATABASE_POOL_TIMEOUT = int(DATABASE_POOL_TIMEOUT)
    except Exception:
        DATABASE_POOL_TIMEOUT = 30

DATABASE_POOL_RECYCLE = os.environ.get("DATABASE_POOL_RECYCLE", 3600)

if DATABASE_POOL_RECYCLE == "":
    DATABASE_POOL_RECYCLE = 3600
else:
    try:
        DATABASE_POOL_RECYCLE = int(DATABASE_POOL_RECYCLE)
    except Exception:
        DATABASE_POOL_RECYCLE = 3600

RESET_CONFIG_ON_START = (
    os.environ.get("RESET_CONFIG_ON_START", "False").lower() == "true"
)

ENABLE_REALTIME_CHAT_SAVE = (
    os.environ.get("ENABLE_REALTIME_CHAT_SAVE", "False").lower() == "true"
)

####################################
# REDIS
####################################

REDIS_URL = os.environ.get("REDIS_URL", "")
REDIS_KEY_PREFIX = os.environ.get("REDIS_KEY_PREFIX", "open_webui:")
REDIS_CLUSTER_MODE = os.environ.get("REDIS_CLUSTER_MODE", "").lower() == "true"
REDIS_SENTINEL_HOSTS = os.environ.get("REDIS_SENTINEL_HOSTS", "")
REDIS_SENTINEL_PORT = os.environ.get("REDIS_SENTINEL_PORT", "26379")

####################################
# UVICORN WORKERS
####################################

# Number of uvicorn worker processes for handling requests
UVICORN_WORKERS = os.environ.get("UVICORN_WORKERS", "1")
try:
    UVICORN_WORKERS = int(UVICORN_WORKERS)
    if UVICORN_WORKERS < 1:
        UVICORN_WORKERS = 1
except ValueError:
    UVICORN_WORKERS = 1
    log.info(f"Invalid UVICORN_WORKERS value, defaulting to {UVICORN_WORKERS}")

####################################
# WEBUI_AUTH (Required for security)
####################################

WEBUI_AUTH = os.environ.get("WEBUI_AUTH", "True").lower() == "true"
WEBUI_AUTH_TRUSTED_EMAIL_HEADER = os.environ.get(
    "WEBUI_AUTH_TRUSTED_EMAIL_HEADER", None
)
WEBUI_AUTH_TRUSTED_NAME_HEADER = os.environ.get("WEBUI_AUTH_TRUSTED_NAME_HEADER", None)

BYPASS_MODEL_ACCESS_CONTROL = (
    os.environ.get("BYPASS_MODEL_ACCESS_CONTROL", "False").lower() == "true"
)

####################################
# AUTO ADMIN CREATION
####################################

WEBUI_ADMIN_EMAIL = os.environ.get("WEBUI_ADMIN_EMAIL", "")
WEBUI_ADMIN_PASSWORD = os.environ.get("WEBUI_ADMIN_PASSWORD", "")
WEBUI_ADMIN_NAME = os.environ.get("WEBUI_ADMIN_NAME", "Admin")

####################################
# WEBUI_SECRET_KEY
####################################

WEBUI_SECRET_KEY = os.environ.get(
    "WEBUI_SECRET_KEY",
    os.environ.get(
        "WEBUI_JWT_SECRET_KEY", "t0p-s3cr3t"
    ),  # DEPRECATED: remove at next major version
)

WEBUI_SESSION_COOKIE_SAME_SITE = os.environ.get("WEBUI_SESSION_COOKIE_SAME_SITE", "lax")

WEBUI_SESSION_COOKIE_SECURE = (
    os.environ.get("WEBUI_SESSION_COOKIE_SECURE", "false").lower() == "true"
)

WEBUI_AUTH_COOKIE_SAME_SITE = os.environ.get(
    "WEBUI_AUTH_COOKIE_SAME_SITE", WEBUI_SESSION_COOKIE_SAME_SITE
)

WEBUI_AUTH_COOKIE_SECURE = (
    os.environ.get(
        "WEBUI_AUTH_COOKIE_SECURE",
        os.environ.get("WEBUI_SESSION_COOKIE_SECURE", "false"),
    ).lower()
    == "true"
)

if WEBUI_AUTH and WEBUI_SECRET_KEY == "":
    raise ValueError(ERROR_MESSAGES.ENV_VAR_NOT_FOUND)

ENABLE_WEBSOCKET_SUPPORT = (
    os.environ.get("ENABLE_WEBSOCKET_SUPPORT", "True").lower() == "true"
)

WEBSOCKET_MANAGER = os.environ.get("WEBSOCKET_MANAGER", "")

WEBSOCKET_REDIS_URL = os.environ.get("WEBSOCKET_REDIS_URL", REDIS_URL)
WEBSOCKET_REDIS_LOCK_TIMEOUT = os.environ.get("WEBSOCKET_REDIS_LOCK_TIMEOUT", 60)

WEBSOCKET_SENTINEL_HOSTS = os.environ.get("WEBSOCKET_SENTINEL_HOSTS", "")

WEBSOCKET_SENTINEL_PORT = os.environ.get("WEBSOCKET_SENTINEL_PORT", "26379")

WEBSOCKET_EVENT_CALLER_TIMEOUT = int(
    os.environ.get("WEBSOCKET_EVENT_CALLER_TIMEOUT", "30")
)

AIOHTTP_CLIENT_TIMEOUT = os.environ.get("AIOHTTP_CLIENT_TIMEOUT", "")

if AIOHTTP_CLIENT_TIMEOUT == "":
    AIOHTTP_CLIENT_TIMEOUT = None
else:
    try:
        AIOHTTP_CLIENT_TIMEOUT = int(AIOHTTP_CLIENT_TIMEOUT)
    except Exception:
        AIOHTTP_CLIENT_TIMEOUT = 300

AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = os.environ.get(
    "AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST",
    os.environ.get("AIOHTTP_CLIENT_TIMEOUT_OPENAI_MODEL_LIST", "10"),
)

if AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST == "":
    AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = None
else:
    try:
        AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = int(AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST)
    except Exception:
        AIOHTTP_CLIENT_TIMEOUT_MODEL_LIST = 10

REQUESTS_VERIFY = os.environ.get("REQUESTS_VERIFY", "True").lower() == "true"

AIOHTTP_CLIENT_SESSION_SSL = (
    os.environ.get("AIOHTTP_CLIENT_SESSION_SSL", "True").lower() == "true"
)

AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = os.environ.get(
    "AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA", "10"
)

if AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA == "":
    AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = None
else:
    try:
        AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = int(
            AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA
        )
    except Exception:
        AIOHTTP_CLIENT_TIMEOUT_TOOL_SERVER_DATA = 10

MCP_STDIO_IDLE_TIMEOUT = os.environ.get("MCP_STDIO_IDLE_TIMEOUT", "180")

try:
    MCP_STDIO_IDLE_TIMEOUT = int(MCP_STDIO_IDLE_TIMEOUT)
except Exception:
    MCP_STDIO_IDLE_TIMEOUT = 180

MCP_STDIO_START_TIMEOUT = os.environ.get("MCP_STDIO_START_TIMEOUT", "60")

try:
    MCP_STDIO_START_TIMEOUT = int(MCP_STDIO_START_TIMEOUT)
except Exception:
    MCP_STDIO_START_TIMEOUT = 60

MCP_STDIO_ALLOWED_COMMANDS = os.environ.get(
    "MCP_STDIO_ALLOWED_COMMANDS",
    "npx,node,python,python3,uvx,uv,deno",
)

MCP_TOOL_CALL_TIMEOUT = os.environ.get("MCP_TOOL_CALL_TIMEOUT", "30")

try:
    MCP_TOOL_CALL_TIMEOUT = int(MCP_TOOL_CALL_TIMEOUT)
except Exception:
    MCP_TOOL_CALL_TIMEOUT = 30

####################################
# TOOL SERVER SSL
####################################

# Path to a custom CA bundle or certificate file for MCP/tool server connections.
# Set to "" to disable SSL verification (not recommended for production).
AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL = os.environ.get(
    "AIOHTTP_CLIENT_SESSION_TOOL_SERVER_SSL", ""
)

####################################
# FOLDER LIMITS
####################################

# Maximum number of chats allowed per folder. 0 = unlimited.
FOLDER_MAX_ITEM_COUNT = int(os.environ.get("FOLDER_MAX_ITEM_COUNT", "0"))

####################################
# OFFLINE_MODE
####################################

OFFLINE_MODE = os.environ.get("OFFLINE_MODE", "false").lower() == "true"

if OFFLINE_MODE:
    os.environ["HF_HUB_OFFLINE"] = "1"

####################################
# AUDIT LOGGING
####################################
# Where to store log file
AUDIT_LOGS_FILE_PATH = f"{DATA_DIR}/audit.log"
# Maximum size of a file before rotating into a new log file
AUDIT_LOG_FILE_ROTATION_SIZE = os.getenv("AUDIT_LOG_FILE_ROTATION_SIZE", "10MB")
# METADATA | REQUEST | REQUEST_RESPONSE
AUDIT_LOG_LEVEL = os.getenv("AUDIT_LOG_LEVEL", "NONE").upper()
try:
    MAX_BODY_LOG_SIZE = int(os.environ.get("MAX_BODY_LOG_SIZE") or 2048)
except ValueError:
    MAX_BODY_LOG_SIZE = 2048

# Comma separated list for urls to exclude from audit
AUDIT_EXCLUDED_PATHS = os.getenv("AUDIT_EXCLUDED_PATHS", "/chats,/chat,/folders").split(
    ","
)
AUDIT_EXCLUDED_PATHS = [path.strip() for path in AUDIT_EXCLUDED_PATHS]
AUDIT_EXCLUDED_PATHS = [path.lstrip("/") for path in AUDIT_EXCLUDED_PATHS]

####################################
# OPENTELEMETRY
####################################

ENABLE_OTEL = os.environ.get("ENABLE_OTEL", "False").lower() == "true"
OTEL_EXPORTER_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317"
)
OTEL_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "open-webui")
OTEL_RESOURCE_ATTRIBUTES = os.environ.get(
    "OTEL_RESOURCE_ATTRIBUTES", ""
)  # e.g. key1=val1,key2=val2
OTEL_TRACES_SAMPLER = os.environ.get(
    "OTEL_TRACES_SAMPLER", "parentbased_always_on"
).lower()

####################################
# TOOLS/FUNCTIONS PIP OPTIONS
####################################

PIP_OPTIONS = os.getenv("PIP_OPTIONS", "").split()
PIP_PACKAGE_INDEX_OPTIONS = os.getenv("PIP_PACKAGE_INDEX_OPTIONS", "").split()


####################################
# PROGRESSIVE WEB APP OPTIONS
####################################

EXTERNAL_PWA_MANIFEST_URL = os.environ.get("EXTERNAL_PWA_MANIFEST_URL")


####################################
# SCIM 2.0 PROVISIONING
####################################

ENABLE_SCIM = os.environ.get("ENABLE_SCIM", "False").lower() == "true"
SCIM_AUTH_BEARER_TOKEN = os.environ.get("SCIM_AUTH_BEARER_TOKEN", "")


####################################
# API RATE LIMITING
####################################

ENABLE_API_RATE_LIMIT = os.environ.get("ENABLE_API_RATE_LIMIT", "False").lower() == "true"
API_RATE_LIMIT_RPM = int(os.environ.get("API_RATE_LIMIT_RPM", "60"))  # requests per minute
