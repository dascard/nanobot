# TimingGate 群聊保守发言优化

## 背景

TimingGate 是群聊消息进入完整回复链路前的发言时机判断器。它的错误成本不对称：

- 假阳性：bot 在群聊里乱插话，直接影响体验。
- 假阴性：少说一句，通常可以接受。

因此群聊默认策略应偏保守，尤其是 ambient 闲聊、斗图、签到、游戏命令、群友互相对话和 bot 刚发言后的跟风消息。

## 目标

1. `TimingGate` 的可选动作只允许 `continue`、`wait`、`no_reply`，和解析器保持一致。
2. 未被 @、未回复 bot、未明确点名 bot 时，默认 `no_reply`。
3. 只有明确指向 bot、回复 bot、叫 bot 名字、直接让 bot 做事时才稳定 `continue`。
4. 半句话、连续贴材料、明显还没说完时才 `wait`，不要对普通闲聊反复 wait。
5. 提供离线测试集覆盖群聊常见误触发场景。

## 验收

- prompt contract 测试能阻止 `reply/ignore/merge` 回流。
- `evals/cases/timing_gate` 至少覆盖 no_reply、continue、wait 三类。
- 全量 pytest 通过。
