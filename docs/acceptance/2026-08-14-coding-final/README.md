# Coding Plan 最终真实验收

日期：2026-08-14

最终状态：`READY_WITH_HUMAN_REVIEW_PENDING`

本轮正式运行时是 Kimi Coding Plan，经 `https://api.kimi.com/coding/v1` 使用 `k3`。没有使用 Fake Provider，也没有把 Coding Plan 冒充 Metered/Open Platform。

## REAL VERIFIED

| 项目 | 结果 | 真实证据摘要 |
| --- | --- | --- |
| Coding Plan 鉴权 | PASS | `/models` HTTP 200，返回 `k3`；最小 Chat HTTP 200 |
| Project Coding Provider | PASS | Settings → Factory → `OpenAICompatibleProvider`；provider=`kimi-coding-plan`，model=`k3` |
| Tool Calling | PASS | 真实返回 `echo_factor_name` tool call，参数 JSON 合法且包含 `momentum_20d` |
| 自然语言因子 | PASS | 真实 k3 共 8 例；FactorSpec 与 AST 合法率均 100% |
| 自动歧义确认 | PASS | 3 个阻塞性歧义均阻塞，浏览器人工选择后才继续 |
| 中英文研报 | PASS | 两份仓库合法文本型 PDF；本地取证、有限片段发模、证据 ID 校验与人工闸门均通过 |
| 真实 Wind | PASS | `momentum_20d --real-wind` exit 0；真实 Adapter、signed manifest、隔离 Worker、三层校验 |
| 真实浏览器 | PASS | 明确因子、阻塞歧义、公式级联失效、字段级联失效、真实结果页五场景均完成 |
| 点时/样本边界 | PASS | 历史成分、预热输入、结果日期裁切、ST/停牌处理和 T/T+1 约束均保留 |

详细证据见：

- [Coding Plan 原始接口](coding-plan-evidence.md)
- [项目 Provider](coding-provider-evidence.md)
- [真实自然语言案例](kimi-real-cases.md)
- [研报模型链路](report-model-evidence.md)
- [浏览器五场景](browser-evidence.md)
- [命令与退出码](commands.md)
- [源码修改](code-changes.md)

## OFFLINE VERIFIED

- Ruff：通过。
- mypy：85 个源文件无问题。
- Backend pytest：647 passed，12 skipped。
- Frontend Vitest：29 passed。
- Frontend build：通过。
- npm audit：0 vulnerabilities。
- Golden suite：37/37，阻塞 recall/precision 100%，不必要追问 0%。
- 终验当时：原始隐藏案例未随仓库分发。2026-08-27 已把本机 10 个历史 `hidden_cases` 入库；它们不再是盲测，不能回溯冒充本次终验的隐藏集重跑。

## HUMAN REVIEW COMPLETED（后续状态更新）

原始 Coding Plan 验收执行时没有擅自修改业务口径。后续用户已向带教老师确认当前
指标定义、Wind 字段映射和统一时间口径；老师反馈当前口径没有问题，可以按照当前
定义使用。当前验收文档状态为 `reviewed`。本更新不改变本轮真实组件测试结果和
`READY_WITH_HUMAN_REVIEW_PENDING` 这一历史验收结论。

确认记录（文档层；运行时 YAML 仍为 `unreviewed`）：

- [metric-review-evidence.md](../metric-review-evidence.md)

## DEPLOYMENT ENVIRONMENT PENDING

当前机器未安装 Docker CLI。Compose 真实冒烟是 `SKIPPED`（2026-08-26：`command not found`，exit 127），不是因子研究核心技术阻塞。证据：[compose-smoke.md](../compose-smoke.md)。

## OPTIONAL / NOT REQUIRED

Metered/Open Platform fallback 本轮未测试、未验证；它被保留为可选能力，不影响老师“优先 Coding Plan”的原始要求。

## 结论

真实模型、真实 Wind、隔离 Worker、验证器和真实浏览器闭环已完成。原始验收时剩余事项为老师确认业务指标口径及在另一个具备 Docker 的环境进行部署冒烟；其中指标口径确认已在后续完成。历史验收结论不作追溯修改。
