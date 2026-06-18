---
name: 图片摘要用户提示词
version: 1
kind: tool
tool_name: image_summary
description: image_summary 视觉模型调用的 user prompt 文本部分。
---
请为以下 {{ image_count }} 张图片生成结构化摘要。

要求：
- 输出严格 JSON，不要 Markdown，不要代码块，不要解释文字。
- 保留图片中的文字、物体、场景、布局和可疑信息。
- 看不清就写入 uncertainties，不要猜测。
- 如果图片里有文字，请尽量做 OCR。
- 额外关注点：{{ focus }}
