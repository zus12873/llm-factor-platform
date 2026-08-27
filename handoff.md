# LLM Factor Platform Handoff

## 项目当前状态

项目目标：将自然语言研究想法或卖方研报转换为可执行、可复现、可审计的量化因子。

当前已完成自然语言因子生成、阻塞性歧义确认、Wind 字段候选与人工确认、受控真实取数、签名 manifest、隔离 Worker、因子计算、三层结果校验，以及研报入口进入共用工作台、因子库发布/浏览、价格复权语义（排序 + 标签 + 规划器跟确认字段）。

Git：

- 仓库：<https://github.com/zus12873/llm-factor-platform>
- 默认分支 `main`，带完整 Git 历史。不要再按「无 `.git` 的 ZIP 工作副本」操作。
- 研报工作流 / 因子库 / 复权语义已合入 `main`（原功能分支 `feat/remaining-p1-p2-gaps`）。
- 文档同步日期：2026-08-27。以 GitHub `main` HEAD 为准，不要引用已过期的 ZIP 对比哈希。

## 已完成核心功能

### LLM 接入

- 使用 OpenAI-compatible Provider。
- 已完善 Provider Router、结构化响应解析、健康检查、错误脱敏和调用用量记录。
- 仓库里现存的**真实验收**是 Kimi Coding Plan（`k3`，`https://api.kimi.com/coding/v1`）：鉴权、最小 Chat、结构化 FactorSpec、工具调用、自然语言 / 研报 / 浏览器闭环。证据：[`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md)。
- 按量 / Open Platform 专项目录（`docs/acceptance/2026-08-14/`、`2026-08-14-k3-final/`）**不在本仓库**。不得把断裂链接当成「按量已通过」或「按量已 401」的 live 证据。
- 未配置或不可达时不会静默回退到 Fake Provider。
- 真实 API Key 只允许通过环境变量或本地未跟踪配置注入，不得写入仓库。

### Wind 真实数据链路

- 已完成真实 Wind MySQL 只读取数。
- 已完成以下真实执行流程：

  1. 后端按受控查询函数或受控查询形状获取数据；
  2. 写入 `raw_input.parquet` 和 `aligned_input.parquet`；
  3. 构建并签名 manifest；
  4. 无 Wind/LLM 凭据、无数据库连接的 Worker 执行因子；
  5. 生成因子结果；
  6. 执行数据、公式和结果三层校验。

- `run-case --real-wind` 已完成接线并真实跑通。
- 支持历史指数成分、ST/停牌过滤、预热窗口、公告日 point-in-time、T 日信号与 T+1 交易口径。
- 后端持有 Wind 凭据；Worker 不持有凭据且不连接数据库。
- 不存在任意 SQL 通道。

### 因子生成流程

当前支持：

```text
自然语言研究想法
→ 因子结构生成（FactorSpec / AST）
→ 阻塞性歧义确认
→ Wind 字段候选
→ 人工确认字段
→ 受控取数计划
→ manifest
→ Worker 执行
→ 结果校验与展示
```

模型只负责生成结构化 FactorSpec；正式 `canonical_formula` 由后端根据 AST 确定性渲染。修改公式或字段会使下游计划、manifest 和旧结果级联失效。

### 研报解析

当前支持：

- 中文文本型 PDF；
- 英文文本型 PDF；
- 本地 PDF 文本提取和 OCR；
- 公式与因子变量抽取；
- 页码、坐标和证据 ID 记录；
- 低置信度或 OCR 公式进入人工确认，不直接执行；
- 对外模型只接收经过边界控制的必要片段，不发送完整内部研报或 Wind 原始数据。

研报 → 共用工作流已接线（已合入 `main`），不是待实现：

- 上传时把抽取结果写成 `{artifact_id}.extraction.json`，与 PDF 同目录；
- `POST /api/reports/{artifact_id}/sessions` 从**服务端**抽取文件建会话，客户端不能投递一份自造 extraction blob；
- 研报页收集与研究想法页相同的可信信封，点「进入因子工作流」后跳到 `/workbench/{session_id}`；
- 进入后状态为 `needs_clarification` 或 `waiting_formula_confirmation`，**不会**直接变成 `searching_fields`；
- 人工公式路径只把 typed formula 交给 `FactorParser`，研报全文不进 prompt（B4）。

### 因子库

`FactorLibrary` 文件系统不可变版本 + HTTP + 页面已接线（已合入 `main`）：

- `GET /api/library`、`GET /api/library/{factor_id}/v/{version}`、`POST /api/library`；
- 只接受 `completed` 会话；复制结果 parquet，不引用运行路径；
- `disputed` 拒绝发布（422 `publish_refused`）；`unreviewed` 可入库，并带入库时存下的 `review_status`；
- 工作台 completed 结果上有「发布到因子库」；结果标签为「含未复核口径，入库将标注「未复核」」，未复核本身不禁用发布；
- 「因子库」页列出最新版本的存储复核状态，不现场用注册表重算。

### 字段语义（复权 vs close）

检索是排序 + 标签层，不是静默改写（已合入 `main`；Tasks 3–5 已关审查残留）：

- `收盘价` 别名仍是 `s_dq_close`；`close ≠ adj_close`；
- 动量 / 收益 / 波动类查询会抬高后复权并贴标签；`收盘价` 别名命中或生产 `discover_fields` → `FieldSearch.search` 注入 `s_dq_close` 时，未复权行在 `limit=5` 下仍保留（`apply_price_semantics` 预留，见 `backend/tests/wind/test_price_semantics.py`）；
- 确认闸门仍在：不自动确认字段；
- 规划器 `adjust_type` 跟已确认字段：`s_dq_close` → `none`（即使 `use_adjusted_price` 为 True）；`s_dq_adjclose` → `post`；`s_dq_adjclose_backward` → `pre` 且走 `wind.get_price`；`s_dq_preclose` 同样不因数据规则被反转；成交量 / limit 字段不继承 `use_adjusted_price`（见 `backend/tests/wind/test_planner.py`）。

### 指标口径确认

- 已完成带教老师人工确认。
- 验收文档中的相关指标审核状态已由 `unreviewed` 更新为 `reviewed`。
- 详细记录见 [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)。
- 本次确认任务按要求只更新文档，没有修改 `backend/data/metric_definitions.yaml` 或单位元数据。若后续要求运行时界面同步显示 `reviewed`，需另行取得授权并更新运行时注册数据，不能把文档状态误当成代码状态。

## 当前未完成事项

研报工作流、因子库页面 + API、字段复权语义（排序 / 标签 / 规划器跟确认字段）已合入 `main`。下面不再把它们列为缺口。

### 1. Docker / Compose 真实冒烟（未执行，不是通过）

- Task 7 闸门：`docker info` → `zsh:1: command not found: docker`（exit 127）。
- **没有**复制 `deploy/compose.env`，**没有** `compose up`，**没有**打镜像，**没有**请求 `/api/health`。
- Compose **契约**测试 13 passed（离线读 `deploy/compose.yaml`）。契约通过 ≠ 冒烟通过。
- 证据：[`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md)。
- `deploy/compose.yaml` 未改。Worker 仍是 `network_mode: none`，环境只有 `MANIFEST_SIGNING_KEY`（验签）。前端仍绑 `127.0.0.1:8080`。

有 Docker 的机器按该文件「冒烟命令」一节重跑，用真实输出替换「未执行」，再标 PASS 或真实失败。

### 2. 口径注册表运行时状态

- 验收文档里的指标审核已记为 `reviewed`（见 [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)）。
- 按当时任务要求，**没有**改 `backend/data/metric_definitions.yaml`。文档状态 ≠ 运行时注册表。界面仍按 YAML 的 `unreviewed` / `disputed` 闸门工作。若要运行时显示 `reviewed`，需另行授权改注册数据。

### 3. 本计划明确不做的

- 多因子指数 UI（`indices` 服务已有单测，本轮无页面）；
- 用 embeddings 替换 BM25；
- 把前端从 loopback 暴露出去；
- 重建隐藏黄金集；
- force-push / 自动 push。

字段语义曾列的三项审查残留（`limit` 挤掉未复权行、`s_dq_adjclose_backward` 未走 `get_price`、成交量继承 `use_adjusted_price`）已由 Tasks 3–5 关闭；覆盖测试见 `backend/tests/wind/test_price_semantics.py` 与 `backend/tests/wind/test_planner.py`。不要当作未完成事项重做。

## 测试状态

两层数字不要混用。

**A. 仓库里现存的完整真实验收**（Coding Plan + 真实 Wind + 浏览器五场景）见 [`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md)：

| 测试项 | 当时记录 |
|---|---|
| 后端测试 | 647 passed，12 skipped |
| Ruff | 通过 |
| mypy | 85 个源文件无问题 |
| 前端测试 | 29 passed |
| 前端生产构建 | 通过 |
| npm audit | 0 vulnerabilities |
| Golden | 37/37 |
| 真实 Wind | 通过 |
| Kimi Coding Plan | 通过 |
| 真实浏览器五场景 | 通过 |

**B. 最新离线门禁**（remaining-todo 修复后本机六命令，没有重跑真实 Wind / Kimi / 浏览器 / Compose / GitHub Actions）见 [`docs/acceptance/2026-08-27-offline-gates.md`](docs/acceptance/2026-08-27-offline-gates.md)：SHA `b6397f3e5855fde381adf1c9d30f7683cafd1366`，ruff / mypy / pytest **701 passed** / vitest **41 passed** / lint / build 全过。层 A 的 Coding Plan + Wind + 浏览器数字仍以 `2026-08-14-coding-final` 为准，不要用本文件覆盖。

本仓库**没有** `docs/acceptance/2026-08-13/`。不要引用该路径。

`backend/data/hidden_cases/` 现有 **10** 个从本机工作副本回收的历史案例，已入库。它们**不再是盲测集**；不得把对着它们跑通的结果写成 2026-08-10 原隐藏验收。新盲测须由未参与调参的人另建。

主要验收材料（均在仓库内）：

- [`docs/acceptance/2026-08-10/README.md`](docs/acceptance/2026-08-10/README.md)：离线开发完成验收；
- [`docs/acceptance/2026-08-27-offline-gates.md`](docs/acceptance/2026-08-27-offline-gates.md)：最新离线门禁（仅 ruff/mypy/pytest/vitest/lint/build）；
- [`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md)：真实 Coding Plan、自然语言、研报、Wind 和浏览器闭环；
- [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)：指标口径人工确认（**只改了文档**）；
- [`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md)：Compose 冒烟 **SKIPPED**，契约 13 passed。

## 环境配置说明

所有真实值只通过环境变量或本地未跟踪配置注入。仓库中仅保留空值模板 `.env.example`。

### Kimi Coding Plan

- `KIMI_CODING_BASE_URL`
- `KIMI_CODING_API_KEY`
- `KIMI_CODING_MODEL`
- `KIMI_CODING_REASONING_EFFORT`（可选）
- `LOCAL_ONLY_MODE`

### Wind

- `WIND_ENABLED`
- `WIND_HOST`
- `WIND_PORT`
- `WIND_USER`
- `WIND_PASSWORD`
- `WIND_DATABASE`

### 平台运行

- `APP_ENV`
- `DATABASE_URL`
- `ARTIFACT_ROOT`
- `JOB_ROOT`
- `SESSION_COOKIE_SECRET`
- `MANIFEST_SIGNING_KEY`

安全要求：

- 不得提交 `.env`、API Key、Wind 账号密码或完整连接串；
- Worker 环境不得包含 Wind 或 LLM 凭据；
- 不得把 Wind 原始数据、完整内部研报或其他敏感内容发送给外部模型；
- 不得关闭 manifest 验签、点时校验或 disputed 闸门；
- 不得引入任意 SQL、`eval` 或 `exec`。

## Git 提交前安全提示

- 仓库跟踪的 `Wind取数尝试.ipynb` **已脱敏**：连接参数来自 `os.environ.get("WIND_*")`，非空 host/password/user 字面量为 0。禁止再写入明文。该路径仍在 `.gitignore` 中，避免把填了凭据的本地副本重新纳入提交。
- `data/artifacts/`、`tmp/`、Parquet、数据库、日志、虚拟环境、Node 依赖、缓存和构建产物均禁止提交。
- `.gitignore` 已覆盖 `/tmp/`、`*.parquet`、`Wind取数尝试.ipynb`，以及 `deploy/compose.env`（Compose 冒烟配方会把填好的密钥复制到该路径；`.env` / `.env.*` 匹配不到这个文件名）。
- 验收截图已做人工视觉检查，未发现凭据、连接串或 Wind 原始数据明细。
- 提交前必须从全新远程 `main` 克隆中执行一次最终 secret scan 和 `git diff --cached` 复核。

## 后续开发建议

推荐顺序：

1. 在有 Docker 的机器按 [`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md) 补真实 Compose 冒烟，用实测输出覆盖「未执行」；不要把契约 13 passed 写成冒烟通过；
2. 若产品要求运行时显示口径已复核，另行授权后改 `metric_definitions.yaml`，不要把文档里的 `reviewed` 当成代码状态；
3. 需要时再跑 secret scan、`git diff` 复核，以及（若要远端证据）触发 GitHub Actions 离线门禁工作流。本机六命令离线门禁已在 Task 7 记过（见 [`docs/acceptance/2026-08-27-offline-gates.md`](docs/acceptance/2026-08-27-offline-gates.md)）；未要求不要重跑真实 Wind / Kimi，也不要重做已关闭的字段语义 Tasks 3–5。

不要使用 `git add .` 或 `git add -A`。不要 force-push。push 须用户明确要求。

## 当前稳定版本

- 真实 Wind + Coding Plan 闭环的产品基线：`32ce5b3`（当时 GitHub `main` 为 `c37fa4a`）。
- 研报工作流 / 因子库 / 复权语义已合入 GitHub `main`。以远程 `main` HEAD 为准。
