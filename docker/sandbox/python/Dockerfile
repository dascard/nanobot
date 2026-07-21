ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1

FROM ${PYTHON_IMAGE}

ARG SANDBOX_IMAGE_VERSION=unknown
LABEL org.opencontainers.image.title="Nanobot Python Sandbox" \
      org.opencontainers.image.version="${SANDBOX_IMAGE_VERSION}" \
      com.nanobot.sandbox-image="true"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.lock /usr/local/share/nanobot-sandbox/requirements.lock
RUN pip install --no-cache-dir --require-hashes \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        -r /usr/local/share/nanobot-sandbox/requirements.lock \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --shell /usr/sbin/nologin sandbox \
    && mkdir -p /workspace /inputs /runtime \
    && chown 10001:10001 /workspace /runtime \
    && chmod 0755 /inputs \
    && chmod 0700 /workspace /runtime

ENV HOME=/runtime/home \
    XDG_CACHE_HOME=/runtime/cache \
    PYTHONPYCACHEPREFIX=/runtime/pycache \
    PIP_CACHE_DIR=/runtime/pip-cache \
    PIP_NO_INDEX=1 \
    TMPDIR=/tmp

USER 10001:10001
WORKDIR /workspace

CMD ["python", "--version"]
