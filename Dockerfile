# syntax=docker/dockerfile:1

# Initialize device type args
# use build args in the docker build command with --build-arg="BUILDARG=true"
ARG USE_CUDA=false
ARG USE_OLLAMA=false
ARG INSTALL_PROFILE=core
ARG PRELOAD_LOCAL_MODELS=false
# Tested with cu117 for CUDA 11 and cu121 for CUDA 12 (default)
ARG USE_CUDA_VER=cu121
# any sentence transformer model; models to use can be found at https://huggingface.co/models?library=sentence-transformers
# Leaderboard: https://huggingface.co/spaces/mteb/leaderboard
# for better performance and multilangauge support use "intfloat/multilingual-e5-large" (~2.5GB) or "intfloat/multilingual-e5-base" (~1.5GB)
# IMPORTANT: If you change the embedding model (sentence-transformers/all-MiniLM-L6-v2) and vice versa, you aren't able to use RAG Chat with your previous documents loaded in the WebUI! You need to re-embed them.
ARG USE_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ARG USE_RERANKING_MODEL=""
ARG HALO_PG_CLIENT_MAJORS="14 15 16 17 18"

# Tiktoken encoding name; models to use can be found at https://huggingface.co/models?library=tiktoken
ARG USE_TIKTOKEN_ENCODING_NAME="cl100k_base"

ARG BUILD_HASH=dev-build
ARG HALO_RUNTIME_PROFILE=main
# Override at your own risk - non-root configurations are untested
ARG UID=0
ARG GID=0

######## WebUI frontend ########
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS frontend-build
ARG BUILD_HASH
ARG ENABLE_PYODIDE=false
ARG VITE_SOURCEMAP=false

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN npm ci

COPY src ./src
COPY static ./static
COPY scripts ./scripts
COPY CHANGELOG.md ./
COPY postcss.config.js ./
COPY svelte.config.js ./
COPY tailwind.config.js ./
COPY tsconfig.json ./
COPY vite.config.ts ./

ENV APP_BUILD_HASH=${BUILD_HASH} \
    ENABLE_PYODIDE=${ENABLE_PYODIDE} \
    VITE_SOURCEMAP=${VITE_SOURCEMAP}

RUN npm run build

######## WebUI backend builder ########
FROM python:3.11-slim-bookworm AS backend-builder

ARG USE_CUDA
ARG INSTALL_PROFILE
ARG PRELOAD_LOCAL_MODELS
ARG USE_CUDA_VER
ARG USE_EMBEDDING_MODEL
ARG USE_RERANKING_MODEL
ARG USE_TIKTOKEN_ENCODING_NAME
ARG HALO_RUNTIME_PROFILE

ENV USE_CUDA_DOCKER=${USE_CUDA} \
    USE_CUDA_DOCKER_VER=${USE_CUDA_VER} \
    INSTALL_PROFILE=${INSTALL_PROFILE} \
    PRELOAD_LOCAL_MODELS=${PRELOAD_LOCAL_MODELS} \
    RAG_EMBEDDING_MODEL=${USE_EMBEDDING_MODEL} \
    RAG_RERANKING_MODEL=${USE_RERANKING_MODEL} \
    TIKTOKEN_ENCODING_NAME=${USE_TIKTOKEN_ENCODING_NAME} \
    WHISPER_MODEL="base" \
    WHISPER_MODEL_DIR="/app/backend/data/cache/whisper/models" \
    SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models" \
    TIKTOKEN_CACHE_DIR="/app/backend/data/cache/tiktoken" \
    HF_HOME="/app/backend/data/cache/embedding/models" \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app/backend

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv

COPY ./backend/requirements ./requirements

RUN set -eux; \
    requirements_file="requirements/${INSTALL_PROFILE}.txt"; \
    test -f "${requirements_file}"; \
    pip install --no-cache-dir --upgrade pip; \
    if [ "$INSTALL_PROFILE" = "local-rag" ] || [ "$INSTALL_PROFILE" = "local-audio" ] || [ "$INSTALL_PROFILE" = "full" ]; then \
        if [ "$USE_CUDA" = "true" ]; then \
            pip install torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/${USE_CUDA_DOCKER_VER}" --no-cache-dir; \
        else \
            pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu --no-cache-dir; \
        fi; \
    fi; \
    pip install --no-cache-dir -r "${requirements_file}"; \
    if [ "$HALO_RUNTIME_PROFILE" = "main" ]; then \
        pip install --no-cache-dir -r requirements/storage-s3.txt; \
    fi; \
    if [ "$HALO_RUNTIME_PROFILE" = "main" ]; then \
        pip install --no-cache-dir uv; \
    fi; \
    if [ "$PRELOAD_LOCAL_MODELS" = "true" ]; then \
        if [ "$INSTALL_PROFILE" = "local-rag" ] || [ "$INSTALL_PROFILE" = "full" ]; then \
            python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')"; \
        fi; \
        if [ "$INSTALL_PROFILE" = "local-audio" ] || [ "$INSTALL_PROFILE" = "full" ]; then \
            python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
        fi; \
        python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    fi

######## WebUI backend runtime ########
FROM python:3.11-slim-bookworm AS base

ARG USE_CUDA
ARG USE_OLLAMA
ARG INSTALL_PROFILE
ARG PRELOAD_LOCAL_MODELS
ARG USE_CUDA_VER
ARG USE_EMBEDDING_MODEL
ARG USE_RERANKING_MODEL
ARG USE_TIKTOKEN_ENCODING_NAME
ARG HALO_PG_CLIENT_MAJORS
ARG UID
ARG GID
ARG HALO_RUNTIME_PROFILE

ENV ENV=prod \
    PORT=8080 \
    USE_OLLAMA_DOCKER=${USE_OLLAMA} \
    INSTALL_PROFILE=${INSTALL_PROFILE} \
    PRELOAD_LOCAL_MODELS=${PRELOAD_LOCAL_MODELS} \
    USE_CUDA_DOCKER=${USE_CUDA} \
    USE_CUDA_DOCKER_VER=${USE_CUDA_VER} \
    USE_EMBEDDING_MODEL_DOCKER=${USE_EMBEDDING_MODEL} \
    USE_RERANKING_MODEL_DOCKER=${USE_RERANKING_MODEL} \
    ENABLE_LOCAL_MODEL_RUNTIME=false \
    OLLAMA_BASE_URL="/ollama" \
    OPENAI_API_BASE_URL="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false \
    WHISPER_MODEL="base" \
    WHISPER_MODEL_DIR="/app/backend/data/cache/whisper/models" \
    RAG_EMBEDDING_MODEL=${USE_EMBEDDING_MODEL} \
    RAG_RERANKING_MODEL=${USE_RERANKING_MODEL} \
    SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models" \
    TIKTOKEN_ENCODING_NAME="$USE_TIKTOKEN_ENCODING_NAME" \
    TIKTOKEN_CACHE_DIR="/app/backend/data/cache/tiktoken" \
    HF_HOME="/app/backend/data/cache/embedding/models" \
    HALO_RUNTIME_PROFILE=${HALO_RUNTIME_PROFILE} \
    PATH="/opt/venv/bin:${PATH}" \
    HOME=/root

WORKDIR /app/backend

RUN if [ $UID -ne 0 ]; then \
    if [ $GID -ne 0 ]; then \
        addgroup --gid $GID app; \
    fi; \
    adduser --uid $UID --gid $GID --home $HOME --disabled-password --no-create-home app; \
    fi

RUN set -eux; \
    extra_apt_packages=""; \
    pg_client_packages=""; \
    case "$INSTALL_PROFILE" in \
        local-audio) extra_apt_packages="ffmpeg libsm6 libxext6" ;; \
        docs-full|full) extra_apt_packages="pandoc ffmpeg libsm6 libxext6" ;; \
    esac; \
    for major in ${HALO_PG_CLIENT_MAJORS}; do \
        pg_client_packages="${pg_client_packages} postgresql-client-${major}"; \
    done; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        ${extra_apt_packages}; \
    install -d /usr/share/postgresql-common/pgdg; \
    curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc; \
    . /etc/os-release; \
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends ${pg_client_packages}; \
    if [ "$HALO_RUNTIME_PROFILE" = "main" ]; then \
        apt-get install -y --no-install-recommends nodejs npm git; \
    fi; \
    if [ "$USE_OLLAMA" = "true" ]; then \
        curl -fsSL https://ollama.com/install.sh | sh; \
    fi; \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p "$HOME/.cache/chroma" /app/backend/data
RUN echo -n 00000000-0000-0000-0000-000000000000 > "$HOME/.cache/chroma/telemetry_user_id"

COPY --from=backend-builder /opt/venv /opt/venv

# copy built frontend files
COPY --chown=$UID:$GID --from=frontend-build /app/build /app/build
COPY --chown=$UID:$GID --from=frontend-build /app/CHANGELOG.md /app/CHANGELOG.md
COPY --chown=$UID:$GID --from=frontend-build /app/package.json /app/package.json

# copy backend files
COPY --chown=$UID:$GID ./backend .

# sync frontend static assets into backend static folder
COPY --chown=$UID:$GID --from=frontend-build /app/backend/open_webui/static /app/backend/open_webui/static

RUN chown -R $UID:$GID /app "$HOME"

EXPOSE 8080

HEALTHCHECK CMD python -c "import json, os, urllib.request; response = urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"8080\")}/health'); assert json.loads(response.read())[\"status\"] is True" || exit 1

USER $UID:$GID

ARG BUILD_HASH
ENV WEBUI_BUILD_VERSION=${BUILD_HASH}
ENV DOCKER=true

CMD ["bash", "start.sh"]
