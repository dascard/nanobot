# Web 生图测试与 SQLite 锁日志实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:test-driven-development 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 Web 管理端直接测试图片生成，并降低可恢复 SQLite 写锁重试的 warning 噪声。

**架构：** 在现有 `GeneratedImagesPage` 上增加测试面板，后端新增 admin POST `/generated-images` 复用 `ImageGenerationTool`，生成后返回 Gallery item。SQLite 锁处理不改变 retry 语义，只调整 `run_sqlite_locked_retry()` 的日志级别策略。

**技术栈：** FastAPI、Pydantic、pytest、React、Vite、Tailwind、现有 `AuthImage` 组件。

---

### 任务 1：红灯测试

**文件：**
- 修改：`tests/test_admin_api.py`
- 修改：`tests/test_webui_app_split.py`
- 修改：`tests/test_tracing_sqlite_retry.py`

- [x] **步骤 1：Admin API 红灯测试**

在 `TestGeneratedImagesAdmin` 中新增测试：

```python
def test_create_generated_image_response(self, client, auth_header, monkeypatch, tmp_path):
    import base64
    import json

    from kohakuterrarium.modules.tool.base import ToolResult
    from core import generated_images

    monkeypatch.setattr(generated_images, "GENERATED_IMAGE_DIR", str(tmp_path))

    async def fake_execute(self, args):
        saved = generated_images.save_generated_image(
            base64.b64encode(b"fake-png").decode("ascii"),
            prompt=args["prompt"],
            metadata={
                "model": "gpt-image",
                "size": args["size"],
                "quality": args["quality"],
                "background": args["background"],
            },
        )
        return ToolResult(output=json.dumps({
            "reply_token": saved["reply_token"],
            "mime": "image/png",
            "model": "gpt-image",
            "size": args["size"],
            "quality": args["quality"],
            "background": args["background"],
        }, ensure_ascii=False), exit_code=0)

    monkeypatch.setattr(
        "creatures.nanobot.prompts.skills.image_generation.tool.ImageGenerationTool.execute",
        fake_execute,
    )

    r = client.post(
        "/api/v1/admin/generated-images",
        json={
            "prompt": "画一只红熊猫喝奶茶",
            "size": "1536x1024",
            "quality": "medium",
            "background": "transparent",
        },
        headers=auth_header,
    )

    data = _ok(r)
    assert data["ok"] is True
    assert data["item"]["prompt"] == "画一只红熊猫喝奶茶"
    assert data["item"]["image_url"].endswith("/image")
```

另增工具失败返回 502 的测试。

- [x] **步骤 2：WebUI 静态红灯测试**

在 `test_generated_images_page_is_wired_for_gallery()` 增加断言：

```python
assert "测试生图" in page_source
assert "api.post('/generated-images'" in page_source
assert "generated-image-prompt" in page_source
assert "最近结果" in page_source
```

- [x] **步骤 3：SQLite retry 红灯测试**

在 `tests/test_tracing_sqlite_retry.py` 增加：

```python
def test_sqlite_locked_retry_logs_transient_retry_below_warning():
    from sqlalchemy.exc import OperationalError
    from core.sqlite_retry import run_sqlite_locked_retry

    calls = {"count": 0}
    logs = {"info": 0, "warning": 0}

    class Logger:
        def info(self, *args, **kwargs):
            logs["info"] += 1
        def warning(self, *args, **kwargs):
            logs["warning"] += 1

    def operation():
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("INSERT ...", {}, Exception("database is locked"))
        return "ok"

    assert run_sqlite_locked_retry(
        operation,
        logger=Logger(),
        attempts=4,
        base_delay_seconds=0,
    ) == "ok"
    assert logs["info"] == 1
    assert logs["warning"] == 0
```

- [x] **步骤 4：运行红灯测试**

运行：

`python -B -m pytest tests/test_admin_api.py::TestGeneratedImagesAdmin tests/test_webui_app_split.py tests/test_tracing_sqlite_retry.py -q`

预期：新增断言失败，原因是 POST 接口、前端测试面板和 retry info 日志策略尚不存在。

### 任务 2：后端实现

**文件：**
- 修改：`api/admin_routes.py`
- 修改：`core/sqlite_retry.py`

- [x] **步骤 1：实现生图 POST 请求模型**

在 `api/admin_routes.py` 中增加 `GeneratedImageCreate`，字段为 `prompt`、`size`、`quality`、`background`。

- [x] **步骤 2：实现 `POST /generated-images`**

调用 `ImageGenerationTool().execute()`，解析 `reply_token`，用 `core.generated_images.get_generated_image()` 读取元数据并补 `image_url`。

- [x] **步骤 3：实现 SQLite retry 日志级别**

在 `run_sqlite_locked_retry()` 中：

- 如果 `attempt + 1 < max_attempts`，调用 `logger.info()`。
- 如果 `attempt + 1 >= max_attempts`，调用 `logger.warning()`。
- 没有对应 logger 方法时跳过。

- [x] **步骤 4：运行后端测试**

运行：

`python -B -m pytest tests/test_admin_api.py::TestGeneratedImagesAdmin tests/test_tracing_sqlite_retry.py -q`

预期：PASS。

### 任务 3：前端实现

**文件：**
- 修改：`webui/src/features/generated-images/GeneratedImagesPage.jsx`

- [x] **步骤 1：新增生成表单状态**

新增 `testPrompt`、`testSize`、`testQuality`、`testBackground`、`generating`、`generationError`、`generated`。

- [x] **步骤 2：新增生成动作**

`runGeneration()` 调用 `api.post('/generated-images', payload)`，成功后刷新列表。

- [x] **步骤 3：新增测试面板 UI**

在 PageHeader 和 Toolbar 之间增加 `Card`，包含提示词 textarea、参数选择、生成按钮、最近结果。

- [x] **步骤 4：运行前端静态测试**

运行：

`python -B -m pytest tests/test_webui_app_split.py -q`

预期：PASS。

### 任务 4：验证与提交

**文件：**
- 无新增

- [x] **步骤 1：运行目标测试**

运行：

`python -B -m pytest tests/test_admin_api.py::TestGeneratedImagesAdmin tests/test_webui_app_split.py tests/test_tracing_sqlite_retry.py tests/test_image_generation_tool.py -q`

预期：PASS。

- [x] **步骤 2：构建 WebUI**

运行：

`npm run build`

预期：exit 0。

- [x] **步骤 3：检查 diff**

运行：

`git diff --check`

预期：无输出。

- [x] **步骤 4：精确暂存并提交**

只暂存本次相关文件，提交信息：

`feat(图片生成): 添加 Web 生图测试入口`
