# P4-2 Admin 标注工作台契约化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 Admin 标注工作台从后端 canonical expected 契约生成可评分标签，并在 promote 写正式 case 前执行 dry-run 预检和二次确认。

**架构：** 后端在 `evals.expected_contract` 中维护字段 schema、suite presets 和类型校验，Admin API 暴露只读契约端点，并让 label / promote 响应保持契约一致。WebUI 加载该契约生成标注表单，`note` 与 `expected` 分离，promote modal 按 dry-run → apply 两阶段执行。

**技术栈：** Python、pytest、FastAPI、Pydantic、React、Vite、现有 `evals` / Admin WebUI。

---

## 文件职责

- `evals/expected_contract.py`：从 key 白名单扩展为字段 schema、suite presets、deprecated key 列表、contract payload 和类型 / 枚举校验。
- `api/admin_routes.py`：新增 `GET /evals/expected-contract`；收紧 `LabelRequest` 冲突检测；让 promote apply 响应与 dry-run 对齐。
- `tests/test_eval_candidate_contract.py`：覆盖后端契约端点、类型 / 枚举校验、label 冲突、promote apply 响应。
- `webui/src/features/evals/EvalsPage.jsx`：加载 expected contract；重写 label modal 的字段构造；新增 `note`、`labelError`、promote modal、`target_dataset`、dry-run plan 和 `promoteError`。
- `tests/test_webui_admin_redesign.py`：用静态测试守卫 WebUI 不再写旧 expected 字段，并验证 promote 两阶段请求形态。
- `docs/evals.md`：记录 P4-2 工作台目标和实现后操作流。
- `docs/todo.md`、`docs/plan_walkthrough.md`：同步 P4-2 阶段状态、拆分和验证计划。

## 子 agent 分工建议

- **P4-2A 后端契约 agent：** 执行任务 1 到任务 3，只修改 `evals/expected_contract.py`、`api/admin_routes.py`、`tests/test_eval_candidate_contract.py` 和必要文档。
- **P4-2B WebUI 工作台 agent：** 在 P4-2A 通过后执行任务 4 到任务 6，只修改 `webui/src/features/evals/EvalsPage.jsx`、`tests/test_webui_admin_redesign.py` 和必要文档。
- **验证 agent：** 只读审查最终 diff，确认未重写 P4-1 store / CLI / runner，并运行或核对验证命令输出。该 agent 不改文件。

后端和前端可以并行读码，但写入集成顺序固定为 P4-2A → P4-2B → 文档收口。每个子阶段通过验证后单独 commit。

## 任务 1：后端 expected schema 与契约端点

**文件：**
- 修改：`evals/expected_contract.py`
- 修改：`api/admin_routes.py`
- 测试：`tests/test_eval_candidate_contract.py`

- [x] **步骤 1：编写 expected contract endpoint 红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
def test_eval_expected_contract_endpoint_exposes_scoreable_keys(client, monkeypatch):
    from evals.expected_contract import SCOREABLE_EXPECTED_KEYS

    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")

    response = client.get(
        "/api/v1/admin/evals/expected-contract",
        headers=_auth_header(),
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert sorted(payload["scoreable_keys"]) == sorted(SCOREABLE_EXPECTED_KEYS)
    assert set(payload["field_schema"]) == set(SCOREABLE_EXPECTED_KEYS)
    assert payload["suite_presets"]["timing_gate"]["fields"][0] == "timing_action"
    assert payload["field_schema"]["timing_action"]["type"] == "enum"
    assert payload["field_schema"]["timing_action"]["values"] == [
        "continue",
        "wait",
        "no_reply",
    ]
    for deprecated in ("expected_action", "should_learn", "quality", "category", "meaning", "delay_seconds"):
        assert deprecated in payload["deprecated_keys"]
        assert deprecated not in payload["scoreable_keys"]
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py::test_eval_expected_contract_endpoint_exposes_scoreable_keys \
-v -p no:cacheprovider
```

预期：失败于 404，说明 endpoint 尚未实现。

- [x] **步骤 3：扩展 expected contract schema**

在 `evals/expected_contract.py` 中保留 `SCOREABLE_EXPECTED_KEYS`，新增：

```python
EXPECTED_FIELD_SCHEMA: dict[str, dict[str, Any]] = {
    "should_reply": {"type": "boolean", "label": "是否应该回复"},
    "timing_action": {
        "type": "enum",
        "values": ["continue", "wait", "no_reply"],
        "label": "TimingGate 动作",
    },
    "scoring": {"type": "object", "label": "评分明细", "advanced": True},
    "forbidden_tools": {"type": "string_list", "label": "禁止工具"},
    "required_tools": {"type": "string_list", "label": "必需工具"},
    "send_mode": {
        "type": "enum",
        "values": ["normal", "quote", "mention"],
        "label": "发送方式",
    },
    "reply_to_message_id": {"type": "string_or_number", "label": "引用消息 ID"},
    "mentions": {"type": "array", "label": "提及目标"},
    "must_contain": {"type": "string_list", "label": "必须包含文本"},
    "must_not_contain": {"type": "string_list", "label": "禁止包含文本"},
    "http_status": {"type": "integer", "label": "HTTP 状态码"},
    "content_type_prefix": {"type": "string", "label": "Content-Type 前缀"},
    "forbidden_terms": {"type": "string_list", "label": "禁止学习词"},
    "should_create_jargon": {"type": "boolean", "label": "应创建黑话"},
    "should_create_expression": {"type": "boolean", "label": "应创建表达"},
    "no_reply": {"type": "boolean", "label": "不应回复"},
    "no_learn": {"type": "boolean", "label": "不应学习"},
    "no_context": {"type": "boolean", "label": "不应入上下文"},
    "should_enter_context": {"type": "boolean", "label": "应进入上下文"},
    "should_write_chatlog": {"type": "boolean", "label": "应写 ChatLog"},
    "should_write_conversation_turn": {"type": "boolean", "label": "应写 ConversationTurn"},
    "model_used": {"type": "string", "label": "应使用模型"},
    "must_not_use": {"type": "string_list", "label": "禁止模型"},
    "should_call_auto_routing": {"type": "boolean", "label": "应调用自动路由"},
    "served_sticker_id": {"type": "string_or_number", "label": "服务贴纸 ID"},
    "send_source": {"type": "string", "label": "发送来源"},
}

SUITE_EXPECTED_PRESETS: dict[str, dict[str, list[str]]] = {
    "timing_gate": {"fields": ["timing_action", "should_reply", "scoring"]},
    "group_reply": {
        "fields": [
            "should_reply",
            "required_tools",
            "forbidden_tools",
            "send_mode",
            "reply_to_message_id",
            "mentions",
            "must_contain",
            "must_not_contain",
        ]
    },
    "reply_contract": {
        "fields": [
            "should_reply",
            "required_tools",
            "forbidden_tools",
            "send_mode",
            "reply_to_message_id",
            "mentions",
            "must_contain",
            "must_not_contain",
        ]
    },
    "memory_learning": {
        "fields": ["no_learn", "should_create_jargon", "should_create_expression", "forbidden_terms"]
    },
    "model_routing": {"fields": ["model_used", "must_not_use", "should_call_auto_routing"]},
    "moderation": {
        "fields": [
            "no_reply",
            "no_learn",
            "no_context",
            "should_enter_context",
            "should_write_chatlog",
            "should_write_conversation_turn",
        ]
    },
    "sticker": {"fields": ["http_status", "content_type_prefix", "served_sticker_id", "send_source"]},
}

DEPRECATED_EXPECTED_KEYS = frozenset({
    "expected_action",
    "should_learn",
    "quality",
    "category",
    "meaning",
    "delay_seconds",
    "reason",
})
```

同文件新增 payload helper：

```python
def expected_contract_payload() -> dict[str, Any]:
    return {
        "scoreable_keys": sorted(SCOREABLE_EXPECTED_KEYS),
        "field_schema": {key: EXPECTED_FIELD_SCHEMA[key] for key in sorted(SCOREABLE_EXPECTED_KEYS)},
        "suite_presets": SUITE_EXPECTED_PRESETS,
        "deprecated_keys": sorted(DEPRECATED_EXPECTED_KEYS),
    }
```

- [x] **步骤 4：新增 Admin contract endpoint**

在 `api/admin_routes.py` 的 eval candidate API 区域导入 helper：

```python
from evals.expected_contract import expected_contract_payload
```

在候选列表 endpoint 前新增：

```python
@router.get("/evals/expected-contract")
def eval_expected_contract(_auth=Depends(verify_admin)):
    return expected_contract_payload()
```

- [x] **步骤 5：运行 endpoint 绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py::test_eval_expected_contract_endpoint_exposes_scoreable_keys \
-v -p no:cacheprovider
```

预期：该测试通过。

- [x] **步骤 6：纳入 P4-2A 后端阶段提交**

```bash
git add evals/expected_contract.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 暴露期望字段契约"
```

## 任务 2：后端类型 / 枚举校验与 label 冲突保护

**文件：**
- 修改：`evals/expected_contract.py`
- 修改：`api/admin_routes.py`
- 测试：`tests/test_eval_candidate_contract.py`

- [x] **步骤 1：编写类型 / 枚举校验红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
@pytest.mark.parametrize(
    ("suite", "expected", "message"),
    [
        ("timing_gate", {"timing_action": 123}, "timing_action"),
        ("timing_gate", {"timing_action": "maybe"}, "timing_action"),
        ("timing_gate", {"should_reply": "false"}, "should_reply"),
        ("group_reply", {"required_tools": "reply"}, "required_tools"),
        ("sticker", {"http_status": "200"}, "http_status"),
        ("timing_gate", {"expected_action": "continue"}, "expected_action"),
    ],
)
def test_validate_expected_rejects_bad_types_and_deprecated_keys(suite, expected, message):
    from evals.expected_contract import validate_expected_contract

    with pytest.raises(ValueError, match=message):
        validate_expected_contract(suite, expected)


def test_validate_expected_accepts_typed_values():
    from evals.expected_contract import validate_expected_contract

    validate_expected_contract(
        "group_reply",
        {
            "should_reply": True,
            "required_tools": ["reply"],
            "mentions": [{"user_id": "456"}],
            "must_contain": ["关键句"],
            "send_mode": "quote",
            "reply_to_message_id": "m-1",
        },
    )
    validate_expected_contract("sticker", {"http_status": 200, "served_sticker_id": 74})
```

- [x] **步骤 2：编写 label 冲突红灯测试**

同文件新增：

```python
def test_eval_label_candidate_rejects_conflicting_expected_fields(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    _insert_candidate(db_session)

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/label",
        headers=_auth_header(),
        json={
            "expected": {"timing_action": "continue"},
            "expected_json": {"timing_action": "no_reply"},
        },
    )

    assert response.status_code == 400
    assert "expected" in response.json()["detail"]
```

- [x] **步骤 3：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py::test_validate_expected_rejects_bad_types_and_deprecated_keys \
tests/test_eval_candidate_contract.py::test_validate_expected_accepts_typed_values \
tests/test_eval_candidate_contract.py::test_eval_label_candidate_rejects_conflicting_expected_fields \
-v -p no:cacheprovider
```

预期：至少类型 / 枚举校验和冲突测试失败。

- [x] **步骤 4：实现类型校验 helper**

在 `evals/expected_contract.py` 增加：

```python
def _validate_type(key: str, value: Any, schema: Mapping[str, Any]) -> None:
    field_type = schema.get("type")
    if field_type == "boolean" and not isinstance(value, bool):
        raise ValueError(f"expected.{key} must be boolean")
    if field_type == "string" and not isinstance(value, str):
        raise ValueError(f"expected.{key} must be string")
    if field_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"expected.{key} must be integer")
    if field_type == "string_or_number" and not isinstance(value, (str, int, float)):
        raise ValueError(f"expected.{key} must be string or number")
    if field_type == "string_list":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"expected.{key} must be list[str]")
    if field_type == "array" and not isinstance(value, list):
        raise ValueError(f"expected.{key} must be array")
    if field_type == "object" and not isinstance(value, Mapping):
        raise ValueError(f"expected.{key} must be object")
    if field_type == "enum":
        values = schema.get("values") or []
        if not isinstance(value, str) or value not in values:
            raise ValueError(f"expected.{key} must be one of {values}")
```

把 `validate_expected_contract()` 改为：

```python
def validate_expected_contract(suite: str, expected: Mapping[str, Any]) -> None:
    if not expected:
        raise ValueError("expected must not be empty")
    if expected.get("needs_label"):
        raise ValueError("expected must not contain needs_label=true")

    deprecated = sorted(str(key) for key in expected if key in DEPRECATED_EXPECTED_KEYS)
    if deprecated:
        raise ValueError(f"expected contains deprecated UI keys for suite={suite}: {deprecated}")

    unknown = sorted(str(key) for key in expected if key not in SCOREABLE_EXPECTED_KEYS)
    if unknown:
        raise ValueError(f"expected contains unscored keys for suite={suite}: {unknown}")

    for key, value in expected.items():
        _validate_type(str(key), value, EXPECTED_FIELD_SCHEMA[str(key)])
```

- [x] **步骤 5：实现 LabelRequest 冲突保护**

在 `api/admin_routes.py` 的 `LabelRequest` 中替换 `normalized_expected()`：

```python
    def normalized_expected(self) -> dict:
        if self.expected and self.expected_json and self.expected != self.expected_json:
            raise ValueError("expected and expected_json conflict")
        return self.expected or self.expected_json or {}
```

在 `eval_label_candidate()` 中包住 normalized 阶段：

```python
    try:
        expected = body.normalized_expected()
        result = label_candidate(db, case_id, expected, note=body.note or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
```

- [x] **步骤 6：运行后端契约绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_eval_candidate_contract.py -v -p no:cacheprovider
```

预期：`tests/test_eval_candidate_contract.py` 全部通过。

- [x] **步骤 7：纳入 P4-2A 后端阶段提交**

```bash
git add evals/expected_contract.py api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "fix(评测): 校验期望字段类型"
```

## 任务 3：promote apply 响应对齐

**文件：**
- 修改：`api/admin_routes.py`
- 测试：`tests/test_eval_candidate_contract.py`

- [x] **步骤 1：编写 promote apply 响应红灯测试**

在 `tests/test_eval_candidate_contract.py` 新增：

```python
def test_eval_promote_candidate_apply_response_matches_dry_run_contract(
    client,
    db_session,
    monkeypatch,
    tmp_path,
):
    _redirect_promote_root(monkeypatch, tmp_path)
    monkeypatch.setattr("api.admin_routes.NANOBOT_ADMIN_TOKEN", "test-token")
    from core.eval_sampling.store import label_candidate

    _insert_candidate(db_session)
    label_candidate(db_session, "cand_timing_gate_1", {"timing_action": "continue"})

    response = client.post(
        "/api/v1/admin/evals/candidates/cand_timing_gate_1/promote",
        headers=_auth_header(),
        json={"dry_run": False, "target_dataset": "timing_gate"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is False
    assert payload["case_id"] == "cand_timing_gate_1"
    assert payload["suite"] == "timing_gate"
    assert payload["target_dataset"] == "timing_gate"
    assert payload["path"].endswith("evals/cases/timing_gate/cand_timing_gate_1.json")
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py::test_eval_promote_candidate_apply_response_matches_dry_run_contract \
-v -p no:cacheprovider
```

预期：失败于响应缺少 `dry_run`、`case_id`、`suite` 或 `target_dataset`。

- [x] **步骤 3：调整 promote apply 响应**

在 `api/admin_routes.py` 的 `eval_promote_candidate()` 中，把 apply 分支改为先 dry-run 取计划，再执行写入：

```python
        plan = plan_candidate_promotion(db, case_id, target_dataset=body.target_dataset)
        path = promote_candidate(db, case_id, target_dataset=body.target_dataset)
```

返回值改为：

```python
    return {
        "ok": True,
        "dry_run": False,
        "case_id": plan["case_id"],
        "suite": plan["suite"],
        "target_dataset": plan["target_dataset"],
        "path": path,
    }
```

- [x] **步骤 4：运行后端 P4-2A 回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py \
-v -p no:cacheprovider
```

预期：候选契约与 CLI 回归全部通过。

- [x] **步骤 5：Commit P4-2A 完整后端阶段**

```bash
git add api/admin_routes.py tests/test_eval_candidate_contract.py
git commit -m "feat(评测): 对齐候选晋升响应"
```

## 任务 4：WebUI 标注表单契约化

**文件：**
- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 测试：`tests/test_webui_admin_redesign.py`

- [x] **步骤 1：编写 WebUI 标注契约红灯测试**

在 `tests/test_webui_admin_redesign.py` 新增：

```python
def test_eval_label_workbench_uses_expected_contract_keys():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "api.get('/evals/expected-contract'" in source
    assert "expectedContract" in source
    assert "labelError" in source
    assert "note: labelNote" in source
    assert "timing_action" in source
    assert "should_create_jargon" in source
    assert "should_create_expression" in source
    assert "forbidden_terms" in source

    for old_key in ("expected_action", "should_learn", "quality", "category", "meaning", "delay_seconds"):
        assert old_key not in source
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_webui_admin_redesign.py::test_eval_label_workbench_uses_expected_contract_keys \
-v -p no:cacheprovider
```

预期：失败于未加载 expected contract，且源码仍包含旧字段。

- [x] **步骤 3：新增契约状态和加载逻辑**

在 `EvalsPage.jsx` state 区域新增：

```javascript
  const [expectedContract, setExpectedContract] = useState(null)
  const [contractError, setContractError] = useState('')
  const [labelNote, setLabelNote] = useState('')
  const [labelError, setLabelError] = useState('')
```

新增加载函数：

```javascript
  const loadExpectedContract = useCallback(() => {
    api.get('/evals/expected-contract')
      .then(r => { setExpectedContract(r.data); setContractError('') })
      .catch(e => setContractError(e.response?.data?.detail || e.message))
  }, [])
```

在 candidates tab 的 `useEffect` 分支里调用：

```javascript
    if (tab === 'candidates') {
      loadCandidates()
      loadExpectedContract()
    }
```

- [x] **步骤 4：新增 expected 构造 helper**

在 `EvalsPage.jsx` 的 `doLabel` 前新增：

```javascript
  const scoreableKeys = new Set(expectedContract?.scoreable_keys || [])
  const deprecatedKeys = new Set(expectedContract?.deprecated_keys || [])

  const parseList = (value) => String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)

  const buildExpectedFromFields = () => {
    const expectedJson = {}
    for (const [key, value] of Object.entries(labelFields)) {
      if (key === '_rawJson' || value === '' || value === undefined || value === null) continue
      if (Array.isArray(value) && value.length === 0) continue
      expectedJson[key] = value
    }
    return expectedJson
  }
```

把 `doLabel()` 改为：

```javascript
  const doLabel = (caseId) => {
    setLabelError('')
    let expectedJson = buildExpectedFromFields()
    if (labelShowJson && labelFields._rawJson) {
      try { expectedJson = JSON.parse(labelFields._rawJson) } catch { setLabelError('JSON 格式错误'); return }
    }
    if (Object.keys(expectedJson).length === 0 || expectedJson.needs_label) {
      setLabelError('请填写可评分 expected 字段')
      return
    }
    const badKeys = Object.keys(expectedJson).filter(key => deprecatedKeys.has(key) || (scoreableKeys.size && !scoreableKeys.has(key)))
    if (badKeys.length) {
      setLabelError(`不可评分字段: ${badKeys.join(', ')}`)
      return
    }
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/label`, {
      expected: expectedJson,
      note: labelNote,
    })
      .then(() => { setShowLabel(null); setLabelNote(''); loadCandidates() })
      .catch(e => setLabelError(e.response?.data?.detail || e.message))
  }
```

- [x] **步骤 5：替换 suite 表单字段**

将旧的 `memory_learning`、`timing_gate`、`group_reply` 表单替换为契约字段：

```javascript
                {labelSuite === 'timing_gate' && (
                  <div className="space-y-3">
                    <div><div className="text-xs text-slate-400 mb-1">TimingGate 动作</div>
                      <select value={labelFields.timing_action || ''} onChange={e => setLabelFields({...labelFields, timing_action: e.target.value})}
                        className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm">
                        <option value="">选择...</option>
                        <option value="continue">continue</option>
                        <option value="wait">wait</option>
                        <option value="no_reply">no_reply</option>
                      </select></div>
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={!!labelFields.should_reply} onChange={e => setLabelFields({...labelFields, should_reply: e.target.checked})} />
                      应回复
                    </label>
                  </div>
                )}
```

`memory_learning` 使用 `no_learn`、`should_create_jargon`、`should_create_expression` 复选框和 `forbidden_terms` 逗号分隔输入。`group_reply` 使用 `should_reply`、`required_tools`、`forbidden_tools`、`must_contain`、`must_not_contain` 等契约字段。旧的 `reason` 输入改为统一 `labelNote`：

```javascript
                <div className="mt-4">
                  <div className="text-xs text-slate-400 mb-1">人工备注</div>
                  <textarea value={labelNote} onChange={e => setLabelNote(e.target.value)}
                    rows={2} className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" />
                </div>
```

- [x] **步骤 6：运行 WebUI 标注静态绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_webui_admin_redesign.py::test_eval_label_workbench_uses_expected_contract_keys \
-v -p no:cacheprovider
```

预期：该测试通过。

- [x] **步骤 7：纳入 P4-2B 工作台阶段提交**

```bash
git add webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py
git commit -m "feat(评测): 契约化标注工作台"
```

## 任务 5：WebUI promote dry-run / apply 两阶段

**文件：**
- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 测试：`tests/test_webui_admin_redesign.py`

- [x] **步骤 1：编写 promote 两阶段红灯测试**

在 `tests/test_webui_admin_redesign.py` 新增：

```python
def test_eval_promote_uses_dry_run_before_apply():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "promotePlan" in source
    assert "promoteError" in source
    assert "target_dataset" in source
    assert "dry_run: true" in source
    assert "dry_run: false" in source
    assert "confirmPromote" in source
    assert "已提升到 regression" not in source
    assert "api.post(`/evals/candidates/${encodeURIComponent(caseId)}/promote`)" not in source


def test_eval_promote_apply_uses_previewed_target_dataset():
    source = EVALS_JS.read_text(encoding="utf-8")

    assert "setPromotePlan(r.data)" in source
    assert "promotePlan.target_dataset" in source
    assert "target_dataset: promoteTargetDataset" in source
    assert "确认提升" in source
```

- [x] **步骤 2：运行红灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_webui_admin_redesign.py::test_eval_promote_uses_dry_run_before_apply \
tests/test_webui_admin_redesign.py::test_eval_promote_apply_uses_previewed_target_dataset \
-v -p no:cacheprovider
```

预期：失败于当前 `doPromote()` 直接裸 `POST /promote`。

- [x] **步骤 3：新增 promote modal 状态**

在 `EvalsPage.jsx` state 区域新增：

```javascript
  const [showPromote, setShowPromote] = useState(null)
  const [promoteTargetDataset, setPromoteTargetDataset] = useState('regression')
  const [promotePlan, setPromotePlan] = useState(null)
  const [promoteError, setPromoteError] = useState('')
  const [promoting, setPromoting] = useState(false)
```

新增打开函数：

```javascript
  const openPromote = (candidate) => {
    setShowPromote(candidate.case_id)
    setPromoteTargetDataset(candidate.suite || 'regression')
    setPromotePlan(null)
    setPromoteError('')
  }
```

- [x] **步骤 4：替换 doPromote 为 dry-run 和 apply**

替换旧 `doPromote`：

```javascript
  const previewPromote = (caseId) => {
    setPromoteError('')
    setPromoting(true)
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/promote`, {
      dry_run: true,
      target_dataset: promoteTargetDataset,
    })
      .then(r => setPromotePlan(r.data))
      .catch(e => setPromoteError(e.response?.data?.detail || e.message))
      .finally(() => setPromoting(false))
  }

  const confirmPromote = (caseId) => {
    setPromoteError('')
    setPromoting(true)
    api.post(`/evals/candidates/${encodeURIComponent(caseId)}/promote`, {
      dry_run: false,
      target_dataset: promoteTargetDataset,
    })
      .then(r => {
        setPromotePlan(r.data)
        setShowPromote(null)
        loadCandidates()
      })
      .catch(e => setPromoteError(e.response?.data?.detail || e.message))
      .finally(() => setPromoting(false))
  }
```

把列表按钮改成：

```javascript
                          <button onClick={() => openPromote(c)}
                            className="px-2 py-1 bg-emerald-700/50 hover:bg-emerald-700 text-emerald-300 rounded text-xs">提升</button>
```

- [x] **步骤 5：新增 promote modal UI**

在 label modal 之后新增：

```javascript
          {showPromote && (
            <Modal onClose={() => setShowPromote(null)}>
              <div className="p-6">
                <h2 className="text-lg font-bold mb-2">提升候选</h2>
                <p className="text-xs text-slate-500 mb-4">{showPromote}</p>
                <div className="space-y-3">
                  <div>
                    <div className="text-xs text-slate-400 mb-1">target_dataset</div>
                    <input value={promoteTargetDataset} onChange={e => { setPromoteTargetDataset(e.target.value); setPromotePlan(null) }}
                      className="w-full p-2 rounded-lg bg-slate-900 border border-slate-700 text-sm" />
                  </div>
                  {promoteError && (
                    <div className="px-3 py-2 rounded-lg border border-red-500/40 bg-red-500/10 text-sm text-red-300">{promoteError}</div>
                  )}
                  {promotePlan && (
                    <div className="space-y-2">
                      <div className="text-xs text-slate-400">目标路径</div>
                      <code className="block p-2 rounded-lg bg-slate-950 border border-slate-800 text-xs break-all">{promotePlan.path}</code>
                      <JsonBlock value={promotePlan.case || promotePlan} className="max-h-56" />
                    </div>
                  )}
                </div>
                <div className="flex gap-2 justify-end mt-4">
                  <button onClick={() => setShowPromote(null)} className="px-4 py-2 bg-slate-700 hover:bg-slate-600 rounded-xl text-sm">取消</button>
                  <button onClick={() => previewPromote(showPromote)} disabled={promoting}
                    className="px-4 py-2 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 rounded-xl text-sm">预检</button>
                  <button onClick={() => confirmPromote(showPromote)} disabled={promoting || !promotePlan}
                    className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 rounded-xl text-sm font-medium">确认提升</button>
                </div>
              </div>
            </Modal>
          )}
```

- [x] **步骤 6：运行 promote 静态绿灯测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_webui_admin_redesign.py::test_eval_promote_uses_dry_run_before_apply \
tests/test_webui_admin_redesign.py::test_eval_promote_apply_uses_previewed_target_dataset \
-v -p no:cacheprovider
```

预期：两条静态测试通过。

- [x] **步骤 7：纳入 P4-2B 工作台阶段提交**

```bash
git add webui/src/features/evals/EvalsPage.jsx tests/test_webui_admin_redesign.py
git commit -m "feat(评测): 契约化标注工作台"
```

## 任务 6：WebUI 构建与前后端集成回归

**文件：**
- 修改：`webui/src/features/evals/EvalsPage.jsx`
- 测试：`tests/test_webui_admin_redesign.py`

- [x] **步骤 1：运行 WebUI 静态测试**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_webui_admin_redesign.py -v -p no:cacheprovider
```

预期：WebUI 静态测试全部通过。

- [x] **步骤 2：运行候选闭环回归**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py \
-v -p no:cacheprovider
```

预期：P4-1 候选闭环和 P4-2 后端契约测试全部通过。

- [x] **步骤 3：运行 WebUI build**

运行：

```bash
npm --prefix webui run build
```

预期：Vite build 退出码为 0。

- [x] **步骤 4：纳入 P4-2B 工作台阶段提交**

如果步骤 1 到步骤 3 需要同步文档状态，提交相关文档；如果没有文档变化，不单独提交空 commit。

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "feat(评测): 契约化标注工作台"
```

## 任务 7：文档收口与最终验证

**文件：**
- 修改：`docs/evals.md`
- 修改：`docs/todo.md`
- 修改：`docs/plan_walkthrough.md`

- [x] **步骤 1：同步操作文档**

在 `docs/evals.md` 记录实现后的 WebUI 操作流：

```markdown
## Admin WebUI 标注工作台

WebUI 从 `/api/v1/admin/evals/expected-contract` 读取 expected 契约。
标注时只提交契约内字段，人工说明写入 `note`，不写入 `expected.reason`。
Promote 必须先 dry-run，确认 `target_dataset` 与 `path` 后再 apply。
```

- [x] **步骤 2：同步路线状态**

在 `docs/todo.md` 的路线项 8 中，已把 P4-2 更新为「已完成」，并写明 WebUI 静态测试、候选闭环回归、WebUI build 和全量回归的实际结果。

- [x] **步骤 3：同步 walkthrough**

在 `docs/plan_walkthrough.md` 的 P4-2 详细计划中记录每个提交、每条验证命令和结果。只把已经提交并验证的任务标为 `[x]`。

- [x] **步骤 4：运行文档扫描**

运行：

```bash
rg -n "T[O]DO|待[定]|后续[实]现|类似[任]务|添加[适]当|为上[述]" \
docs/evals.md docs/todo.md docs/plan_walkthrough.md \
.Codex/plans/admin-eval-workbench-contract.md \
docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md
```

预期：无输出。

- [x] **步骤 5：运行 U+FFFD 扫描**

运行：

```bash
python - <<'PY'
from pathlib import Path
paths = [
    Path("docs/evals.md"),
    Path("docs/todo.md"),
    Path("docs/plan_walkthrough.md"),
    Path("docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md"),
    Path(".Codex/plans/admin-eval-workbench-contract.md"),
]
bad = [str(path) for path in paths if path.exists() and "\ufffd" in path.read_text(encoding="utf-8")]
if bad:
    raise SystemExit("U+FFFD found in: " + ", ".join(bad))
print("U+FFFD check passed")
PY
```

预期：输出 `U+FFFD check passed`。

- [x] **步骤 6：运行 diff whitespace 检查**

运行：

```bash
git diff --check -- \
docs/evals.md docs/todo.md docs/plan_walkthrough.md \
docs/superpowers/specs/2026-06-18-admin-eval-workbench-contract-design.md \
.Codex/plans/admin-eval-workbench-contract.md \
api/admin_routes.py evals/expected_contract.py \
webui/src/features/evals/EvalsPage.jsx \
tests/test_eval_candidate_contract.py tests/test_webui_admin_redesign.py
```

预期：无输出。

- [x] **步骤 7：运行全量 pytest**

运行：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

预期：0 failures。

- [x] **步骤 8：最终 Commit**

只暂存本阶段文档状态文件：

```bash
git add docs/evals.md docs/todo.md docs/plan_walkthrough.md
git commit -m "docs(评测): 收口标注工作台状态"
```

## 验证矩阵

P4-2A 后端阶段：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest \
tests/test_eval_candidate_contract.py tests/test_eval_candidates_cli.py \
-v -p no:cacheprovider
```

P4-2B 前端阶段：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/test_webui_admin_redesign.py -v -p no:cacheprovider
```

```bash
npm --prefix webui run build
```

最终阶段：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
PYTHONDONTWRITEBYTECODE=1 python -B -m pytest tests/ -v -p no:cacheprovider
```

## 提交边界

- 计划阶段：`docs(评测): 设计标注工作台契约`
- P4-2A-1：`feat(评测): 暴露期望字段契约`
- P4-2A-2：`fix(评测): 校验期望字段类型`
- P4-2A-3：`feat(评测): 对齐候选晋升响应`
- P4-2B：`feat(评测): 契约化标注工作台`
- 文档收口：随 P4-2B 提交同步阶段状态；若全量验证后仍需单独调整，再使用 `docs(评测): 收口标注工作台状态`
