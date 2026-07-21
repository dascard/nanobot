# 基础镜像固定到具体补丁版本；升级时必须重新生成并验证依赖锁。
ARG NODE_IMAGE=node:20.19.4-bookworm-slim@sha256:6db5e436948af8f0244488a1f658c2c8e55a3ae51ca2e1686ed042be8f25f70a
ARG PYTHON_IMAGE=python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1

# ── 第一阶段：构建 React 前端 ──
FROM ${NODE_IMAGE} AS webui-builder
WORKDIR /webui
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' \
		/etc/apt/sources.list.d/debian.sources \
	&& apt-get update && apt-get install -y --no-install-recommends \
	ca-certificates \
	&& rm -rf /var/lib/apt/lists/*
COPY webui/package.json webui/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY webui/ ./
RUN npm run build

# ── 第二阶段：Python 运行时 ──
FROM ${PYTHON_IMAGE} AS runtime
WORKDIR /app
RUN sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' \
		/etc/apt/sources.list.d/debian.sources \
	&& apt-get update && apt-get install -y --no-install-recommends \
	ca-certificates \
	tzdata \
	sqlite3 \
	wkhtmltopdf \
	xvfb \
	fonts-wqy-zenhei \
	curl \
	&& rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai \
	PIP_DISABLE_PIP_VERSION_CHECK=1 \
	PYTHONDONTWRITEBYTECODE=1 \
	PYTHONUNBUFFERED=1

# 先安装稳定的第三方依赖。CPU-only PyTorch 索引避免无 GPU 服务携带 CUDA 运行库。
COPY requirements-prod.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
	torch_requirement="$(sed -n '/^torch==/p' requirements-prod.lock)" \
	&& test -n "${torch_requirement}" \
	&& pip install --no-deps \
		--index-url https://download.pytorch.org/whl/cpu \
		"${torch_requirement}" \
	&& pip install \
		--index-url https://pypi.tuna.tsinghua.edu.cn/simple \
		--trusted-host pypi.tuna.tsinghua.edu.cn \
		-r requirements-prod.lock

# KT 源码变化只重建本地包层，不使第三方依赖层失效。
COPY vendor/KohakuTerrarium/ ./vendor/KohakuTerrarium/
RUN --mount=type=cache,target=/root/.cache/pip \
	pip install --no-deps ./vendor/KohakuTerrarium

# 业务代码位于依赖层之后；构建上下文由 .dockerignore 严格收窄。
COPY . .
# 从第一阶段复制 WebUI 构建产物
COPY --from=webui-builder /webui/dist ./webui/dist
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]


# 版本信息通过 build-arg 注入，不依赖 .git 目录
ARG GIT_COMMIT=unknown
ARG GIT_BRANCH=unknown
ARG GIT_FULL_COMMIT=
ARG GIT_COMMIT_DATE=
ARG GIT_DIRTY=null

ENV NANOBOT_GIT_COMMIT=$GIT_COMMIT
ENV NANOBOT_GIT_BRANCH=$GIT_BRANCH
ENV NANOBOT_GIT_FULL_COMMIT=$GIT_FULL_COMMIT
ENV NANOBOT_GIT_COMMIT_DATE=$GIT_COMMIT_DATE
ENV NANOBOT_GIT_DIRTY=$GIT_DIRTY
