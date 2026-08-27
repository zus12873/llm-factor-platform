# LLM Factor Platform

把研究员的一句话想法或一份卖方研报，转换成**可执行、可复现、可审计**的量化因子。

```
自然语言研究想法 / 中英文卖方研报
    ↓  结构化 FactorSpec（因子定义）
    ↓  识别阻塞性歧义并向用户确认        ← 不猜，宁可问
    ↓  搜索并确认 Wind 字段
    ↓  匹配已有函数或受控通用查询
    ↓  确定性构建执行 manifest
    ↓  隔离执行（无密钥 Worker）
    ↓  数据、公式、结果三层校验
    ↓  保存、展示和检验因子
```

**范围**：日频 A 股、中小规模研究区间、线性多因子，**不含实盘交易**。

---

## ⚠️ 当前状态（2026-08-27）

原计划 36 个任务已实现。研报进入共用工作台、因子库 HTTP/页面、价格复权语义已合入 `main`。

| | 状态 |
|---|---|
| 领域契约、事件状态机、语义解析、澄清引擎 | ✅ 已实现并测试 |
| 字段发现（7,478 字段元数据 + 中文检索 + Schema/样本验证） | ✅ 已实现并测试 |
| 公式编译、预处理流水线、签名 manifest、隔离 Worker | ✅ 已实现并测试 |
| 数据 / 公式 / 结果三层校验、口径三值闸门 | ✅ 已实现并测试 |
| FastAPI 服务、React 工作台、SSE 可恢复事件流 | ✅ 已实现并测试 |
| 研报 PDF 解析、受限 OCR、因子抽取、进入共用工作流 | ✅ 已实现并测试 |
| 因子检验（IC / 分位 / 换手）、因子库发布/浏览、多因子指数服务、本地认证 | ✅ 已实现并测试（指数 **无独立页面**） |
| **真实 Wind 数据库连接** | ✅ 真实 Adapter、受控查询与完整因子执行通过 |
| **`run-case --real-wind`** | ✅ 已接线并真实跑通 |
| **真实大模型（Coding Plan / k3）** | ✅ 仓库内终验通过，见 `docs/acceptance/2026-08-14-coding-final/` |
| **按量 / Open Platform** | ➖ 专项目录不在本仓库，不宣称通过或 401 |
| **Docker 镜像构建与 Compose 冒烟** | ❌ **SKIPPED**（开发机无 Docker CLI） |

| 门禁 | 结果 |
|---|---|
| `pytest`（后端，P1/P2 合入后本机） | 698 passed，1 failed（字典条数旧上界，相对合入前 `main` 该测试零 diff） |
| `vitest`（前端，同上） | 41 passed |
| 完整真实验收当时 | 后端 647 passed / 12 skipped，前端 29 passed（`2026-08-14-coding-final`） |
| `ruff` / `mypy` | 终验记录：全过 / 85 个源文件无问题 |
| 黄金验收集 | 37 / 37 |
| 隐藏验收集 | 10 个历史案例已入库（`backend/data/hidden_cases/`）；不再是盲测，不得冒充 2026-08-10 原隐藏验收 |

- 功能与操作：[`docs/使用说明.md`](docs/使用说明.md)
- 工程问题与优化：[`docs/工程优化记录.md`](docs/工程优化记录.md)
- 交接与未完成项：[`handoff.md`](handoff.md)
- 离线开发完成验收：[`docs/acceptance/2026-08-10/README.md`](docs/acceptance/2026-08-10/README.md)
- 真实 Coding Plan / Wind / 浏览器：[`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md)
- Compose 冒烟（SKIPPED）：[`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md)

---

## 设计要点

几个刻意做出的取舍，构成了这个项目和「让模型直接写因子代码」之间的区别：

**公式只有一个事实来源。** 模型只产出 `formula_ast`（结构化语法树），后端用确定性渲染器生成 `canonical_formula` 供用户确认。模型的自然语言描述降级为展示用途，不参与任何判断。用户看到什么，机器就算什么，结构上不可能不一致。

**预处理是有序流水线，不是算子。** 去极值、标准化、行业中性化有执行顺序语义——先中性化再标准化和反过来结果不同。它们从公式 AST 中移出，进入带显式 `order` 的流水线，并区分作用对象是变量还是因子。

**时间口径是显式契约。** 「不读还没公布的字段」只解决了未来函数问题的一半。用 T 日收盘价算出的因子在 T 日盘中不可知，不能在 T 日交易。`TimeConvention` 把观测时点、可得时点、信号日、交易日、执行价、前向收益起止全部写进契约，并在校验器里强制「信号不可得就不准交易」。

**Worker 不执行模型生成的代码。** Worker 执行后端确定性构建的签名 manifest；生成的 `factor.py` 只是给用户看的导出产物。模型输出不进入执行路径。

**歧义阻塞而非猜测。** 「盈利质量高的股票」这类表述会阻塞流程并弹出澄清卡片，而不是默默替你选一个口径。

---

## 技术栈

| 层 | 选型 |
|---|---|
| 后端 | Python 3.11、FastAPI、Pydantic 2、SQLAlchemy 2、SQLite |
| 前端 | React 18、TypeScript、Vite、Ant Design、TanStack Query |
| 数据 | Wind MySQL 只读副本；工件落 Parquet |
| 模型 | 统一 `LLMProvider` 抽象，OpenAI 兼容接口 |
| 工具链 | uv、pytest、ruff、mypy、Alembic |

---

## 快速开始

需要 [uv](https://docs.astral.sh/uv/) 与 Node 22+。

```bash
# 安装依赖
uv sync --project backend
npm --prefix frontend install

# 全部离线运行，不需要任何凭据
uv run --project backend pytest backend/tests -q          # 见 README 门禁表，勿写死过期数字
uv run --project backend factor-platform list-cases       # 37 个黄金案例
uv run --project backend factor-platform run-case momentum_20d
uv run --project backend factor-platform run-case-suite --set golden

# 起服务
APP_ENV=test uv run --project backend uvicorn --factory \
  factor_platform.main:create_app --port 8000
npm --prefix frontend run dev                             # http://localhost:5173
```

配置：复制 `.env.example` 为 `.env` 并填入真实值。`.env` 已被 gitignore。
不填凭据仍可运行离线测试与黄金案例；应用不会把 Fake Provider 当成真实模型，
需要模型的请求会稳定返回 `llm_provider_unavailable` 或 `local_only_mode`。

完整的功能说明与操作流程见 [`docs/使用说明.md`](docs/使用说明.md)。

## 关于缺失的 Wind 数据字典

字段检索功能依赖 Wind 字段索引 Markdown。**Wind 数据字典受商业授权保护，不随本仓库分发。**

若你有 Wind 授权，把字段索引放到 `windquery/windquery/references/wind_field_index.md`，格式如下：

```markdown
### AShareEODPrices（2个字段）

S_INFO_WINDCODE, S_DQ_CLOSE
```

然后构建本地目录：

```bash
uv run --project backend factor-platform build-wind-catalog \
  --source windquery/windquery/references/wind_field_index.md \
  --output backend/data/generated/wind_fields.jsonl
```

没有这个文件时，依赖它的测试会自动 skip，其余测试正常运行。

同样地，`imgs/` 下的内部需求文档也不随仓库分发。

---

## 安全约定

- 真实凭据只进入本地 `.env`；仓库只提交全空值的 `.env.example`。
- `get_secret_value()` 在整个代码库中只出现一处（`secrets.py`）；Wind 与 LLM
  适配器只在实际连接/发请求的边界调用统一解包函数，便于审计。
- Worker 不持有 Wind 或 LLM 密钥；生产部署下 Worker 容器保持 `network_mode: none`。
- **边界 B4**：Wind 原始数据、密钥、研报全文默认不得发送至外部模型服务。
- 不使用任意 SQL、`eval`、`exec` 或任意网络访问；Wind 取数走六种受控查询形状。

---

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/使用说明.md`](docs/使用说明.md) | **功能清单与操作流程** —— 从这里开始 |
| [`docs/工程优化记录.md`](docs/工程优化记录.md) | **实现中遇到的问题与优化过程** |
| [`docs/acceptance/2026-08-10/README.md`](docs/acceptance/2026-08-10/README.md) | 离线开发完成验收报告 |
| [`docs/acceptance/2026-08-14-coding-final/README.md`](docs/acceptance/2026-08-14-coding-final/README.md) | 真实 Coding Plan、Wind、研报与浏览器闭环 |
| [`docs/acceptance/compose-smoke.md`](docs/acceptance/compose-smoke.md) | Compose 冒烟 SKIPPED 与契约测试 |
| [`docs/acceptance/metric-review-evidence.md`](docs/acceptance/metric-review-evidence.md) | 口径老师确认（文档层，YAML 未改） |
| [`docs/acceptance/deferred-credential-steps.md`](docs/acceptance/deferred-credential-steps.md) | 凭据相关步骤：已完成 / 仍未完成 |
| [`docs/技术设计方案.md`](docs/技术设计方案.md) | 设计思路与框架 |
| [`docs/技术设计方案问题与调整建议.md`](docs/技术设计方案问题与调整建议.md) | 技术评审意见（13 条，驱动了 6 个修订任务） |
| [`docs/阶段汇报-2026-08-07.md`](docs/阶段汇报-2026-08-07.md) | 中期阶段汇报（历史存档） |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | 实施计划，36 个任务，含逐任务实现修订记录 |
| [`docs/superpowers/specs/`](docs/superpowers/specs/) | 架构设计（含被取代条款的修订记录） |

---

## 许可

尚未选定许可协议。在此之前保留所有权利。
