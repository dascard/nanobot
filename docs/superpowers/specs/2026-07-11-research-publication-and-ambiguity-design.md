# 研究发布 URL 与主动外呼不确定态设计

- 日期：2026-07-11
- 状态：已实现
- 审查范围：`1a19417`

## 背景

研究发布层当前要求模型正文中的 URL 与 `web_search` 已规范化来源逐字一致。该策略能确保
实际发布链接就是已核验链接，但也会把带 UTM、默认端口或尾斜杠的同源链接判为
`unverified_url`。直接按 canonical URL 集合放行会把未经核验的原始链接交给用户，尤其会放过
被 canonicalizer 删除的重定向参数，因此不能直接采用。

主动外呼把 `ambiguous` 的冻结期绑定到 `max_silence_min`。这是旧设计中的保守选择，不是偶然
实现错误；但“最长沉默兜底”和“投递结果不确定后的隔离期”是两个独立运维概念，默认 48 小时
也会使一次不确定投递长期阻塞后续外呼。

## 目标

1. 容忍能安全映射到已核验来源的 HTTP(S) URL 展示变体。
2. 最终发布文本中只出现已核验的 canonical URL，不能原样放行变体。
3. 未核验 URL、危险 scheme、协议相对 URL、裸域名和无法 canonicalize 的候选继续失败关闭。
4. 为 `ambiguous` 提供独立、可热重载的冻结配置，默认 120 分钟。
5. 保持现有研究预算、来源提取、工具白名单和 Prompt Runtime 输入结构不变。

## 非目标

- 不放宽公网主机、userinfo、非 HTTP scheme 或敏感 query 的 URL 策略。
- 不修改 `web_search` provider 的来源核验协议。
- 不改变主动外呼同一 idempotency key 永不重新发布的约束。
- 不修改 QQbot 或其推送协议。

## URL 规范化方案

新增纯函数 `normalize_research_publication_text(text, sources)`。它先建立已核验 canonical URL 集合，
再扫描正文中的绝对 HTTP(S) token：

1. token 无法 canonicalize，保持原文，随后由严格校验拒绝。
2. canonical URL 不在已核验集合，保持原文，随后返回 `unverified_url`。
3. canonical URL 在已核验集合，将 token 替换成该 canonical URL，并保留 URL 后的句末标点。
4. 重写完成后继续调用 `validate_research_publication_text()`；发布门仍要求正文 URL 与来源逐字一致。

该顺序允许 `https://example.test/report/?utm_source=search` 映射为
`https://example.test/report`，但最终消息绝不会包含原始 tracking 参数。Markdown destination 中的
绝对 URL 使用同一 token 重写规则。协议相对 URL、裸 `www`、裸域名和危险 scheme 不参与重写，
继续被 `_scan_url_references()` 拒绝。

重写在三个边界执行：研究 runner 生成草稿后、候选内核接收研究结果后、publisher 读取研究
grounding 后。后两处是纵深防御，防止测试替身、旧持久化候选或其他调用方绕过 runner。

## Ambiguous 冻结配置

新增设置：

```text
key: proactive_outreach.ambiguous_hold_min
env: PROACTIVE_OUTREACH_AMBIGUOUS_HOLD_MIN
type: int
default: 120
min: 1
max: 10080
```

`DEFAULT_SENDING_AMBIGUITY_MINUTES=30` 继续只判断 `sending` 是否陈旧；
`max_silence_min` 继续只控制最长沉默和 surge ramp；`ambiguous_hold_min` 只计算最新
`publish_outcome_unknown` 记录的 `hold_until`。

冻结到期后允许创建新的 idempotency key 并重新评估，但旧 ambiguous key 永远不会再次调用
publisher。已有 ambiguous 行按新默认重新计算，升级时超过 120 分钟的记录会在下一轮恢复评估。
需要保持旧行为的部署可显式设置 `PROACTIVE_OUTREACH_AMBIGUOUS_HOLD_MIN=2880`。

## 配置与界面

- `core/config_registry.py` 注册新设置。
- Admin 主动外呼路由把它加入受管设置列表。
- WebUI 主动外呼页增加“投递不确定冻结”字段，沿用现有数值设置控件。
- 默认值来自 registry，不需要数据库迁移或手工初始化。

## 测试矩阵

- UTM、默认端口、host/scheme 大小写和尾斜杠变体被重写为已核验 URL并通过。
- 最终研究草稿和候选消息不包含原始变体。
- `utm_redirect` 即使 canonicalize 到来源，也只能发布去除参数后的 canonical URL。
- 伪造域名、userinfo、坏端口、危险 scheme、协议相对 URL 和裸域名继续拒绝。
- `ambiguous_hold_min=120` 与 `max_silence_min=2880` 独立生效。
- 冻结到期后使用新 key 恢复评估，旧 key 不重复发布。
- registry、Admin payload、WebUI 源码和构建产物包含新设置。

## 验收条件

1. 审查复现的 UTM、尾斜杠和默认端口用例返回 `draft_ready`，且输出只含 canonical URL。
2. 原有 URL 安全负例继续全部通过。
3. ambiguous 默认冻结 120 分钟，修改 `max_silence_min` 不改变冻结期。
4. 无数据库迁移、Prompt 模板或 QQbot diff。
