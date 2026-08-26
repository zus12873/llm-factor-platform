# LLM Factor Platform Handoff

## 项目当前状态

项目目标：将自然语言研究想法或卖方研报转换为可执行、可复现、可审计的量化因子。

当前已完成自然语言因子生成、阻塞性歧义确认、Wind 字段候选与人工确认、受控真实取数、签名 manifest、隔离 Worker、因子计算、三层结果校验以及真实浏览器闭环。真实 Kimi Coding Plan、真实 Wind 和前后端端到端流程均已完成验收。

当前工作目录最初来自 GitHub ZIP，不包含 `.git`，因此它不是可直接提交的 Git 工作树。本次提交前检查确认：

- 原仓库：<https://github.com/zus12873/llm-factor-platform>
- 远程 `main`：`29597625e482284d85d5719623d3dbd208066851`
- 本地分支、remote 和 HEAD：不存在
- 与远程 `main` 的文件级对比：过滤运行时产物后，57 个已修改文件、43 个本地新增文件、0 个远程独有文件
- 正式接回 GitHub 时，应先重新克隆远程 `main`，再有选择地迁移本地增量；不要在当前 ZIP 目录中直接假定历史或分支关系

## 已完成核心功能

### LLM 接入

- 使用 OpenAI-compatible Provider。
- 已完善 Provider Router、结构化响应解析、健康检查、错误脱敏和调用用量记录。
- 当前支持 Kimi Coding Plan。
- Coding Plan Base URL：<https://api.kimi.com/coding/v1>
- 已真实验证 `k3` 模型、最小 Chat、结构化 FactorSpec 和工具调用能力。
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

研报 → 共用工作流（`feat/remaining-p1-p2-gaps`，Tasks 1–2）已接线，不是待实现：

- 上传时把抽取结果写成 `{artifact_id}.extraction.json`，与 PDF 同目录；
- `POST /api/reports/{artifact_id}/sessions` 从**服务端**抽取文件建会话，客户端不能投递一份自造 extraction blob；
- 研报页收集与研究想法页相同的可信信封，点「进入因子工作流」后跳到 `/workbench/{session_id}`；
- 进入后状态为 `needs_clarification` 或 `waiting_formula_confirmation`，**不会**直接变成 `searching_fields`；
- 人工公式路径只把 typed formula 交给 `FactorParser`，研报全文不进 prompt（B4）。

### 因子库

`FactorLibrary` 文件系统不可变版本 + HTTP + 页面（Tasks 3–4）已接线：

- `GET /api/library`、`GET /api/library/{factor_id}/v/{version}`、`POST /api/library`；
- 只接受 `completed` 会话；复制结果 parquet，不引用运行路径；
- `disputed` 拒绝发布（422 `publish_refused`）；`unreviewed` 可入库，并带入库时存下的 `review_status`；
- 工作台 completed 结果上有「发布到因子库」；结果标签为「含未复核口径，入库将标注「未复核」」，未复核本身不禁用发布；
- 「因子库」页列出最新版本的存储复核状态，不现场用注册表重算。

### 字段语义（复权 vs close）

检索是排序 + 标签层，不是静默改写（Tasks 5–6）：

- `收盘价` 别名仍是 `s_dq_close`；`close ≠ adj_close`；
- 动量 / 收益 / 波动类查询会抬高后复权并贴标签，未复权行仍列出；
- 确认闸门仍在：不自动确认字段；
- 规划器 `adjust_type` 跟已确认字段：`s_dq_close` → `none`（即使 `use_adjusted_price` 为 True）；`s_dq_adjclose` → `post`；`s_dq_preclose` 同样不因数据规则被反转。

### 指标口径确认

- 已完成带教老师人工确认。
- 验收文档中的相关指标审核状态已由 `unreviewed` 更新为 `reviewed`。
- 详细记录见 [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)。
- 本次确认任务按要求只更新文档，没有修改 `backend/data/metric_definitions.yaml` 或单位元数据。若后续要求运行时界面同步显示 `reviewed`，需另行取得授权并更新运行时注册数据，不能把文档状态误当成代码状态。

## 当前未完成事项

研报工作流、因子库页面 + API、字段复权语义（排序 / 标签 / 规划器跟确认字段）已在 `feat/remaining-p1-p2-gaps` 接线。下面不再把它们列为缺口。

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

### 3. 字段语义残留（非原 P1 缺口）

原缺口已关。审查留下的次要项，不是「close 被当成 adj_close」：

- 检索 `limit` 在注入优先字段后仍可能挤掉未复权行；
- `s_dq_adjclose_backward` → `adjust_type=pre` 在 `get_price` 路径上未接通（该列不在价格输出映射里）；
- 成交量等未映射列的 `adjust_type` 仍回退到 `use_adjusted_price`。

### 4. 本计划明确不做的

- 多因子指数 UI（`indices` 服务已有单测，本轮无页面）；
- 用 embeddings 替换 BM25；
- 把前端从 loopback 暴露出去；
- 重建隐藏黄金集；
- force-push / 自动 push。

## 测试状态

最近一次**完整**真实验收（本 remaining P1/P2 分支之前，稳定版 `32ce5b3` / `main` `c37fa4a` 一带）记录：

| 测试项 | 结果 |
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

`feat/remaining-p1-p2-gaps` 在此之上加了研报工作流、因子库、价格语义与 Compose 契约测试。各任务自己的定向测试见 `.superpowers/sdd/2026-08-24-remaining-p1-p2-gaps/task-*-report.md`。

**本次文档同步（Task 8）没有重跑完整项目验收**，没有重跑 `pytest backend/tests` 全量，没有重跑真实 Wind，没有重跑 Kimi，没有跑浏览器闭环。Compose 真实冒烟见上：SKIPPED，不是通过。原始隐藏案例集当前不存在，不能重建替代集冒充重跑。

主要验收材料：

- [`docs/acceptance/2026-08-13/README.md`](docs/acceptance/2026-08-13/README.md)：真实 Wind、Worker、PIT 和校验证据；
- [`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md)：真实 Coding Plan、自然语言、研报和浏览器闭环；
- [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)：指标口径人工确认记录；
- [`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md)：Compose 冒烟 **SKIPPED**（本机无 Docker CLI），契约 13 passed。

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

- 项目根目录 `Wind取数尝试.ipynb` 含非空 Wind 连接配置，禁止提交，应移出待提交工作树并加入忽略规则。
- `data/artifacts/`、`tmp/`、Parquet、数据库、日志、虚拟环境、Node 依赖、缓存和构建产物均禁止提交。
- `.gitignore` 已覆盖 `/tmp/`、`*.parquet`、`Wind取数尝试.ipynb`，以及 `deploy/compose.env`（Compose 冒烟配方会把填好的密钥复制到该路径；`.env` / `.env.*` 匹配不到这个文件名）。
- 验收截图已做人工视觉检查，未发现凭据、连接串或 Wind 原始数据明细。
- 提交前必须从全新远程 `main` 克隆中执行一次最终 secret scan 和 `git diff --cached` 复核。

## 后续开发建议

推荐顺序：

1. 在有 Docker 的机器按 [`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md) 补真实 Compose 冒烟，用实测输出覆盖「未执行」；不要把契约 13 passed 写成冒烟通过；
2. 若产品要求运行时显示口径已复核，另行授权后改 `metric_definitions.yaml`，不要把文档里的 `reviewed` 当成代码状态；
3. 需要时再处理字段语义残留（`limit` 挤掉未复权行、前复权 close 未走 `get_price`）；
4. 合并本分支前跑完整离线门禁、secret scan 和 `git diff` 复核。未要求不要重跑真实 Wind / Kimi。

不要使用 `git add .` 或 `git add -A`。不要直接 push。不要 force-push。

## 当前稳定版本

- 当前稳定版本 commit：`32ce5b3f63479befa7bc01194c1187a33d513e5b`
- GitHub `main` 已同步。
