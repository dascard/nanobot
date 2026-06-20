# TimingGate scoring 可观测性收尾实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 补齐 Admin WebUI 的 TimingGate scoring 详情字段，让真实样本复盘能直接看到冲突分、软拒绝上限、transport tier 和 wait 子信号。

**架构：** 后端已通过 `timing.scoring` 原样返回完整 `TimingDecision` 字段，本阶段只补前端展示和静态守卫测试。实现不改变 API、ChatLog schema、TimingGate 公式或 eval 逻辑。

**技术栈：** React (`webui/src/App.jsx`)、pytest 静态源码守卫。

---

## 文件职责

- 修改：`tests/test_webui_observability.py`  
  负责新增 WebUI 字段守卫，先制造红灯。
- 修改：`webui/src/App.jsx`  
  负责在 `TimingEventDetail` 中展示新增 scoring 字段。

## 任务 1：补 WebUI scoring 字段守卫

**文件：**
- 修改：`tests/test_webui_observability.py`

- [x] **步骤 1：编写失败的静态测试**

在 `test_timing_gate_detail_exposes_scoring_breakdown()` 末尾增加断言：

```python
    assert "conflict_score" in source
    assert "soft_reject_cap" in source
    assert "delay_seconds" in source
    assert "s_transport_tier" in source
    assert "w_marker" in source
    assert "w_file" in source
    assert "w_incomplete" in source
```

- [x] **步骤 2：运行测试验证红灯**

运行：

```bash
python -B -m pytest tests/test_webui_observability.py::test_timing_gate_detail_exposes_scoring_breakdown -q -p no:cacheprovider
```

结果：失败，报缺少 `"s_transport_tier"`，红灯符合预期。

## 任务 2：补 TimingEventDetail 展示

**文件：**
- 修改：`webui/src/App.jsx`
- 测试：`tests/test_webui_observability.py`

- [x] **步骤 1：在规则评分网格展示决策调试字段**

在 `TimingEventDetail` 的规则评分网格里，在 `band` 卡片后增加：

```jsx
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">conflict</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.conflict_score)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">soft_cap</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.soft_reject_cap)}</div>
              </div>
              <div className="rounded border border-slate-800 bg-slate-900 px-2 py-1.5">
                <div className="text-slate-500">delay</div>
                <div className="font-mono text-slate-200">{scoreValue(scoring.delay_seconds)}</div>
              </div>
```

- [x] **步骤 2：在信号分解展示 transport tier 和 wait 子信号**

在 `s_transport` 后补充 tier，并在 `s_bot` 后补充 wait 子信号：

```jsx
                <div>s_transport_tier: <span className="font-mono text-slate-200">{subSignals.s_transport_tier || '-'}</span></div>
                <div>w_marker: <span className="font-mono text-slate-200">{scoreValue(subSignals.w_marker)}</span></div>
                <div>w_file: <span className="font-mono text-slate-200">{scoreValue(subSignals.w_file)}</span></div>
                <div>w_incomplete: <span className="font-mono text-slate-200">{scoreValue(subSignals.w_incomplete)}</span></div>
```

- [x] **步骤 3：运行绿灯测试**

运行：

```bash
python -B -m pytest tests/test_webui_observability.py::test_timing_gate_detail_exposes_scoring_breakdown -q -p no:cacheprovider
```

结果：`1 passed, 1 warning in 0.67s`。

## 任务 3：回归验证与提交

**文件：**
- 修改：`webui/src/App.jsx`
- 修改：`tests/test_webui_observability.py`

- [x] **步骤 1：运行相邻回归**

运行：

```bash
python -B -m pytest \
  tests/test_webui_observability.py \
  tests/test_admin_api.py::TestPersonaAdmin::test_timing_gate_events_returns_scoring \
  tests/test_timing_score.py::test_decision_exposes_conflict_and_soft_reject_debug_fields \
  -q -p no:cacheprovider
```

结果：`7 passed, 1 warning in 1.12s`。

- [x] **步骤 2：运行全量回归**

运行：

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
PYTHONDONTWRITEBYTECODE=1 NANOBOT_TESTING=1 DATABASE_URL=sqlite:///:memory: NEW_API_KEY=test-key-for-ci NANOBOT_ADMIN_TOKEN=test-admin-token \
python -B -m pytest tests/ -q -p no:cacheprovider
```

结果：`1380 passed, 6 skipped, 139 warnings in 103.22s`。

- [x] **步骤 3：检查 diff**

运行：

```bash
git diff --check -- webui/src/App.jsx tests/test_webui_observability.py
```

结果：无输出。

- [x] **步骤 4：提交本阶段代码**

运行：

```bash
git add webui/src/App.jsx tests/test_webui_observability.py
git commit -m "feat(时机): 补齐评分可观测字段"
```

提交：`9d5817c feat(时机): 补齐评分可观测字段`。

## 额外验证

- WebUI build：`npm --prefix webui run build` 退出码 0，Vite 仅输出 chunk size / plugin timing 警告。
