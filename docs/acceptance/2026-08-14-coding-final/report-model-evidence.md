# 中英文研报真实模型证据

合法本地夹具：

- `backend/tests/fixtures/reports/text_zh.pdf`
- `backend/tests/fixtures/reports/text_en.pdf`

两份均为仓库测试 PDF。只向真实 Coding Plan k3 发送必要的 bounded excerpts；没有发送 Wind 原始数据、凭据、DSN、公司 Secret、完整源码或未经授权的内部研报。

## 中文 PDF

- 文本型 PDF：2 页
- EvidenceBlock：4 个
- 发模文本：86 字符
- 抽取变量：`roe_ttm`
- formula AST：存在且通过校验
- 引用：`p1b2`、`p1b1`，ID、页码与 bbox 有效
- confidence：0.80
- direction：原文证据不足，保持空值并进入人工确认

## 英文 PDF

- 文本型 PDF：1 页
- EvidenceBlock：3 个
- 发模文本：146 字符
- 抽取变量：`roe_ttm`
- formula AST：存在且通过校验
- 引用 ID、页码与 bbox：有效
- confidence：0.78
- direction：原文证据不足，保持空值并进入人工确认

## 安全闸门

- hallucinated evidence ID：被后端拒绝；
- 低置信度：进入人工确认，不直接执行；
- 缺失方向/可信信封：保留草稿与证据，不自动生成可执行 FactorSpec；
- OCR 与本地 PDF 取证能力继续由离线测试覆盖。

结论：中英文研报的真实模型语义抽取通过，同时没有为了“自动成功”绕过人工确认闸门。
