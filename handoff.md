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

### 指标口径确认

- 已完成带教老师人工确认。
- 验收文档中的相关指标审核状态已由 `unreviewed` 更新为 `reviewed`。
- 详细记录见 [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)。
- 本次确认任务按要求只更新文档，没有修改 `backend/data/metric_definitions.yaml` 或单位元数据。若后续要求运行时界面同步显示 `reviewed`，需另行取得授权并更新运行时注册数据，不能把文档状态误当成代码状态。

## 当前未完成事项

### 1. 研报提取结果进入因子工作流闭环

当前状态：

- PDF 解析已完成；
- 因子抽取已完成；
- “进入因子工作流”按钮尚未完成完整接线；
- 当前按钮没有 `onClick` handler，不调用创建 Factor Session 的 API；
- 后端存在通用 `POST /api/sessions`，但缺少“研报抽取结果 → FactorSpec / Factor Session”的映射与专用调用链。

### 2. 因子库页面

当前状态：

- 页面和路由存在；
- 后端 `FactorLibrary` 核心服务部分存在并有单元测试；
- 前端页面仍显示“待实现”；
- 缺少完整的因子库 API 路由、前端数据加载和展示接线。

### 3. 字段语义增强

当前字段检索仍需增强金融语义规则，例如：

- 动量、收益率类因子优先使用复权价格；
- `close` 不应默认等同于 `adj_close`；
- 复权方式应成为显式、可确认的数据规则；
- 字段语义增强不能绕过现有人工确认和 Schema 校验。

### 4. 部署与版本化

- 当前机器没有完成 Docker/Compose 真实冒烟。
- 当前 ZIP 工作副本需要迁回带 Git 历史的全新远程 `main` 克隆。
- 在人工确认提交范围前，不要执行 `git add`、`git commit` 或 `git push`。

## 测试状态

最近一次完整真实验收结果：

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

上述为最近一次完整验收记录。本次 Git 整理和文档更新没有修改业务代码，因此没有重复运行完整项目验收。原始隐藏案例集当前不存在，不能重建替代集冒充重跑。

主要验收材料：

- [`docs/acceptance/2026-08-13/README.md`](docs/acceptance/2026-08-13/README.md)：真实 Wind、Worker、PIT 和校验证据；
- [`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md)：真实 Coding Plan、自然语言、研报和浏览器闭环；
- [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md)：指标口径人工确认记录。

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
- 当前 `.gitignore` 已覆盖大部分运行时产物，但建议在正式迁移工作树中补充 `/tmp/`、`*.parquet` 和 `/Wind取数尝试.ipynb`；该建议尚未应用。
- 验收截图已做人工视觉检查，未发现凭据、连接串或 Wind 原始数据明细。
- 提交前必须从全新远程 `main` 克隆中执行一次最终 secret scan 和 `git diff --cached` 复核。

## 后续开发建议

推荐顺序：

1. 固化 Git 版本：从远程 `main` 新建干净克隆，选择性迁入源码、测试和脱敏文档，人工复核后再提交；
2. 完成研报提取结果 → 因子工作流的会话创建和状态接线；
3. 完成因子库 API、前端页面和端到端测试；
4. 增强字段语义检索，明确复权价格与原始收盘价的差异；
5. 在具备 Docker 的环境补做 Compose 冒烟；
6. 运行完整离线门禁、真实组件定向回归、secret scan 和提交差异复核。

不要在未人工确认迁移范围时使用 `git add .` 或 `git add -A`。不要直接 push。
