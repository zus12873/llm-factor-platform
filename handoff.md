# Factor Platform Handoff

更新时间：2026-08-11  
仓库：https://github.com/zus12873/llm-factor-platform（**公开仓库**）  
当前分支：`main`，30 次提交，本地与远端一致  
当前阶段：**离线开发完成（36/36 任务）**，等待凭据接入真实数据

---

## 1. 先读这三份

| 文档 | 内容 |
|---|---|
| [`docs/使用说明.md`](docs/使用说明.md) | 功能清单与完整操作流程 |
| [`docs/工程优化记录.md`](docs/工程优化记录.md) | 实现中的 18 个问题与优化过程 |
| [`docs/acceptance/2026-08-10/README.md`](docs/acceptance/2026-08-10/README.md) | 验收报告，含**未能验证的部分** |

设计与计划（发现实现与计划冲突时，先以已批准设计为准，再把必要修订写回原计划档）：

- [`docs/superpowers/specs/2026-08-04-factor-platform-design.md`](docs/superpowers/specs/2026-08-04-factor-platform-design.md) —— 顶部有修订记录，列出 15 条被取代条款
- [`docs/superpowers/plans/2026-08-04-factor-platform.md`](docs/superpowers/plans/2026-08-04-factor-platform.md) —— 36 个任务，**每个任务下都有实现修订说明**

**不要另写第二套架构或另起一套任务命名。**

---

## 2. 用户已确认的约束（不要重复询问）

- 范围覆盖 P0、P1、P2；时间约束 1 个月
- 人力：1 名全职开发者，**无专职研究人员全程参与**；关键指标与最终验收由带教老师抽样确认
- 优先级：**核心闭环的正确性与安全性第一**
- 范围：日频 A 股、中小规模研究，**不含实盘交易**
- 架构：模块化 FastAPI 单体 + React 工作台 + 隔离 Worker
- **工作方式（2026-08-10）**：先离线完成全部开发，凭据在开发完成后统一索取
- **隐藏验收集（2026-08-10）**：由开发者本人编写，靠纪律隔离；该隔离是软的，须在报告中标注

---

## 3. 当前状态

### 已完成

**36 / 36 任务**，含技术评审新增的 6 个（2.5 / 3.5 / 4.5 / 6.5 / 9.5 / 10.5）。

```
ruff   → All checks passed!
mypy   → Success: no issues found in 82 source files
pytest → 612 passed（后端）
vitest → 28 passed（前端）
黄金集 → 37/37    阻塞召回 100%  工具选择准确率 100%
隐藏集 → 10/10    首次运行，与黄金集无差距
```

规模：后端 82 模块 / 13,423 行，测试 48 文件 / 7,800 行，前端 25 个 TS/TSX 文件。

### 已用真实组件验证（非替身）

- 真实浏览器（Playwright + 独立临时 profile 的 Chrome）：导航、路由、健康横幅、表单提交、错误呈现
- 真实 uvicorn：`/api/health` 200，`POST /api/sessions` 201
- 真实 OCR 引擎（RapidOCR）：识别 `ROE TTM cross-sectional rank`，置信度 0.99
- 真实 Worker CLI：任务从 `pending` 走到 `completed` 并产出 Parquet
- 真实 Wind 数据字典解析：678 份文档 → 7,478 字段，93.6% 有描述
- P0 端到端：想法 → 公式 → 计划 → manifest → 签名 → Worker → 三层校验（Wind 与模型为替身，**组件间接缝全真**）

### 未完成 —— 不要凭文档推断这些已可用

| 项目 | 状态 | 前置 |
|---|---|---|
| 真实 Wind 数据库连接 | **从未跑通** | 凭据轮换 |
| 真实大模型接口 | **未实测** | Kimi 凭据 |
| `run-case --real-wind` 取数接线 | **未实现** | 同上 |
| Docker 镜像构建与 Compose 冒烟 | **未执行** | 开发机无 Docker |
| 9 条口径复核 | 全部 `unreviewed` | 带教老师（不需凭据） |
| Task 20 五场景浏览器验收中的三项 | 未执行 | 取数接线 |

完整清单：[`docs/acceptance/deferred-credential-steps.md`](docs/acceptance/deferred-credential-steps.md)

---

## 4. 安全红线

1. **不得在聊天、日志、测试输出、提交信息或文档中复述实际凭据值。**
   仓库是**公开的**，这条比之前更严格。
2. **启用真实 Wind 前，必须由具备数据库管理权限的人轮换已暴露凭据。**
3. 真实密钥只进入本地 `.env`；仓库只提交全空值的 `.env.example`。
4. Worker 不得获取 Wind 或 LLM 密钥；生产 Compose 必须保持 `network_mode: none`。
5. 不得用「为了赶进度」放开任意 SQL、`eval`、`exec`、系统命令或任意网络访问。
6. **边界 B4**：Wind 原始数据、密钥、研报全文默认不得发送至外部模型服务。
7. 受商业授权的 Wind 数据字典（`windquery/`）与内部需求文档（`imgs/`）
   **不入公开仓库**，已在 `.gitignore` 中。

代码层面：`get_secret_value()` 在整个代码库中只出现一处（`wind/connection.py`），便于审计。

---

## 5. 关键技术决策

- Python 3.11，**必须**使用 uv 项目环境（`uv run --project backend`）
- pytest 必须用**绝对路径**调用，从仓库根用相对路径会让 rootdir 解析出错
- 前端类型由后端 OpenAPI 生成（`backend/scripts/export_openapi.py` → `npm run gen:api`）
- 状态：后端事件状态机是唯一真相；前端只渲染后端状态
- **公式**：模型只出 `formula_ast`；`canonical_formula` 由后端渲染，是用户确认的正式公式
- **预处理**：有序流水线，区分 `target=variables|factor`，不得与 AST 双重表达
- **时间口径**：默认 T 收盘计算、T+1 交易、次日开盘执行；公告时点不确定时保守顺延
- **执行**：受信任后端取数写 Parquet；无密钥 Worker 校验并执行签名 manifest
- **修复**：只修复分类后的参数/公式错误，最多两轮，每轮产出新版本并需重新确认

---

## 6. 执行模式

- **直接在 `main` 上开发**（用户明确否决了 worktree 方案）
- 每个独立任务验证后单独提交；禁止 `git add .` / `git add -A`
- **提交必须由用户本人运行 `/commit`** —— 该 skill 为 `disable-model-invocation`，
  Agent 不可调用，也不得用其他方式替代其流程
- **push 需用户明确授权**
- 声称完成前必须在当前回合跑过验证命令并贴出证据
- UI 改动必须通过 `browser-control` 在真实浏览器验证

---

## 7. 下一步

按依赖顺序：

1. **Wind 凭据轮换** —— 需数据库管理员执行。整条真实数据线堵在这里
2. 配置 `.env`（含 `SESSION_COOKIE_SECRET`、`MANIFEST_SIGNING_KEY` 两个随机串）
3. 三种查询形态的 `verify-field` 实测
4. 实现 `run-case --real-wind` 的取数接线，跑通真实端到端
5. 单位口径核对（`s_dq_volume` 是股还是万股，直接影响换手率）
6. 带教老师抽样确认 9 条口径 —— **可与 1–5 并行，不需凭据**
7. Docker 环境就绪后构建镜像并跑 Compose 冒烟
8. 补做 Task 20 依赖执行端的三个浏览器验收场景

---

## 8. 已知局限（方法论层面，无法靠代码解决）

- **隐藏验收集由开发者本人编写**，只能检出无意的过拟合，检不出有意的
- **无专职研究员**，口径由开发者整理并初步核验，正确性依赖带教老师抽样
- 补偿控制：口径基线登记表、数量级与参考值校验、三值复核状态闸门

这三条必须在任何对外汇报中如实呈现。
