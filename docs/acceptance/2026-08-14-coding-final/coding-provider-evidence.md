# Project Coding Provider 证据

## 实际链路

`Settings → Provider Factory → OpenAICompatibleProvider → https://api.kimi.com/coding/v1 → k3`

实测值：

- provider name：`kimi-coding-plan`
- model：`k3`
- base URL：Coding Plan endpoint
- provider class：`OpenAICompatibleProvider`
- Fake fallback：未发生
- Metered endpoint/key：未读取、未调用
- response：HTTP 200，可解析

## 定位并修复的问题

1. Coding Plan/k3 明确拒绝固定 `temperature=0`。Coding Provider 现在不发送 temperature；Metered Provider 的既有配置兼容性保留。
2. 调用方自带 system message 时，结构化输出 schema 曾未附加，可能导致 k3 返回非 FactorSpec 文本。现在 schema 始终被安全附加到 system 内容。
3. 澄清答案现在会确定性重写变量和说明，避免模型原猜测在人工选择后残留。
4. 健康信息显示实际 active provider/model，而不是 Router 的泛化名称。

## 专项回归覆盖

- Coding base URL 与 model；
- Coding API key 从环境加载并经 `SecretStr` 解包；
- Factory 选择 coding；
- 健康检查使用实际 provider；
- 响应解析与错误转换；
- 不 fallback 到 Fake；
- Metered 配置仍可构造；
- 异常和 repr 不泄漏 Secret。

最终 Ruff、mypy、647 项后端测试均通过。
