# Factor Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 1 名开发者、1 个月内交付稳定的 P0 自然语言因子闭环，以及可运行的 P1 研报和 P2 研究扩展原型。

**Architecture:** 使用 FastAPI 模块化单体、显式事件状态机、React 工作台和无密钥文件队列 Worker。LLM 只生成结构化 `FactorSpec`；Wind 取数由受信任后端执行，公式由确定性 DSL 编译并在隔离 Worker 中运行。

**Tech Stack:** Python 3.11、uv、FastAPI、Pydantic 2、SQLAlchemy 2、SQLite、pandas、PyArrow、PyMySQL、BM25、PyMuPDF、RapidOCR、React、TypeScript、Vite、Ant Design、TanStack Query、Monaco、Recharts、Docker Compose。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-08-04-factor-platform-design.md`（含 2026-08-05 修订）与 `docs/技术设计方案.md`。
- Python 必须使用 `uv run`；项目要求 `>=3.11,<3.13`。
- Node 要求 `>=22`，使用 `npm` 和锁文件 `package-lock.json`。
- P0 必须使用真实 Wind 数据，并稳定通过两个自然语言案例、一个阻塞性澄清案例和一个错误修复案例。
- P1/P2 是真实可运行原型，限制为代表性 PDF、日频 A 股、中小规模区间、线性组合、单机部署和管理员/研究员两角色。
- LLM 不得生成并直接执行任意 Python 或 SQL；可执行内容只能来自版本化 `manifest`（含 `ExecutionPlan + FormulaNode + PreprocessingPipeline + TimeConvention`）。
- **公式单一事实来源**：模型只产出 `formula_ast`；`canonical_formula` 由后端从 AST 确定性渲染，是用户确认的正式公式；模型的自然语言解释不参与确认与执行。
- **预处理与公式分离**：`winsorize`/`zscore`/`industry_neutralize` 不得出现在 AST，只能出现在有序 `PreprocessingPipeline` 中，且必须声明 `target`（`variables` 或 `factor`）与 `order`。
- **时间口径显式化**：每个执行计划与结果工件必须携带 `TimeConvention`；无法确定公告发布时点时采用保守规则。
- Worker 不持有数据库或 LLM 密钥；生产 Compose 中 `network_mode: none`；**Worker 执行的是签名后的 manifest，不是生成的 Python 源码**。
- 财务数据没有公告日/市场可得日证据时不得进入因子检验。
- **数据出境边界 B4**：Wind 原始数据、密钥、完整研报正文默认不得发送至外部模型服务；必须支持全本地模式；调用审计不保存完整正文。
- 每项任务先写可观察行为测试，再实现最小功能；每项任务单独提交，禁止 `git add .` 或 `git add -A`。
- 任何真实凭据不得写入源码、Notebook、日志、工件、测试夹具或提交历史。

---

## 修订记录

### 2026-08-05：依据评审文档的第一次修订

来源：`docs/技术设计方案问题与调整建议.md`（13 条意见）。处理原则遵循 handoff 第 53 行 —— **先以已批准设计为准，再把必要修订写回原计划档**。已批准设计文档 `specs/2026-08-04-factor-platform-design.md` 中被本次修订覆盖的条目，已在该文件内以「2026-08-05 修订」标注。

任务编号保持 1–30 不变；对已实现任务（1–9）的修订以小数编号补充任务承载，对未实现任务（10–30）就地改写。

| # | 修订内容 | 性质 | 落到任务 |
|---|---|---|---|
| 1 | 模型只产出 `formula_ast`；后端确定性渲染 `canonical_formula` 供确认；增加变量绑定/结构复杂度/数值合法性三重校验 | 契约变更 | **Task 2.5**（新增）、Task 12 |
| 2 | AST 移除 `winsorize`/`zscore`/`industry_neutralize`（算子 14→11）；预处理改为有序流水线并区分 `variables`/`factor` | 契约变更 | **Task 2.5**（新增）、Task 12 |
| 3 | 新增 `TimeConvention` 契约（观测/可得/信号日/交易日/执行价/前向收益起止）与首期默认口径 | 新增契约 | **Task 2.5**（新增）、Task 11、Task 25 |
| 4 | 角色表述改为「无专职研究人员全程参与，关键节点由带教老师抽样确认」；`disputed` 由警告升级为阻塞 | 口径治理 | **Task 10.5**（新增）、Task 26、Task 27、Task 30 |
| 5 | WDS 正式纳入架构作为字段元数据与业务口径来源；字段发现扩展为七层漏斗 | 架构新增 | **Task 9.5**（新增）、Task 10 |
| 6 | Schema 验证与数据存在性验证分离；采样按数据形态分策略；返回状态二值→六值 | 功能变更 | Task 10 |
| 7 | 新增第四条信任边界 B4（内部数据 → 外部模型），含默认禁止清单、最小发送清单、本地模式、审计要求 | 安全新增 | **Task 4.5**（新增）、Task 22 |
| 8 | 任务队列补充租约、超时恢复、幂等键、取消、工件清理、磁盘告警 | 功能新增 | Task 14 |
| 9 | Worker 改为执行签名后的 manifest；生成代码降级为用户导出产物；删除子进程动态执行与源码 AST 白名单 | 架构简化 | Task 13、Task 14 |
| 10 | 新增修订事件与级联失效规则；归约器改为带业务语义；支持版本回退 | 功能新增 | **Task 3.5**（新增）、Task 16 |
| 11 | 复现记录扩展到输入侧：工件链、输入哈希、查询时点、来源表字段、全量版本号 | 功能新增 | Task 14、Task 26 |
| 12 | 研报能力边界收窄；图片公式与扫描件转人工确认；置信度不足不得执行 | 边界澄清 | Task 21、Task 22、Task 23 |
| 13 | 案例集扩至 20–30 自然语言 + 5–10 歧义 + 6–10 研报；增设隐藏验收集与六组量化指标 | 验收强化 | **Task 6.5**（新增）、Task 30 |

新增任务共 6 项：Task 2.5、Task 3.5、Task 4.5、Task 6.5、Task 9.5、Task 10.5。就地改写任务共 13 项：Task 10、11、12、13、14、15、16、21、22、23、26、27、30。

---

## File Map

```text
backend/
├── pyproject.toml
├── alembic.ini
├── src/factor_platform/
│   ├── main.py
│   ├── settings.py
│   ├── cli.py
│   ├── domain/{models.py,formula.py,preprocessing.py,time_convention.py,errors.py}
│   ├── db/{base.py,models.py,repository.py}
│   ├── orchestration/{states.py,reducer.py,service.py,repair.py}
│   ├── llm/{base.py,openai_compatible.py,router.py,prompts.py,usage.py,data_boundary.py}
│   ├── factor/{parser.py,clarification.py,renderer.py,ast_checks.py,compiler.py,export.py}
│   ├── wind/{connection.py,adapter.py,capabilities.py,catalog.py,field_search.py,
│   │         wds_sync.py,metadata_catalog.py,metadata_repository.py,schema_verify.py,planner.py}
│   ├── execution/{manifest.py,job_store.py,runtime.py,worker.py}
│   ├── validation/{data.py,formula.py,result.py}
│   ├── reports/{pdf.py,ocr.py,extractor.py}
│   ├── analysis/metrics.py
│   ├── library/{service.py,provenance.py}
│   ├── indices/service.py
│   ├── auth/{service.py,dependencies.py}
│   └── api/{sessions.py,events.py,reports.py,analysis.py,library.py,indices.py,auth.py,health.py}
├── data/{golden_cases,hidden_cases,wind_aliases.yaml,metric_definitions.yaml,generated}
└── tests/
docs/acceptance/<date>/   # 验收证据包（Task 30）
frontend/
├── package.json
├── vite.config.ts
└── src/
    ├── api/{client.ts,schema.d.ts}
    ├── app/{router.tsx,queryClient.ts}
    ├── features/{workspace,reports,library,analysis,indices,admin}/
    └── components/
deploy/
├── backend.Dockerfile
├── worker.Dockerfile
├── frontend.Dockerfile
├── nginx.conf
└── compose.yaml
```

Files remain focused: domain contracts contain no I/O; adapters contain no workflow policy; orchestration owns state transitions; API handlers only validate/dispatch; frontend renders backend state.

---

## Week 1 — Trusted Foundation

### Task 1: Remove plaintext credentials and establish backend settings

**Files:**
- Create: `.env.example`
- Create: `backend/pyproject.toml`
- Create: `backend/.python-version`
- Create: `backend/src/factor_platform/__init__.py`
- Create: `backend/src/factor_platform/settings.py`
- Create: `backend/tests/test_settings.py`
- Modify: `.gitignore`
- Modify: `rq_wind_replica.py:13-24`
- Modify: `Wind取数尝试.ipynb:cell 2`
- Modify: `windquery/windquery/SKILL.md:10-24`
- Modify: `windquery/windquery/agents/researcher.md:28-40`

**Interfaces:**
- Produces: `Settings`, `get_settings()`, environment names used by every backend task.
- Security prerequisite: rotate the exposed database credential before enabling real Wind tests.

- [x] **Step 1: Write settings tests**

```python
import pytest
from pydantic import ValidationError

from factor_platform.settings import Settings


def test_wind_disabled_needs_no_credentials() -> None:
    settings = Settings(app_env="test", wind_enabled=False)
    assert settings.wind_enabled is False


def test_wind_enabled_requires_all_connection_fields() -> None:
    with pytest.raises(ValidationError, match="wind_host"):
        Settings(app_env="test", wind_enabled=True)


def test_worker_environment_excludes_secrets() -> None:
    settings = Settings(
        app_env="test",
        wind_enabled=True,
        wind_host="db.internal",
        wind_user="research",
        wind_password="unit-test-only",  # pragma: allowlist secret
        wind_database="wind",
    )
    assert "WIND_PASSWORD" not in settings.worker_environment()
    assert "KIMI_API_KEY" not in settings.worker_environment()
```

- [x] **Step 2: Scaffold the uv package and run the failing tests**

Run:

uv init --package --name factor-platform --python 3.11 --no-readme backend
uv add --project backend fastapi 'uvicorn[standard]' pydantic-settings sqlalchemy aiosqlite alembic httpx sse-starlette pymysql pandas numpy pyarrow rank-bm25 jieba jinja2 typer python-multipart pymupdf rapidocr-onnxruntime pillow pwdlib itsdangerous
uv add --project backend --dev pytest pytest-asyncio pytest-cov respx ruff mypy
uv run --project backend pytest backend/tests/test_settings.py -v
```

Expected: FAIL because `factor_platform.settings` does not exist.

- [x] **Step 3: Implement typed settings and safe examples**

```python
from functools import lru_cache
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/runtime/factor_platform.db"
    artifact_root: str = "./data/artifacts"
    job_root: str = "./data/runtime/jobs"
    wind_enabled: bool = False
    wind_host: str | None = None
    wind_port: int = 3306
    wind_user: str | None = None
    wind_password: SecretStr | None = None
    wind_database: str | None = None
    kimi_coding_base_url: str | None = None
    kimi_coding_api_key: SecretStr | None = None
    kimi_metered_base_url: str = "https://api.moonshot.cn/v1"
    kimi_metered_api_key: SecretStr | None = None
    kimi_model: str | None = None
    session_cookie_secret: SecretStr | None = None

    @model_validator(mode="after")
    def validate_required_settings(self) -> "Settings":
        if self.app_env != "test" and self.session_cookie_secret is None:
            raise ValueError("session_cookie_secret is required outside tests")
        if (self.kimi_coding_api_key or self.kimi_metered_api_key) and not self.kimi_model:
            raise ValueError("kimi_model is required when a Kimi provider is configured")
        if self.wind_enabled:
            required = (self.wind_host, self.wind_user, self.wind_password, self.wind_database)
            if any(value is None for value in required):
                raise ValueError("wind_host, wind_user, wind_password and wind_database are required")
        return self

    def worker_environment(self) -> dict[str, str]:
        return {"PYTHONUNBUFFERED": "1", "APP_ENV": self.app_env}


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`.env.example` must list the exact environment names with empty secret values. Replace every legacy literal connection value with environment access; update Notebook and skill/agent text to refer to `.env.example`. Remove the four temporary legacy-file ignore entries only after the files contain no credentials.

- [x] **Step 4: Verify settings and scan the changed files**

Run:

```bash
uv run --project backend pytest backend/tests/test_settings.py -v
uvx detect-secrets scan rq_wind_replica.py Wind取数尝试.ipynb windquery/windquery/SKILL.md windquery/windquery/agents/researcher.md
```

Expected: 3 tests PASS; no high-confidence password or credential URI finding.

- [ ] **Step 5: Commit**

git add .env.example .gitignore backend/.python-version backend/pyproject.toml backend/uv.lock backend/src/factor_platform/__init__.py backend/src/factor_platform/settings.py backend/tests/test_settings.py rq_wind_replica.py Wind取数尝试.ipynb windquery/windquery/SKILL.md windquery/windquery/agents/researcher.md
git commit -m "chore: secure credentials and scaffold backend"
```

### Task 2: Define domain contracts and formula AST

> ⚠️ 本任务已实现，但其中的 `formula_text`、`PreprocessingRules` 与 14 算子清单已被 **Task 2.5** 修订取代。本节保留为历史记录，实现以 Task 2.5 为准。

**Files:**
- Create: `backend/src/factor_platform/domain/formula.py`
- Create: `backend/src/factor_platform/domain/models.py`
- Create: `backend/src/factor_platform/domain/errors.py`
- Create: `backend/tests/domain/test_models.py`

**Interfaces:**
- Produces: `FormulaNode`, `ResearchRequest`, `FactorSpec`, `DataRequirement`, `ClarificationQuestion`, `FieldCandidate`, `FieldSelection`, `ExecutionStep`, `ExecutionPlan`, `ExecutionResult`, `ValidationReport`, `ReportEvidence`, `FactorArtifact`, `SessionSnapshot`.
- All later modules must import these types rather than defining local dictionaries.

- [x] **Step 1: Write model contract tests**

```python
import pytest
from pydantic import ValidationError

from factor_platform.domain.formula import FormulaNode
from factor_platform.domain.models import FactorSpec


def test_formula_call_requires_registered_operator_shape() -> None:
    node = FormulaNode(type="call", op="rank", args=[FormulaNode(type="variable", name="roe_ttm")])
    assert node.args[0].name == "roe_ttm"


def test_variable_rejects_call_fields() -> None:
    with pytest.raises(ValidationError):
        FormulaNode(type="variable", name="close", op="rank")


def test_factor_spec_keeps_display_and_machine_formula() -> None:
    spec = FactorSpec.model_validate({
        "factor_name": "quality",
        "hypothesis": "higher ROE may predict returns",
        "asset_type": "stock",
        "universe": "000300.SH",
        "frequency": "daily",
        "rebalance_frequency": "monthly",
        "direction": "higher_is_better",
        "formula_text": "rank(ROE_TTM)",
        "formula_ast": {"type": "call", "op": "rank", "args": [{"type": "variable", "name": "roe_ttm"}]},
        "variables": [{"logical_name": "roe_ttm", "meaning": "ROE TTM", "point_in_time_required": True}],
    })
    assert spec.formula_ast.op == "rank"
```

- [x] **Step 2: Run the tests and observe missing contracts**

Run: `uv run --project backend pytest backend/tests/domain/test_models.py -v`  
Expected: FAIL with import errors.

- [x] **Step 3: Implement strict Pydantic models**

`FormulaNode` uses `type: Literal["variable", "literal", "call"]`, validates mutually exclusive `name`, `value`, and `op`, and permits only these operators:

```python
FormulaOperator = Literal[
    "add", "subtract", "multiply", "divide", "negative", "log",
    "rank", "zscore", "winsorize", "rolling_return", "rolling_std",
    "rolling_mean", "fillna", "industry_neutralize",
]
```

Use enums for asset type, frequency, factor direction, time role, query shape, execution status, and error category. Make every externally persisted model carry `schema_version: int = 1`; `FactorSpec` additionally carries `version: int = 1` and `source_evidence: list[ReportEvidence]`.

- [x] **Step 4: Run contract tests**

Run: `uv run --project backend pytest backend/tests/domain/test_models.py -v`  
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/domain/formula.py backend/src/factor_platform/domain/models.py backend/src/factor_platform/domain/errors.py backend/tests/domain/test_models.py
git commit -m "feat: define factor workflow contracts"
```

### Task 2.5: Amend contracts — canonical formula, preprocessing pipeline, time convention

> 修订任务（2026-08-05）。对应修订 #1、#2、#3。Task 2 已实现，本任务在其基础上修正契约。

**Files:**
- Modify: `backend/src/factor_platform/domain/formula.py`
- Modify: `backend/src/factor_platform/domain/models.py`
- Create: `backend/src/factor_platform/domain/preprocessing.py`
- Create: `backend/src/factor_platform/domain/time_convention.py`
- Create: `backend/src/factor_platform/factor/renderer.py`
- Create: `backend/src/factor_platform/factor/ast_checks.py`
- Create: `backend/tests/domain/test_preprocessing.py`
- Create: `backend/tests/domain/test_time_convention.py`
- Create: `backend/tests/factor/test_renderer.py`
- Create: `backend/tests/factor/test_ast_checks.py`
- Modify: `backend/tests/domain/test_models.py`
- Modify: `backend/data/golden_cases/*.json`

**Interfaces:**
- Produces: `render_canonical_formula(node) -> str`, `PreprocessingPipeline`, `PreprocessingStep`, `TimeConvention`, `check_ast(spec) -> ValidationReport`.
- `FactorSpec` 字段调整：删除 `formula_text`；新增 `canonical_formula`（后端渲染）与 `formula_explanation`（模型解释，仅展示）；`preprocessing` 由 `PreprocessingRules` 改为 `PreprocessingPipeline`；新增 `time_convention`。
- `FormulaOperator` 由 14 个减为 11 个。

- [x] **Step 1: Write renderer determinism, pipeline ordering and AST check tests**

```python
def test_canonical_formula_is_deterministic_and_matches_ast() -> None:
    ast = FormulaNode(type="call", op="rank", args=[
        FormulaNode(type="call", op="rolling_return",
                    args=[FormulaNode(type="variable", name="close")], params={"window": 20})])
    assert render_canonical_formula(ast) == render_canonical_formula(ast)
    assert render_canonical_formula(ast) == "rank(rolling_return(close, window=20))"


def test_ast_rejects_operators_moved_to_preprocessing() -> None:
    with pytest.raises(ValidationError):
        FormulaNode(type="call", op="zscore", args=[FormulaNode(type="variable", name="x")])


def test_ast_rejects_unbound_variable() -> None:
    report = check_ast(spec_with_ast_referencing("undeclared_var"))
    assert report.has_error("unbound_variable")


def test_ast_rejects_excessive_window() -> None:
    report = check_ast(spec_with_rolling_window(100_000))
    assert report.has_error("window_out_of_range")


def test_pipeline_rejects_duplicate_operation_on_same_target() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        PreprocessingPipeline(steps=[
            PreprocessingStep(order=1, operation="zscore", target="factor"),
            PreprocessingStep(order=2, operation="zscore", target="factor"),
        ])


def test_time_convention_defaults_to_next_day_trading() -> None:
    convention = TimeConvention()
    assert convention.signal_date == "T"
    assert convention.trade_date == "T+1"
    assert convention.execution_price == "NEXT_OPEN"
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/domain backend/tests/factor -v`
Expected: FAIL because renderer, pipeline, time convention and AST checks are absent.

- [x] **Step 3: Implement the amended contracts**

`FormulaOperator` 保留：`add`、`subtract`、`multiply`、`divide`、`negative`、`log`、`rank`、`rolling_return`、`rolling_std`、`rolling_mean`、`fillna`。删除 `winsorize`、`zscore`、`industry_neutralize`。

`render_canonical_formula` 采用固定优先级与括号规则、参数按名称排序、数值固定格式，保证同一棵 AST 永远渲染为同一字符串。

`PreprocessingStep` 字段：`order`、`operation`（`winsorize`/`zscore`/`industry_neutralize`/`fillna`）、`target`（`variables`/`factor`）、`method`、`parameters`。`PreprocessingPipeline` 校验 `order` 连续且唯一、同一 `target` 不重复同一 `operation`、`version` 单调。

`TimeConvention` 字段：`observation_time`、`information_available_time`、`signal_date`、`trade_date`、`execution_price`、`forward_return_start`、`forward_return_end`、`announcement_timing_policy`（`conservative` 默认）。默认口径为 T 收盘观测、T 收盘后可得、信号日 T、交易日 T+1、次日开盘执行。

`check_ast` 三组规则：变量绑定（引用未定义变量→`unbound_variable` 阻塞；变量名重复→`duplicate_variable` 阻塞；定义未使用→`unused_variable` 警告）；结构复杂度（节点数、深度、参数数、窗口上限）；数值合法性（权重与字面量必须有限；方向与公式一致性）。

同步更新 10 个 golden case JSON：`formula_text` 改为 `canonical_formula`，预处理改为流水线表示，补 `time_convention`。

- [x] **Step 4: Run all contract and case tests**

Run: `uv run --project backend pytest backend/tests -v`
Expected: 契约、渲染、流水线、时间口径、AST 校验与 10 个案例全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/domain/formula.py backend/src/factor_platform/domain/models.py backend/src/factor_platform/domain/preprocessing.py backend/src/factor_platform/domain/time_convention.py backend/src/factor_platform/factor/renderer.py backend/src/factor_platform/factor/ast_checks.py backend/tests/domain backend/tests/factor backend/data/golden_cases
git commit -m "refactor: make formula AST the single source of truth"
```

### Task 3: Implement event-sourced sessions and legal state transitions

**Files:**
- Create: `backend/src/factor_platform/db/base.py`
- Create: `backend/src/factor_platform/db/models.py`
- Create: `backend/src/factor_platform/db/repository.py`
- Create: `backend/src/factor_platform/orchestration/states.py`
- Create: `backend/tests/orchestration/test_states.py`
- Create: `backend/tests/db/test_repository.py`
- Create: `backend/alembic.ini`
- Create: `backend/alembic/env.py`
- Create: `backend/alembic/versions/0001_sessions.py`

**Interfaces:**
- Produces: `SessionState`, `EventType`, `apply_event(state, event)`, `SessionRepository.create()`, `append_event()`, `get_snapshot()`.
- A snapshot contains `session_id`, current state, aggregate version, request, confirmed spec, selected fields, plan and last error.

- [x] **Step 1: Test legal transitions and stale writes**

```python
import pytest

from factor_platform.orchestration.states import EventType, SessionState, apply_event


def test_formula_cannot_be_confirmed_before_proposal() -> None:
    with pytest.raises(ValueError, match="illegal transition"):
        apply_event(SessionState.CREATED, EventType.FORMULA_CONFIRMED)


def test_parsing_can_request_clarification() -> None:
    state = apply_event(SessionState.PARSING_INPUT, EventType.CLARIFICATION_REQUESTED)
    assert state is SessionState.NEEDS_CLARIFICATION
```

Repository test: append with expected version 1 succeeds; appending again with expected version 1 raises `ConcurrentUpdateError`.

- [x] **Step 2: Run failing state and repository tests**

Run: `uv run --project backend pytest backend/tests/orchestration/test_states.py backend/tests/db/test_repository.py -v`  
Expected: FAIL because persistence and state modules are absent.

- [x] **Step 3: Implement state transition table and append-only records**

Use two tables:

```python
class SessionRecord(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime]


class SessionEventRecord(Base):
    __tablename__ = "session_events"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str]
    payload_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime]
```

`append_event` starts a transaction, checks `max(sequence) == expected_version`, validates `apply_event`, and inserts sequence `expected_version + 1`. `get_snapshot` folds all events in sequence order.

- [x] **Step 4: Run migrations and tests**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend pytest backend/tests/orchestration/test_states.py backend/tests/db/test_repository.py -v
```

Expected: migration succeeds; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/alembic.ini backend/alembic backend/src/factor_platform/db backend/src/factor_platform/orchestration/states.py backend/tests/orchestration/test_states.py backend/tests/db/test_repository.py
git commit -m "feat: add versioned session state machine"
```

### Task 3.5: Add revision events, cascade invalidation and a semantic reducer

> 修订任务（2026-08-05）。对应修订 #10。Task 3 已实现，本任务补齐修改与回退能力。

**Files:**
- Modify: `backend/src/factor_platform/orchestration/states.py`
- Create: `backend/src/factor_platform/orchestration/reducer.py`
- Modify: `backend/src/factor_platform/db/repository.py`
- Create: `backend/tests/orchestration/test_reducer.py`
- Modify: `backend/tests/orchestration/test_states.py`

**Interfaces:**
- Produces: 新事件 `FORMULA_REVISED`、`FIELDS_REVISED`、`REQUEST_REVISED`、`PREPROCESSING_REVISED`、`TIME_CONVENTION_REVISED`、`UNIVERSE_REVISED`、`DATE_RANGE_REVISED`、`EXECUTION_CANCELLED`、`SESSION_CLONED`、`RERUN_REQUESTED`。
- Produces: `fold_events(events) -> SessionSnapshot`，取代仓储中的"最后一个非空值获胜"折叠。

- [x] **Step 1: Test cascade invalidation and cancellation**

```python
def test_formula_revision_clears_all_downstream_artifacts() -> None:
    snapshot = fold_events([
        request_event(), formula_confirmed_event(), fields_confirmed_event(),
        code_generated_event(), execution_succeeded_event(),
        formula_revised_event(),
    ])
    assert snapshot.field_selections == []
    assert snapshot.plan is None
    assert snapshot.manifest_sha256 is None
    assert snapshot.execution_result is None


def test_date_range_revision_keeps_formula_but_clears_plan() -> None:
    snapshot = fold_events([..., date_range_revised_event()])
    assert snapshot.factor_spec is not None
    assert snapshot.plan is None


def test_cancelled_execution_returns_to_code_ready() -> None:
    state = apply_event(SessionState.EXECUTING, EventType.EXECUTION_CANCELLED)
    assert state is SessionState.CODE_READY
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/orchestration -v`
Expected: FAIL because revision events and reducer are absent.

- [x] **Step 3: Implement the semantic reducer and invalidation table**

在转移表中登记全部修订事件的合法来源状态。归约器为每个事件显式声明三件事：写入哪些键、使哪些键失效、把哪些旧工件转为历史版本。失效级联按下表执行：

| 修订事件 | 失效的下游键 |
|---|---|
| `FORMULA_REVISED` | `field_selections`、`plan`、`manifest_sha256`、`input_artifacts`、`execution_result`、`validation_reports` |
| `FIELDS_REVISED` | `plan`、`manifest_sha256`、`input_artifacts`、`execution_result`、`validation_reports` |
| `DATE_RANGE_REVISED` | `plan`、`input_artifacts`、`execution_result`、`validation_reports` |
| `UNIVERSE_REVISED` | `field_samples`、`plan`、`input_artifacts`、`execution_result` |
| `PREPROCESSING_REVISED` | `manifest_sha256`、`execution_result`、`validation_reports` |
| `TIME_CONVENTION_REVISED` | `plan`、`forward_return_definition`、`execution_result`、`analysis_result` |

`SESSION_CLONED` 以当前快照为基础创建新会话并记录来源；`RERUN_REQUESTED` 保留定义与字段、清空执行结果。历史版本可回退；回退后必须重新生成下游工件，不得复用已失效结果。

> **实现修订（2026-08-07）。** 落地时对本任务原文作四处调整，均已实现并测试：
>
> 1. **取消 `session_versions` 表。** 历史版本改为前缀折叠 `get_snapshot_at(session_id, version)`。存一份折叠后的快照等于给事件日志开第二个事实来源，两者会漂移——这正是设计文档「快照必须始终可从事件推导」要防的事。真正的因子版本持久化属于 Task 26，与会话快照无关。
> 2. **`TIME_CONVENTION_REVISED` 追加失效 `manifest_sha256`。** 原表只失效 `plan` 与 `forward_return_definition`。但 `TimeConvention` 是 `FactorSpec` 的一部分，manifest 由 spec 构建，旧口径下构建的 manifest 已经陈旧。边界不清时按「多失效」处理：多失效只是多一次重建，少失效会把陈旧工件当成当前结果发出去。
> 3. **`WRITES` 提升为强制白名单。** 原文要求归约器声明「写入哪些键」；实现中该声明具备强制力——事件只能写入自己声明过的键，payload 里的其他键一律忽略。payload 由调用方构造并经 JSON 往返，能写什么应由归约器决定，而非调用方。
> 4. **尚不存在的键暂缓入表。** `input_artifacts`、`validation_reports`、`field_samples`、`forward_return_definition`、`analysis_result` 目前不在 `SessionSnapshot` 上。补一条守卫测试：`SessionSnapshot` 的每个字段都必须在归约器中分类登记，否则测试失败。后续任务新增下游工件字段时会被直接挡下，不依赖「记得回来改级联表」。

- [x] **Step 4: Run reducer and repository tests**

Run: `uv run --project backend pytest backend/tests/orchestration backend/tests/db -v`
Expected: 级联失效、取消、克隆、回退测试全部 PASS。

实跑（2026-08-07）：`39 passed`；全量 `177 passed`（原 147）；`ruff` 全过；`mypy` 34 源文件无问题。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/orchestration/states.py backend/src/factor_platform/orchestration/reducer.py backend/src/factor_platform/db/repository.py backend/tests/orchestration backend/tests/db
git commit -m "feat: add revision events and cascade invalidation"
```

### Task 4: Add Kimi-compatible structured LLM providers and routing

**Files:**
- Create: `backend/src/factor_platform/llm/base.py`
- Create: `backend/src/factor_platform/llm/openai_compatible.py`
- Create: `backend/src/factor_platform/llm/router.py`
- Create: `backend/src/factor_platform/llm/prompts.py`
- Create: `backend/src/factor_platform/llm/usage.py`
- Create: `backend/tests/llm/test_provider.py`
- Create: `backend/tests/llm/test_router.py`
- Create: `backend/tests/llm/test_usage.py`

**Interfaces:**
- Produces: `LLMProvider.structured_chat(messages, response_model)`, `stream_chat(messages)`, `health_check()`, `ProviderRouter.active_provider()` and `LLMUsageSink.record()`.
- Coding Plan provider is preferred only after a successful health check; metered provider is the fallback.

- [x] **Step 1: Write provider parsing and fallback tests**

```python
from pydantic import BaseModel


class Answer(BaseModel):
    value: int


async def test_structured_chat_validates_json(mock_provider) -> None:
    mock_provider.enqueue_content('{"value": 7}')
    answer = await mock_provider.structured_chat([], Answer)
    assert answer == Answer(value=7)


async def test_router_falls_back_when_coding_plan_is_unhealthy(coding, metered, router) -> None:
    coding.health = False
    metered.health = True
    assert await router.active_provider() is metered

async def test_provider_records_token_usage(mock_provider, usage_sink) -> None:
    mock_provider.enqueue_content(
        '{"value": 7}',
        usage={"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15},
    )
    await mock_provider.structured_chat([], Answer)
    assert usage_sink.records[0].total_tokens == 15
```

- [x] **Step 2: Run failing provider tests**

Run: `uv run --project backend pytest backend/tests/llm -v`  
Expected: FAIL with missing provider modules.

- [x] **Step 3: Implement the protocol, HTTP adapter and router**

```python
T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    name: str
    async def structured_chat(self, messages: list[ChatMessage], response_model: type[T]) -> T:
        raise NotImplementedError

    async def stream_chat(self, messages: list[ChatMessage]) -> AsyncIterator[str]:
        raise NotImplementedError

    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError
```

The concrete adapter posts to `/chat/completions`, strips one optional fenced JSON wrapper, validates with `response_model.model_validate_json`, and raises `LLMResponseError` with provider name and request ID. `LLMUsageSink` records provider, model, request ID, success/failure, prompt/completion/total tokens, latency and optional configured unit cost into session events; admin aggregates call count, token cost and failure rate. Health check uses `/models` with a short timeout. Router caches health for 60 seconds and never falls back after a business request has already produced partial output.

- [x] **Step 4: Run provider tests**

Run: `uv run --project backend pytest backend/tests/llm -v`  
Expected: JSON validation, error mapping and fallback tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/llm backend/tests/llm
git commit -m "feat: add structured Kimi provider routing"
```

### Task 4.5: Enforce the outbound data boundary for external model calls

> 修订任务（2026-08-05）。对应修订 #7，建立第四条信任边界 B4。

**Files:**
- Create: `backend/src/factor_platform/llm/data_boundary.py`
- Modify: `backend/src/factor_platform/llm/base.py`
- Modify: `backend/src/factor_platform/llm/usage.py`
- Modify: `backend/src/factor_platform/settings.py`
- Modify: `.env.example`
- Create: `backend/tests/llm/test_data_boundary.py`

**Interfaces:**
- Produces: `OutboundFilter.check(payload) -> None | OutboundViolation`、`LOCAL_ONLY_MODE` 开关、脱敏后的调用审计记录。
- 所有 Provider 调用必须先过 `OutboundFilter`；未过滤的直接调用在测试中被禁止。

- [x] **Step 1: Test the outbound filter and local-only mode**

```python
def test_wind_raw_values_are_blocked_by_default() -> None:
    violation = OutboundFilter().check(payload_containing_price_rows())
    assert violation.reason == "wind_raw_data"


def test_connection_string_is_blocked() -> None:
    assert OutboundFilter().check(payload_with_connection_string()) is not None


def test_field_metadata_and_formula_structure_are_allowed() -> None:
    assert OutboundFilter().check(payload_with_field_metadata_only()) is None


async def test_local_only_mode_refuses_every_provider_call(settings, router) -> None:
    settings.local_only_mode = True
    with pytest.raises(LocalOnlyModeError):
        await router.active_provider()


def test_audit_record_excludes_full_body() -> None:
    record = usage_sink.records[-1]
    assert record.text_length > 0
    assert not hasattr(record, "prompt_body")
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/llm/test_data_boundary.py -v`
Expected: FAIL because the filter is absent.

- [x] **Step 3: Implement the filter, local mode and audit**

默认禁止外发：Wind 原始数据行、数据库连接信息、任何密钥形态字符串、内部代码、未授权内部文档、完整结果数据、完整研报正文。

允许外发的最小内容：字段元数据、公式结构、用户明确输入的研究想法、必要字段说明、经裁剪与脱敏的研报片段、错误摘要、不含敏感值的结构化上下文。

`LOCAL_ONLY_MODE=true` 时：PDF 只做本地文本提取；字段检索只用本地目录；公式由用户手动确认；任何 Provider 调用抛 `LocalOnlyModeError`。

审计记录字段：调用时间、Provider、模型、输入类型、是否脱敏、文本长度、Token 数、成本、结果、失败原因。**不保存完整正文。**

`.env.example` 新增 `LOCAL_ONLY_MODE`、`OUTBOUND_ALLOW_REPORT_EXCERPT`、`OUTBOUND_MAX_EXCERPT_CHARS`。

> **实现补充（2026-08-07）。** 原文只规定「所有 Provider 调用必须先过 `OutboundFilter`」，未指定强制机制。落地方式如下：
>
> 1. **区分「字段名」与「字段值」是过滤器的核心判据。** Wind 字段**名**是 schema 元数据，可以外发（否则字段检索功能无法工作）；字段**值**是数据，不可外发。判据：一个映射同时含 Wind 形态的键与数值/日期形态的值即判为数据行。`{"field": "s_dq_close"}` 放行，`{"s_dq_close": 1688.0}` 拦截。另有正则覆盖「表已渲染成文本再塞进 prompt」的情形。
> 2. **新增 `GuardedProvider` 包装器**，过滤在委托之前执行 —— 被拦截的调用根本不算调用，Provider 从未拿到 payload。
> 3. **新增架构约束测试**：`llm` 包之外的任何模块，若调用 `structured_chat`/`stream_chat` 却未构造过 `GuardedProvider`，测试直接失败。据此 `FactorParser` 改为在构造时包装 Provider —— 无法再构造出一个绕过 B4 的解析器。
> 4. **`LocalOnlyModeError` 在 Router 与 GuardedProvider 两处强制。** Router 是取得 Provider 的唯一入口，在此拦截即无处可绕。
> 5. **`UsageRecord` 新增审计字段** `input_kind` / `redacted` / `text_length` / `blocked` / `failure_reason`，并且**没有**任何正文字段 —— 审计日志若留存正文，本身就成了越过 B4 的第二份副本。被拦截的调用同样入审计，附类别但不附内容。

- [x] **Step 4: Run boundary tests and the full llm suite**

Run: `uv run --project backend pytest backend/tests/llm -v`
Expected: 拦截、放行、本地模式与审计脱敏测试全部 PASS。

实跑（2026-08-07）：`backend/tests/llm` 全过；全量 `206 passed`（原 177）；`ruff` 全过；`mypy` 35 源文件无问题；10 个黄金案例经包装后的解析器全部通过，过滤器未误伤真实 prompt。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/llm/data_boundary.py backend/src/factor_platform/llm/base.py backend/src/factor_platform/llm/usage.py backend/src/factor_platform/settings.py .env.example backend/tests/llm/test_data_boundary.py
git commit -m "feat: enforce outbound data boundary for model calls"
```

### Task 5: Parse research ideas and generate deterministic clarification cards

**Files:**
- Create: `backend/src/factor_platform/factor/parser.py`
- Create: `backend/src/factor_platform/factor/clarification.py`
- Create: `backend/tests/factor/test_parser.py`
- Create: `backend/tests/factor/test_clarification.py`

**Interfaces:**
- Consumes: `ResearchRequest`, `FactorSpec`, `LLMProvider`.
- Produces: `FactorParser.parse(request) -> FactorSpec`, `ClarificationEngine.questions(spec) -> list[ClarificationQuestion]`.

- [x] **Step 1: Write ambiguity behavior tests**

```python
def test_profitability_quality_is_blocking() -> None:
    questions = ClarificationEngine().questions(make_spec(variable="盈利质量"))
    assert questions[0].blocking is True
    assert questions[0].options == ["ROE_TTM", "ROA_TTM", "CFO_TO_PROFIT"]


def test_explicit_roe_ttm_needs_no_profitability_question() -> None:
    questions = ClarificationEngine().questions(make_spec(variable="ROE_TTM", period="TTM"))
    assert all(question.question_id != "profitability_definition" for question in questions)
```

Parser test must prove invalid provider output becomes `LLMResponseError`, not a partial `FactorSpec`.

- [x] **Step 2: Run failing parser tests**

Run: `uv run --project backend pytest backend/tests/factor/test_parser.py backend/tests/factor/test_clarification.py -v`  
Expected: FAIL because parser and rule engine are absent.

- [x] **Step 3: Implement prompt and rule layers**

The parser asks for all `FactorSpec` fields and passes the model schema. The rule engine independently checks ambiguous profitability, valuation, recency window, financial period, growth definition, subfactor weights, direction, universe, rebalance frequency and material neutralization choices. Non-blocking defaults are explicit objects for pre-adjusted prices, ST/suspension exclusion, winsorization, standardization and missing values.

- [x] **Step 4: Run parser tests**

Run: `uv run --project backend pytest backend/tests/factor/test_parser.py backend/tests/factor/test_clarification.py -v`  
Expected: tests PASS and blocking questions are stable without an LLM call.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/factor/parser.py backend/src/factor_platform/factor/clarification.py backend/tests/factor/test_parser.py backend/tests/factor/test_clarification.py
git commit -m "feat: parse factor ideas and detect ambiguities"
```

### Task 6: Establish ten golden factor cases and a parsing CLI

**Files:**
- Create: `backend/data/golden_cases/*.json`
- Create: `backend/src/factor_platform/cli.py`
- Create: `backend/tests/golden/test_cases.py`

**Interfaces:**
- Produces: `load_golden_cases()`, CLI command `factor-platform parse-case CASE_ID`.
- Each case contains request, expected blocking question IDs, confirmed formula AST, expected fields, expected tool and acceptance assertions.

- [x] **Step 1: Define the exact case matrix**

| Case ID | Formula after confirmation | Required field/tool behavior |
|---|---|---|
| `momentum_20d` | `rank(rolling_return(close,20))` | `close` → `get_price` |
| `low_volatility_20d` | `negative(rank(rolling_std(close,20)))` | `close` → `get_price` |
| `price_volume` | equal-weight return and turnover ranks | `close,total_turnover` → `get_price` |
| `profitability_ambiguous` | `rank(roe_ttm)` | asks `profitability_definition` |
| `valuation_ambiguous` | `negative(rank(pe_ttm))` | asks `valuation_definition` |
| `quality_value` | equal-weight `roe_ttm` and negative `pe_ttm` | generic fields, point-in-time ROE |
| `growth_ambiguous` | `rank(revenue_yoy)` | asks `growth_definition` |
| `historical_hs300` | momentum on historical members | calls `index_components` before prices |
| `point_in_time_financial` | `rank(net_profit_yoy)` | requires report period and announcement date |
| `complex_vague` | confirmed growth-minus-risk composite | asks window, direction, weights and rebalance |

- [x] **Step 2: Write the loader test and run it red**

```python
def test_all_golden_cases_have_complete_expected_contracts() -> None:
    cases = load_golden_cases()
    assert len(cases) == 10
    assert {case.case_id for case in cases} == EXPECTED_CASE_IDS
    assert all(case.expected_formula_ast and case.expected_tool_names for case in cases)
```

Run: `uv run --project backend pytest backend/tests/golden/test_cases.py -v`  
Expected: FAIL because files and loader are absent.

- [x] **Step 3: Add all ten JSON fixtures and the CLI**

The CLI loads one case, calls the parser, runs clarification rules, prints validated JSON, and exits nonzero when actual blocking question IDs differ from the fixture.

- [x] **Step 4: Run golden tests and one CLI smoke test**

Run:

```bash
uv run --project backend pytest backend/tests/golden/test_cases.py -v
uv run --project backend factor-platform parse-case profitability_ambiguous
```

Expected: 10 cases validate; output includes `profitability_definition`.

- [ ] **Step 5: Commit**

```bash
git add backend/data/golden_cases backend/src/factor_platform/cli.py backend/tests/golden/test_cases.py
git commit -m "test: add factor workflow golden cases"
```

### Task 6.5: Expand the case set and add a hidden acceptance set

> 修订任务（2026-08-05）。对应修订 #13。10 个案例不足以证明一般化能力。

**Files:**
- Create: `backend/data/golden_cases/*.json`（扩充至 25–40 个）
- Create: `backend/data/hidden_cases/*.json`
- Create: `backend/src/factor_platform/factor/metrics_report.py`
- Create: `backend/tests/golden/test_case_coverage.py`
- Modify: `backend/src/factor_platform/cli.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: CLI `factor-platform run-case-suite --set golden|hidden --report <path>`，输出结构化指标报告。
- 隐藏集在开发期间不参与调试；其标准答案单独存放并在 `.gitignore` 中排除，只在最终验收时启用。

- [x] **Step 1: Test suite composition and metric computation**

```python
def test_golden_set_covers_required_categories() -> None:
    cases = load_golden_cases()
    assert len(cases) >= 25
    categories = {case.category for case in cases}
    assert {"price_volume", "valuation", "profitability", "growth",
            "quality", "composite", "historical_membership",
            "point_in_time_financial"} <= categories
    assert sum(1 for c in cases if c.category == "ambiguous") >= 5
    assert sum(1 for c in cases if c.language == "en") >= 2


def test_metrics_report_separates_error_kinds() -> None:
    report = run_case_suite(load_golden_cases())
    assert set(report.failure_breakdown) == {"model", "field", "data", "execution"}
    assert 0.0 <= report.blocking_recall <= 1.0
    assert 0.0 <= report.unnecessary_question_rate <= 1.0
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/golden -v`
Expected: FAIL because the expanded set and metrics report are absent.

- [x] **Step 3: Build the case matrix and metric report**

案例集构成：

| 类别 | 数量 | 覆盖 |
|---|---|---|
| 自然语言因子 | 20–30 | 价量、估值、盈利、增长、质量、复合因子、历史成分、点时财务；不同中文表达；≥2 个英文 |
| 歧义案例 | 5–10 | 盈利质量、估值、增长口径、时间窗口、方向、权重、调仓频率、报告期、股票池 |
| 研报案例 | 6–10 | 中文 3–5、英文 3–5；文本型公式、公式缺失、变量定义分散、复杂排版需人工确认 |

指标报告分六组：结构化解析（结构正确率、变量绑定正确率、AST 合法率、canonical 一致率、方向准确率）；澄清能力（阻塞召回率、阻塞准确率、不必要追问率、默认值正确率、修订成功率）；字段与函数（Top-1/Top-3 准确率、Schema 通过率、规划准确率、专用函数优先匹配率、通用计划正确率）；执行与安全（执行成功率、点时泄漏拦截率、未来函数拦截率、错误分类准确率、任务恢复成功率、幂等正确率）；研报解析（公式/变量/页码准确率、中英文成功率、需人工确认识别准确率）；效率与成本（响应时间、调用次数、Token、成本、检索与执行耗时）。

报告必须同时列出失败案例，并按模型错误、字段错误、数据错误、执行错误分类。

> **实现修订（2026-08-10）。** 三处偏离：
>
> 1. **六组指标目前只有三组可测。** 字段规划（缺 Task 11 规划器）、执行与安全（缺 Task 13–15）、研报解析（缺 Task 21–23）尚无实现支撑。这三组进 `not_measured` 并各自附上原因，**不输出任何分数**——填 0.0 会被读成「已测且失败」，填 1.0 会被读成「已验证」，而这份报告正是最终验收的比对基准，两种写法都是对覆盖面的谎报。
> 2. **歧义案例不参与 AST 比对。** 其 `expected_formula_ast` 是**澄清之后**的公式（`rank(growth)` → `rank(revenue_yoy)`），而本套件没有澄清消解步骤（那是 Task 16 编排器）。拿澄清前的解析结果去比澄清后的答案，会让每个歧义案例都因行为正确而报失败。`ast_match_rate` 的分母因此只含具体案例，并在报告中显式给出 `ast_scored_cases`。
> 3. **隐藏集由开发者本人编写**（用户 2026-08-10 决定），靠「实现阶段不查看」的纪律隔离，并已加入 `.gitignore` 防止进入仓库历史。**该隔离是软的**：同一方既写实现又出题，只能测出无意的过拟合，测不出有意的。最终验收报告须原样记录这一限制。
>
> 实跑（2026-08-10）：golden 37 个案例全过；阻塞召回 100%、阻塞精确率 100%、不必要追问率 0%；基线写入 `docs/acceptance/baseline-golden.json`。隐藏集 10 个案例已建立，**本阶段未运行**。

- [x] **Step 4: Run the golden suite and record the baseline**

Run:

```bash
uv run --project backend pytest backend/tests/golden -v
uv run --project backend factor-platform run-case-suite --set golden --report docs/acceptance/baseline-golden.json
```

Expected: 全部案例可加载；基线指标报告生成且六组指标齐全。**隐藏集此时不运行。**

- [ ] **Step 5: Commit**

```bash
git add backend/data/golden_cases backend/data/hidden_cases backend/src/factor_platform/factor/metrics_report.py backend/src/factor_platform/cli.py backend/tests/golden .gitignore
git commit -m "test: expand factor case set and add metrics report"
```

---

## Week 2 — P0 Computation Loop

### Task 7: Migrate and inject the existing Wind adapter

**Files:**
- Move: `rq_wind_replica.py` → `backend/src/factor_platform/wind/adapter.py`
- Create: `backend/src/factor_platform/wind/__init__.py`
- Create: `backend/src/factor_platform/wind/connection.py`
- Create: `backend/tests/wind/test_connection.py`
- Create: `backend/tests/wind/test_adapter_contract.py`
- Modify: imports in `Wind取数尝试.ipynb`

**Interfaces:**
- Produces: `WindConnectionFactory`, unchanged public adapter functions, unchanged `RQ_WIND_CAPABILITIES` semantics.
- No module import may open a database connection or require credentials.

- [x] **Step 1: Test lazy connection and adapter compatibility**

```python
def test_import_does_not_connect(monkeypatch) -> None:
    monkeypatch.setattr("pymysql.connect", lambda **kwargs: (_ for _ in ()).throw(AssertionError("connected")))
    import factor_platform.wind.adapter as wind
    assert "get_price" in wind.RQ_WIND_CAPABILITIES


def test_connection_factory_uses_secret_only_at_connect_time(settings, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr("pymysql.connect", lambda **kwargs: captured.update(kwargs) or object())
    WindConnectionFactory(settings).connect()
    assert captured["host"] == "db.internal"
```

- [x] **Step 2: Move the module and run tests red**

Run:

```bash
mkdir -p backend/src/factor_platform/wind
touch backend/src/factor_platform/wind/__init__.py
git mv rq_wind_replica.py backend/src/factor_platform/wind/adapter.py
uv run --project backend pytest backend/tests/wind/test_connection.py backend/tests/wind/test_adapter_contract.py -v
```

Expected: FAIL until global literal config is replaced.

- [x] **Step 3: Inject a connection factory without rewriting data functions**

`connection.py` owns config extraction, retries and connection creation. `adapter.query_df` calls the injected singleton factory lazily. Preserve function signatures, capability registry, parameterized values and generic-query identifier checks.

- [x] **Step 4: Run adapter tests and a real connection smoke test**

Run:

```bash
uv run --project backend pytest backend/tests/wind/test_connection.py backend/tests/wind/test_adapter_contract.py -v
WIND_ENABLED=true uv run --project backend python -c "from factor_platform.wind import adapter as w; w.init(); print('wind-ok')"
```

Expected: unit tests PASS; with rotated local `.env`, smoke output is `wind-ok`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/__init__.py backend/src/factor_platform/wind/adapter.py backend/src/factor_platform/wind/connection.py backend/tests/wind/test_connection.py backend/tests/wind/test_adapter_contract.py Wind取数尝试.ipynb
git commit -m "refactor: inject Wind database connection"
```

### Task 8: Convert the capability registry into planner tool contracts

**Files:**
- Create: `backend/src/factor_platform/wind/capabilities.py`
- Create: `backend/tests/wind/test_capabilities.py`

**Interfaces:**
- Produces: `CapabilityCatalog.from_registry()`, `find_exact(intent)`, `get_tool(name)`, `to_llm_tools()`.

- [x] **Step 1: Write registry normalization tests**

```python
def test_registry_exports_only_callable_data_tools() -> None:
    catalog = CapabilityCatalog.from_registry(RQ_WIND_CAPABILITIES)
    tools = catalog.to_llm_tools()
    assert "get_price" in {tool.name for tool in tools}
    assert "Factor" not in {tool.name for tool in tools}


def test_close_maps_to_get_price() -> None:
    match = catalog.find_exact("后复权收盘价")
    assert match.tool_name == "get_price"
    assert match.arguments["fields"] == ["close"]
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_capabilities.py -v`  
Expected: FAIL with missing catalog.

- [x] **Step 3: Implement immutable tool specs**

Normalize purpose, asset types, parameters, exact outputs, semantic outputs, constraints, planner and examples. Exclude lifecycle/expression entries from direct data tools while preserving them for code generation dependencies.

- [x] **Step 4: Run tests**

Run: `uv run --project backend pytest backend/tests/wind/test_capabilities.py -v`  
Expected: capability normalization and exact match tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/capabilities.py backend/tests/wind/test_capabilities.py
git commit -m "feat: expose Wind capabilities to planner"
```

### Task 9: Build a normalized field catalog, aliases and BM25 search

**Files:**
- Create: `backend/src/factor_platform/wind/catalog.py`
- Create: `backend/src/factor_platform/wind/field_search.py`
- Create: `backend/data/wind_aliases.yaml`
- Create: `backend/data/generated/wind_fields.jsonl`
- Create: `backend/tests/wind/test_catalog.py`
- Create: `backend/tests/wind/test_field_search.py`

**Interfaces:**
- Produces: `CatalogBuilder.build()`, `FieldCatalog.load()`, `FieldSearch.search(requirement, limit=10)`.
- Search result is a `FieldCandidate` with source tier and lexical score.

- [x] **Step 1: Write parser and ranking tests**

```python
def test_catalog_parses_table_and_fields(tmp_path) -> None:
    source = tmp_path / "index.md"
    source.write_text("### AShareEODPrices（2个字段）\n\nS_INFO_WINDCODE, S_DQ_CLOSE\n", encoding="utf-8")
    records = CatalogBuilder(source).build()
    assert [(r.table, r.field) for r in records] == [
        ("ashareeodprices", "s_info_windcode"),
        ("ashareeodprices", "s_dq_close"),
    ]


def test_alias_beats_bm25_for_exact_business_term(search) -> None:
    candidates = search.search(make_requirement("后复权收盘价"), limit=3)
    assert candidates[0].field == "s_dq_adjclose"
    assert candidates[0].source_tier == "alias"
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_catalog.py backend/tests/wind/test_field_search.py -v`  
Expected: FAIL because catalog modules are absent.

- [x] **Step 3: Implement normalization and the first alias set**

Aliases must cover open/high/low/close, pre/post adjustment, volume, turnover, total/free-float market cap, PE/PB/PS, ROE/ROA, revenue, net profit, operating cash flow, announcement date, report period, industry, index components, ST and suspension. Tokenize Chinese with `jieba`, normalize English snake/camel case, and combine exact alias score with BM25 only after asset/frequency filters.

- [x] **Step 4: Build the real catalog and run tests**

Run:

```bash
uv run --project backend factor-platform build-wind-catalog --source windquery/windquery/references/wind_field_index.md --output backend/data/generated/wind_fields.jsonl
uv run --project backend pytest backend/tests/wind/test_catalog.py backend/tests/wind/test_field_search.py -v
```

Expected: generated catalog has 7,000–8,000 records; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/catalog.py backend/src/factor_platform/wind/field_search.py backend/data/wind_aliases.yaml backend/data/generated/wind_fields.jsonl backend/tests/wind/test_catalog.py backend/tests/wind/test_field_search.py
git commit -m "feat: add Wind field catalog search"
```

### Task 9.5: Localize WDS field metadata and rebuild the discovery funnel

> 修订任务（2026-08-05）。对应修订 #5。WDS 正式纳入架构：WDS 回答"应该查什么"，Wind MySQL 回答"如何取出真实数据"。

**Files:**
- Create: `backend/src/factor_platform/wind/wds_sync.py`
- Create: `backend/src/factor_platform/wind/metadata_catalog.py`
- Create: `backend/src/factor_platform/wind/metadata_repository.py`
- Create: `backend/data/generated/wind_metadata.jsonl`
- Modify: `backend/src/factor_platform/wind/field_search.py`
- Modify: `backend/src/factor_platform/cli.py`
- Create: `backend/tests/wind/test_metadata_catalog.py`
- Modify: `backend/tests/wind/test_field_search.py`

**Interfaces:**
- Produces: `MetadataCatalog.load()`、`get(table, field) -> FieldMetadata | None`、`MetadataRepository.merge(index_records, wds_records)`。
- Produces: CLI `factor-platform sync-wds-metadata --input <导出文件> --output backend/data/generated/wind_metadata.jsonl`。
- 运行时**不访问 WDS 网站**；只读本地目录。未覆盖字段走人工补充流程。

- [x] **Step 1: Test metadata merge and metadata-aware filtering**

```python
def test_metadata_supplies_asset_type_and_frequency() -> None:
    meta = catalog.get("ashareeodderivativeindicator", "s_val_mv")
    assert meta.asset_type == AssetType.STOCK
    assert meta.frequency == Frequency.DAILY
    assert meta.unit == "ten_thousand_cny"
    assert meta.observation_date_field == "trade_dt"


def test_quarterly_field_is_filtered_out_for_daily_requirement(search) -> None:
    candidates = search.search(requirement(meaning="市值", frequency=Frequency.DAILY))
    assert all(c.frequency != Frequency.QUARTERLY for c in candidates)


def test_merge_prefers_wds_over_index_for_overlapping_fields() -> None:
    merged = MetadataRepository.merge(index_records(), wds_records())
    assert merged[("ashareincome", "oper_rev")].metadata_source == "WDS"


def test_field_without_metadata_is_marked_not_dropped(search) -> None:
    candidates = search.search(requirement(meaning="某冷门字段"))
    assert any(c.metadata_source is None for c in candidates)
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_metadata_catalog.py backend/tests/wind/test_field_search.py -v`
Expected: FAIL because metadata modules are absent.

- [x] **Step 3: Implement sync, merge and the seven-layer funnel**

本地化的元数据字段：表中文名、表英文名、字段中文解释、字段英文解释、单位、数据频率、资产类型、主键、关联键、证券代码字段、交易日字段、公告日字段、报告期字段、数据更新时间、更新方式、是否原始字段、是否衍生字段、业务备注、数据可得性说明、字段适用范围、是否存在修订版本、`metadata_source`、`metadata_version`。

合并规则：WDS 记录优先于 Markdown 索引记录；索引中存在而 WDS 缺失的字段保留并标 `metadata_source=None`（**不得丢弃**）；合并结果按 `metadata_version` 版本化。

字段发现漏斗调整为七层：已注册专用函数 → 人工别名 → **WDS 本地元数据目录** → BM25 → `information_schema` 验证 → 数据样本验证 → 用户确认。前四层负责召回，后三层负责证伪。

元数据到位后，`FieldSearch` 的 `asset_type`/`frequency` 过滤**真正生效**（原实现因目录无元数据而空转）；无元数据的候选不参与过滤但需在结果中标注。

同时用元数据回填 `wind_aliases.yaml` 中的时间角色与单位，并把两条已知错误映射（`流通市值 → float_a_shr`、`经营活动现金流 → asharecashflow.net_profit`）标记为待 Task 10.5 处理。

> **实现修订（2026-08-10）。** 本任务全程离线完成，无需访问 WDS 网站，元数据源为本地 `windquery/.../wind字典/` 的 678 份逐表数据字典。落地时有六处偏离或补充：
>
> 1. **合并改为并集，取消「索引驱动」。** 原设计假定字段索引是完整召回基准。实测不成立：索引与字典**都是同一份 schema 的受损 PDF 抽取**，索引丢了 74 个字典有的字段，其中包括 `ashareeodderivativeindicator.s_val_mv`（市值）。索引驱动会静默删除它们，用户搜市值得到空结果并据此认为 Wind 没有这个字段。并集同时也守住了漏斗分工——前四层召回应过度包含，存在性由 `information_schema` 与样本验证对着真实库裁定，而不是对着一份 PDF 抽取。
> 2. **频率从「业务主键」推导，不看表名。** 业务主键即观测粒度：主键含「交易日期」→ 日频，含「报告期」→ 季频。无法识别的主键留空，不猜——静态描述表被猜成日频会污染所有过滤。
> 3. **单位在字典中根本不存在**，改由人工覆盖文件 `backend/data/wind_field_units.yaml` 提供，全部条目 `verified: false`，交 Task 10.5 逐条确认。**不在覆盖文件中的字段单位保持 `None`**：市值按元而非万元读取是 10,000 倍的静默错误，下游没有任何检查会发现。
> 4. **新增 `market` 字段**（源自注释中的「所属数据库」）。A 股表与港股表的 `asset_type` 同为 `stock`、中文字段名完全一致，仅凭原有元数据无法区分；实测 581 个港股字段会排在「市值」查询首位。首期范围是 A 股，故按市场**排序分层**而非硬过滤（分层是召回层该做的，过滤是证伪层的职责）。
> 5. **修掉一个既有 BM25 缺陷。** 原实现用 `score <= 0` 判定非命中，但 BM25 对出现在过半文档中的词给负分——「匹配但普遍」被当成了「没匹配」，「日期」「代码」这类高频词直接返回空。改为按词项重叠判定命中。同样地，市场降权不能用乘性系数：负分乘小于 1 的系数会**变大**，会把本该降权的字段顶到首位。
> 6. **元数据并入 BM25 文档**（中文名 + 释义）。原文档只含英文标识符，中文查询只能命中 32 条人工别名；接入后中文可直达全部 7,478 个字段——否则元数据层只会过滤、从不参与召回，不构成漏斗的第三层。
>
> `wind_metadata.jsonl` 含 Wind 版权释义原文，随 `backend/data/generated/` 一并 gitignore，属本地生成物。

- [x] **Step 4: Run metadata tests and rebuild the local catalog**

Run:

```bash
uv run --project backend factor-platform sync-wds-metadata --input <导出文件> --output backend/data/generated/wind_metadata.jsonl
uv run --project backend pytest backend/tests/wind -v
```

Expected: 元数据目录生成；过滤、合并、缺元数据保留测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/wds_sync.py backend/src/factor_platform/wind/metadata_catalog.py backend/src/factor_platform/wind/metadata_repository.py backend/src/factor_platform/wind/field_search.py backend/src/factor_platform/cli.py backend/data/generated/wind_metadata.jsonl backend/data/wind_aliases.yaml backend/tests/wind
git commit -m "feat: localize WDS field metadata"
```

### Task 10: Separate Schema verification from data-existence verification

> 就地改写（2026-08-05）。对应修订 #6。原"统一 3 只证券 × 5 个交易日"只适合日频行情，会把"样本区间无数据"误判为"字段无效"。

**Files:**
- Create: `backend/src/factor_platform/wind/schema_verify.py`
- Create: `backend/src/factor_platform/wind/sample_verify.py`
- Create: `backend/tests/wind/test_schema_verify.py`
- Create: `backend/tests/wind/test_sample_verify.py`

**Interfaces:**
- Produces: `SchemaVerifier.verify(candidate) -> SchemaVerdict`、`SampleVerifier.verify(candidate, plan) -> SampleVerdict`、`VerifiedFieldCandidate`。
- 两类验证**分开返回**；候选状态取六值之一，不再是通过/失败二值。
- 只使用参数化、有界的查询，经受信任连接层执行。

- [x] **Step 1: Test the six-state verdict and per-shape sampling**

```python
async def test_unknown_column_is_schema_invalid(fake_query) -> None:
    fake_query.schema_columns = {"s_info_windcode", "trade_dt"}
    verdict = await schema_verifier.verify(candidate(field="s_dq_close"))
    assert verdict.status == VerificationStatus.FIELD_INVALID
    assert verdict.rejection_reason == "column_not_found"


async def test_valid_field_with_empty_sample_is_not_an_error(fake_query) -> None:
    fake_query.rows = []
    verdict = await sample_verifier.verify(quarterly_candidate(), sample_plan())
    assert verdict.status == VerificationStatus.SCHEMA_VALID_NO_DATA_IN_SAMPLE
    assert verdict.is_blocking is False


async def test_quarterly_field_samples_report_periods_not_trading_days(fake_query) -> None:
    await sample_verifier.verify(quarterly_candidate(), sample_plan())
    assert fake_query.last_sample_codes == 3
    assert fake_query.last_sample_report_periods == 8


async def test_index_membership_sampling_covers_two_rebalances(fake_query) -> None:
    await sample_verifier.verify(index_member_candidate(), sample_plan())
    assert fake_query.last_sample_rebalance_dates >= 2


async def test_time_role_mismatch_is_reported(fake_query) -> None:
    verdict = await schema_verifier.verify(candidate_using_report_period_field_as_observation())
    assert verdict.status == VerificationStatus.TIME_ROLE_INVALID
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_schema_verify.py -v`  
Expected: FAIL because verifier is absent.

- [x] **Step 3: Implement two verifiers and per-shape sampling**

**Schema 验证**（不查数据）：表是否存在、字段是否存在、字段类型、时间字段、代码字段、主键或业务键、字段是否适用于当前查询形状。经 `information_schema`，绝不插值用户值或未验证标识符。

**数据存在性验证**（查数据，有界）：样本区间是否有数据、非空率、重复键、数值范围、单位、时间覆盖、返回行数、样例值。

采样策略按数据形态决定，不再统一：

| 数据类型 | 采样 |
|---|---|
| 日频行情 | 3 只证券 × 5–20 个交易日 |
| 季度财务 | 3 只证券 × 8 个报告期 |
| 年度数据 | 3 只证券 × 5 个年度报告期 |
| 公告事件 | 3 只证券 × 2–3 年 |
| 静态信息 | 10 只不同板块证券的截面 |
| 区间数据 | 至少覆盖一次进入与退出事件 |
| 指数成分 | 至少两个历史调仓日 |
| 行业归属 | 至少一次行业变更或多个历史截面 |
| 基金数据 | 至少一个完整披露周期 |

返回状态六值：`SCHEMA_VALID_DATA_PRESENT`、`SCHEMA_VALID_NO_DATA_IN_SAMPLE`、`SCHEMA_VALID_DATA_SPARSE`、`SCHEMA_INVALID`、`FIELD_INVALID`、`TIME_ROLE_INVALID`。

**只有 `SCHEMA_INVALID` / `FIELD_INVALID` / `TIME_ROLE_INVALID` 阻塞。** `SCHEMA_VALID_NO_DATA_IN_SAMPLE` 必须如实呈现为"字段存在，但当前样本区间无数据"，交由用户判断，不得判定为字段错误。

> **实现说明（2026-08-10）。** 离线部分已完成，实跑 21 条单测全过。四点补充：
>
> 1. **采样计划按 `QueryShape` 显式穷举，无默认分支。** 缺省回落到日频计划正是本任务要修的缺陷；写成 `_PLANS[shape]` 让漏配的形态直接 KeyError，而不是静默按日频采样。有一条测试遍历 `QueryShape` 全部取值做守卫。
> 2. **两个验证器共用 `QueryExecutor` 协议**，生产实现 `WindQueryExecutor` 单独成模块，用 `asyncio.to_thread` 包住同步的 pymysql，并在 Wind 未配置时**构造即拒绝**——构造成功、查询时才失败会把「缺凭据」伪装成「数据有问题」，且离故障点隔了好几层。
> 3. **标识符一律走绑定参数。** 表名与字段名来自模型输出和 PDF 抽取，两条测试断言 SQL 文本里不出现表名、字段名、库名，另有一条用 `x; DROP TABLE ... --` 做敌意输入。
> 4. **修了一个 CLI 报错错位。** 无 `.env` 时 `verify-field` 原本抛出「session_cookie_secret is required」的 pydantic 校验堆栈——用户问的是 Wind 字段，得到的是 cookie 密钥报错。现在翻译成「复制 .env.example 并填写 Wind 凭据」。
>
> **延后的真实取数冒烟（需凭据轮换后执行）**：Step 4 的三条 `verify-field` 命令（`point_range` / `report_period` / `interval_overlap` 各一）尚未对真实库执行。

- [ ] **Step 4: Run tests and verify one field of each shape**

Run:

```bash
uv run --project backend pytest backend/tests/wind/test_schema_verify.py backend/tests/wind/test_sample_verify.py -v
uv run --project backend factor-platform verify-field ashareeodprices s_dq_close --codes 600519.SH --shape point_range
uv run --project backend factor-platform verify-field asharefinancialindicator s_fa_roe --codes 600519.SH --shape report_period
uv run --project backend factor-platform verify-field aindexmembers s_con_indate --codes 000300.SH --shape interval_overlap
```

Expected: 单测 PASS；三种形态各自返回正确状态与对应采样统计。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/schema_verify.py backend/src/factor_platform/wind/sample_verify.py backend/src/factor_platform/cli.py backend/tests/wind/test_schema_verify.py backend/tests/wind/test_sample_verify.py
git commit -m "feat: separate schema and data existence verification"
```

### Task 10.5: Externalize metric definitions into a reviewable registry

> 修订任务（2026-08-05）。对应修订 #4。口径判断原本散落在澄清规则常量、字段别名与案例夹具三处，没有一处能被非程序员读懂并签字。

**Files:**
- Create: `backend/data/metric_definitions.yaml`
- Create: `backend/src/factor_platform/factor/metric_registry.py`
- Modify: `backend/src/factor_platform/factor/clarification.py`
- Modify: `backend/src/factor_platform/wind/field_search.py`
- Create: `backend/tests/factor/test_metric_registry.py`
- Modify: `backend/tests/factor/test_clarification.py`

**Interfaces:**
- Produces: `MetricRegistry.load()`、`get(key) -> MetricDefinition`、`options_for(category) -> list[str]`、`plausible_range(key)`。
- 澄清候选项、字段口径、结果层量级阈值、对拍参照**统一来自本表**。

- [x] **Step 1: Test registry-driven clarification and disputed blocking**

```python
def test_clarification_options_come_from_registry() -> None:
    questions = ClarificationEngine(registry).questions(make_spec(variable="盈利质量"))
    assert questions[0].options == registry.options_for("profitability")


def test_disputed_metric_blocks_execution() -> None:
    with pytest.raises(DisputedMetricError, match="流通市值"):
        planner.plan(spec_using("FLOAT_MV"), confirmed_fields())


def test_unreviewed_metric_is_allowed_but_flagged() -> None:
    verdict = registry.gate("ROE_TTM")
    assert verdict.allowed is True
    assert verdict.requires_warning is True


def test_registry_covers_every_clarification_option() -> None:
    for category in ("profitability", "valuation", "growth"):
        for option in registry.options_for(category):
            assert registry.get(option) is not None
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/factor/test_metric_registry.py -v`
Expected: FAIL because the registry is absent.

- [x] **Step 3: Build the registry and wire it into three consumers**

每条口径至少包含：`display_zh`、`definition`、`wind_table`、`wind_field`、`time_role`、`announcement_field`、`unit`、`plausible_range`、`reference_check`、`review_status`、`reviewer`、`reviewed_at`、`review_comment`、`evidence_version`、`note`。

首版覆盖：`ROE_TTM`、`ROA_TTM`、`CFO_TO_PROFIT`、`PE_TTM`、`PB`、`PS_TTM`、`REVENUE_YOY`、`NET_PROFIT_YOY`、`OPERATING_PROFIT_YOY`，全部标 `review_status: unreviewed`。

**两条已知错误映射标 `disputed` 并阻塞**（非警告）：`流通市值 → float_a_shr`（实为流通 A 股股本）、`经营活动现金流 → asharecashflow.net_profit`（实为间接法起点净利润）。

三值状态的权限：`unreviewed` 可用于原型测试与内部试算、不得作为正式发布结果、界面必须标注"未复核"；`reviewed` 可用于正式演示、因子库与发布，须保存复核人/时间/意见；`disputed` **禁止执行、禁止发布、禁止进入因子库、禁止进入多因子与指数**，不允许通过普通操作绕过。

`ClarificationEngine` 删除硬编码候选元组，改为注入 `MetricRegistry`。

> **实现说明（2026-08-10）。** 离线部分完成，14 条注册表单测 + 2 条澄清单测全过。三点：
>
> 1. **触发词留在澄清引擎，候选项来自注册表。** 判断「盈利质量」是模糊表述属于语言判断，留在代码里；但候选项是口径定义，必须由不写代码的人可读可签字。多留一份副本，正是「界面提供了一个注册表已标 disputed 的选项」的成因——补了一条测试断言凡是提供给用户的候选项，注册表都必须放行。
> 2. **未登记口径默认拒绝**，与 `disputed` 同为不放行。默认放行会让「忘记登记」变成静默通过。
> 3. **两条已知错误映射登记为 `disputed` 而非删除。** 删掉只会让错误重新变回沉默的；登记在此，平台再次踩中时能直接拒绝并说明错在哪。两者都会产出看起来完全合理的因子——`float_a_shr` 是股本不是市值，现金流量表的 `net_profit` 是间接法起始行，用它算现金流对利润比会恒等于 1。
>
> **延后项**：全部 9 条口径当前为 `unreviewed`，需带教老师抽样确认后改为 `reviewed`；每条已附 `reference_check` 说明如何核对。


- [x] **Step 4: Run registry, clarification and case tests**

Run: `uv run --project backend pytest backend/tests/factor backend/tests/golden -v`
Expected: 注册表驱动的澄清、disputed 阻塞、unreviewed 放行加警告全部 PASS；扩充后的案例集不回归。

- [ ] **Step 5: Commit**

```bash
git add backend/data/metric_definitions.yaml backend/src/factor_platform/factor/metric_registry.py backend/src/factor_platform/factor/clarification.py backend/src/factor_platform/wind/field_search.py backend/tests/factor
git commit -m "feat: externalize metric definitions into a registry"
```

### Task 11: Plan registered functions and generic Wind queries

> 就地改写（2026-08-05）。计划必须携带 `TimeConvention`；口径的公告日字段来自 Task 10.5 的注册表。

**Files:**
- Create: `backend/src/factor_platform/wind/planner.py`
- Create: `backend/tests/wind/test_planner.py`

**Interfaces:**
- Consumes: confirmed `FactorSpec`（含 `TimeConvention`）, confirmed `FieldCandidate` values, `CapabilityCatalog`, `MetricRegistry`, `MetadataCatalog`.
- Produces: `WindPlanner.plan() -> ExecutionPlan`，计划中必须写入 `time_convention` 与 `warmup_start`。

- [x] **Step 1: Test function priority and financial point-in-time rules**

```python
def test_prices_use_registered_get_price(planner) -> None:
    plan = planner.plan(momentum_spec(), confirmed_close())
    assert plan.steps[0].tool == "wind.index_components"
    assert plan.steps[1].tool == "wind.get_price"


def test_financial_plan_requires_announcement_date(planner) -> None:
    with pytest.raises(PlanningError, match="announcement_date"):
        planner.plan(roe_spec(), confirmed_roe_without_announcement_date())


def test_plan_carries_time_convention(planner) -> None:
    plan = planner.plan(momentum_spec(), confirmed_close())
    assert plan.time_convention.signal_date == "T"
    assert plan.time_convention.trade_date == "T+1"


def test_warmup_start_covers_largest_rolling_window(planner) -> None:
    plan = planner.plan(momentum_spec(window=20), confirmed_close())
    assert trading_days_between(plan.warmup_start, plan.metadata["start_date"]) >= 20


def test_semantic_only_capability_gets_arguments_filled(planner) -> None:
    plan = planner.plan(historical_membership_spec(), confirmed_fields())
    step = next(s for s in plan.steps if s.tool == "wind.index_components")
    assert step.arguments["index_code"] == "000300.SH"
    assert step.arguments["start_date"] and step.arguments["end_date"]


def test_after_close_announcement_is_not_used_same_day(planner) -> None:
    plan = planner.plan(roe_spec(), confirmed_roe_with_after_close_announcement())
    assert plan.steps[-1].arguments["as_of_offset_days"] == 1
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_planner.py -v`  
Expected: FAIL because planner is absent.

- [x] **Step 3: Implement deterministic planning priority**

每个变量的决策顺序：能力精确匹配 → 已确认的通用字段 → 拒绝（未确认字段直接 `PlanningError`，不做猜测）。

规划器必须主动补齐：数据调用前先取**历史** `index_components`；从最大滚动窗口反推 `warmup_start`；按预处理流水线的股票池规则加 ST/停牌过滤；保留报告期与公告日字段；**把 `TimeConvention` 写入计划**。通用计划只能使用适配器已允许的六种形状。

**能力目录的语义匹配路径返回空参数**（`find_exact` 对日历/状态/成分类工具只给出工具名），规划器必须从 `CapabilityCatalog.get_tool(name).parameters` 读取必需参数，并从请求信封填入 `order_book_ids`、`start_date`、`end_date`、`index_code`。

**公告时点保守规则**：口径注册表标记为盘后公告、或无法确定发布时点的财务字段，`as_of` 至少顺延一个交易日，不得在公告日当日使用。

无法证明点时可得的财务数据在规划期直接失败，不是警告。

> **实现修订（2026-08-10）。** 四点：
>
> 1. **`plan(spec, confirmed, request)` 多收一个请求信封。** `FactorSpec` 不含日期，日期属于受信任信封（与解析器合并信封的做法一致），规划器需要它来算 `warmup_start` 与查询区间。
> 2. **专用函数的路由改问能力目录，不按表名硬编码。** 我第一版把 `ashareeodderivativeindicator` 也归进「价格表」，PE/PB/市值会被路由到 `get_price` —— 而该函数只支持 9 个 OHLCV 字段，其余抛 `NotImplementedError`，也就是说这个错误要等 Worker 真跑一遍才暴露。能力目录里已经声明了每个工具的 `exact_outputs` 与 `source_dependencies`，硬编码表名等于把这份知识抄了一遍还抄错。
> 3. **`ExecutionPlan` 新增 `time_convention` 字段。** 计划是被签名、入队、重放的对象，时间口径必须随它一起走，而不是执行时回头去读 spec。
> 4. **修正了 43 个案例夹具里我编造的工具名。** Task 6.5 写夹具时我凭想象填了 `wind.get_financial_indicator` / `get_derivative_indicator` / `get_financial_statement` / `get_index_members` 四个**从不存在**的工具，而当时没有任何东西会发现——夹具是唯一断言这些名字的地方，套件对着一个不存在的接口「通过」了。现补一条守卫测试：凡 `expected_tool_names` 中的工具，能力目录里必须存在。歧义案例的工具名清空为准确状态：未澄清就没有确定的字段绑定，也就没有确定的检索计划。



- [x] **Step 4: Run planner and golden-case tests**

Run: `uv run --project backend pytest backend/tests/wind/test_planner.py backend/tests/golden/test_cases.py -v`  
Expected: tests PASS; expected tool names match all applicable golden cases.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/planner.py backend/tests/wind/test_planner.py
git commit -m "feat: plan safe Wind data retrieval"
```

### Task 12: Compile the formula DSL and execute the preprocessing pipeline

> 就地改写（2026-08-05）。对应修订 #1、#2。算子由 14 减为 11；去极值/标准化/中性化移出 AST，由独立的流水线执行器按声明顺序执行。

**Files:**
- Create: `backend/src/factor_platform/factor/compiler.py`
- Create: `backend/src/factor_platform/factor/pipeline_executor.py`
- Create: `backend/tests/factor/test_compiler.py`
- Create: `backend/tests/factor/test_pipeline_executor.py`

**Interfaces:**
- Produces: `FormulaCompiler.evaluate(node, variables, context) -> DataFrame`（index 为日期，columns 为证券代码）。
- Produces: `PipelineExecutor.apply(pipeline, variables, factor, context) -> tuple[dict[str, DataFrame], DataFrame]`，按 `order` 依次执行，`target=variables` 作用于原始变量、`target=factor` 作用于公式结果。
- 编译器**不再实现** `winsorize`/`zscore`/`industry_neutralize`；这三个算子只存在于流水线执行器中。

- [x] **Step 1: Write operator semantics tests**

```python
def test_rank_is_cross_sectional() -> None:
    values = frame([[1.0, 3.0], [4.0, 2.0]])
    result = compiler.evaluate(call("rank", var("x")), {"x": values}, context())
    assert result.iloc[0].tolist() == [0.5, 1.0]
    assert result.iloc[1].tolist() == [1.0, 0.5]


def test_rolling_return_uses_only_past_values() -> None:
    result = compiler.evaluate(call("rolling_return", var("close"), window=2), {"close": prices()}, context())
    assert result.iloc[1, 0] == pytest.approx(prices().iloc[1, 0] / prices().iloc[0, 0] - 1)


def test_divide_replaces_infinite_values_with_nan() -> None:
    result = compiler.evaluate(call("divide", var("x"), var("zero")), variables_with_zero(), context())
    assert result.isna().all().all()


def test_pipeline_applies_steps_in_declared_order() -> None:
    pipeline = PreprocessingPipeline(steps=[
        PreprocessingStep(order=1, operation="industry_neutralize", target="factor"),
        PreprocessingStep(order=2, operation="zscore", target="factor"),
    ])
    _, out = PipelineExecutor().apply(pipeline, variables(), raw_factor(), context())
    assert out.equals(zscore(industry_neutralize(raw_factor())))


def test_pipeline_order_matters() -> None:
    neutralize_first = PipelineExecutor().apply(pipeline_nz(), variables(), raw_factor(), context())[1]
    standardize_first = PipelineExecutor().apply(pipeline_zn(), variables(), raw_factor(), context())[1]
    assert not neutralize_first.equals(standardize_first)


def test_variable_target_applies_before_formula_evaluation() -> None:
    processed, _ = PipelineExecutor().apply(winsorize_variables(), variables(), None, context())
    assert processed["close"].max() < variables()["close"].max()
```

- [x] **Step 2: Run compiler tests red**

Run: `uv run --project backend pytest backend/tests/factor/test_compiler.py backend/tests/factor/test_pipeline_executor.py -v`

Expected: FAIL because compiler is absent.

- [x] **Step 3: Implement the eleven operators and the ordered pipeline**

编译器实现的 11 个算子：`add`、`subtract`、`multiply`、`divide`、`negative`、`log`、`rank`、`rolling_return`、`rolling_std`、`rolling_mean`、`fillna`。每个算子是显式函数，用 dict 分派，**禁止 `eval` 和动态 import**。

固定轴语义：`rank` 按日期跨列（`pct=True`）；滚动算子按列跨日期，窗口必须是正整数且 **`min_periods=window`**（pandas 默认 `min_periods=1` 会在窗口未满时用不足数据出值，等于用更少历史"假装"算出了 20 日动量）；`divide` 的 `inf`/`-inf` 一律转 `NaN`；`log` 非正输入转 `NaN`。

流水线执行器实现 `winsorize`（逐日分位数或 MAD）、`zscore`（当日截面均值/标准差）、`industry_neutralize`（减当日所属行业均值）、`fillna`。严格按 `order` 执行；`target=variables` 在公式求值**之前**作用于每个原始变量，`target=factor` 在公式求值**之后**作用于因子结果。

执行器必须记录实际执行序列写入结果元数据，供公式层校验比对是否出现重复标准化。

> **实现说明（2026-08-10）。** 27 条单测全过。三点需要记录：
>
> 1. **`window` 在两类算子中含义不同，这是刻意的。** 滚动聚合的 window 是**观测个数**（`rolling_mean(x,20)` 平均 20 个值）；`rolling_return` 的 window 是**滞后期数**（`close[t]/close[t-20]-1`），因为这才是提出「20 日动量」的人所指的东西。按观测个数解释会让 20 日动量实际跨 19 期——一个永远存在、且看上去完全合理的差一错误。两种语义各有测试钉死。
> 2. **`min_periods=window` 是必须显式写的。** pandas 默认 `min_periods=1`，会在窗口未满时用不足的数据出值：第 3 天就返回一个「20 日动量」。下游无法分辨，每次回测的前段都悄悄变成了另一个因子。
> 3. **`industry_neutralize` 缺行业映射时直接拒绝，不静默跳过。** 跳过会让 spec 声称「已中性化」而因子实际仍载荷在行业上，且没有任何下游环节会重新推导这一点。部分覆盖（个别证券无行业）同样拒绝。
>
> 另：`divide` 的 inf 与 `zscore` 的零方差日都转 NaN——无穷值会在 rank 中存活并把当日整个截面一起带偏，损失范围从一个格子扩大到一整天。
> 依赖新增 `pandas-stubs`（dev），使 mypy 能检查 DataFrame 操作。


- [x] **Step 4: Run compiler tests**

Run: `uv run --project backend pytest backend/tests/factor/test_compiler.py backend/tests/factor/test_pipeline_executor.py -v`

Expected: all operator, NaN, zero-division, alignment and future-data tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/factor/compiler.py backend/src/factor_platform/factor/pipeline_executor.py backend/tests/factor/test_compiler.py backend/tests/factor/test_pipeline_executor.py
git commit -m "feat: compile formulas and run preprocessing pipeline"
```

### Task 13: Build the signed manifest and the user-facing code export

> 就地改写（2026-08-05）。对应修订 #9。原设计让 Worker 用子进程执行生成的 Python，为此引入源码 AST 白名单、动态 import 检查、源码参数解析，以及"展示代码与执行代码逐字节一致"的证明成本。但生成的源码只是一层固定包装，真正的计算逻辑在平台自己的 runtime 里，用户也不能改它再执行 —— 这等于**为了证明安全，先制造一个需要额外安全处理的对象**。
>
> 修订后：Worker 直接执行签名并校验过的 `manifest.json`；`factor.py` 降级为用户导出产物，不进入内部执行路径。

**Files:**
- Create: `backend/src/factor_platform/execution/manifest.py`
- Create: `backend/src/factor_platform/factor/export.py`
- Create: `backend/tests/execution/test_manifest.py`
- Create: `backend/tests/factor/test_export.py`

**Interfaces:**
- Produces: `ManifestBuilder.build(spec, plan, selections, input_artifacts) -> Manifest`、`sign(manifest) -> SignedManifest`、`verify(signed) -> Manifest`。
- Produces: `CodeExporter.render(manifest) -> ExportedProgram`（`.source` + `.sha256`），仅供用户审查、复制、下载与在批准环境中复现。
- Manifest 内容：`FactorSpec`、`ExecutionPlan`、`PreprocessingPipeline`、`TimeConvention`、`FieldSelection` 列表、输入工件哈希、各项版本号、签名、执行参数。**它是平台唯一实际执行的对象。**

- [x] **Step 1: Write determinism and rejection tests**

```python
def test_same_inputs_build_identical_manifest() -> None:
    first = ManifestBuilder().build(spec(), plan(), selections(), inputs())
    second = ManifestBuilder().build(spec(), plan(), selections(), inputs())
    assert first.sha256 == second.sha256


def test_tampered_manifest_fails_verification() -> None:
    signed = sign(ManifestBuilder().build(spec(), plan(), selections(), inputs()))
    signed.payload["execution_plan"]["steps"][0]["arguments"]["end_date"] = "2099-01-01"
    with pytest.raises(ManifestVerificationError):
        verify(signed)


def test_manifest_rejects_unregistered_tool() -> None:
    with pytest.raises(ManifestSchemaError, match="unregistered_tool"):
        ManifestBuilder().build(spec(), plan_with_tool("wind.arbitrary_sql"), selections(), inputs())


def test_manifest_requires_time_convention_and_input_hashes() -> None:
    manifest = ManifestBuilder().build(spec(), plan(), selections(), inputs())
    assert manifest.time_convention is not None
    assert all(a.sha256 for a in manifest.input_artifacts)


def test_exported_code_is_deterministic_and_traceable_to_manifest() -> None:
    manifest = ManifestBuilder().build(spec(), plan(), selections(), inputs())
    first, second = CodeExporter().render(manifest), CodeExporter().render(manifest)
    assert first.source == second.source
    assert manifest.sha256 in first.source
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/execution/test_manifest.py backend/tests/factor/test_export.py -v`

Expected: FAIL because manifest builder and exporter are absent.

- [x] **Step 3: Implement manifest construction, signing and code export**

**确定性构建**：manifest 的 JSON 序列化使用排序键、固定分隔符、固定数值格式；不嵌入时间戳、随机数或依赖字典迭代顺序的内容。同一组输入必须产出相同 `sha256`。

**结构校验（fail-closed）**：`ExecutionPlan` 中每个 `tool` 必须在能力目录或六种受控查询形状中登记；`FormulaNode` 必须通过 Task 2.5 的 `check_ast`；`PreprocessingPipeline` 必须通过顺序与重复校验；`TimeConvention` 必填；每个输入工件必须带 `sha256`。任何未登记内容直接拒绝。

**签名**：用 `Settings` 中的 manifest 签名密钥对规范化 JSON 签名。Worker 侧独立验签，篡改任何字段都会导致验证失败。签名密钥**不下发给 Worker 之外的执行面**，Worker 只需验签公钥/共享密钥，不需要任何数据库或模型密钥。

**代码导出**：`CodeExporter` 从同一份 manifest 确定性渲染可读 Python，供用户审查、复制、下载与在批准环境复现。导出代码中嵌入 manifest 的 `sha256` 以建立对应关系。**导出产物不进入内部执行路径**，因此不需要源码 AST 白名单。

原计划的 `execution/ast_validator.py`（源码级 AST 白名单）随本次修订**取消**；公式层的 AST 校验由 Task 2.5 的 `factor/ast_checks.py` 承担。

> **实现说明（2026-08-10）。** 23 条单测全过。三点：
>
> 1. **`runtime_versions` 由调用方传入，不读活动解释器。** 读 `sys.version` 会让同一份输入在不同机器上产出不同 sha256，「同输入同 manifest」当场失效。
> 2. **签名用 HMAC 而非裸哈希，且常数时间比较。** 哈希只能证明未损坏，签名才能证明「这是平台批准过的那一份」——而真正的攻击场景是「用户签字后把日期区间改宽」，那种改动会产生一个完全合法的新哈希。
> 3. **导出代码的安全论证是「移出路径」而非「加控制」。** 生成代码曾经是执行路径，因此需要源码 AST 白名单、动态 import 检查和逐字节一致性证明；改为 manifest 执行后，这些控制不是被加强了，而是不再需要——被保护的对象已经不在需要保护的位置上。导出文件顶部明确声明「这不是平台实际执行的对象」，并有测试断言这句话在。
>
> 实测：两次构建 sha256 一致；改写 payload 中的 `end_date` 后验签被拒；导出代码 73 行、内嵌 manifest 哈希。


- [x] **Step 4: Run tests**

Run: `uv run --project backend pytest backend/tests/execution/test_manifest.py backend/tests/factor/test_export.py -v`

Expected: manifest 确定性、篡改检测、未登记工具拒绝、导出代码确定性全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/execution/manifest.py backend/src/factor_platform/factor/export.py backend/tests/execution/test_manifest.py backend/tests/factor/test_export.py
git commit -m "feat: build signed execution manifest and code export"
```

### Task 14: Add the no-secret job queue with leases, idempotency and cancellation

> 就地改写（2026-08-05）。对应修订 #8、#9、#11。Worker 执行 manifest 而非生成的源码；原子 rename 解决不了崩溃滞留，补租约与超时恢复。

**Files:**
- Create: `backend/src/factor_platform/execution/job_store.py`
- Create: `backend/src/factor_platform/execution/runtime.py`
- Create: `backend/src/factor_platform/execution/worker.py`
- Create: `backend/src/factor_platform/execution/recovery.py`
- Create: `backend/src/factor_platform/execution/retention.py`
- Create: `backend/tests/execution/test_job_store.py`
- Create: `backend/tests/execution/test_worker.py`
- Create: `backend/tests/execution/test_recovery.py`

**Interfaces:**
- Produces: `JobStore.enqueue()`、`claim_next()`、`renew_lease()`、`complete()`、`fail()`、`cancel()`；CLI `factor-worker run-once|serve|recover`。
- Worker 输入是**签名后的 manifest** 加输入 Parquet；输出是结果 Parquet、结果 JSON 与工件哈希清单。
- `job.json` 至少包含：`job_id`、`session_id`、`session_version`、`idempotency_key`、`manifest_sha256`、`input_sha256`、`claimed_by`、`claimed_at`、`lease_expires_at`、`attempt`、`max_attempts`、`timeout_seconds`、`cancel_requested`、`created_at`、`started_at`、`finished_at`、`artifact_retention_until`。

- [x] **Step 1: Test atomic claim and secret-free Worker execution**

```python
def test_job_can_be_claimed_only_once(job_store) -> None:
    job_id = job_store.enqueue(manifest(), input_artifacts())
    assert job_store.claim_next().job_id == job_id
    assert job_store.claim_next() is None


def test_worker_completes_formula_job_without_secret_environment(worker, monkeypatch) -> None:
    monkeypatch.setenv("WIND_PASSWORD", "unit-test-only")  # pragma: allowlist secret
    result = worker.run_once()
    assert result.status == "completed"
    assert "WIND_PASSWORD" not in result.environment_keys


def test_expired_lease_without_result_is_requeued(job_store, clock) -> None:
    job = job_store.claim_next()
    clock.advance(job.lease_expires_at + 1)
    recovered = RecoveryScanner(job_store).scan()
    assert recovered[0].outcome == "requeued"
    assert job_store.claim_next().job_id == job.job_id


def test_expired_lease_with_complete_result_is_completed(job_store, clock) -> None:
    job = job_store.claim_next()
    write_complete_result(job)
    clock.advance(job.lease_expires_at + 1)
    assert RecoveryScanner(job_store).scan()[0].outcome == "completed"


def test_lease_expiry_beyond_max_attempts_fails(job_store, clock) -> None:
    job = exhaust_attempts(job_store)
    clock.advance(job.lease_expires_at + 1)
    outcome = RecoveryScanner(job_store).scan()[0]
    assert outcome.outcome == "failed"
    assert outcome.reason == "max_attempts_exceeded"


def test_identical_request_reuses_idempotency_key(job_store) -> None:
    first = job_store.enqueue(manifest(), input_artifacts())
    second = job_store.enqueue(manifest(), input_artifacts())
    assert first == second
    assert job_store.pending_count() == 1


def test_cancelled_running_job_writes_no_result(job_store, worker) -> None:
    job = job_store.claim_next()
    job_store.cancel(job.job_id)
    result = worker.run_once()
    assert result.status == "cancelled"
    assert not result_artifact_exists(job)


def test_worker_rejects_tampered_manifest(worker, job_store) -> None:
    tamper_manifest_in_queue(job_store)
    result = worker.run_once()
    assert result.status == "failed"
    assert result.error.category == ErrorCategory.INPUT
    assert result.error.code == "manifest_verification_failed"
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/execution/test_job_store.py backend/tests/execution/test_worker.py -v`  
Expected: FAIL because queue and Worker are absent.

- [x] **Step 3: Implement leases, idempotency, cancellation and retention**

**目录与原子性**：`pending`、`running`、`completed`、`failed` 四个目录。入队先写临时目录再原子 rename（避免 Worker 看到写了一半的任务）；抢占用原子 rename（同一文件系统上两个 Worker 只有一个成功，另一个收到 `FileNotFoundError` 继续找下一个，无需锁与心跳）。

**租约与恢复**：抢占时写入 `claimed_by`、`claimed_at`、`lease_expires_at`。`factor-worker recover` 扫描 `running`：租约过期 → 检查是否已有完整结果 → 有则转 `completed`；无结果且 `attempt < max_attempts` 则放回 `pending` 并递增 `attempt`；达上限则标 `failed`。每次恢复写入恢复事件与原因。

**幂等键**：由 `session_id`、`session_version`、`manifest_sha256`、`input_sha256` 共同生成。重复入队返回既有 `job_id`，不产生等价任务。

**取消**：`pending` 任务直接标记取消；`running` 任务写入 `cancel_requested`，Worker 在安全检查点读取并中止；**取消后不得写入正式结果**。

**超时**：每个任务设 `timeout_seconds`，超时终止并返回结构化错误；是否可重试由错误分类决定。

**Worker 执行流程**：抢占 → 用 `Settings.worker_environment()` **白名单构造**干净环境（不是复制父环境再删密钥）→ 施加 CPU/内存/文件大小限制 → **独立验签并校验 manifest**（不信任上游已验过）→ 校验输入工件哈希 → 调用 runtime 执行 → 写结果与工件哈希清单 → 原子移动任务目录。

**工件保留与磁盘**：`retention.py` 为上传 PDF、中间文本、输入/输出 Parquet、日志、错误工件、临时文件、导出代码分别设定保留期限。定期检查可用空间，达阈值时停止接收新任务、优先清理过期临时工件、**保留不可变正式工件**并记录清理日志。

> **实现说明（2026-08-10）。** 56 条单测全过，CLI 实跑一个任务从 pending 到 completed 并产出 Parquet。五点：
>
> 1. **`factor-worker` 是独立入口，不与 `factor-platform` 共用。** 主 CLI 会加载 `Settings`（需要 Wind 与模型凭据），而 Worker 主机上正确的状态就是没有这些凭据——共用入口会让配置正确的机器反而启动失败。
> 2. **租约过期不等于该重跑。** Worker 可能在写完完整结果之后才死，盲目重跑会重复计算并覆盖用户可能已经打开过的工件。恢复扫描先看结果是否完整（Parquet **与** result.json 都在），三种处置：已完成 / 重新入队 / 达上限失败。只有 Parquet 没有 JSON 判为写到一半，重新入队。
> 3. **取消单独结算，不并入失败。** 取消是决策不是缺陷，记进失败会让所有统计里的「主动停止」看起来像 Worker 不稳定。
> 4. **环境按白名单构造。** 复制父环境再删已知密钥的做法，会漏掉每一个没人记得加进拒绝列表的变量，而那个列表总是泄露之后才更新。有测试注入 `WIND_PASSWORD` 与一个虚构的未来密钥，断言两者都不在 Worker 环境里。
> 5. **保留策略的关键不是年龄而是「有没有别的东西引用它」。** 已发布因子的输出是因子库条目所指向的对象，清掉它会让用户几个月后打开自己保存的因子时发现指向为空。因此 `PUBLISHED_OUTPUT` 永不清扫；磁盘压力下改为停止接收新任务——拒绝开工可恢复，删掉已发布因子的证据不可恢复。
>
> 另修一处测试质量问题：磁盘可用率原本直接读真实磁盘，测试会随开发机的占用情况时通过时失败。改为可注入。


- [x] **Step 4: Run unit tests and a real one-shot job**

Run:

```bash
uv run --project backend pytest backend/tests/execution -v
uv run --project backend factor-worker run-once --job-root data/runtime/jobs
uv run --project backend factor-worker recover --job-root data/runtime/jobs
```

Expected: 单测 PASS；一个夹具任务从 `pending` 走到 `completed` 并产出 Parquet；`recover` 对人工制造的过期租约任务给出正确处置。

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/execution/job_store.py backend/src/factor_platform/execution/runtime.py backend/src/factor_platform/execution/worker.py backend/src/factor_platform/execution/recovery.py backend/src/factor_platform/execution/retention.py backend/tests/execution
git commit -m "feat: execute manifests in isolated worker with leases"
```

### Task 15: Add data, formula and result validation reports

**Files:**
- Create: `backend/src/factor_platform/validation/data.py`
- Create: `backend/src/factor_platform/validation/formula.py`
- Create: `backend/src/factor_platform/validation/result.py`
- Create: `backend/tests/validation/test_data.py`
- Create: `backend/tests/validation/test_formula.py`
- Create: `backend/tests/validation/test_result.py`

**Interfaces:**
- Produces: `DataValidator.validate()`, `FormulaValidator.validate()`, `ResultValidator.validate()` returning structured findings with severity, code, message and evidence.

- [x] **Step 1: Test plausible failures**

```python
def test_duplicate_keys_are_blocking() -> None:
    report = DataValidator().validate(frame_with_duplicate_date_code(), metadata())
    assert report.has_error("duplicate_key")


def test_financial_value_before_announcement_is_blocking() -> None:
    report = FormulaValidator().validate(point_in_time_fixture_with_leakage(), spec())
    assert report.has_error("future_financial_data")


def test_constant_result_emits_warning() -> None:
    report = ResultValidator().validate(constant_factor())
    assert report.has_warning("constant_factor")


def test_result_outside_metric_plausible_range_is_blocking() -> None:
    report = ResultValidator(registry).validate(roe_factor_with_values_over_3000_percent())
    assert report.has_error("implausible_magnitude")


def test_reference_mismatch_emits_warning() -> None:
    report = ResultValidator(registry).validate(pe_factor_disagreeing_with_wind_pe())
    assert report.has_warning("reference_mismatch")


def test_unreviewed_metric_emits_warning() -> None:
    report = ResultValidator(registry).validate(factor_using_unreviewed_metric())
    assert report.has_warning("unreviewed_metric")


def test_signal_used_on_same_day_is_blocking() -> None:
    report = FormulaValidator().validate(fixture_trading_on_signal_date(), spec())
    assert report.has_error("signal_traded_before_available")


def test_duplicate_standardization_in_pipeline_is_blocking() -> None:
    report = FormulaValidator().validate(fixture_with_zscore_twice_on_factor(), spec())
    assert report.has_error("duplicate_standardization")
```

- [x] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/validation -v`  
Expected: FAIL because validators are absent.

- [x] **Step 3: Implement exact validation rules**

**数据层**：区间覆盖、证券数量、重复键（阻塞）、缺失率、单位、数量级、全零/常数、极值、历史成分、公告日存在性、未来数据、复权口径、字段口径。

**公式层**：轴语义、窗口、方向一致性、权重、除零、缺失值、中性化、**重复标准化**（比对 `PipelineExecutor` 记录的实际执行序列，阻塞）、未来价格、财务可得性（阻塞）、**信号与交易时点一致性**（用 `TimeConvention` 检查是否在信息可得之前形成信号或交易，阻塞）。

**结果层**：分布、逐日覆盖率、Top/Bottom 样本、原始变量对照、因子时间变化、耗时、输入行数、警告，外加三条口径治理规则：

| 规则码 | 级别 | 触发 |
|---|---|---|
| `implausible_magnitude` | ERROR | 结果超出口径注册表的 `plausible_range` |
| `reference_mismatch` | WARNING | 与注册表 `reference_check` 的独立算路差异超阈值 |
| `unreviewed_metric` | WARNING | 因子引用了 `review_status: unreviewed` 的口径 |

`disputed` 口径由 Task 10.5 在规划期即阻塞，不进入本层。

> **实现说明（2026-08-10）。** 21 条单测全过。三点：
>
> 1. **每条检查针对的都是「能跑通且看起来合理」的错误**——这是选择标准。自己就会抛异常的东西不需要校验器。
> 2. **严重级别的划分才是实质。** 阻塞错了会训练用户去绕过闸门，该阻塞的降成告警会让单位错误进入正式发布。因此每条测试钉的不只是「检出」，还有「级别」：重复键阻塞（横截面算子会重复计入）、空样本告警（字段可能有效，区间是用户的判断）、量级越界阻塞（几乎总是元/万元或比值/百分数的口径错误）、参照不一致告警（可能是本因子错，也可能是参照过时）。
> 3. **重复标准化比对的是实际执行序列，不是声明的流水线。** 且区分 target：对变量标准化和对因子标准化是两件事，不算重复。
>
> `implausible_magnitude` 是「无专职研究员」的核心补偿控制：领域专家一眼就知道 3000% 的 ROE 是错的，而流水线不知道——3000 是个完全合法的浮点数。把专家脑子里的合理区间写进注册表，就是让这层能替代那一眼。


- [x] **Step 4: Run validation tests**

Run: `uv run --project backend pytest backend/tests/validation -v`  
Expected: all blocking/warning distinctions PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/validation backend/tests/validation
git commit -m "feat: validate factor data formulas and results"
```

### Task 16: Orchestrate and smoke-test the complete P0 CLI

**Files:**
- Create: `backend/src/factor_platform/orchestration/service.py`
- Create: `backend/tests/orchestration/test_service.py`
- Create: `backend/tests/e2e/test_cli_p0.py`
- Modify: `backend/src/factor_platform/cli.py`

**Interfaces:**
- Produces: `WorkflowService.create_session()`、`submit_message()`、`confirm_formula()`、`search_fields()`、`confirm_fields()`、`build_manifest()`、`execute()`；每个变更方法返回新的 `SessionSnapshot`。
- Produces 修订命令：`revise_formula()`、`revise_fields()`、`revise_request()`、`revise_preprocessing()`、`revise_time_convention()`、`cancel_execution()`、`clone_session()`、`rerun()`，全部经 Task 3.5 的归约器触发级联失效。
- CLI command: `factor-platform run-case CASE_ID --real-wind`。

- [x] **Step 1: Write a fake-adapter end-to-end test**

```python
async def test_momentum_case_reaches_completed(workflow, fake_llm, fake_wind, worker) -> None:
    session = await workflow.create_session(momentum_request())
    session = await workflow.submit_message(session.id, momentum_request().research_idea, session.version)
    session = await workflow.confirm_formula(
        session.id, confirmed_momentum_spec(), expected_version=session.version
    )
    session = await workflow.search_fields(session.id, expected_version=session.version)
    session = await workflow.confirm_fields(
        session.id, confirmed_close_fields(), expected_version=session.version
    )
    session = await workflow.build_manifest(session.id, expected_version=session.version)
    result = await workflow.execute(session.id, expected_version=session.version)
    assert result.state == "completed"
    assert result.artifact_uri.endswith("factor.parquet")


async def test_revising_formula_invalidates_fields_and_manifest(workflow, session) -> None:
    revised = await workflow.revise_formula(session.id, new_spec(), expected_version=session.version)
    assert revised.state == "waiting_formula_confirmation"
    assert revised.field_selections == []
    assert revised.manifest_sha256 is None


async def test_disputed_metric_blocks_before_any_external_call(workflow, fake_wind) -> None:
    with pytest.raises(DisputedMetricError):
        await workflow.confirm_fields(session.id, disputed_selection(), expected_version=1)
    assert fake_wind.call_count == 0
```

- [x] **Step 2: Run the E2E test red**

Run: `uv run --project backend pytest backend/tests/orchestration/test_service.py backend/tests/e2e/test_cli_p0.py -v`  
Expected: FAIL because the service is absent.

- [x] **Step 3: Implement orchestration with explicit commits between external effects**

每个公开方法的固定骨架：检查状态与版本 → 发出"开始"事件 → 执行**一次**外部操作 → 校验其结果 → 发出"完成"或"失败"事件。

**铁律：调用 LLM / Wind / Worker 时绝不持有 SQLite 事务。** SQLite 写锁是全库级的，一次 30 秒的模型调用握着事务会阻塞所有其他会话。因此外部操作前后各提交一次，中间是无锁窗口。"开始"事件必须先落库，这样即使外部调用崩溃，会话状态也能反映"曾经开始过执行"，而不是凭空回到上一步。

修订命令走同一骨架，但发出的是 Task 3.5 的修订事件，由归约器执行级联失效。`disputed` 口径与未确认字段在**任何外部调用之前**拦截。

`build_manifest` 取代原 `generate_code`：构建 → 校验 → 签名 → 连同输入工件入队。导出的 `factor.py` 与 manifest 同源，供界面展示与下载。

> **实现说明（2026-08-10）。** 11 条编排单测 + 3 条端到端全过。四点：
>
> 1. **端到端测试的价值在接缝，不在组件。** 每个组件各自的测试都过、链条仍然断——因为规划器产出的计划被 manifest 构建器拒绝，或 runtime 写出的 frame 校验层读不了。`tests/e2e` 里 Wind 与模型是 fake 的，但**我们自己组件之间的每一道接缝都是真的**：计划、manifest、签名、Parquet、三份报告。
> 2. **`build_manifest` 需要请求信封。** 日期不在 `FactorSpec` 里（与解析器合并信封的做法一致），规划器要用它算 `warmup_start`。
> 3. **所有修订走同一条 `_revise` 私有路径**，确保级联失效无法被绕过——新增一个修订命令时不会有人忘记接归约器。
> 4. **`rerun` 从 `code_ready` 不合法。** 还没跑过就没有可重跑的东西；允许它会让界面在一个从未产出结果的因子上显示「重跑」，用户会合理地据此认为结果存在。

> **实测（2026-08-10）**：`run-case momentum_20d` → 4 步计划、预热回溯至 2022-11-25；`run-case profitability_ambiguous` → 在澄清处阻塞；`run-case roe_ttm_rank` → 走受控通用查询形状。**`--real-wind` 的取数阶段尚未接线，需凭据后补**，已记入延后清单。


- [ ] **Step 4: Run fake and real P0 CLI scenarios**

Run:

```bash
uv run --project backend pytest backend/tests/orchestration/test_service.py backend/tests/e2e/test_cli_p0.py -v
uv run --project backend factor-platform run-case momentum_20d --real-wind
uv run --project backend factor-platform run-case profitability_ambiguous --real-wind
```

Expected: fake tests PASS; real momentum completes; profitability case stops at clarification until an answer is supplied, then completes.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/orchestration/service.py backend/src/factor_platform/cli.py backend/tests/orchestration/test_service.py backend/tests/e2e/test_cli_p0.py
git commit -m "feat: complete command line factor workflow"
```

---

## Week 3 — P0 Web Stability and P1 Reports

### Task 17: Expose versioned session APIs and resumable SSE

**Files:**
- Create: `backend/src/factor_platform/main.py`
- Create: `backend/src/factor_platform/api/sessions.py`
- Create: `backend/src/factor_platform/api/events.py`
- Create: `backend/src/factor_platform/api/health.py`
- Create: `backend/tests/api/test_sessions.py`
- Create: `backend/tests/api/test_events.py`

**Interfaces:**
- Produces the PRD endpoints for create/get/message/formula confirmation/field confirmation/code/execute/result, `GET /events`, and `GET /health`.
- Mutation bodies carry `expected_version`; conflicts return HTTP 409 with current version.

- [x] **Step 1: Write API contract tests**

```python
async def test_stale_formula_confirmation_returns_409(client, session_id) -> None:
    response = await client.post(
        f"/api/sessions/{session_id}/confirm-formula",
        json={"expected_version": 1, "factor_spec": confirmed_spec_payload()},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "stale_session_version"


async def test_events_resume_after_last_event_id(client, populated_session) -> None:
    response = await client.get(f"/api/sessions/{populated_session}/events", headers={"Last-Event-ID": "2"})
    assert "id: 3" in response.text
```

- [x] **Step 2: Run API tests red**

Run: `uv run --project backend pytest backend/tests/api/test_sessions.py backend/tests/api/test_events.py -v`  
Expected: FAIL because app and routers are absent.

- [x] **Step 3: Implement thin handlers and SSE event IDs**

Map domain exceptions to stable JSON error codes. SSE reads persisted events after `Last-Event-ID`, then watches for new records with a bounded polling interval and heartbeat. `GET /health` reports database, Wind, active LLM provider, job queue and version without exposing credentials.

> **实现说明（2026-08-10）。** 11 条 API 单测全过，真实 uvicorn 启动 `/api/health` 返回 200。四点：
>
> 1. **错误码是契约，状态码不是。** 前端若按 message 分支，别人改一次措辞就会坏；按 `stale_session_version` 分支则一直可用。因此每个领域异常都有稳定标识符，人读的文案可以自由修改。版本过期用 409 而非 400 是刻意的：请求本身合法、只是晚了一步，客户端需要「重读后重试」而不是「修正 payload」——两种恢复动作不同。
> 2. **SSE 是持久化日志的投影，不是广播。** 这正是它可恢复的原因：浏览器休眠、断网、刷新之后带 `Last-Event-ID` 回来，拿到的恰好是错过的部分。纯广播设计会让这个浏览器永久落后且无从察觉。SSE 的 `id` 直接用事件序号——另造一套流 id 等于引入第二套需要与第一套保持一致的顺序。
> 3. **`Last-Event-ID` 无法解析时从头重放，而不是跳过。** 重发是可见的，跳过是不可见的。
> 4. **健康检查只报「是否可达」，绝不报「如何可达」。** 凌晨两点看这个输出的人，会把它整段粘进聊天窗口。有测试断言响应体里不出现 password / api_key / secret / 连接串。另外「降级」与「宕机」分开：平台设计上就能离线运行，缺 Wind 或模型时各自报自身状态，不拖垮整体。

> **实测**：`/api/health` → `database: ok`、`wind: disabled`、`llm: unconfigured`、`job_queue: ok`，整体 `ok`——如实报告而非伪装健康。


- [x] **Step 4: Run API tests and launch smoke**

Run:

```bash
uv run --project backend pytest backend/tests/api/test_sessions.py backend/tests/api/test_events.py -v
uv run --project backend uvicorn factor_platform.main:app --host 127.0.0.1 --port 8000
```

Expected: tests PASS; `/api/health` returns HTTP 200 and structured component status.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/main.py backend/src/factor_platform/api/sessions.py backend/src/factor_platform/api/events.py backend/src/factor_platform/api/health.py backend/tests/api/test_sessions.py backend/tests/api/test_events.py
git commit -m "feat: expose factor session API"
```

### Task 18: Scaffold the typed React client and application shell

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/api/schema.d.ts`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/app/queryClient.ts`
- Create: `frontend/src/app/AppShell.tsx`
- Create: `frontend/src/app/AppShell.test.tsx`
- Create: `backend/scripts/export_openapi.py`

**Interfaces:**
- Produces: generated API types, `apiClient`, router paths and shared application shell.

> **实现说明（2026-08-10）。** 4 条 shell 单测 + `tsc --noEmit` + `vite build` + 真实浏览器验收全过。四点：
>
> 1. **前端类型由后端 OpenAPI 生成**（`backend/scripts/export_openapi.py` → `npm run gen:api`，1543 行）。改名字段会在构建期报错，而不是运行时变成组件里的 `undefined`。导出脚本用内存库与离线模型替身，**不需要任何凭据**——需要生产密钥的构建步骤是会被人跳过的构建步骤。
> 2. **`FactorSpec` 在 OpenAPI 中被拆成 `-Input` / `-Output`**，因为收发形状确实不同（默认值字段在输出里必填、输入里可选）。客户端保留这个区分而非合并成一个类型，这样把 snapshot 原样回传是类型检查过的，而不是碰运气。
> 3. **健康横幅逐项列出离线组件，不是一盏绿灯。** 平台设计上就能离线运行；研究员看不到 Wind 未连接，就会把 fake 数据的运行读成真实运行。实测横幅显示「离线模式：wind、llm」。
> 4. **4xx 不重试。** 版本过期或被拒绝不会因为重试而变成真的，而重试过的拒绝在用户看来就是服务器不稳定。
>
> 另：vitest 自带一份 vite 导致插件类型冲突，用 npm `overrides` 去重而非绕开。
>
> **浏览器验收（Playwright + 独立临时 profile 的 Chrome，不触碰用户浏览器数据）**：三个导航项渲染正确、路由切换与高亮正常、健康横幅真实连到后端 `/api/health`；console 仅 favicon 404，无功能错误。截图存 `docs/acceptance/screenshots/`。


- [x] **Step 1: Scaffold and write the shell test**

Run:

```bash
npm create vite@latest frontend -- --template react-ts
npm --prefix frontend install antd @tanstack/react-query react-router-dom @monaco-editor/react recharts
npm --prefix frontend install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom openapi-typescript
```

Test:

```tsx
it("renders all primary navigation entries", () => {
  render(<AppShell />)
  expect(screen.getByText("因子工作台")).toBeInTheDocument()
  expect(screen.getByText("研报提取")).toBeInTheDocument()
  expect(screen.getByText("因子库")).toBeInTheDocument()
})
```

- [x] **Step 2: Run the shell test red**

Run: `npm --prefix frontend test -- --run src/app/AppShell.test.tsx`  
Expected: FAIL because shell/routes are absent.

- [x] **Step 3: Export OpenAPI and implement the typed client**

Add scripts `generate:api`, `test`, `typecheck`, and `build`. `apiClient` prepends `/api`, parses the stable error envelope, and sends `expected_version` on every mutation. Routes include workspace, reports, library, analysis, indices and admin.

- [x] **Step 4: Generate types and verify frontend foundation**

Run:

```bash
uv run --project backend python backend/scripts/export_openapi.py
npm --prefix frontend run generate:api
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
```

Expected: shell test PASS; generated schema type-checks.

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts frontend/src/main.tsx frontend/src/api frontend/src/app backend/scripts/export_openapi.py
git commit -m "feat: scaffold typed factor platform frontend"
```

### Task 19: Build research input, state steps and resumable session UI

**Files:**
- Create: `frontend/src/features/workspace/WorkspacePage.tsx`
- Create: `frontend/src/features/workspace/ResearchForm.tsx`
- Create: `frontend/src/features/workspace/WorkflowSteps.tsx`
- Create: `frontend/src/features/workspace/useSession.ts`
- Create: `frontend/src/features/workspace/sessionView.ts`
- Create: `frontend/src/features/workspace/sessionView.test.ts`
- Create: `frontend/src/components/StatusBanner.tsx`

**Interfaces:**
- Produces: workspace creation/resume, SSE subscription and deterministic mapping from backend state to the ten UI steps.

> **实现说明（2026-08-10）。** 12 条前端单测 + `tsc --noEmit` + 真实浏览器验收全过。**浏览器验收抓出了两个单测抓不到的真实缺陷**：
>
> 1. **点击后界面毫无反应。** 后端返回 502（模型未配置——这是正确行为），但前端只处理了版本冲突，其余错误被静默吞掉。用户点「开始解析」，什么都不变，无法分辨是成功、还在跑、还是失败了——这是最糟的一种结果。现已按错误码给出可执行的说明：`llm_response_invalid` → 「模型未配置…请在 .env 中配置模型接口后重试」。
> 2. **antd v5 只支持 React 16~18，而我选了 React 19**，控制台持续告警。已降到 React 18.3 而不是带着告警交付。
>
> 另两点设计：
>
> - **状态映射是纯函数**（`sessionView.ts`）。后端状态机是唯一事实来源；让各组件自行解释状态会产生自相矛盾的界面——步骤条显示「执行中」而按钮写着「运行」。
> - **失败停在出错的那一步**，不跳转到通用错误页。用户需要知道是哪一环节坏了，才能判断该改公式还是该放宽区间。失败步骤由快照内容推断而非存储的标签——归约器会在修订时清空下游产物，所以「存在性」才是可靠信号。
> - **重载时先取快照再开 SSE**。反过来会让用户眼看着步骤条从历史事件里一点点拼出来。


- [x] **Step 1: Test state-to-step mapping**

```ts
it("maps field confirmation to step six", () => {
  expect(toWorkflowView({ state: "waiting_field_confirmation", version: 8 }).activeStep).toBe(6)
})

it("keeps failed execution on the execution step", () => {
  expect(toWorkflowView({ state: "failed", failed_stage: "executing", version: 10 }).activeStep).toBe(9)
})
```

- [x] **Step 2: Run view tests red**

Run: `npm --prefix frontend test -- --run src/features/workspace/sessionView.test.ts`  
Expected: FAIL because view mapping is absent.

- [x] **Step 3: Implement the reference-layout shell**

Top controls: factor type, universe, start/end date, provider/model status, Wind switch and reset. Left column reserves formula/code/result tabs. Right column renders workflow steps, status banner, event log and research input. Reloading `/workspace/:id` fetches the snapshot before opening SSE.

- [x] **Step 4: Run tests and type-check**

Run:

```bash
npm --prefix frontend test -- --run src/features/workspace/sessionView.test.ts
npm --prefix frontend run typecheck
```

Expected: state tests and type-check PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/workspace frontend/src/components/StatusBanner.tsx
git commit -m "feat: add resumable factor workspace"
```

### Task 20: Add confirmation cards, code/results panes and P0 browser acceptance

**Files:**
- Create: `frontend/src/features/workspace/ClarificationCard.tsx`
- Create: `frontend/src/features/workspace/FormulaConfirmation.tsx`
- Create: `frontend/src/features/workspace/FieldCandidateTable.tsx`
- Create: `frontend/src/features/workspace/CodePane.tsx`
- Create: `frontend/src/features/workspace/ResultPane.tsx`
- Create: `frontend/src/features/workspace/ValidationFindings.tsx`
- Create: `frontend/src/features/workspace/confirmations.test.tsx`
- Create: `frontend/src/features/workspace/WorkflowMotion.css`
- Modify: `frontend/src/features/workspace/WorkspacePage.tsx`

**Interfaces:**
- Produces every P0 human-in-the-loop action and result presentation.

> **实现说明（2026-08-10）。** 23 条前端单测 + `tsc --noEmit` + `vite build` 全过。组件放在 `features/workbench/`（与 Task 18 建立的目录一致，非计划中的 `workspace/`）。
>
> 每个确认组件共有的性质：**提交时携带用户当时看到的版本**，而不是最新版本。用最新版本会让每次确认都成功——包括那个基于三次修订之前的界面做出的确认，而这正是版本检查存在的意义。
>
> 四处 UI 决定：
>
> 1. **`canonical_formula` 是主体，模型说明在其下方且带「仅供阅读，不参与执行」标签。** 这张卡片要防的唯一失败是：用户确认了说明而不是公式。
> 2. **字段表把「单位」放在确认者视线内**，未登记时显式标「单位未知」。市值按元而非万元读取是 10,000 倍误差，下游没有任何检查会发现。同样，无元数据的字段标注为「本地字典未能解析」而非留空——留空会被读成「这个字段没有意义」。
> 3. **澄清卡片没有「跳过」或「使用默认」。** 这里的默认值就是整个设计要避免的那个猜测。
> 4. **校验发现按严重级别分组呈现。** 混在一起时，满屏黄色会训练人们略过红色，而红色才是阻止发布的那一类。
>
> 另修一处**真实的可访问性缺陷**：行复选框原本设 `name` 属性，而屏幕阅读器不播报它——用户无法知道每个复选框选的是哪一行绑定。改为 `aria-label`。测试先发现的是它，但问题本身与测试无关。
>
> `WorkflowMotion.css` 的动效在 `prefers-reduced-motion: reduce` 下**完全禁用**而非缩短：对前庭敏感的人来说更快的动画仍是动画，而这些动效不承载静态布局没有的信息。
>
> jsdom 缺 `matchMedia` 与 `ResizeObserver`（antd 响应式布局要用），已在 setup 中补桩——这不是掩盖缺陷：真实浏览器提供它们，而真实浏览器验收覆盖了它们驱动的行为。


- [x] **Step 1: Test that explicit confirmation payloads include the visible version**

```tsx
it("submits selected fields with current version", async () => {
  render(<FieldCandidateTable sessionVersion={8} candidates={candidates} onConfirm={onConfirm} />)
  await user.click(screen.getByRole("checkbox", { name: /后复权收盘价/ }))
  await user.click(screen.getByRole("button", { name: "确认字段" }))
  expect(onConfirm).toHaveBeenCalledWith({ expected_version: 8, selections: [expectedSelection] })
})
```

- [x] **Step 2: Run confirmation tests red**

Run: `npm --prefix frontend test -- --run src/features/workspace/confirmations.test.tsx`  
Expected: FAIL because components are absent.

- [x] **Step 3: Implement cards and panes**

Clarification card groups blocking questions; formula card shows variables, direction, windows, weights and defaults; field table shows source, table, field, meaning, time role, unit, Schema status and sample statistics. Monaco is read-only by default; execution uses the exact generated hash and offers copy plus `.py` download. Result pane shows distribution, coverage, Top/Bottom, sample rows, timing and findings, with CSV/Parquet artifact download. `WorkflowMotion.css` adds 180 ms step/card transitions and indeterminate progress movement, while `prefers-reduced-motion: reduce` disables every nonessential animation.

- [ ] **Step 4: Perform P0 browser verification**

Run backend, Worker and frontend. Invoke the `browser-control` skill and verify in an independent browser:

1. `momentum_20d` reaches completed with real result rows.
2. `profitability_ambiguous` blocks before formula confirmation.
3. Refresh during field confirmation restores the same version and selections.
4. A stale browser tab receives 409 and refresh guidance.
5. An invalid formula parameter shows a structured error and a new version after repair.

Expected: all five scenarios visibly match backend state; no browser console error or failed API request remains unexplained.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/workspace/ClarificationCard.tsx frontend/src/features/workspace/FormulaConfirmation.tsx frontend/src/features/workspace/FieldCandidateTable.tsx frontend/src/features/workspace/CodePane.tsx frontend/src/features/workspace/ResultPane.tsx frontend/src/features/workspace/ValidationFindings.tsx frontend/src/features/workspace/WorkflowMotion.css frontend/src/features/workspace/confirmations.test.tsx frontend/src/features/workspace/WorkspacePage.tsx
git commit -m "feat: complete factor confirmation workspace"
```

### Task 21: Parse safe text PDFs with page evidence

**Files:**
- Create: `backend/src/factor_platform/reports/pdf.py`
- Create: `backend/tests/reports/test_pdf.py`
- Create: `backend/tests/fixtures/reports/text_zh.pdf`
- Create: `backend/tests/fixtures/reports/text_en.pdf`

**Interfaces:**
- Produces: `PdfParser.extract(file) -> ParsedReport` with page-numbered text blocks and metadata.

- [x] **Step 1: Test page evidence and file limits**

```python
def test_text_pdf_preserves_page_numbers(parser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert report.pages[0].page_number == 1
    assert "净资产收益率" in report.pages[0].text


def test_oversized_pdf_is_rejected(parser, oversized_pdf) -> None:
    with pytest.raises(ReportLimitError, match="file_size"):
        parser.extract(oversized_pdf)
```

- [x] **Step 2: Run PDF tests red**

Run: `uv run --project backend pytest backend/tests/reports/test_pdf.py -v`  
Expected: FAIL because parser is absent.

- [x] **Step 3: Implement MIME, size, page and timeout limits**

校验 `%PDF-` 签名与 MIME；原型上限 50 MiB、200 页；未提供密码的加密 PDF 直接拒绝；用 PyMuPDF 提取块级文本；归一化空白同时保留页码与 bounding box 证据；**绝不执行嵌入内容**。

每页额外产出 `text_density` 与 `layout_flags`（多栏、表格、图片区块、上下标密集），供 Task 23 决定是否走 OCR、供 Task 22 决定公式是否需要人工确认。

> **实现说明（2026-08-10）。** 16 条单测全过。四点：
>
> 1. **限制先于解析。** 签名、大小、页数、加密全部在解释任何字节之前检查——2 GB 的上传必须因为体积被拒，而不是在 PyMuPDF 试图打开之后。校验的是 `%PDF-` 签名而非扩展名：扩展名由上传者决定。
> 2. **扫描判据从「密度阈值」改为复合判据。** 我最初把阈值设成 0.5，而实测正常文本页只有 0.13——那会把每一页都判成扫描件。更根本的是**密度单独无法区分「稀疏文本页」与「带图注的扫描件」**：现在无文本层（密度 0）直接判定，低密度则需要 `has_image` 佐证。宁可多跑一次 OCR，也不能静默丢掉一页内容。补了一条测试专门钉住「一行字的正常页不算扫描件」。
> 3. **文档元数据只保留已知字段。** 元数据是上传者可控的文本，可能进入 prompt 或日志。
> 4. **测试 PDF 由脚本生成而非提交二进制。** 没人能读的夹具是没人能推理的夹具。


- [x] **Step 4: Run PDF tests**

Run: `uv run --project backend pytest backend/tests/reports/test_pdf.py -v`  
Expected: Chinese/English evidence and limit tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/reports/pdf.py backend/tests/reports/test_pdf.py backend/tests/fixtures/reports/text_zh.pdf backend/tests/fixtures/reports/text_en.pdf
git commit -m "feat: parse page-aware research PDFs"
```

### Task 22: Extract report factors and connect the report UI to the shared workflow

**Files:**
- Create: `backend/src/factor_platform/reports/extractor.py`
- Create: `backend/src/factor_platform/api/reports.py`
- Create: `backend/tests/reports/test_extractor.py`
- Create: `backend/tests/api/test_reports.py`
- Create: `frontend/src/features/reports/ReportsPage.tsx`
- Create: `frontend/src/features/reports/EvidenceViewer.tsx`
- Create: `frontend/src/features/reports/ReportsPage.test.tsx`

**Interfaces:**
- Produces: `ReportExtractor.extract(parsed_report) -> FactorSpec`, `POST /api/reports/upload`, and creation of a normal workflow session at `NEEDS_CLARIFICATION` or `WAITING_FORMULA_CONFIRMATION`.

- [x] **Step 1: Test evidence preservation and unknown values**

```python
async def test_report_extraction_never_invents_missing_rebalance(fake_llm, extractor) -> None:
    spec = await extractor.extract(report_without_rebalance())
    assert spec.rebalance_frequency is None
    assert "rebalance_frequency" in {item.field for item in spec.ambiguities}
    assert spec.source_evidence[0].page_number == 3
```

Frontend test verifies the upload response displays source quote and page number before “进入因子工作流” is enabled.

- [x] **Step 2: Run backend and frontend tests red**

Run:

```bash
uv run --project backend pytest backend/tests/reports/test_extractor.py backend/tests/api/test_reports.py -v
npm --prefix frontend test -- --run src/features/reports/ReportsPage.test.tsx
```

Expected: FAIL because extractor/API/UI are absent.

- [x] **Step 3: Implement retrieval-assisted extraction with explicit confidence**

对研报块按公式/变量/样本/预处理相关词打分排序；只把**有界的**高分片段（带页码 ID）送 Provider；每个抽取出的变量必须携带证据 ID；**输入中不存在的证据 ID 一律判非法**（防幻觉硬检查）。上传接口用生成的 artifact ID 存文件，**不用原始文件名**。

**首期能力边界（写入接口文档与界面提示）**：

> 首期支持正文中可复制、变量定义明确的中英文文本型研报；图片公式、复杂数学排版和扫描版研报仅识别正文提示，并要求人工确认公式。

抽取结果携带 `formula_extraction` 状态块：`status`（`extracted` / `needs_manual_confirmation`）、`confidence`、`source_pages`、`extracted_text`、`warning`。命中 Task 21 的 `layout_flags`（图片公式、复杂排版、跨页）或置信度低于阈值时，状态强制为 `needs_manual_confirmation`，允许用户手动输入或修改公式后再转 AST。

**系统不得在公式识别置信度不足时直接进入执行。**

**B4 边界**：送模型前必须过 Task 4.5 的 `OutboundFilter`；`LOCAL_ONLY_MODE` 下不调用外部模型，只做本地提取并要求用户手动确认公式；调用审计不保存完整研报正文。

> **实现说明（2026-08-10）。** 后端 33 条 + 前端 5 条单测全过。**B4 边界在这里第一次真正生效**——之前的任务里它是一条纪律，从这里开始它是唯一让研报正文不外流的机制。
>
> 1. **只有打过分的有界片段能出境，研报正文不能。** 分块打分完全在本地做（关键词权重表），所以「送什么出去」这个决定本身也不需要外发任何东西。片段数量与长度都有硬上限，与研报大小无关。
> 2. **模型必须为每条结论引用输入中存在的证据 ID；引用了不存在的 ID 直接判定不可信。** 被要求给出处的模型会很乐意编一个页码，而编造的引用比没有引用更糟——它读起来像是已核实的。
> 3. **置信度不足或版式不可靠时强制人工确认，绝不进入执行。** 从多栏页面或图注里抽出来的公式能正常解析，而且是错的。命中 `multi_column` / `has_image` 时无论置信度多高都要人工确认。
> 4. **抽取失败仍然返回证据。** 失败且不给证据，等于让用户从零手工重来。
> 5. **上传文件按生成的 artifact ID 存储，不用原始文件名。** 文件名由上传者控制，可以穿越目录、可以撞名、还会进日志和 prompt。
> 6. **`ReportLimitError` 映射为 413 而非 400**：请求本身格式正确，只是太大——客户端要做的是换个文件，不是改请求。
>
> 前端「进入因子工作流」按钮在公式未经确认前保持禁用：低置信度的抽取结果在屏幕上看起来和好结果一模一样，按钮状态是唯一在执行这条规则的东西。


- [x] **Step 4: Run tests and browser-smoke one Chinese and one English report**

Expected: tests PASS; both reports show page evidence and enter the same formula/field confirmation flow.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/reports/extractor.py backend/src/factor_platform/api/reports.py backend/tests/reports/test_extractor.py backend/tests/api/test_reports.py frontend/src/features/reports/ReportsPage.tsx frontend/src/features/reports/EvidenceViewer.tsx frontend/src/features/reports/ReportsPage.test.tsx
git commit -m "feat: extract factors from research reports"
```

---

## Week 4 — P2 Prototypes and Delivery

### Task 23: Add bounded OCR for scanned PDFs

**Files:**
- Create: `backend/src/factor_platform/reports/ocr.py`
- Create: `backend/tests/reports/test_ocr.py`
- Create: `backend/tests/fixtures/reports/scanned_bilingual.pdf`
- Modify: `backend/src/factor_platform/reports/pdf.py`

**Interfaces:**
- Produces: `OcrEngine.extract_page(image) -> OcrPage`; `PdfParser` selects OCR only when text density is below the configured threshold.

- [x] **Step 1: Test OCR routing and page identity**

```python
def test_scanned_page_uses_ocr_and_keeps_page_number(parser, fake_ocr) -> None:
    report = parser.extract(FIXTURES / "scanned_bilingual.pdf")
    assert fake_ocr.calls == [1]
    assert report.pages[0].page_number == 1
    assert report.pages[0].source == "ocr"
```

- [x] **Step 2: Run OCR tests red**

Run: `uv run --project backend pytest backend/tests/reports/test_ocr.py -v`  
Expected: FAIL because OCR engine is absent.

- [x] **Step 3: Implement RapidOCR adapter and limits**

200 DPI 渲染；每份研报最多 50 个 OCR 页；保留 bounding box 与置信度；按阅读顺序合并行；置信度低于 0.60 标记待人工复核。**文本密度高于阈值的页不做 OCR**（文本型直接提取又快又准）。

`source` 字段区分 `text` / `ocr`，让用户知道证据的可靠性来源。

**OCR 页上的公式一律标 `needs_manual_confirmation`** —— 通用 OCR 对数学公式识别不稳定，不承诺自动准确识别（见 Task 22 的边界声明）。

> **实现说明（2026-08-10）。** 12 条单测 + 真实引擎冒烟通过。四点：
>
> 1. **有文本层的页面绝不做 OCR。** 把精确文本换成识别猜测是一次静默降级，而结果看起来仍然像文本。OCR 还改为**按调用显式开启**而非自动——它比文本提取慢几个数量级。
> 2. **真实引擎冒烟抓出一个 fake 永远抓不到的 bug**：我把 PNG 原始字节直接喂给识别器，得到的是一维数组，报错出现在好几层之下且信息模糊。识别器要的是解码后的图像。已改为经 PyMuPDF 解码（本来就是依赖，无需为一次调用引入图像库），并补了防回归测试。修复后实测识别出 `ROE TTM cross-sectional rank`，置信度 0.99。
> 3. **行按阅读顺序合并**（先上下、再左右），而不是按识别返回顺序——后者是任意的。
> 4. **`formula_is_trustworthy("ocr")` 恒为 False。** 通用 OCR 会把数学记号弄坏成仍能解析的样子：漏一个负号，因子方向就反了。这个约束写在代码里而不是寄望于流程。


- [x] **Step 4: Run tests and real OCR smoke**

Run:

```bash
uv run --project backend pytest backend/tests/reports/test_ocr.py -v
uv run --project backend factor-platform extract-report backend/tests/fixtures/reports/scanned_bilingual.pdf
```

Expected: test PASS; output includes Chinese/English text, page 1 evidence and OCR confidence.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/reports/ocr.py backend/src/factor_platform/reports/pdf.py backend/tests/reports/test_ocr.py backend/tests/fixtures/reports/scanned_bilingual.pdf
git commit -m "feat: extract scanned report text with OCR"
```

### Task 24: Add structured error classification and two-round repair

**Files:**
- Create: `backend/src/factor_platform/orchestration/repair.py`
- Create: `backend/tests/orchestration/test_repair.py`
- Modify: `backend/src/factor_platform/orchestration/service.py`
- Modify: `backend/src/factor_platform/api/sessions.py`

**Interfaces:**
- Produces: `ErrorClassifier.classify()`, `RepairService.propose()`, `RepairAttempt`; maximum attempts is exactly 2.

- [x] **Step 1: Test repair eligibility and cap**

```python
def test_field_not_found_is_not_repaired_as_formula(repair) -> None:
    decision = repair.classify(error(code="unknown_wind_field"))
    assert decision.repairable is False


async def test_third_repair_is_rejected(repair_service) -> None:
    with pytest.raises(RepairLimitError):
        await repair_service.propose(session_with_two_repairs(), formula_error())
```

- [x] **Step 2: Run repair tests red**

Run: `uv run --project backend pytest backend/tests/orchestration/test_repair.py -v`  
Expected: FAIL because repair service is absent.

- [x] **Step 3: Implement classified repair versions**

Only invalid parameter, formula type, divide-by-zero policy and alignment errors are repairable. LLM receives the confirmed spec, structured error and allowed operators; output must be a new `FactorSpec` version. The user must confirm the repaired formula before execution.

- [x] **Step 4: Run repair tests and one browser repair scenario**

Expected: tests PASS; an invalid rolling window produces a proposed version, requires confirmation, and then completes; a third attempt is blocked.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/orchestration/repair.py backend/src/factor_platform/orchestration/service.py backend/src/factor_platform/api/sessions.py backend/tests/orchestration/test_repair.py
git commit -m "feat: repair classified factor failures"
```

### Task 25: Compute IC, Rank IC, quantile returns and turnover

**Files:**
- Create: `backend/src/factor_platform/analysis/metrics.py`
- Create: `backend/src/factor_platform/api/analysis.py`
- Create: `backend/tests/analysis/test_metrics.py`
- Create: `frontend/src/features/analysis/AnalysisPage.tsx`

**Interfaces:**
- Produces: `analyze_factor(factor, forward_returns, groups=5) -> AnalysisResult`, `POST /api/analysis`.

- [x] **Step 1: Write deterministic metric tests**

```python
def test_perfect_factor_has_unit_ic() -> None:
    result = analyze_factor(perfect_factor(), matching_forward_returns(), groups=2)
    assert result.ic.mean == pytest.approx(1.0)
    assert result.rank_ic.mean == pytest.approx(1.0)


def test_turnover_uses_membership_changes() -> None:
    result = analyze_factor(rotating_factor(), forward_returns(), groups=2)
    assert result.turnover.iloc[1] == pytest.approx(0.5)
```

- [x] **Step 2: Run metric tests red**

Run: `uv run --project backend pytest backend/tests/analysis/test_metrics.py -v`  
Expected: FAIL because analysis module is absent.

- [x] **Step 3: Implement no-lookahead metrics**

Shift forward returns after factor observation, align by date/code, compute Pearson IC, Spearman Rank IC, quantile equal-weight returns, long-short spread, coverage and one-way membership turnover. Reject dates with insufficient cross-sectional observations.

- [x] **Step 4: Run tests and render analysis UI**

Run:

```bash
uv run --project backend pytest backend/tests/analysis/test_metrics.py -v
npm --prefix frontend run typecheck
```

Expected: metric tests PASS; analysis page renders summary cards and Recharts series from the API result.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/analysis/metrics.py backend/src/factor_platform/api/analysis.py backend/tests/analysis/test_metrics.py frontend/src/features/analysis/AnalysisPage.tsx
git commit -m "feat: add factor performance analysis"
```

### Task 26: Persist immutable factor versions and artifacts

**Files:**
- Create: `backend/src/factor_platform/library/service.py`
- Create: `backend/src/factor_platform/api/library.py`
- Create: `backend/alembic/versions/0002_factor_library.py`
- Create: `backend/tests/library/test_service.py`
- Create: `frontend/src/features/library/LibraryPage.tsx`

**Interfaces:**
- Produces: `FactorLibrary.publish(session_id)`, `get_version(factor_id, version)`, `list_factors()`, `GET/POST /api/library`.

- [x] **Step 1: Test immutability and artifact hashes**

```python
async def test_published_version_cannot_be_overwritten(library, completed_session) -> None:
    first = await library.publish(completed_session.id)
    with pytest.raises(ImmutableArtifactError):
        await library.replace(first.factor_id, first.version, changed_spec())


async def test_same_program_and_data_record_stable_hashes(library, completed_session) -> None:
    item = await library.publish(completed_session.id)
    assert len(item.program_sha256) == 64
    assert len(item.result_sha256) == 64
```

- [x] **Step 2: Run library tests red**

Run: `uv run --project backend pytest backend/tests/library/test_service.py -v`  
Expected: FAIL because library is absent.

- [x] **Step 3: Implement metadata rows, provenance and Parquet ownership**

> 就地改写（2026-08-05）。对应修订 #11、#4。只存程序哈希与结果哈希不足以复现 —— Wind 财务数据会被修订，同一份程序在不同时间跑出的结果可能不同。

基础元数据：因子标识、版本、来源会话、定义 JSON、字段映射、计划、`manifest_sha256`、`result_sha256`、工件相对路径、创建者、时间戳。发布已修改的完成会话产生版本 `N+1`；文件**复制**进版本化的不可变目录。

**复现记录（`library/provenance.py`）**必须额外保存：`input_artifact_sha256`、`query_timestamp`、`source_database`、`source_table`、`source_fields`、`query_parameters`、`data_date_range`、`row_count`、`input_schema`、`input_non_null_ratio`、`query_plan_sha256`，以及全量版本号 —— `factor_spec_version`、`execution_plan_version`、`preprocessing_version`、`time_convention_version`、`field_metadata_version`、`metric_definition_version`、`wind_adapter_version`、`code_commit`、`runtime_version`。

**工件链**：`query_manifest.json` → `raw_input.parquet` → `aligned_input.parquet` → `factor_output.parquet` → `validation_report.json`。每个工件保存文件哈希、大小、行数、列数、结构、生成时间、来源、**上游工件哈希** —— 上游哈希把整条链锁在一起，任何一环被替换都能被发现。

**复核状态**：因子工件保存 `review_status`（`unreviewed` / `reviewed` / `disputed`）、`reviewer`、`reviewed_at`、`review_comment`、`evidence_version`。`disputed` 的因子**禁止发布进因子库**；`unreviewed` 可入库但界面必须标注"未复核"，且不得作为正式发布结果。

- [x] **Step 4: Run migration, tests and UI type-check**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend pytest backend/tests/library/test_service.py -v
npm --prefix frontend run typecheck
```

Expected: tests PASS; library page lists versions and opens lineage.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/library/service.py backend/src/factor_platform/api/library.py backend/alembic/versions/0002_factor_library.py backend/tests/library/test_service.py frontend/src/features/library/LibraryPage.tsx
git commit -m "feat: add versioned factor library"
```

### Task 27: Add linear multifactor composites and index weight generation

**Files:**
- Create: `backend/src/factor_platform/indices/service.py`
- Create: `backend/src/factor_platform/api/indices.py`
- Create: `backend/tests/indices/test_service.py`
- Create: `frontend/src/features/indices/IndicesPage.tsx`

**Interfaces:**
- Produces: `combine_factors(inputs, weights, directions)`, `generate_index(composite, rule) -> IndexArtifact`.

- [x] **Step 1: Test weight invariants and rebalance output**

```python
def test_composite_normalizes_directions_before_weighting() -> None:
    result = combine_factors([quality(), valuation()], [0.6, 0.4], [1, -1])
    assert result.loc[DATE, "A"] > result.loc[DATE, "B"]


def test_index_weights_sum_to_one_per_rebalance() -> None:
    artifact = generate_index(composite(), IndexRule(top_n=2, weighting="equal", rebalance="monthly"))
    assert artifact.weights.groupby("date")["weight"].sum().eq(1.0).all()
```

- [x] **Step 2: Run index tests red**

Run: `uv run --project backend pytest backend/tests/indices/test_service.py -v`  
Expected: FAIL because index service is absent.

- [x] **Step 3: Implement the prototype boundaries and the review gate**

输入必须是**已完成的因子库版本**（不能用会话中间态）。对齐日期与股票池 → 按方向统一 → 逐个 z-score → 拒绝非有限权重 → 权重归一化到 1。指数规则支持 Top N、等权或因子得分加权、月度/周度调仓、CSV 导出；**不含实盘下单**。

**复核闸门（修订 #4）**：

```python
def combine_factors(inputs, weights, directions, *, allow_unreviewed: bool = False):
    disputed = [i for i in inputs if i.review_status == "disputed"]
    if disputed:
        raise DisputedFactorError(f"存在争议口径的因子不得进入指数合成: {[i.factor_id for i in disputed]}")
    unreviewed = [i for i in inputs if i.review_status != "reviewed"]
    if unreviewed and not allow_unreviewed:
        raise UnreviewedFactorError(
            f"未经复核的因子不能进入指数合成: {[i.factor_id for i in unreviewed]}；"
            f"如确认接受该风险，显式传入 allow_unreviewed=True"
        )
```

`disputed` **无逃生门**；`unreviewed` 需显式传参放行，且该决定写入指数工件元数据。所有输入必须使用一致的 `TimeConvention`，不一致直接拒绝。

- [x] **Step 4: Run tests and export one index artifact**

Run:

```bash
uv run --project backend pytest backend/tests/indices/test_service.py -v
uv run --project backend factor-platform generate-index quality_value_demo --top-n 50 --weighting equal
```

Expected: tests PASS; output contains rebalance dates, constituents and weights summing to one.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/indices/service.py backend/src/factor_platform/api/indices.py backend/tests/indices/test_service.py frontend/src/features/indices/IndicesPage.tsx
git commit -m "feat: combine factors and generate index weights"
```

### Task 28: Add local authentication and two-role authorization

**Files:**
- Create: `backend/src/factor_platform/auth/service.py`
- Create: `backend/src/factor_platform/auth/dependencies.py`
- Create: `backend/src/factor_platform/api/auth.py`
- Create: `backend/alembic/versions/0003_users.py`
- Create: `backend/tests/auth/test_auth.py`
- Create: `frontend/src/features/admin/AdminPage.tsx`
- Modify: all mutation routers to require a user

**Interfaces:**
- Produces: signed HttpOnly session cookie, roles `admin` and `researcher`, `require_role()`.

- [x] **Step 1: Test role enforcement and cookie properties**

```python
async def test_researcher_cannot_create_users(client, researcher_cookie) -> None:
    response = await client.post("/api/auth/users", cookies=researcher_cookie, json=user_payload())
    assert response.status_code == 403


async def test_login_cookie_is_http_only(client, seeded_user) -> None:
    response = await client.post(  # pragma: allowlist secret
        "/api/auth/login",
        json={"username": "researcher", "password": "unit-test-only"},
    )
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=Lax" in response.headers["set-cookie"]
```

- [x] **Step 2: Run auth tests red**

Run: `uv run --project backend pytest backend/tests/auth/test_auth.py -v`  
Expected: FAIL because auth is absent.

- [x] **Step 3: Implement password hashing and authorization**

Hash with Argon2 through `pwdlib`; sign random session IDs with `itsdangerous`; store only session hashes and expiry; rotate cookie on login; require CSRF token for cookie-authenticated mutations. Admin manages users/providers and sees provider health, call count, token cost and failure rate; researcher manages own sessions, reports, factors, analyses and indices.

- [x] **Step 4: Run migration and auth tests**

Run:

```bash
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend pytest backend/tests/auth/test_auth.py -v
```

Expected: login, expiry, CSRF and role tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/auth backend/src/factor_platform/api/auth.py backend/alembic/versions/0003_users.py backend/tests/auth/test_auth.py frontend/src/features/admin/AdminPage.tsx backend/src/factor_platform/api/sessions.py backend/src/factor_platform/api/reports.py backend/src/factor_platform/api/analysis.py backend/src/factor_platform/api/library.py backend/src/factor_platform/api/indices.py
git commit -m "feat: add local role based access"
```

### Task 29: Package single-host deployment with a networkless Worker

**Files:**
- Create: `deploy/backend.Dockerfile`
- Create: `deploy/worker.Dockerfile`
- Create: `deploy/frontend.Dockerfile`
- Create: `deploy/nginx.conf`
- Create: `deploy/compose.yaml`
- Create: `deploy/compose.env.example`
- Create: `backend/tests/deploy/test_compose_contract.py`

**Interfaces:**
- Produces services `backend`, `worker`, `frontend`; shared `jobs` and `artifacts` volumes; Worker has `network_mode: none`.

- [x] **Step 1: Write deployment contract test**

```python
def test_worker_has_no_network_and_no_secret_environment() -> None:
    compose = load_compose("deploy/compose.yaml")
    worker = compose["services"]["worker"]
    assert worker["network_mode"] == "none"
    assert "WIND_PASSWORD" not in worker.get("environment", {})
    assert "KIMI_METERED_API_KEY" not in worker.get("environment", {})
```

- [x] **Step 2: Run deployment test red**

Run: `uv run --project backend pytest backend/tests/deploy/test_compose_contract.py -v`  
Expected: FAIL because Compose is absent.

- [x] **Step 3: Implement images, health checks and volumes**

Backend binds inside the Compose network, frontend exposes the only host port, Nginx proxies `/api` and SSE with buffering disabled, Worker polls the shared filesystem queue with no network, SQLite/artifacts/jobs use named volumes, and only backend receives Wind/LLM secrets.

- [x] **Step 4: Build and smoke the deployment**

Run:

```bash
docker compose --env-file deploy/compose.env.example -f deploy/compose.yaml config
docker compose --env-file deploy/compose.env.example -f deploy/compose.yaml up --build -d
docker compose --env-file deploy/compose.env.example -f deploy/compose.yaml ps
```

Expected: all services healthy; backend health is reachable through frontend; Worker has no network in inspected Compose config.

- [ ] **Step 5: Commit**

```bash
git add deploy backend/tests/deploy/test_compose_contract.py
git commit -m "feat: add single host container deployment"
```

### Task 30: Run full acceptance, finish user docs and package the demo

**Files:**
- Create: `README.md`
- Create: `docs/runbook.md`
- Create: `docs/demo-cases.md`
- Create: `docs/security.md`
- Create: `backend/data/demo/README.txt`
- Modify: `.env.example`

**Interfaces:**
- Produces a reproducible local/Compose startup path, demo script, security boundary and final acceptance evidence.

- [x] **Step 1: Run backend and frontend quality gates**

Run:

```bash
uv run --project backend ruff check backend/src backend/tests
uv run --project backend mypy backend/src
uv run --project backend pytest backend/tests -v --cov=factor_platform --cov-report=term-missing
npm --prefix frontend test -- --run
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all commands exit 0. Coverage gaps in state transitions, formula operators, SQL planning, auth or repair block release.

- [x] **Step 2: Execute the real-data acceptance matrix and produce evidence packets**

> 就地改写（2026-08-05）。对应修订 #4、#13。验收状态从二值改为三值；每个案例产出可异步复核的证据包；隐藏集在此刻首次运行。

运行并记录 artifact ID：

1. 历史沪深 300 成分上的动量因子；
2. 需要阻塞口径确认的质量/估值因子；
3. 一个故意写错的滚动窗口，修复一次后成功；
4. 一份中文文本研报；
5. 一份英文文本研报；
6. 一份双语扫描研报（公式应落到"需人工确认"）；
7. 已完成因子的 IC / Rank IC / 分组收益 / 换手率；
8. 双因子合成与月度指数权重；
9. **一次上游修订触发的级联失效与重跑**（改日期区间后旧结果失效、重跑通过）；
10. **一次 Worker 崩溃恢复**（人工制造过期租约，`recover` 正确处置）。

每个案例在 `docs/acceptance/<日期>/<case>/` 产出证据包：`request.json`、`spec.json`（含 `canonical_formula`）、`metrics.yaml`（本案例引用的口径条目快照）、`lineage.json`（变量 → 表.字段 → 时间角色 → 公告日字段）、`manifest.json` + `factor.py` + 两者哈希、`validation.json`、`result_sample.csv`（Top/Bottom 各 20 行 + 覆盖率 + 分布）、`reference_diff.json`、`provenance.yaml`（全量版本号与输入哈希）。

**运行隐藏验收集**：

```bash
uv run --project backend factor-platform run-case-suite --set hidden --report docs/acceptance/<日期>/hidden-metrics.json
```

同时输出与 Task 6.5 基线的对比，报告六组量化指标与失败案例分类（模型/字段/数据/执行错误）。

**验收状态取三值**，不得笼统宣称"通过"：

| 状态 | 含义 |
|---|---|
| `machine_passed` | 三层校验全绿，无 ERROR 级发现 |
| `awaiting_domain_review` | 机器通过，但引用了 `unreviewed` 口径 —— **P0 默认落到这一档** |
| `domain_approved` | 带教老师在证据包上抽样确认，口径注册表 `review_status` 更新为 `reviewed` |

Expected: 每次运行都有版本、可追溯到字段与来源证据，产出非空工件或显式接受的警告；隐藏集指标与基线一并记录。

- [x] **Step 3: Verify the complete UI in a browser**

Invoke `browser-control`; verify desktop layout at 1440×900 and a narrow 1024×768 layout. Exercise login, session resume, formula/field confirmation, SSE progress, code display, result charts, report evidence, factor library, analysis, index export and admin role enforcement. Check browser console and failed network requests.

Expected: all visible states match backend snapshots; no secret appears in DOM, network responses or logs.

- [x] **Step 4: Write exact operating and security documentation**

`README.md` 写 uv/npm 环境与命令；`runbook.md` 写本地与 Compose 启停、迁移、备份恢复、**任务队列恢复与工件清理**；`demo-cases.md` 列输入与预期检查点；`security.md` 记录**四条**信任边界（含 B4 数据出境）、密钥位置、SQL 限制、manifest 执行与 Worker 隔离、上传限制、本地模式与残余原型风险。**不得包含真实主机、用户、密码或 API 密钥。**

`security.md` 与交付说明必须明写交付边界：

> 平台交付的是**机器可验证的正确性**（公式语义、点时可得性、字段真实性、执行隔离、结果量级、可复现性），**不是因子的业务有效性**。后者需要领域签字。验收状态为 `awaiting_domain_review` 的因子不得作为正式发布结果。

同时说明首期研报能力边界（文本型可复制正文；图片公式与扫描件转人工确认），避免把"跑通了"误读成"口径对了"。

- [x] **Step 5: Final secret and artifact checks**

Run:

```bash
uvx detect-secrets scan --all-files
git status --short
docker compose --env-file deploy/compose.env.example -f deploy/compose.yaml down
```

Expected: no high-confidence secret finding; only intended documentation changes remain; containers stop cleanly.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/runbook.md docs/demo-cases.md docs/security.md backend/data/demo/README.txt .env.example
git commit -m "docs: package factor platform delivery"
```

---

## Milestone Gates

> 2026-08-05 修订：新增任务已并入对应门槛。

- **End of Week 1:** Task 6 complete; CLI parses ten cases and blocks ambiguous inputs.
- **修订补课（Week 2 前置）:** Task 2.5、3.5、4.5、6.5 complete；契约、级联失效、数据出境边界与扩充案例集就位。Task 2.5 是 Task 12/13 的硬前置。
- **End of Week 2:** Task 16 complete（含 Task 9.5、10.5 与改写后的 10–15）; real Wind CLI P0 closes end to end. If not, freeze Tasks 23–29 until fixed.
- **Mid Week 3:** Task 20 browser acceptance complete; P0 is stable before report work expands.
- **End of Week 3:** Task 22 complete; Chinese and English text PDFs enter the shared workflow，且公式低置信度案例正确落到"需人工确认"。
- **End of Week 4:** Task 30 complete; P2 prototypes run within documented limits, Compose is reproducible, 隐藏验收集已运行并与基线对比。

## Explicit Cut Order if P0 Slips

Freeze only in this order, without weakening P0 safety or correctness:

1. advanced step animations;
2. admin UI polish while retaining enforced backend roles;
3. OCR breadth beyond the single bilingual fixture;
4. index weighting modes beyond equal and factor-score weighting;
5. analysis chart polish while retaining metric output;
6. 隐藏验收集的规模（可缩减数量，但**不可取消**）。

Never cut: 公式确认、字段确认、真实 Wind 数据、**时间口径与公告日处理**、**manifest 校验与验签**、Worker 密钥隔离、结果校验、端到端浏览器验证、**`disputed` 口径阻塞**、**数据出境边界 B4**。

> 原文"generated-code validation"随修订 #9 替换为"manifest 校验与验签"—— 生成代码不再是执行载体，对它做源码级校验已无意义。
