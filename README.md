# Nanobot Server (v4 Headless Dify Controller)

Nanobot Server 是一个独立的自进化 Python 微服务网关。它与 Dify 分离，通过 Tampermonkey 脚本对 Gemini Web 网页端进行浏览器端“无痛注入”以实现持久化记忆和系统角色护栏。

## 架构

```mermaid
graph TD
    subgraph Frontend [Browser]
        A[Tampermonkey 脚本] -->|1. 注入 config.txt| B[Gemini Web Chat]
        B -->|2. 监听并拦截气泡| A
    end

    subgraph Backend [Nanobot Server]
        A <---|GET /api/v1/context| C[FastAPI 接口层]
        A --->|POST /api/v1/log| C
        C <--> D[(SQLite\n持久化状态)]
        
        C -.->|阈值满 20 触发| E((后台自进化任务\nEvolution Thread))
    end

    subgraph Dify Engine [Dify Workflows]
        E --->|POST 02 Key| F[日志高密度收集器]
        E --->|POST 03 Key| G[画像原子提炼器]
        E --->|POST 04 Key| H[Prompt 架构审计师]
        F -.-> E
        G -.-> E
        H -.-> E
    end
```

## 本地开发启动

```bash
# 安装依赖
pip install -r requirements.txt

# 运行服务器 (热重载模式)
uvicorn server:app --reload --port 8000
```

## Docker 部署

```bash
# 复制并配置你的 .env 文件
cp .env.example .env

# 构建并启动
docker-compose up -d --build
```
