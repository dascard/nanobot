# TimingGate 群聊保守发言计划

## 步骤

1. 先补测试：
   - 检查内置 `TIMING_GATE_PROMPT` 和 `prompts.default/timing_gate.md` 只声明 `continue|wait|no_reply`。
   - 检查 prompt 明确写出群聊默认少插话、假阳性更严重、冷却期保守等规则。
   - 增加 `timing_gate` eval cases，并验证分布覆盖。
2. 跑定向测试确认 RED。
3. 修改：
   - `clients/classifier_client.py`
   - `prompts.default/timing_gate.md`
   - 本地运行时 `data/prompts/timing_gate.md` 同步更新。
4. 跑定向测试和全量测试。

## 不做

- 不接入真实模型跑 eval，避免本地路由地址影响测试稳定性。
- 不修改群聊主回复 prompt。
