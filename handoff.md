# Factor Platform Handoff

更新时间：2026-08-04  
仓库路径：`/Users/huanmeng/Downloads/Projects/llm_source_catch`  
当前分支：`main`  
当前阶段：设计和实施计划已批准、已提交；业务实现尚未开始。

## 1. 用户目标

构建“基于大模型的因子开发——自然语言转换成因子”平台：

```text
自然语言研究想法 / 中英文卖方研报
→ 结构化 FactorSpec
→ 识别阻塞性歧义并向用户确认
→ 搜索并确认 Wind 字段
→ 匹配已有函数或受控通用查询
→ 确定性生成 Python
→ 隔离执行
→ 数据、公式和结果校验
→ 保存、展示和检验因子
```

用户已确认以下约束，不要在新会话重复询问：

- 范围覆盖 P0、P1、P2。
- 时间约束：1 个月。
- 人力约束：1 名全职开发者。
- 验收口径：P0 稳定；P1/P2 为真实可运行原型。
- 选定架构：模块化 FastAPI 单体 + React 工作台 + 隔离 Worker。
- P1/P2 原型限制：代表性 PDF、日频 A 股、中小规模区间、线性多因子、单机部署、管理员/研究员两角色。

## 2. 已批准文档

先读以下文件，内容已经用户批准：

1. 设计文档：
   - `docs/superpowers/specs/2026-08-04-factor-platform-design.md`
2. 完整实施计划：
   - `docs/superpowers/plans/2026-08-04-factor-platform.md`

实施计划包含：

- Task 1–30，依赖顺序已经确定；
- 151 个 checkbox 步骤；
- 精确文件路径；
- 跨任务接口；
- TDD 红—绿步骤；
- 验证命令和预期结果；
- 每项任务的独立提交命令；
- 四周里程碑和 P0 延误时的切线规则。

不要另写第二套架构或另起一套任务命名。发现实现现实与计划冲突时，先以已批准设计为准，再把必要修订写回原计划。

## 3. 当前仓库内容

现有业务资产：

- `rq_wind_replica.py`
  - 约 1,500 行；
  - 已有 Wind MySQL 查询、证券信息、交易日、ST、停牌、股票/指数日行情、历史指数成分、简单因子、受控通用查询计划；
  - 含 `RQ_WIND_CAPABILITIES` 能力注册表；
  - 尚未迁入正式 backend 包。
- `Wind取数尝试.ipynb`
  - Wind 连接和小样本查询实验；
  - 不是生产入口。
- `windquery/windquery/references/`
  - Wind 表索引、字段索引和详细字典；
  - 约 678 张表、7,480 个字段；
  - 已提交，可作为字段目录源数据。
- `imgs/基于大模型的因子开发_需求文档.md`
  - 原始完整 PRD。
- `imgs/简要核心需求.md`
  - 简版需求。
- `imgs/1.png`、`imgs/2.png`
  - 工作台 UI 参考图。
- `windquery.rar`
  - 遗留压缩包；当前被忽略，不作为运行时依赖。

尚不存在：

- FastAPI 应用；
- React 应用；
- Pydantic 领域协议；
- 状态机和会话数据库；
- Kimi Provider；
- 字段 BM25 检索；
- 公式 DSL；
- 隔离 Worker；
- 校验模块；
- PDF/OCR；
- 因子检验、因子库、指数和权限；
- Python/Node 工程环境及应用测试。

## 4. 安全红线

以下遗留文件含明文凭据，当前通过 `.gitignore` 暂时排除：

- `/rq_wind_replica.py`
- `/Wind取数尝试.ipynb`
- `/windquery/windquery/SKILL.md`
- `/windquery/windquery/agents/researcher.md`

接手要求：

1. 不得在聊天、日志、测试输出、提交信息或文档中复述实际值。
2. Task 1 开始前，由具备数据库管理权限的人轮换已暴露凭据。
3. 只在脱敏完成后移除对应 `.gitignore` 条目并提交文件。
4. 真实密钥仅进入本地 `.env`；仓库只提交空值 `.env.example`。
5. `windquery.rar` 未确认脱敏前不得加入版本库。
6. Worker 不得获取 Wind 或 LLM 密钥，生产 Compose 必须保持 `network_mode: none`。
7. 不得用“为了赶进度”为理由放开任意 SQL、`eval`、`exec`、系统命令或任意网络访问。

## 5. 已确定的关键技术决策

- Python：3.11，必须使用 uv 项目环境和 `uv run`。
- 前端：Node 22、npm、React、TypeScript、Vite、Ant Design。
- 后端：FastAPI、Pydantic 2、SQLAlchemy 2、SQLite。
- 工件：Parquet；SQLite 只存会话、事件、元数据和版本关系。
- 状态：后端事件状态机是唯一真相；前端只渲染后端状态。
- LLM：统一 `LLMProvider`；Coding Plan 健康时优先，按量 API 兜底。
- 公式：`formula_text` 仅展示；`formula_ast` 才能进入确定性编译器。
- 字段：能力注册表 → 人工别名 → BM25 → 实际 Schema → 小样本验证 → 用户确认。
- 取数：已有专用函数优先；未覆盖字段才使用受控通用查询计划。
- 财务数据：必须保留报告期、公告日和市场可得日，禁止未来函数。
- 执行：受信任后端先取数；无密钥 Worker 只处理输入 Parquet 和白名单公式。
- 修复：只修复分类后的参数/公式错误，最多两轮，每轮生成新版本并重新确认。

## 6. 四周里程碑

### 第 1 周：可信基础

对应 Task 1–6：

- 凭据轮换和脱敏；
- uv 后端工程；
- 领域协议和公式 AST；
- 事件状态机；
- Kimi Provider；
- 解析、澄清规则；
- 10 个标准因子案例。

完成门槛：CLI 能解析 10 个案例，并稳定阻塞有歧义的输入。

### 第 2 周：P0 计算闭环

对应 Task 7–16：

- Wind 适配层迁移；
- 能力目录；
- 字段目录、别名和 BM25；
- Schema 与小样本验证；
- 函数规划；
- 公式编译；
- 确定性代码生成；
- 文件队列 Worker；
- 三层校验；
- 真实 Wind CLI 闭环。

硬门槛：第 2 周末真实 P0 CLI 未通过时，冻结非必要 P2，不能削弱安全或准确性。

### 第 3 周：P0 网页稳定与 P1

对应 Task 17–22：

- 会话 API 和可恢复 SSE；
- React 工作台；
- 公式/字段确认卡；
- 代码、日志、校验和结果；
- 中文、英文文本 PDF；
- 页码证据；
- 浏览器 P0 验收。

### 第 4 周：P2 原型与交付

对应 Task 23–30：

- OCR；
- 两轮结构化修复；
- IC、Rank IC、分组收益、换手率；
- 因子库；
- 线性多因子；
- 指数成分和权重；
- 两角色权限；
- Docker Compose；
- 全量验收和交付文档。

## 7. 新会话接手步骤

新 Agent 收到“继续实现”指令后：

1. 调用并阅读 `using-superpowers`。
2. 阅读本文件、设计文档和实施计划。
3. 调用 `using-git-worktrees`，在隔离 worktree 开始功能开发。
4. 根据用户选择：
   - 推荐 `subagent-driven-development`；或
   - 使用 `executing-plans` 在当前 Agent 内分批执行。
5. 从实施计划 Task 1 开始，不跳过凭据整改。
6. 每个永久功能或 bug 修复采用 `test-driven-development`。
7. 遇到失败先使用 `systematic-debugging`，不要猜修复。
8. UI 改动必须调用 `browser-control`，通过浏览器真实验证。
9. 每个独立任务验证后单独提交，禁止 `git add .`、`git add -A`、自动 push。
10. 声称完成前调用 `verification-before-completion`，贴出当前回合的验证证据。

当前用户尚未选择执行模式。不要擅自开始实现；在新会话用户明确说“继续”或选择执行模式后再执行。

## 8. Task 1 的精确起点

实施计划 Task 1 是唯一正确起点：

- 创建 `backend/` uv 工程和 `Settings`；
- 创建 `.env.example`；
- 轮换并移除遗留明文凭据；
- 修改 Notebook、Wind skill/agent 文档中的连接说明；
- 运行 settings 测试与 secrets scan；
- 只在扫描通过后把 4 个遗留文件纳入 Git；
- 提交 `chore: secure credentials and scaffold backend`。

不要先搭 React，也不要先调用真实 Wind。真实 Wind 测试必须等凭据轮换和 Settings 注入完成。

## 9. 已有提交

```text
a0bd75a docs: add factor platform implementation plan
2b314bd docs: add factor platform design
0e12c28 chore: initial baseline
```

本 handoff 创建前的状态：

```text
branch main
staged 0, unstaged 0, untracked 0
```

## 10. 计划自检证据

实施计划已检查：

- Task 编号严格为 1–30；
- 共 151 个 checkbox 步骤；
- Markdown 代码围栏平衡；
- 无 `TBD`、`TODO`、省略号或模糊占位步骤；
- 安全、P0、P1、OCR、检验、因子库、指数、权限、Compose 和浏览器验收均有对应任务；
- `git diff --check` 通过；
- 计划提交后工作区干净。

## 11. 不要误判为已完成的内容

当前只完成了需求梳理、设计、计划和版本归档。以下均未实现或验证：

- FastAPI/React 是否能启动；
- Kimi API 是否可用；
- 轮换后的 Wind 凭据是否可连接；
- 任何 P0/P1/P2 业务路径；
- Docker Compose；
- 自动化测试和浏览器验收。

新 Agent 必须以真实执行证据更新这些状态，不得从设计文档或本 handoff 推断它们已经可用。
