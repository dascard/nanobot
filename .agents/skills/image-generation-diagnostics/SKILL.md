---
name: image-generation-diagnostics
description: Use when validating or debugging Nanobot image_generation calls, New-API Responses image generation, generated_image reply tokens, QQ image delivery, HTTP 400/401/500 errors, token_expired, transparent background failures, or PNG save verification.
---

# 图片生成诊断

## 概述

本技能用于验证 Nanobot 的 `image_generation` 工具链路。核心原则：先证明配置、网络、鉴权、上游能力和本地 PNG 保存分别处于什么状态，再下结论；不要只根据工具返回的 `HTTP Error ...` 猜原因。

## 何时使用

- 用户要求测试生图、生成图片、透明/不透明背景、QQ 发图或 `reply_token`。
- `ImageGenerationTool` 返回 `Connection refused`、`HTTP Error 400/401/500`、`token_expired`、超时或没有生成文件。
- 需要确认 `[generated_image:<id>]` 是否对应本地 PNG，以及能否被最终出口渲染发送。
- 不适用于已有图片理解、OCR、图片内容分析；那些使用 `image_summary`。

## 快速规则

| 问题 | 判定方法 | 结论 |
|---|---|---|
| 当前 `.env` 地址是否可用 | TCP 连接 `NEW_API_BASE_URL` host/port | 连接拒绝就是配置/网络问题 |
| Key 是否基本有效 | `GET /v1/models` | 200 且包含模型说明网关鉴权通过 |
| 生图上游是否有效 | `POST /v1/responses` | 读取错误体，不只看状态码 |
| `token_expired` | `/responses` 401 body | 上游 provider token 过期，不等同于本地 key 为空 |
| 透明背景失败 | 400 body 含 transparent unsupported | 当前模型不支持透明背景 |
| 工具是否真的产图 | 检查 `saved_path`、文件大小、PNG magic | magic 必须是 `89504e470d0a1a0a` |

## 诊断顺序

1. 先打印配置：`NEW_API_BASE_URL`、`IMAGE_GENERATION_MODEL`、`IMAGE_GENERATION_TIMEOUT`，不要打印完整 key。
2. 对当前配置地址做 TCP 检查；如果项目文档或部署约定有 LAN 地址，也并列检查。
3. 对可达地址请求 `/models`，确认 key 和模型列表。
4. 调用 `ImageGenerationTool().execute(...)` 生成最小图片，先用 `background=opaque`、`quality=low`。
5. 如果工具只返回状态码，直接构造同样的 `/responses` 请求读取 HTTPError body。
6. 成功后校验 `saved_path`，不要只相信 `result.success`。
7. 报告时区分：配置不可达、鉴权失败、上游模型能力限制、本地保存失败、日志数据库旁路错误。

## 可复用命令

检查配置、TCP 和 `/models`：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python - <<'PY'
import json
import socket
import urllib.request
import config

base_url = str(config.NEW_API_BASE_URL).rstrip("/")
host_port = base_url.removeprefix("http://").removeprefix("https://").split("/", 1)[0]
host, _, port_s = host_port.partition(":")
port = int(port_s or 80)
print(json.dumps({
    "base_url": base_url,
    "model": config.IMAGE_GENERATION_MODEL,
    "has_key": bool(config.NEW_API_KEY),
}, ensure_ascii=False))
with socket.create_connection((host, port), timeout=5):
    print(json.dumps({"tcp_ok": True}, ensure_ascii=False))
req = urllib.request.Request(
    f"{base_url}/models",
    headers={"Authorization": f"Bearer {config.NEW_API_KEY}"},
    method="GET",
)
with urllib.request.urlopen(req, timeout=20) as resp:
    body = resp.read(4000).decode("utf-8", errors="replace")
    print(json.dumps({
        "models_ok": True,
        "status": getattr(resp, "status", None),
        "contains_gpt_image": "gpt-image" in body,
    }, ensure_ascii=False))
PY
```

直接调用工具并校验 PNG：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python - <<'PY'
import asyncio
import json
from pathlib import Path
from creatures.nanobot.prompts.skills.image_generation.tool import ImageGenerationTool

async def main():
    result = await ImageGenerationTool().execute({
        "prompt": "生成一个简单测试 PNG 图标：蓝绿色水滴，白色星光，主体居中。",
        "size": "1024x1024",
        "quality": "low",
        "background": "opaque",
    })
    if not result.success:
        print(json.dumps({"ok": False, "error": result.error}, ensure_ascii=False))
        return 1
    payload = json.loads(result.output or "{}")
    path = Path(str(payload.get("saved_path") or ""))
    head = path.read_bytes()[:8].hex() if path.exists() else ""
    print(json.dumps({
        "ok": path.exists() and head == "89504e470d0a1a0a",
        "reply_token": payload.get("reply_token"),
        "saved_path": str(path),
        "image_bytes": payload.get("image_bytes"),
        "png_magic": head,
        "background": payload.get("background"),
    }, ensure_ascii=False, indent=2))
    return 0 if path.exists() and head == "89504e470d0a1a0a" else 1

raise SystemExit(asyncio.run(main()))
PY
```

读取 `/responses` 错误体：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
python - <<'PY'
import json
import urllib.error
import urllib.request
import config

base_url = str(config.NEW_API_BASE_URL).rstrip("/")
payload = {
    "model": config.IMAGE_GENERATION_MODEL,
    "instructions": "You are an image generation assistant. When asked for an image, call the image_generation tool.",
    "input": [{"role": "user", "content": [{"type": "input_text", "text": "生成一个简单测试图标。"}]}],
    "tools": [{"type": "image_generation", "output_format": "png", "size": "1024x1024", "quality": "low", "background": "opaque"}],
    "tool_choice": "auto",
    "store": False,
    "stream": True,
}
try:
    req = urllib.request.Request(
        f"{base_url}/responses",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.NEW_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        print(resp.read(2000).decode("utf-8", errors="replace"))
except urllib.error.HTTPError as exc:
    print(json.dumps({
        "status": exc.code,
        "reason": exc.reason,
        "body": exc.read(4000).decode("utf-8", errors="replace"),
    }, ensure_ascii=False, indent=2))
PY
```

## 调用规则

- 聊天侧：只在用户明确要求生成新图片、画图、做头像、贴纸、海报或插画时使用 `image_generation`。
- 图片理解、OCR、分析已有图片时使用 `image_summary`，不要调用生图。
- 工具成功后，把 `reply_token` 原样放进 `reply(content)`，例如：`图好了：[generated_image:img_xxx]`。
- 不要把 `saved_path`、base64 或手写 CQ 码发给用户。
- 当前实测 `gpt-image` 可用组合优先用 `quality=low`、`background=opaque`。
- 如果用户要求透明背景，先确认模型是否支持；当前 `gpt-image` 返回 `Transparent background is not supported for this model.` 时，应明确说明模型能力限制，而不是继续重试。

## 报告格式

报告必须包含：

- 使用的 `base_url`、模型、尺寸、质量、背景参数。
- 每张图的 `ok`、`reply_token`、`saved_path`、`image_bytes`、`png_magic`。
- 失败时的 HTTP 状态码和响应体关键字段。
- 旁路问题单独列出，例如 `sqlite3.DatabaseError: database disk image is malformed` 只影响 trace 日志，不等同于生图失败。

## 常见错误

| 错误 | 修正 |
|---|---|
| 只看到 `HTTP Error 400` 就猜 prompt 有问题 | 直接读 `/responses` 响应体 |
| `.env` 指向容器内地址但当前进程在宿主机跑 | 临时覆盖 `NEW_API_BASE_URL` 或修正部署网络 |
| `/models` 成功就断言生图可用 | 还必须测 `/responses` |
| `token_expired` 被误判为本地 key 缺失 | 它通常是上游 provider token 过期 |
| 透明背景 400 后反复重试 | 先判断模型能力，不支持就改用 `opaque` 或换模型/后处理 |
| 只检查 `reply_token` | 同时检查 PNG 文件存在、大小和 magic |

## 质量门

- 不能在没有实际命令输出时说“已生成”。
- 不能把连接拒绝、鉴权失败、模型能力限制混为一类。
- 生成结果必须有 `saved_path`、文件存在且 PNG magic 为 `89504e470d0a1a0a`。
- 如果最终要发 QQ，还要确认 `NANOBOT_PUBLIC_BASE_URL` 和 `/api/v1/generated-images/{image_id}/image` 可被 OneBot/NapCat 拉取。
