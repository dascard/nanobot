# Web Search Provider Priority 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 或在当前会话内按 TDD 执行。步骤使用复选框（`- [ ]`）语法跟踪进度。

**目标：** 为 Web Search 自动 provider 选择增加可配置 priority 排序，并按 `search` capability 过滤候选池。

**架构：** `provider_catalog` 提供默认 priority；`provider_settings` 解析 DB/env/default priority；`search_runtime` 在自动模式下按 capability 与 priority 构建候选池；WebUI 展示和保存 priority。

**技术栈：** Python、FastAPI、SQLAlchemy SystemSetting、pytest、React/Vite。

---

## 文件结构

- 修改：`core/web_search/provider_catalog.py`
  - 增加 `default_priority` 字段。
- 修改：`core/web_search/provider_settings.py`
  - 解析、校验、保存 `priority`。
- 修改：`core/config_registry.py`
  - 注册 `web_search.providers.<id>.priority` setting。
- 修改：`core/web_search/search_runtime.py`
  - 自动模式按 `search` capability 过滤并按 priority 排序。
- 修改：`api/admin/web_search_routes.py`
  - `ProviderUpdateRequest` 接收 `priority`，audit 记录是否改动 priority。
- 修改：`webui/src/features/web-search/WebSearchPage.jsx`
  - 卡片展示 priority，配置弹窗编辑 priority。
- 修改：`tests/test_admin_web_search_routes.py`
  - 覆盖 API 返回/保存 priority、自动排序、capability 过滤。
- 修改：`tests/test_webui_app_split.py`
  - 覆盖 WebUI priority 文案/字段。

## 任务

### 任务 1：配置契约测试

- [ ] **步骤 1：编写失败测试**

在 `tests/test_admin_web_search_routes.py` 增加：

```python
def test_list_providers_returns_priority(client, auth_header):
    data = _ok(client.get("/api/v1/admin/web-search/providers", headers=auth_header))
    assert _provider(data, "searxng")["priority"] == 100

def test_update_provider_saves_priority(client, auth_header):
    data = _ok(client.put(
        "/api/v1/admin/web-search/providers/brave",
        headers=auth_header,
        json={"priority": 10},
    ))
    assert data["provider"]["priority"] == 10
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_admin_web_search_routes.py::test_list_providers_returns_priority tests/test_admin_web_search_routes.py::test_update_provider_saves_priority -q`

预期：缺少 `priority` 字段失败。

- [ ] **步骤 3：实现最少后端配置**

修改 catalog/settings/config_registry/admin route，支持 `priority`。

- [ ] **步骤 4：运行测试验证通过**

运行同一命令，预期通过。

### 任务 2：Runtime 排序与能力过滤

- [ ] **步骤 1：编写失败测试**

在 `tests/test_admin_web_search_routes.py` 增加：

```python
def test_preview_web_search_uses_priority_order(client, auth_header, monkeypatch):
    # brave priority=10，searxng priority=20，自动模式应先调用 brave。
```

以及：

```python
def test_preview_web_search_filters_non_search_capability(client, auth_header, monkeypatch):
    # monkeypatch catalog item capabilities=("extract",)，自动候选池应跳过它。
```

- [ ] **步骤 2：运行测试验证失败**

运行新增测试，预期仍按 catalog 固定顺序或未过滤 capability。

- [ ] **步骤 3：实现 runtime 排序过滤**

自动模式候选池只包含 `search` capability，排序用 `(priority, catalog_index, provider_id)`。

- [ ] **步骤 4：运行测试验证通过**

运行新增测试和现有 Web Search 定向测试。

### 任务 3：WebUI 展示与编辑

- [ ] **步骤 1：编写失败测试**

在 `tests/test_webui_app_split.py` 增加断言：

```python
assert "优先级" in page_source
assert "priority" in page_source
```

- [ ] **步骤 2：运行测试验证失败**

运行：`python -m pytest tests/test_webui_app_split.py::test_web_search_page_displays_provider_usage_counts -q`

- [ ] **步骤 3：实现 WebUI 字段**

在 provider 卡片展示 priority，在配置弹窗添加数字输入并保存。

- [ ] **步骤 4：运行测试和构建**

运行：

```bash
python -m pytest tests/test_webui_app_split.py -q
cd webui && npm run build
```

## 验收命令

```bash
python -m py_compile core/web_search/provider_catalog.py core/web_search/provider_settings.py core/web_search/search_runtime.py api/admin/web_search_routes.py
python -m pytest tests/test_web_search_relevance.py tests/test_admin_web_search_routes.py tests/test_webui_app_split.py -q
python -m pytest tests/ -q
cd webui && npm run build
```
