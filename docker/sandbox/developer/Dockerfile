ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1

FROM ${PYTHON_IMAGE}

ARG SANDBOX_IMAGE_VERSION=unknown
LABEL org.opencontainers.image.title="Nanobot Developer Sandbox" \
      org.opencontainers.image.version="${SANDBOX_IMAGE_VERSION}" \
      com.nanobot.sandbox-image="true" \
      com.nanobot.sandbox-profile="developer"

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        diffutils \
        findutils \
        g++ \
        gcc \
        git \
        grep \
        gzip \
        jq \
        make \
        nodejs \
        npm \
        openssh-client \
        patch \
        pkg-config \
        ripgrep \
        sed \
        tar \
        unzip \
        wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /usr/local/share/nanobot-sandbox/requirements.lock
RUN pip install --no-cache-dir --require-hashes \
        -r /usr/local/share/nanobot-sandbox/requirements.lock \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --shell /bin/bash sandbox \
    && mkdir -p /workspace /inputs /runtime \
    && chown 10001:10001 /workspace /runtime \
    && chmod 0755 /inputs \
    && chmod 0700 /workspace /runtime

COPY toolchain-manifest.json \
    /usr/local/share/nanobot-sandbox/toolchain-manifest.json

ENV HOME=/runtime/home \
    XDG_CACHE_HOME=/runtime/cache \
    PIP_CACHE_DIR=/runtime/pip-cache \
    UV_CACHE_DIR=/runtime/uv-cache \
    npm_config_cache=/runtime/npm-cache \
    PYTHONPYCACHEPREFIX=/runtime/pycache \
    TMPDIR=/tmp

USER 10001:10001
WORKDIR /workspace

CMD ["/bin/bash", "--version"]
