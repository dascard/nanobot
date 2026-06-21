# API Routes 群消息 Helper 去重实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 删除 `api/routes.py` 中与 `app.group_ingress.helpers` 重复的群消息 helper 实现，并保留旧 underscore 名称兼容导入。

**架构：** `app/group_ingress/helpers.py` 作为唯一真实实现；`api/routes.py` 只保留 `GroupMessageRequest`、`group_message()` 路由壳和旧私有 helper 的兼容别名。`group_timing_timer()` 和私聊流式错误路径继续通过旧名调用，但旧名指向 app 层 helper。

**技术栈：** Python 3.12、FastAPI、pytest、Pydantic、项目既有 `app.group_ingress` service/helper。

---

## 文件职责

- 修改：`tests/test_api_routes_group_helper_facade.py`
  - 新增红灯测试，锁定 `api.routes` 旧 underscore helper 指向 `app.group_ingress.helpers`。
  - 锁定 `api/routes.py` 行数低于 3000。
- 修改：`api/routes.py`
  - 导入 `app.group_ingress.helpers`，绑定旧 underscore 兼容别名。
  - 删除 route-local 的群消息重复 helper 和重复常量。
  - 保留 `GroupMessageRequest`、`group_message()`、`GroupTimingRequest`、`group_timing_timer()` 路由行为不变。
- 修改：`docs/todo.md`
  - 更新 P3 超大文件拆分进展。
- 修改：`docs/plan_walkthrough.md`
  - 记录本阶段执行、验证和提交号。
- 修改：`.Codex/plans/api-routes-group-helper-split.md`
  - 勾选任务并记录验证结果。

## 任务 1：补红灯测试

**文件：**
- 创建：`tests/test_api_routes_group_helper_facade.py`

- [ ] **步骤 1：创建 facade 兼容测试文件**

创建 `tests/test_api_routes_group_helper_facade.py`：

```python
from pathlib import Path


def test_api_routes_group_helpers_are_app_helper_facades():
    import api.routes as routes
    from app.group_ingress import helpers

    expected = {
        "_normalize_onebot_segments": helpers.normalize_onebot_segments,
        "_extract_mentions_from_segments": helpers.extract_mentions_from_segments,
        "_normalize_group_mentions": helpers.normalize_group_mentions,
        "_normalize_group_reply_to": helpers.normalize_group_reply_to,
        "_derive_group_direction": helpers.derive_group_direction,
        "_detect_group_bot_sender": helpers.detect_group_bot_sender,
        "_build_group_message_meta": helpers.build_group_message_meta,
        "_safe_group_client_meta": helpers.safe_group_client_meta,
        "_group_sticker_payloads": helpers.group_sticker_payloads,
        "_render_segments_to_text": helpers.render_segments_to_text,
        "_build_group_message_text": helpers.build_group_message_text,
        "_register_group_stickers_from_message": helpers.register_group_stickers_from_message,
        "_annotate_group_timing_event": helpers.annotate_group_timing_event,
        "_normalize_reply_for_duplicate": helpers.normalize_reply_for_duplicate,
        "_pop_bridge_reply_meta": helpers.pop_bridge_reply_meta,
        "_derive_group_agent_result": helpers.derive_group_agent_result,
        "_find_recent_duplicate_group_reply": helpers.find_recent_duplicate_group_reply,
        "_log_group_no_reply": helpers.log_group_no_reply,
        "_persist_group_bridge_reply": helpers.persist_group_bridge_reply,
        "_derive_group_trigger_reason": helpers.derive_group_trigger_reason,
    }
    for name, target in expected.items():
        assert getattr(routes, name) is target


def test_api_routes_group_helper_split_keeps_routes_file_under_3000_lines():
    line_count = len(Path("api/routes.py").read_text(encoding="utf-8").splitlines())
    assert line_count < 3000
```

- [ ] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_api_routes_group_helper_facade.py -q
```

预期：测试失败，失败原因是 `api.routes` 旧 helper 仍为本地函数，且 `api/routes.py` 行数仍约 3434。

## 任务 2：实现 `api.routes` 兼容 facade

**文件：**
- 修改：`api/routes.py`

- [ ] **步骤 1：在 `api/routes.py` 导入 app 层 helper**

在现有 `from core.message_envelope import build_chat_response_envelope` 附近添加：

```python
from app.group_ingress import helpers as group_ingress_helpers
```

- [ ] **步骤 2：添加旧 underscore helper 兼容别名**

在 `GroupMessageRequest` 类定义之后添加：

```python
# 旧群消息 helper 已收敛到 app.group_ingress.helpers；此处保留旧私有导入路径兼容。
_normalize_onebot_segments = group_ingress_helpers.normalize_onebot_segments
_extract_mentions_from_segments = group_ingress_helpers.extract_mentions_from_segments
_normalize_group_mentions = group_ingress_helpers.normalize_group_mentions
_normalize_group_reply_to = group_ingress_helpers.normalize_group_reply_to
_derive_group_direction = group_ingress_helpers.derive_group_direction
_detect_group_bot_sender = group_ingress_helpers.detect_group_bot_sender
_build_group_message_meta = group_ingress_helpers.build_group_message_meta
_safe_group_client_meta = group_ingress_helpers.safe_group_client_meta
_group_sticker_payloads = group_ingress_helpers.group_sticker_payloads
_render_segments_to_text = group_ingress_helpers.render_segments_to_text
_build_group_message_text = group_ingress_helpers.build_group_message_text
_register_group_stickers_from_message = group_ingress_helpers.register_group_stickers_from_message
_annotate_group_timing_event = group_ingress_helpers.annotate_group_timing_event
_normalize_reply_for_duplicate = group_ingress_helpers.normalize_reply_for_duplicate
_pop_bridge_reply_meta = group_ingress_helpers.pop_bridge_reply_meta
_derive_group_agent_result = group_ingress_helpers.derive_group_agent_result
_find_recent_duplicate_group_reply = group_ingress_helpers.find_recent_duplicate_group_reply
_log_group_no_reply = group_ingress_helpers.log_group_no_reply
_persist_group_bridge_reply = group_ingress_helpers.persist_group_bridge_reply
_derive_group_trigger_reason = group_ingress_helpers.derive_group_trigger_reason
```

- [ ] **步骤 3：删除重复群消息 helper 块**

删除 `api/routes.py` 中 `# ── 结构化消息 helper（Batch 1）──` 到 `def _derive_group_trigger_reason(...)` 结束的重复实现，包括：

- `_MAX_SEGMENTS`
- `_MAX_MENTIONS`
- `_MAX_REPLY_CONTENT`
- `_MAX_SEGMENT_TEXT`
- `_MAX_MENTION_NICK`
- `_ALLOWED_SEGMENT_KEYS`
- `_normalize_onebot_segments()`
- `_extract_mentions_from_segments()`
- `_normalize_group_mentions()`
- `_normalize_group_reply_to()`
- `_derive_group_direction()`
- `_detect_group_bot_sender()`
- `_build_group_message_meta()`
- `_safe_group_client_meta()`
- `_read_client_meta_from_log()`
- `_group_sticker_payloads()`
- `_render_segments_to_text()`
- `_build_group_message_text()`
- `_register_group_stickers_from_message()`
- `_annotate_group_timing_event()`
- `_cache_sticker_preview_bg()`
- `_pop_bridge_reply_meta()`
- `_derive_group_agent_result()`
- `_derive_group_trigger_reason()`

如果执行时再次搜索确认 `_read_client_meta_from_log()` 仍无引用，直接删除，不保留别名。

- [ ] **步骤 4：删除重复群回复持久化 helper 块**

删除 `api/routes.py` 中 `_persist_group_bridge_reply()`、`_normalize_reply_for_duplicate()`、
`_find_recent_duplicate_group_reply()` 和 `_log_group_no_reply()` 的 route-local 重复实现。

- [ ] **步骤 5：清理不再需要的 import**

运行：

```bash
python - <<'PY'
import ast
from pathlib import Path
tree = ast.parse(Path('api/routes.py').read_text(encoding='utf-8'))
names = set()
class Visitor(ast.NodeVisitor):
    def visit_Name(self, node):
        names.add(node.id)
        self.generic_visit(node)
Visitor().visit(tree)
for name in ['SequenceMatcher']:
    print(name, name in names)
PY
```

如果输出 `SequenceMatcher False`，删除 `from difflib import SequenceMatcher`。不要做无关 ruff 清理。

- [ ] **步骤 6：运行绿灯测试**

运行任务 1 的红灯命令。预期：全部通过。

## 任务 3：相邻回归与静态检查

**文件：**
- 修改：`api/routes.py`
- 创建：`tests/test_api_routes_group_helper_facade.py`

- [ ] **步骤 1：运行旧私有导入与 facade 定向回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_api_routes_group_helper_facade.py \
  tests/test_api.py::test_persist_group_bridge_reply_uses_runtime_bot_name \
  tests/test_api.py::test_find_recent_duplicate_group_reply_detects_long_repeat \
  tests/test_api.py::test_find_recent_duplicate_group_reply_ignores_short_repeat \
  tests/test_reply_admin.py::test_group_agent_result_uses_popped_no_reply_meta \
  tests/test_reply_admin.py::test_group_agent_result_preserves_prompt_v2_audit_failure \
  -q
```

预期：全部通过。

- [ ] **步骤 2：运行群消息与私聊相邻回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider \
  tests/test_api.py \
  tests/test_group_response_envelope.py \
  tests/test_api_push_envelope.py \
  tests/test_chat_response_envelope.py \
  tests/test_streaming_api.py \
  tests/test_streaming_response_envelope.py \
  -q
```

预期：全部通过。

- [ ] **步骤 3：运行静态检查**

运行：

```bash
python -m compileall api/routes.py app/group_ingress/helpers.py -q
rg -n "def _(normalize_onebot_segments|extract_mentions_from_segments|normalize_group_mentions|normalize_group_reply_to|derive_group_direction|detect_group_bot_sender|build_group_message_meta|safe_group_client_meta|group_sticker_payloads|render_segments_to_text|build_group_message_text|register_group_stickers_from_message|annotate_group_timing_event|normalize_reply_for_duplicate|pop_bridge_reply_meta|derive_group_agent_result|find_recent_duplicate_group_reply|log_group_no_reply|persist_group_bridge_reply|derive_group_trigger_reason)" api/routes.py
wc -l api/routes.py
git diff --check -- api/routes.py tests/test_api_routes_group_helper_facade.py
```

预期：

- `compileall` 无输出，退出码为 0。
- `rg` 无匹配，退出码为 1。
- `wc -l` 显示 `api/routes.py` 低于 3000 行。
- `git diff --check` 无输出。

- [ ] **步骤 4：运行全量回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
python -m pytest tests/ -v
```

预期：0 failures。

## 任务 4：提交实现阶段

**文件：**
- 修改：`api/routes.py`
- 创建：`tests/test_api_routes_group_helper_facade.py`

- [ ] **步骤 1：按文件显式暂存**

运行：

```bash
git add api/routes.py tests/test_api_routes_group_helper_facade.py
```

- [ ] **步骤 2：检查暂存区**

运行：

```bash
git diff --cached --name-status
git diff --cached --check
```

预期：暂存区只包含本任务列出的 2 个文件；`--check` 无输出。

- [ ] **步骤 3：提交实现**

运行：

```bash
git commit -m "refactor(路由): 收敛群消息 helper 实现"
```

- [ ] **步骤 4：提交后检查**

运行：

```bash
git show --stat --oneline -1
git status --short -- api/routes.py tests/test_api_routes_group_helper_facade.py
```

预期：目标文件提交后干净。

## 任务 5：文档收口

**文件：**
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`
- 修改：`.Codex/plans/api-routes-group-helper-split.md`

- [ ] **步骤 1：更新 `docs/todo.md`**

在 P3「超大文件 >800 行拆分」下追加进展：

- `api/routes.py` 第一刀已收敛群消息 helper 重复实现到 `app/group_ingress/helpers.py`；
  旧 underscore helper 名称保留为兼容别名；`api/routes.py` 行数低于 3000。

- [ ] **步骤 2：更新 `docs/plan_walkthrough.md`**

追加 `2026-06-21 API Routes 群消息 Helper 去重` 章节，记录：

- 设计文档：`docs/superpowers/specs/2026-06-21-api-routes-group-helper-split-design.md`
- 计划文件：`.Codex/plans/api-routes-group-helper-split.md`
- 设计提交、计划提交、实现提交
- 已完成列表
- 红灯、绿灯、相邻回归、静态检查、全量回归结果
- 执行约束：不改 `/group/message` 主流程、不改 `/chat`、不新增 `asyncio.run()`

- [ ] **步骤 3：更新本计划执行结果**

在本计划顶部追加 `执行结果摘要（2026-06-21）`，记录验证结果和提交号。

- [ ] **步骤 4：文档门禁**

运行：

```bash
git diff --check -- docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-routes-group-helper-split.md
python - <<'PY'
from pathlib import Path
markers = [
    '\u5f85\u5b9a',
    'TO' + 'DO',
    chr(60) + '实际',
    chr(60) + '失败',
    chr(60) + '通过',
]
paths = [
    Path('docs/todo.md'),
    Path('docs/plan_walkthrough.md'),
    Path('.Codex/plans/api-routes-group-helper-split.md'),
]
hits = []
for path in paths:
    text = path.read_text(encoding='utf-8')
    hits.extend(f'{path}: {marker}' for marker in markers if marker in text)
if hits:
    raise SystemExit('\n'.join(hits))
PY
```

预期：两个命令均无输出，退出码为 0。

- [ ] **步骤 5：提交文档收口**

运行：

```bash
git add docs/todo.md docs/plan_walkthrough.md .Codex/plans/api-routes-group-helper-split.md
git diff --cached --name-status
git diff --cached --check
git commit -m "docs(计划): 收口群消息 helper 去重"
```

预期：暂存区只包含 3 个文档文件；提交后目标文档文件干净。
