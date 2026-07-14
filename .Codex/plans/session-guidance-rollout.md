# Prompt Runtime 与全会话专属指导统一执行索引

## 目的

本文件是两项连续工作的唯一执行入口，防止上下文压缩、换 agent 或分阶段实施时只修
眼前问题而遗忘此前已经批准的全会话 `session_guidance`。

固定顺序：

```text
Prompt Runtime 契约整改
→ 专项回归与兼容门
→ 全会话 session_guidance
→ 跨链路全量验收
```

不得跳过前置整改直接接入 Session Guidance。

## 规范与详细计划

### 阶段 A：Prompt Runtime 契约整改

- 设计：
  `docs/superpowers/specs/2026-07-13-prompt-runtime-contract-remediation-design.md`
- 实施计划：
  `.codex/plans/prompt-runtime-contract-remediation.md`
- 任务数：13（安全前置 S1-S6 + 原整改任务 1-7）
- 状态：S1-S6 与原整改任务 1-7 已实现，专项兼容门通过；最终全量测试
  `3101 passed, 6 skipped, 0 failed`

解决范围：

1. 硬禁用不安全 Python 执行，并把只读 SQL 安全边界下沉到底层。
2. Final Action 与富终结结果绑定真实 tool name/call ID/declaration。
3. runtime facts 使用强转义 JSON，不可信 metadata 只进入最后 user event。
4. 关键 TaskContract、输入角色和 Memory 可恢复失败语义。
5. `persona_update` 绑定当前 actor，并移除未执行参数。
6. `ai_daily` 时间参数贯穿 pipeline/cache，并建立 schema/执行器测试。
7. 显式代码鉴权事实 `is_super_user`。
8. 核心 flow 与 strict audit。
9. 请求级 wire tool schema 和重复 overlay。
10. Prompt/outbound payload hash 与 token 分项。
11. 私聊 `group_id` 清空。
12. 超级用户权限上限与本轮工具相关性拆分。
13. SDK 最终请求、Admin preview 和 live runtime 专项回归。

### 阶段 B：全会话 Session Guidance

- 已批准设计：
  `docs/superpowers/specs/2026-07-12-session-guidance-design.md`
- 实施计划：
  `.codex/plans/session-guidance.md`
- 任务数：10
- 状态：阶段 A 与阶段 B 任务 1–10 均已通过验收并完成规范化提交

解决范围：

1. Canonical chat stream identity。
2. 数据库字段、历史 alias 迁移和可恢复备份。
3. Guidance validator、安全摘要和 resolver。
4. Prompt Compiler/flow/audit 的可空唯一 section。
5. Runtime flow 幂等迁移、原子备份与 CLI 回滚。
6. Bridge 群聊/私聊公共链路。
7. Admin CRUD、session 发现和脱敏审计。
8. DB 有效值与未保存草稿 preview。
9. Admin WebUI。
10. 隔离、恢复、preview/live 一致性和全量发布回归。

## 当前进度账本

执行 agent 每完成一个阶段都必须更新这里；不能只在聊天中口头报告。

- [x] Session Guidance 设计完成并获批准。
- [x] Prompt Runtime 原始请求问题完成只读审查。
- [x] Prompt Runtime 整改设计和详细计划完成。
- [x] Session Guidance 详细计划完成校正。
- [x] 阶段 A / S1：Python 执行安全阻断与只读 SQL 边界完成定向验收。
- [x] 阶段 A / S2：Final Action 与富结果 provenance 完成全量验收。
- [x] 阶段 A / S3：运行时元数据结构化与角色隔离；完整测试
  `2909 passed, 6 skipped, 0 failed`。
- [x] 阶段 A / S4：关键 TaskContract 与可恢复失败语义；完整测试
  `2937 passed, 6 skipped, 0 failed`。
- [x] 阶段 A / S5：persona_update actor 绑定与能力缩窄；完整测试
  `2946 passed, 6 skipped, 0 failed`。
- [x] 阶段 A / S6：ai_daily 时间契约与 schema/执行器测试；完整测试
  `2993 passed, 6 skipped, 0 failed`。
- [x] 阶段 A 安全前置任务 S1-S6 实现完成。
- [x] 阶段 A / 任务 1：显式透传代码鉴权事实；完整测试
  `3000 passed, 6 skipped, 0 failed`。
- [x] 阶段 A 原整改任务 1–6 实现完成。
- [x] 阶段 A 任务 7 专项兼容门通过；专项合同测试 `509 passed`，恢复与研究工具链
  `220 passed`，最终全量测试 `3101 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 1：统一 canonical chat stream identity；定向测试 `43 passed`，
  关联回归 `95 passed`，后端全量测试 `3141 passed, 6 skipped, 0 failed`，独立复审
  `GO`。
- [x] 阶段 B / 任务 2：增加专属指导字段、历史 alias 规范化、可恢复快照和并发迁移
  临界区；定向测试 `57 passed`，关联回归 `313 passed`，后端全量测试
  `3159 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 3：实现指导正文校验、安全摘要和只读 resolver；定向测试
  `44 passed`，关联回归 `204 passed`，后端全量测试
  `3203 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 4：把可空唯一 guidance section 接入 Compiler、flow 与 strict
  audit；核心定向测试 `130 passed`，关联回归 `266 passed`，后端全量测试
  `3225 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 5：实现 runtime flow 幂等迁移、精确备份、摘要校验、共享写锁、
  原子替换与 CLI 显式回滚；定向测试 `119 passed`，关联回归 `194 passed`，后端
  全量测试 `3263 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 6：在 Bridge 群聊与私聊公共链路解析并注入会话指导，保持
  `private_superuser` 仅作用于工具策略，并对 trace/debug/异常执行正文脱敏；任务定向
  测试 `116 passed`，关联回归 `355 passed`，后端全量测试
  `3268 passed, 6 skipped, 0 failed`，生产实现独立复审 `GO`。
- [x] 阶段 B / 任务 7：实现 Admin 会话配置 CRUD、canonical/legacy 身份发现、
  列表摘要脱敏和配置/审计原子事务；任务定向测试 `115 passed`，关联回归
  `221 passed`，后端全量测试 `3292 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 8：Admin Prompt 有效预览支持数据库指导、未保存草稿和临时清空，
  非字符串输入由服务层固定 422 拒绝且不回显正文；任务与关联矩阵 `184 passed`，后端
  全量测试 `3299 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 阶段 B / 任务 9：实现 Admin WebUI 会话策略页面、详情加载失败写保护、
  runtime/external session ID 分流和未保存草稿预览；全部 WebUI Python 测试
  `57 passed`，关联后端测试 `40 passed`，新页面 ESLint 零问题，Vite 构建成功，
  Playwright 桌面与移动端验收通过，独立复审 `GO`。
- [x] 阶段 B 任务 1–10 实现完成。
- [x] 阶段 B 任务 10 跨链路发布门通过；功能回归 `452 passed`，补充关联矩阵
  `351 passed`，后端全量测试 `3329 passed, 6 skipped, 0 failed`，独立复审 `GO`。
- [x] 用户授权后完成规范化提交：`fbf8455`。

## 阶段 A 验收门

进入阶段 B 前必须同时满足：

- `python_sandbox` 在 ToolPlan、KT 执行器、底层 helper 和 legacy 入口均 fail closed；
- 只读 SQL 底层拒绝写入、ATTACH、危险 PRAGMA、extension 和内部路径泄漏；
- Final Action 只接受 verified `reply/no_reply`，普通工具 marker/HTML 与 assistant
  JSON/HTML 不能终结；
- ai_daily/group_analysis 富结果使用独立 envelope 和 typed settlement；
- runtime facts 为可解析强转义 JSON，不可信 metadata 只位于最后 user event；
- 关键 task 缺必需变量时不启用，classifier/timing payload 只在 user role 出现一次；
- Memory 契约错误保留未处理状态，合法空或成功提交才消费；
- persona_update 只能作用于当前 runtime actor，且 schema 不发布未实现参数；
- ai_daily 的 freshness/target_date 真实进入 pipeline 和 cache key；
- `is_super_user` 从 API/Bridge 显式透传，代码生成的 runtime context 明确输出事实；
- 自定义 identity template 即使省略授权变量，也不能删除运行时事实；
- base/platform/branch/runtime/identity/current user 受 strict audit 保护；
- 工具 schema overlay 幂等，最终 description 中同一 V2 marker 最多一次；
- ToolPlan、PromptPlan、KT round-trip 和初始 SDK kwargs 的 wire tools 完全一致；
- compiler 和真实 outbound 都记录 messages/tools/total metrics；
- 私聊不输出或使用 group ID，也不命中 group ToolOverride；
- 简单超级用户问题不携带完整高风险工具集；
- 私聊分类缺失不回退到 full；
- 阶段 A 详细计划规定的定向测试为 0 failures；
- 没有修改 QQbot 或身份模板正文。

任一项未满足，不得开始 Session Guidance 的 flow 或 Bridge 接线。

## 阶段 B 验收门

最终交付前必须同时满足：

- 所有 group/private session 使用 canonical 配置身份；
- `private_superuser` 只用于工具策略，guidance identity 始终为 `private`；
- DB migration 幂等，实际备份可以恢复；
- guidance 空值不新增 system message，正文摘要 hash 为 `""`；
- 非空 guidance 只出现一次，位置固定在 identity 与 persona 之间；
- guidance 不能改变鉴权事实、runtime preset、tool schema 或 `reply/no_reply` 契约；
- Admin 列表从任何嵌套字段都不泄漏正文，认证详情才返回正文；
- Admin audit 只保存字符数和完整 SHA-256；
- preview strict audit，不调用任何模型、不写 DB；
- preview 复用服务端超级用户事实和 live ToolPlan；无模型依赖时，preview 与 live
  runtime 对相同输入的 envelope、section hash、Prompt hash 和 metrics 一致；群记忆
  依赖 reranker 时既不调用模型也不读取模型派生缓存，并返回显式降级状态，不伪装成
  精确预览；
- 群聊和私聊 resolver 失败进入现有技术失败/恢复路径，不产生假成功投递；
- runtime flow 可幂等升级、列出备份和显式回滚；
- 会话配置页面 lint、WebUI build 和后端全量测试通过；全仓 lint 不超过已冻结基线；
- 没有修改 QQbot。

## 全量验证命令

实现完成后使用：

```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
  -u all_proxy -u ALL_PROXY \
  python -m pytest tests/ -v

npm --prefix webui run lint
(cd webui && npm exec eslint -- src/features/session-config/SessionConfigsPage.jsx)
npm --prefix webui run build
python -m compileall core app api nanobot_kt bootstrap scripts
git diff --check
git status --short
```

只有 pytest 0 failures、会话配置页面 ESLint 0 errors、全仓 ESLint 不超过已冻结的
`9 errors / 5 warnings` 基线、Vite build exit 0、compileall exit 0 且
`git diff --check` 无输出，才允许声明实现完成。

## 固定边界

- 不修改 QQbot 端、QQ push 协议、入站协议或 CQ renderer。
- 不修改身份模板中的安全规则、角色设定、立绘提示词和说话方式。
- 不修改任何 Prompt 模板正文；TaskContract、schema 和执行器的程序化修复不通过改文案
  代替。
- 不自动覆盖服务器已有 `data/prompts_v2/chat/identity_context.md` 正文。
- 不在代码、文档、测试 fixture、日志或提交信息中写入具体超级用户 ID。
- 不新增超级用户环境变量别名；继续使用已经统一的唯一变量。
- 不修改聊天历史、入站 claim、恢复记录和 outbox 使用的原始运行时 `session_id`。
- 不把 `session_guidance` 当作权限、工具或鉴权输入。
- 不缓存 guidance 正文或 Prompt 编译结果。
- 不运行 `git add -A` 或 `git add .`。
- 用户未明确说“提交”前不执行任何 `git commit`。

## 工作区保护

执行前和每个提交检查点都运行：

```bash
git status --short
git diff --check
```

只按详细计划列出的实际文件路径暂存。WebUI build 后先记录
`webui/dist/assets` 的真实 hashed 路径，再逐个 `git add`/`git rm`；不得在计划或命令
中使用伪造的 hash 占位路径。用户已有未跟踪文件和 `package-lock.json` 差异必须
原样保留。

## 提交策略

详细计划中的每个提交步骤都是“用户授权后的检查点”，不是自动授权。收到用户明确
“提交”后仍要：

1. 使用 `verification-before-completion` 复核最新测试证据；
2. 使用 `chinese-code-review` 审查实际 diff；
3. 使用 `chinese-commit-conventions` 生成中文 commit message；
4. 按文件指定暂存；
5. 提交前再次检查 staged diff 和敏感信息；
6. 禁止提交用户原有文件、运行时数据库、快照或 `cc2codex/`。

## 明确延期事项

以下问题已经登记，但不进入本轮两份详细计划：

- 身份模板安全规则与角色 Prompt 文案重写；
- 身份 Prompt 体量拆分、立绘/图片人设按需注入；
- 高风险工具真正的后端二阶段 approval/confirmation 状态机；
- `python_sandbox` 的 OS 级隔离恢复方案；在此之前维持硬禁用；
- persona_update 的纠正、删除、禁用事实、重建及确认状态机；
- `outreach_judge` 错误示例文案和其他 Prompt 正文统一整改；
- provider constrained decoding / JSON Schema 能力探测与定向重试；
- Admin 原始 LLM 请求复制功能的默认脱敏策略专项整改；
- 自定义 Runtime identity template 的自动迁移或覆盖；
- Session Guidance 版本历史、审批和多 guidance kind。

延期不等于已解决。后续创建独立设计和计划前，不得在当前实现中顺手扩大范围。

## 续跑协议

任何新会话、自动续跑或子 agent 接手时，先依次读取：

1. 本索引；
2. 当前阶段详细计划；
3. 当前阶段设计文档；
4. `git status --short` 和最新测试证据。

然后从“当前进度账本”第一个未完成项继续。不得因为阶段 A 修复完成就把阶段 B
视为取消，也不得因为 Session Guidance 实现开始就跳过阶段 A 的专项兼容门。
