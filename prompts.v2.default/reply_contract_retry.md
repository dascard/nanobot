---
name: 回复合约重试 V2
version: 1
description: V2 reply/no_reply 合约重试模板。
---
上一轮没有调用 reply 或 no_reply。本轮必须只调用一个最终工具：要回复则调用 reply(content)，不回复则调用 no_reply(reason)。
