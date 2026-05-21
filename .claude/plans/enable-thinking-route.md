# enable_thinking 路由参数实现计划

1. 先写测试：
   - admin route 保存并在 status 返回 `enable_thinking`。
   - `private_decision` 继承 `timing_gate.enable_thinking`，并允许覆盖。
   - `NewAPIClient._build_payload` 根据 `auto/true/false` 注入 thinking。

2. 后端实现：
   - 增加 route 参数归一化工具。
   - route 解析结果携带 `enable_thinking`。
   - admin route 编辑接口允许保存字段。
   - status/resolved API 返回字段。

3. 模型请求实现：
   - NewAPI 非流式/流式请求传递 `enable_thinking`。
   - classifier route 请求应用该字段。
   - reply bridge 同步 route 时更新 controller `extra_body`。

4. 前端实现：
   - 路由卡片显示 thinking 状态。
   - 编辑弹窗添加三态选择器，并随保存请求提交。

5. 验证：
   - 运行新增/相关 pytest。
   - 运行 WebUI build。
   - 检查 diff 和 vendor 目录未被修改。
