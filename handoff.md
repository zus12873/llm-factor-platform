# Factor Platform Handoff

更新时间：2026-08-05  
仓库路径：`/Users/huanmeng/Downloads/Projects/llm_source_catch`  
当前分支：`main`  
当前阶段：Week 1（Task 1–6）与 Week 2 前段（Task 7–9）已实现并提交；2026-08-05 评审修订已写回计划档，修订任务 Task 2.5 已完成。

## 1. 用户目标

构建"基于大模型的因子开发——自然语言转换成因子"平台：

```text
自然语言研究想法 / 中英文卖方研报
→ 结构化 FactorSpec
→ 识别阻塞性歧义并向用户确认
→ 搜索并确认 Wind 字段
→ 匹配已有函数或受控通用查询
→ 确定性构建 manifest
→ 隔离执行
→ 数据、公式和结果校验
→ 保存、展示和检验因子
```

用户已确认以下约束，不要在新会话重复询问：

- 范围覆盖 P0、P1、P2。
- 时间约束：1 个月。
- 人力约束：1 名全职开发者；**无专职研究人员全程参与**，由开发者完成口径整理与初步核验，关键指标及最终验收由带教老师抽样确认。
- 验收口径：P0 稳定；P1/P2 为真实可运行原型。
- 选定架构：模块化 FastAPI 单体 + React 工作台 + 隔离 Worker。
- P1/P2 原型限制：代表性 PDF、日频 A 股、中小规模区间、线性多因子、单机部署、管理员/研究员两角色。
- 优先级：**核心闭环的正确性与安全性第一**。
- 范围：日频 A 股、中小规模研究，**不含实盘交易**。
- 资源：Wind 数据权限、模型接口与预算提前落实。

## 2. 已批准文档

先读以下文件：

1. 设计文档（已批准，2026-08-05 部分条款经评审修订）：
   - `docs/superpowers/specs/2026-08-04-factor-platform-design.md` —— 顶部有「修订记录」表，列出 15 条被取代条款
2. 完整实施计划（已批准，2026-08-05 修订）：
   - `docs/superpowers/plans/2026-08-04-factor-platform.md` —— 顶部有「修订记录」表
3. 技术设计方案（讲设计思路、框架、功能）：
   - `docs/技术设计方案.md`
4. 评审意见（本次修订的来源，13 条）：
   - `docs/技术设计方案问题与调整建议.md`

发现实现现实与计划冲突时，先以已批准设计为准，再把必要修订写回原计划档。**不要另写第二套架构或另起一套任务命名。**

## 3. 2026-08-05 评审修订（必读）

以下 9 条改变了架构或契约，**不要误当成旧设计实现**：

| # | 修订 | 落到任务 |
|---|---|---|
| 1 | 模型只产出 `formula_ast`；后端确定性渲染 `canonical_formula` 供用户确认；`formula_text` 已删除 | Task 2.5 ✅ |
| 2 | AST 算子 14→11，`winsorize`/`zscore`/`industry_neutralize` 移出 AST，改由有序 `PreprocessingPipeline` 执行 | Task 2.5 ✅ |
| 3 | 新增 `TimeConvention` 契约（观测/可得/信号日/交易日/执行价/前向收益起止） | Task 2.5 ✅ |
| 4 | `disputed` 口径由警告升级为阻塞；三值复核状态 | Task 10.5 |
| 5 | WDS 纳入架构作为字段元数据来源；字段发现扩为七层漏斗 | Task 9.5 |
| 6 | Schema 验证与数据存在性验证分离；采样按数据形态分策略；状态六值 | Task 10 |
| 7 | 新增第四条信任边界 B4（内部数据 → 外部模型）+ 全本地模式 | Task 4.5 |
| 8 | 任务队列补租约、超时恢复、幂等键、取消、工件清理 | Task 14 |
| 9 | **Worker 执行签名 manifest，不再执行生成的 Python**；`factor.py` 降级为用户导出产物；源码 AST 白名单取消 | Task 13 |

另 4 条补需求：修订事件与级联失效（Task 3.5）、复现记录扩到输入侧（Task 14/26）、研报边界收窄（Task 21–23）、案例集扩至 30+ 并增设隐藏验收集（Task 6.5）。

## 4. 安全红线

原 4 个含明文凭据的遗留文件**已完成值盲脱敏并入库**（Task 1，`b1f53df`）：`rq_wind_replica.py`（已迁至 `backend/src/factor_platform/wind/adapter.py`）、`Wind取数尝试.ipynb`、`windquery/windquery/SKILL.md`、`windquery/windquery/agents/researcher.md`。

持续要求：

1. 不得在聊天、日志、测试输出、提交信息或文档中复述实际凭据值。
2. **启用真实 Wind 前，必须由具备数据库管理权限的人轮换已暴露凭据**；新 `.env` 必须是轮换后的值。
3. 真实密钥仅进入本地 `.env`；仓库只提交空值 `.env.example`。
4. `windquery.rar` 未确认脱敏前不得加入版本库。
5. Worker 不得获取 Wind 或 LLM 密钥，生产 Compose 必须保持 `network_mode: none`。
6. 不得用"为了赶进度"为理由放开任意 SQL、`eval`、`exec`、系统命令或任意网络访问。
7. **B4 边界**：Wind 原始数据、密钥、完整研报正文默认不得发送至外部模型服务。

代码层面：`get_secret_value()` 在整个代码库中只出现一处（`wind/connection.py`），便于审计。

## 5. 已确定的关键技术决策

- Python：3.11，必须使用 uv 项目环境和 `uv run`。
- 前端：Node 22、npm、React、TypeScript、Vite、Ant Design。
- 后端：FastAPI、Pydantic 2、SQLAlchemy 2、SQLite。
- 工件：Parquet；SQLite 只存会话、事件、元数据和版本关系。
- 状态：后端事件状态机是唯一真相；前端只渲染后端状态。
- LLM：统一 `LLMProvider`；Coding Plan 健康时优先，按量 API 兜底；所有调用必须过 B4 出境过滤。
- **公式：模型只出 `formula_ast`；`canonical_formula` 由后端渲染，是用户确认的正式公式。**
- **预处理：有序流水线，区分 `target=variables|factor`，不得与 AST 双重表达。**
- **时间口径：默认 T 收盘计算、T+1 交易、次日开盘执行；无法确定公告时点时保守顺延。**
- 字段：专用函数 → 人工别名 → WDS 元数据 → BM25 → `information_schema` → 样本验证 → 用户确认（七层）。
- 取数：已有专用函数优先；未覆盖字段才使用六种受控通用查询形状。
- 财务数据：必须保留报告期、公告日和市场可得日，禁止未来函数。
- **执行：受信任后端取数写 Parquet；无密钥 Worker 校验并执行签名 manifest。**
- 修复：只修复分类后的参数/公式错误，最多两轮，每轮生成新版本并重新确认。

## 6. 当前实现状态

### 已完成并提交

| Task | 提交 | 内容 |
|---|---|---|
| 1 | `b1f53df` | 凭据脱敏 + uv 后端工程 + `Settings` |
| 2 | `0334636` | 领域契约 + 公式 AST |
| 3 | `b479457` | 事件状态机 + 仓储 + Alembic 迁移 |
| 4 | `31648b0` | LLM Provider 协议 + OpenAI 兼容适配 + 路由 + 用量 |
| 5 | `a8c1ce9` | 语义解析 + 澄清引擎 |
| 6 | `db0b12a` | 10 个标准案例 + 解析 CLI |
| 7 | `82edf1e` | Wind 适配层迁移 + 连接工厂注入 |
| 8 | `da6b464` | 能力注册表 → 规划器工具契约（13 条 → 10 个工具） |
| 9 | `1ff4b6c` | 字段目录 + 别名 + BM25 检索（7,487 记录 / 676 表） |

### 已实现待提交（见 §9）

- PyYAML 显式声明（Task 9 review 遗留）
- **Task 2.5**：canonical 公式渲染、有序预处理流水线、时间口径契约、AST 三重校验
- 两份新文档 + 计划档与设计文档的修订

### 未开始

Task 3.5、4.5、6.5、9.5、10.5，以及 Task 10–30。

### 最近一次验证证据（2026-08-05 实跑）

```text
uv run --project backend ruff check backend/src backend/tests   → All checks passed!
uv run --project backend mypy backend/src                       → no issues in 33 source files
uv run --project backend pytest <绝对路径>/backend/tests -q      → 147 passed
factor-platform parse-case ×10                                  → 全过，退出码 0
```

**注意**：pytest 必须用**绝对路径**调用，从仓库根用相对路径会让 rootdir 解析出错。

## 7. 执行模式（用户已选定）

- **混合模式**：Task 1–6 由主 Agent 直接实现（基础与敏感工作）；Task 7+ 采用 `subagent-driven-development`（每任务一个 fresh subagent + 两段式 review）。
- **直接在 `main` 上开发**（用户明确否决了 worktree 方案）。
- 每个独立任务验证后单独提交，禁止 `git add .`、`git add -A`、自动 push。
- **提交必须由用户本人运行 `/commit`** —— 该 skill 为 `disable-model-invocation`，Agent 不可调用，也不得用其他方式替代其流程。
- 声称完成前必须在当前回合跑过验证命令并贴出证据。
- UI 改动必须通过 `browser-control` 在真实浏览器验证。

## 8. 下一步

按修订后的依赖顺序：

1. **Task 3.5** —— 修订事件与级联失效归约器（当前归约器"最后非空值获胜"会留下陈旧产物）
2. **Task 4.5** —— B4 数据出境边界
3. **Task 6.5** —— 扩充案例集与隐藏验收集
4. **Task 9.5** —— WDS 元数据本地化
5. **Task 10.5** —— 口径基线登记表
6. 然后回到 Task 10 → 16 主线

Task 2.5 是 Task 12/13 的硬前置，已完成，主线不再被阻塞。

## 9. 待提交内容（建议分 4 次）

1. `backend/pyproject.toml`、`backend/uv.lock` → `fix: declare pyyaml explicitly`
2. `docs/技术设计方案.md`、`docs/技术设计方案问题与调整建议.md` → `docs: add technical design and review notes`
3. `docs/superpowers/plans/…`、`docs/superpowers/specs/…`、`handoff.md` → `docs: write review revisions back into plan and spec`
4. backend 源码与测试（4 个新模块、4 个新测试、6 个改动源文件、10 个夹具）→ `refactor: make formula AST the single source of truth`

提交前已确认：无 `.env`/凭据/密钥/数据库文件混入；`detect-secrets` 对本轮改动文件 0 高可信发现。

## 10. 不要误判为已完成的内容

以下均未实现或未验证：

- FastAPI 应用与 React 前端（尚未开始）；
- Kimi API 实际可用性（凭据未注入 `.env`）；
- 轮换后的 Wind 凭据是否可连接（**所有真实 Wind 冒烟步骤仍未执行**）；
- 公式编译、manifest 构建、Worker 执行、三层校验；
- 研报 PDF / OCR / 抽取；
- 因子检验、因子库、指数、权限、Docker Compose；
- 浏览器验收。

新 Agent 必须以真实执行证据更新这些状态，不得从设计文档或本 handoff 推断它们已经可用。
