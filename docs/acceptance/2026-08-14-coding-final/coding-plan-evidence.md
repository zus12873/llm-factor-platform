# Coding Plan 原始接口证据

## 配置边界

- Base URL：`https://api.kimi.com/coding/v1`
- Model：`k3`
- 协议：OpenAI-compatible Chat Completions
- Key 来源：临时运行环境；未写入仓库或报告
- 未请求 `moonshot.cn`，未使用 Metered Key，未伪造客户端 User-Agent

## `/models`

- HTTP status：200
- 可用模型中确认存在：`k3`
- 认证判定：`REAL VERIFIED`

## 最小 raw Chat

- Endpoint：`POST /chat/completions`
- Model：`k3`
- 输入：无公司、Wind 或研报信息的最小“只回复 ok”请求
- HTTP status：200
- Completion：正常、可解析
- Finish reason：`stop`
- Usage：接口返回 usage；最近一次最小探针总用量约 138 tokens
- Secret/Authorization：未记录

上述结果证明 Coding Plan Key、Base URL 和 k3 在当前机器及当前网络上可以真实工作；本轮没有继续调查 Metered 401。

## Tool Calling

对真实 k3 发送无副作用工具 `echo_factor_name`：

- `tool_choice="required"` 成功；
- 返回真实 `tool_calls`，不是普通文本冒充；
- tool name 为 `echo_factor_name`；
- arguments 为合法 JSON；
- `name` 为 `momentum_20d`。

K3 thinking 模式不接受指定某个 named tool 的旧式强制选择；这属于接口约束。项目的正式因子链路本身采用“模型生成结构化 FactorSpec、后端确定性执行”，不依赖模型直接调用后端函数。
