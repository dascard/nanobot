# ── 第一阶段：构建 React 前端 ──
FROM node:20-slim AS webui-builder
WORKDIR /webui
RUN apt-get update && apt-get install -y --no-install-recommends \
	ca-certificates \
	&& rm -rf /var/lib/apt/lists/*
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ ./
RUN npm run build

# ── 第二阶段：Python 运行时 ──
FROM python:3.10-slim-bullseye
WORKDIR /app
RUN apt-get update && apt-get install -y \
	tzdata \
	sqlite3 \
	wkhtmltopdf \
	xvfb \
	fonts-wqy-zenhei \
	curl \
	&& rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai

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

COPY requirements.txt .
COPY vendor/ ./vendor/
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
	pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir imgkit markdown2
COPY . .
# 从第一阶段复制 WebUI 构建产物
COPY --from=webui-builder /webui/dist ./webui/dist
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
