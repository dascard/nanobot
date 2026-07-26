# Eval 闭环修复计划

## 背景

2026-07-26 审查结论:eval 基建完整但闭环断在三处——

1. **门禁侧**:`regression` 套件(11 个历史 bug 用例)未接入任何 gate 脚本;
   在跑的 capability 套件仅 1/3/5 个 case,门禁从未拦截过任何东西。
2. **晋升死胡同**:`promote_candidate` 把晋升产物写到
   `RUNTIME_PATHS.data_dir/evals/cases/`(运行数据目录),而 `evals/run.py`
   只读仓库 `evals/cases/`;数据目录侧无任何消费方,标注、晋升做完也进不了门禁。
3. **环境不同步**:标注发生在生产(候选积压 2175/标注 3/晋升 0,7/9 快照),
   评测门禁在仓库/CI;两侧无同步机制。抽样 95% 集中在 memory_learning,
   噪声淹没标注队列。

另:`reply_eval`(唯一评测真实 LLM 行为的套件,44 case)只能管理端手动触发,
5 月之后再未运行。

## 阶段 1:regression 套件接入门禁

已验证:当前代码 `python -m evals.run --suite regression` → 11/11 全过
(5/28 数据库记录中的失败已随代码演进消失)。

- [x] 新增 `evals/baselines/regression.json`(SuiteReport 格式,11/11)
- [x] `scripts/run_eval_pr_gate.sh` 增加 regression 步骤
      (`--min-pass-rate 1.0 --max-new-failures 0`,与 capability 套件一致)
- [x] `scripts/run_eval_periodic.sh` 增加同名 `run_step`
- [x] 守护测试:断言两个 gate 脚本包含 regression 调用与基线路径
      (防止再次被静默移出门禁)

## 阶段 2:晋升回路打通(生产标注 → 仓库门禁)

原则:不改生产运行时行为(promote 默认仍落 data_dir),在 CLI 侧加桥。

- [x] `core/eval_sampling/store.py`:`candidate_readiness` /
      `plan_candidate_promotion` / `promote_candidate` 增加可选
      `cases_root` 参数(默认 None = 现行 `RUNTIME_PATHS.data_dir` 行为)
- [x] `evals/candidates.py` CLI:
  - `promote` 增加 `--cases-root`(开发侧直接晋升进仓库 `evals/cases/`)
  - 新增 `export-cases` 子命令:从 DB 读 `status=promoted` 候选,
    按 store 的 case 构建逻辑重建 case JSON 写入 `--cases-root`
    (救回已在生产晋升、文件滞留在生产 data_dir 的存量;已存在文件跳过)
- [x] 测试:`tests/test_eval_candidates_cli.py` 扩展(promote 落仓库目录、
      export-cases 重建与跳过)
- [x] 工作流(写入 docs,阶段 5):
      生产 DB 快照同步到开发机(现有做法)→ 开发侧
      `python -m evals.candidates promote --cases-root evals/cases --apply`
      → git 提交 → 进 PR 门禁

## 阶段 3:抽样限流 + 积压清理工具

- [x] `core/config_registry.py`:新增 `eval.sample_max_pending_per_suite`
      (默认 200,0 = 不限)
- [x] `core/eval_sampling/scheduler.py`:每轮采样前统计各 suite
      `status=candidate` 数量,达到上限的 suite 本轮跳过插入并记 log
      (防止 memory_learning 式无限积压)
- [x] `core/eval_sampling/store.py`:新增 `bulk_reject_candidates`
      (按 suite/status/created_before 过滤,复用 REJECT_REASON_CODES,
      写 AdminAuditLog,返回计数)
- [x] `evals/candidates.py` CLI:新增 `batch-reject` 子命令
      (`--suite --status --created-before --reason-code --limit
      --dry-run/--apply`)——供生产清理 2000+ 存量积压
- [x] 测试:限流逻辑 + bulk reject + CLI

## 阶段 4:reply_eval 调度化(默认关闭)

- [x] 从 `api/admin/reply_routes.py` 的 `/reply-eval/run` 路由中提取
      套件运行内核为可复用协程(路由与调度器共用,行为不变)
- [x] `core/config_registry.py`:`eval.reply_eval_schedule_enabled`
      (默认 False)、`eval.reply_eval_interval_hours`(默认 24)、
      `eval.reply_eval_variant`(默认 "v2_code_retry",与 /reply-eval/run 默认一致)
- [x] 新增调度线程(仿 `eval_sampling_scheduler`),接入
      `bootstrap/schedulers.py`
- [x] 测试:开关默认关闭、调度触发时调用运行内核、结果落
      `reply_eval_runs`

## 阶段 5:文档

- [x] `docs/evals.md` 补"闭环操作手册":
      生产快照同步 → 标注(WebUI/离线 JSONL)→ 晋升回仓 → 门禁;
      积压批量清理命令;reply_eval 调度开关;各 gate 脚本覆盖的套件清单

## 明确不做(本次降范围)

- **block session memory 的 eval case**:依赖主工作区未提交的重构,
  应随该功能 PR 一起补,此处不动。
- **生产→测试自动快照基建**:运维侧任务,本次只在文档中给出手动流程。
- 不改 webui(EvalsPage/ReplyEvalPage 已能消费现有接口)。

## 验证

- 每阶段:运行新增/相关测试文件(单文件不加 `-n`)
- 最终:`python -m pytest tests/ -n auto --dist loadfile`(0 failures)
  + 实际执行 `bash scripts/run_eval_pr_gate.sh` 确认含 regression 且通过
- 提交:等待用户明确说"提交"后,按 chinese-commit-conventions 分阶段提交
