# Factor Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 1 名开发者、1 个月内交付稳定的 P0 自然语言因子闭环，以及可运行的 P1 研报和 P2 研究扩展原型。

**Architecture:** 使用 FastAPI 模块化单体、显式事件状态机、React 工作台和无密钥文件队列 Worker。LLM 只生成结构化 `FactorSpec`；Wind 取数由受信任后端执行，公式由确定性 DSL 编译并在隔离 Worker 中运行。

**Tech Stack:** Python 3.11、uv、FastAPI、Pydantic 2、SQLAlchemy 2、SQLite、pandas、PyArrow、PyMySQL、BM25、PyMuPDF、RapidOCR、React、TypeScript、Vite、Ant Design、TanStack Query、Monaco、Recharts、Docker Compose。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-08-04-factor-platform-design.md`。
- Python 必须使用 `uv run`；项目要求 `>=3.11,<3.13`。
- Node 要求 `>=22`，使用 `npm` 和锁文件 `package-lock.json`。
- P0 必须使用真实 Wind 数据，并稳定通过两个自然语言案例、一个阻塞性澄清案例和一个错误修复案例。
- P1/P2 是真实可运行原型，限制为代表性 PDF、日频 A 股、中小规模区间、线性组合、单机部署和管理员/研究员两角色。
- LLM 不得生成并直接执行任意 Python 或 SQL；可执行内容只能来自版本化 `ExecutionPlan + FormulaNode`。
- Worker 不持有数据库或 LLM 密钥；生产 Compose 中 `network_mode: none`。
- 财务数据没有公告日/市场可得日证据时不得进入因子检验。
- 每项任务先写可观察行为测试，再实现最小功能；每项任务单独提交，禁止 `git add .` 或 `git add -A`。
- 任何真实凭据不得写入源码、Notebook、日志、工件、测试夹具或提交历史。

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
│   ├── domain/{models.py,formula.py,errors.py}
│   ├── db/{base.py,models.py,repository.py}
│   ├── orchestration/{states.py,service.py}
│   ├── llm/{base.py,openai_compatible.py,router.py,prompts.py}
│   ├── factor/{parser.py,clarification.py,compiler.py,codegen.py}
│   ├── wind/{connection.py,adapter.py,capabilities.py,catalog.py,field_search.py,schema_verify.py,planner.py}
│   ├── execution/{ast_validator.py,job_store.py,runtime.py,worker.py}
│   ├── validation/{data.py,formula.py,result.py}
│   ├── reports/{pdf.py,ocr.py,extractor.py}
│   ├── analysis/metrics.py
│   ├── library/service.py
│   ├── indices/service.py
│   ├── auth/{service.py,dependencies.py}
│   └── api/{sessions.py,events.py,reports.py,analysis.py,library.py,indices.py,auth.py,health.py}
├── data/{golden_cases,wind_aliases.yaml,generated}
└── tests/
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

- [ ] **Step 1: Write settings tests**

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

- [ ] **Step 2: Scaffold the uv package and run the failing tests**

Run:

uv init --package --name factor-platform --python 3.11 --no-readme backend
uv add --project backend fastapi 'uvicorn[standard]' pydantic-settings sqlalchemy aiosqlite alembic httpx sse-starlette pymysql pandas numpy pyarrow rank-bm25 jieba jinja2 typer python-multipart pymupdf rapidocr-onnxruntime pillow pwdlib itsdangerous
uv add --project backend --dev pytest pytest-asyncio pytest-cov respx ruff mypy
uv run --project backend pytest backend/tests/test_settings.py -v
```

Expected: FAIL because `factor_platform.settings` does not exist.

- [ ] **Step 3: Implement typed settings and safe examples**

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

- [ ] **Step 4: Verify settings and scan the changed files**

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

**Files:**
- Create: `backend/src/factor_platform/domain/formula.py`
- Create: `backend/src/factor_platform/domain/models.py`
- Create: `backend/src/factor_platform/domain/errors.py`
- Create: `backend/tests/domain/test_models.py`

**Interfaces:**
- Produces: `FormulaNode`, `ResearchRequest`, `FactorSpec`, `DataRequirement`, `ClarificationQuestion`, `FieldCandidate`, `FieldSelection`, `ExecutionStep`, `ExecutionPlan`, `ExecutionResult`, `ValidationReport`, `ReportEvidence`, `FactorArtifact`, `SessionSnapshot`.
- All later modules must import these types rather than defining local dictionaries.

- [ ] **Step 1: Write model contract tests**

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

- [ ] **Step 2: Run the tests and observe missing contracts**

Run: `uv run --project backend pytest backend/tests/domain/test_models.py -v`  
Expected: FAIL with import errors.

- [ ] **Step 3: Implement strict Pydantic models**

`FormulaNode` uses `type: Literal["variable", "literal", "call"]`, validates mutually exclusive `name`, `value`, and `op`, and permits only these operators:

```python
FormulaOperator = Literal[
    "add", "subtract", "multiply", "divide", "negative", "log",
    "rank", "zscore", "winsorize", "rolling_return", "rolling_std",
    "rolling_mean", "fillna", "industry_neutralize",
]
```

Use enums for asset type, frequency, factor direction, time role, query shape, execution status, and error category. Make every externally persisted model carry `schema_version: int = 1`; `FactorSpec` additionally carries `version: int = 1` and `source_evidence: list[ReportEvidence]`.

- [ ] **Step 4: Run contract tests**

Run: `uv run --project backend pytest backend/tests/domain/test_models.py -v`  
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/domain/formula.py backend/src/factor_platform/domain/models.py backend/src/factor_platform/domain/errors.py backend/tests/domain/test_models.py
git commit -m "feat: define factor workflow contracts"
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

- [ ] **Step 1: Test legal transitions and stale writes**

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

- [ ] **Step 2: Run failing state and repository tests**

Run: `uv run --project backend pytest backend/tests/orchestration/test_states.py backend/tests/db/test_repository.py -v`  
Expected: FAIL because persistence and state modules are absent.

- [ ] **Step 3: Implement state transition table and append-only records**

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

- [ ] **Step 4: Run migrations and tests**

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

- [ ] **Step 1: Write provider parsing and fallback tests**

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

- [ ] **Step 2: Run failing provider tests**

Run: `uv run --project backend pytest backend/tests/llm -v`  
Expected: FAIL with missing provider modules.

- [ ] **Step 3: Implement the protocol, HTTP adapter and router**

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

- [ ] **Step 4: Run provider tests**

Run: `uv run --project backend pytest backend/tests/llm -v`  
Expected: JSON validation, error mapping and fallback tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/llm backend/tests/llm
git commit -m "feat: add structured Kimi provider routing"
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

- [ ] **Step 1: Write ambiguity behavior tests**

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

- [ ] **Step 2: Run failing parser tests**

Run: `uv run --project backend pytest backend/tests/factor/test_parser.py backend/tests/factor/test_clarification.py -v`  
Expected: FAIL because parser and rule engine are absent.

- [ ] **Step 3: Implement prompt and rule layers**

The parser asks for all `FactorSpec` fields and passes the model schema. The rule engine independently checks ambiguous profitability, valuation, recency window, financial period, growth definition, subfactor weights, direction, universe, rebalance frequency and material neutralization choices. Non-blocking defaults are explicit objects for pre-adjusted prices, ST/suspension exclusion, winsorization, standardization and missing values.

- [ ] **Step 4: Run parser tests**

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

- [ ] **Step 1: Define the exact case matrix**

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

- [ ] **Step 2: Write the loader test and run it red**

```python
def test_all_golden_cases_have_complete_expected_contracts() -> None:
    cases = load_golden_cases()
    assert len(cases) == 10
    assert {case.case_id for case in cases} == EXPECTED_CASE_IDS
    assert all(case.expected_formula_ast and case.expected_tool_names for case in cases)
```

Run: `uv run --project backend pytest backend/tests/golden/test_cases.py -v`  
Expected: FAIL because files and loader are absent.

- [ ] **Step 3: Add all ten JSON fixtures and the CLI**

The CLI loads one case, calls the parser, runs clarification rules, prints validated JSON, and exits nonzero when actual blocking question IDs differ from the fixture.

- [ ] **Step 4: Run golden tests and one CLI smoke test**

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

- [ ] **Step 1: Test lazy connection and adapter compatibility**

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

- [ ] **Step 2: Move the module and run tests red**

Run:

```bash
mkdir -p backend/src/factor_platform/wind
touch backend/src/factor_platform/wind/__init__.py
git mv rq_wind_replica.py backend/src/factor_platform/wind/adapter.py
uv run --project backend pytest backend/tests/wind/test_connection.py backend/tests/wind/test_adapter_contract.py -v
```

Expected: FAIL until global literal config is replaced.

- [ ] **Step 3: Inject a connection factory without rewriting data functions**

`connection.py` owns config extraction, retries and connection creation. `adapter.query_df` calls the injected singleton factory lazily. Preserve function signatures, capability registry, parameterized values and generic-query identifier checks.

- [ ] **Step 4: Run adapter tests and a real connection smoke test**

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

- [ ] **Step 1: Write registry normalization tests**

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

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_capabilities.py -v`  
Expected: FAIL with missing catalog.

- [ ] **Step 3: Implement immutable tool specs**

Normalize purpose, asset types, parameters, exact outputs, semantic outputs, constraints, planner and examples. Exclude lifecycle/expression entries from direct data tools while preserving them for code generation dependencies.

- [ ] **Step 4: Run tests**

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

- [ ] **Step 1: Write parser and ranking tests**

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

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_catalog.py backend/tests/wind/test_field_search.py -v`  
Expected: FAIL because catalog modules are absent.

- [ ] **Step 3: Implement normalization and the first alias set**

Aliases must cover open/high/low/close, pre/post adjustment, volume, turnover, total/free-float market cap, PE/PB/PS, ROE/ROA, revenue, net profit, operating cash flow, announcement date, report period, industry, index components, ST and suspension. Tokenize Chinese with `jieba`, normalize English snake/camel case, and combine exact alias score with BM25 only after asset/frequency filters.

- [ ] **Step 4: Build the real catalog and run tests**

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

### Task 10: Verify candidates against live Schema and bounded samples

**Files:**
- Create: `backend/src/factor_platform/wind/schema_verify.py`
- Create: `backend/tests/wind/test_schema_verify.py`

**Interfaces:**
- Produces: `SchemaVerifier.verify(candidate, sample_request) -> VerifiedFieldCandidate`.
- Uses only parameterized, bounded queries through the trusted connection layer.

- [ ] **Step 1: Test unknown fields and bounded sampling**

```python
async def test_unknown_column_is_rejected(fake_query) -> None:
    fake_query.schema_columns = {"s_info_windcode", "trade_dt"}
    result = await verifier.verify(candidate(field="s_dq_close"), sample_request())
    assert result.schema_valid is False
    assert result.rejection_reason == "column_not_found"


async def test_sample_is_bounded(fake_query) -> None:
    await verifier.verify(candidate(field="s_dq_close"), sample_request(codes=10, days=30))
    assert fake_query.last_sample_codes == 3
    assert fake_query.last_sample_days <= 5
```

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_schema_verify.py -v`  
Expected: FAIL because verifier is absent.

- [ ] **Step 3: Implement verification**

Check table, column type, code field and time fields through `information_schema`; fetch at most 3 securities × 5 dates; calculate row count, non-null ratio, duplicate-key count, min/max and sample values. Never interpolate user values or unvalidated identifiers.

- [ ] **Step 4: Run tests and one real candidate verification**

Run:

```bash
uv run --project backend pytest backend/tests/wind/test_schema_verify.py -v
uv run --project backend factor-platform verify-field ashareeodprices s_dq_close 600519.SH 2026-07-01 2026-07-05
```

Expected: tests PASS; real result reports valid Schema and bounded sample statistics.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/schema_verify.py backend/tests/wind/test_schema_verify.py
git commit -m "feat: verify Wind field candidates"
```

### Task 11: Plan registered functions and generic Wind queries

**Files:**
- Create: `backend/src/factor_platform/wind/planner.py`
- Create: `backend/tests/wind/test_planner.py`

**Interfaces:**
- Consumes: confirmed `FactorSpec`, confirmed `FieldCandidate` values, `CapabilityCatalog`.
- Produces: `WindPlanner.plan() -> ExecutionPlan`.

- [ ] **Step 1: Test function priority and financial point-in-time rules**

```python
def test_prices_use_registered_get_price(planner) -> None:
    plan = planner.plan(momentum_spec(), confirmed_close())
    assert plan.steps[0].tool == "wind.index_components"
    assert plan.steps[1].tool == "wind.get_price"


def test_financial_plan_requires_announcement_date(planner) -> None:
    with pytest.raises(PlanningError, match="announcement_date"):
        planner.plan(roe_spec(), confirmed_roe_without_announcement_date())
```

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/wind/test_planner.py -v`  
Expected: FAIL because planner is absent.

- [ ] **Step 3: Implement deterministic planning priority**

For each variable: exact capability → confirmed generic field. Add historical `index_components` before data calls, calculate warm-up start from maximum rolling window, add ST/suspension filters, preserve report period and announcement date, and reject unconfirmed fields. Generic plans can use only the six shapes already allowed by the adapter.

- [ ] **Step 4: Run planner and golden-case tests**

Run: `uv run --project backend pytest backend/tests/wind/test_planner.py backend/tests/golden/test_cases.py -v`  
Expected: tests PASS; expected tool names match all applicable golden cases.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/planner.py backend/tests/wind/test_planner.py
git commit -m "feat: plan safe Wind data retrieval"
```

### Task 12: Compile the formula DSL against aligned DataFrames

**Files:**
- Create: `backend/src/factor_platform/factor/compiler.py`
- Create: `backend/tests/factor/test_compiler.py`

**Interfaces:**
- Produces: `FormulaCompiler.evaluate(node, variables, context) -> DataFrame` where index is date and columns are order book IDs.

- [ ] **Step 1: Write operator semantics tests**

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
```

- [ ] **Step 2: Run compiler tests red**

Run: `uv run --project backend pytest backend/tests/factor/test_compiler.py -v`  
Expected: FAIL because compiler is absent.

- [ ] **Step 3: Implement every registered operator explicitly**

Use pandas operations with fixed axis semantics. Rolling operators require positive integer windows and `min_periods=window`; `rank` and `zscore` operate by date across columns; `winsorize` uses per-date quantiles; `industry_neutralize` subtracts per-date industry means. Do not use `eval` or dynamic imports.

- [ ] **Step 4: Run compiler tests**

Run: `uv run --project backend pytest backend/tests/factor/test_compiler.py -v`  
Expected: all operator, NaN, zero-division, alignment and future-data tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/factor/compiler.py backend/tests/factor/test_compiler.py
git commit -m "feat: compile safe factor formulas"
```

### Task 13: Generate deterministic Python and reject unsafe AST

**Files:**
- Create: `backend/src/factor_platform/factor/codegen.py`
- Create: `backend/src/factor_platform/execution/ast_validator.py`
- Create: `backend/tests/factor/test_codegen.py`
- Create: `backend/tests/execution/test_ast_validator.py`

**Interfaces:**
- Produces: `CodeGenerator.generate(plan, spec) -> GeneratedProgram`, `AstValidator.validate(source)`.
- Generated source may import only `json`, `pathlib` and `factor_platform.execution.runtime`, then call `run_program` once.

- [ ] **Step 1: Write determinism and rejection tests**

```python
def test_same_inputs_generate_identical_source() -> None:
    first = generator.generate(plan(), spec())
    second = generator.generate(plan(), spec())
    assert first.source == second.source
    assert first.sha256 == second.sha256


@pytest.mark.parametrize("source", [
    "import os\nos.system('id')",
    "open('/etc/passwd').read()",
    "eval('1 + 1')",
    "__import__('socket').socket()",
])
def test_dangerous_source_is_rejected(source: str) -> None:
    with pytest.raises(UnsafeProgramError):
        AstValidator().validate(source)
```

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/factor/test_codegen.py backend/tests/execution/test_ast_validator.py -v`  
Expected: FAIL because generator and validator are absent.

- [ ] **Step 3: Implement a fixed program template**

Generated source embeds only canonical JSON for `FactorSpec` and `ExecutionPlan`, reads input/output paths from command-line arguments, and calls `run_program(manifest, input_dir, output_path)`. Validator checks imports, attribute chains, calls, names and literal size; any node outside the whitelist fails closed.

- [ ] **Step 4: Run tests**

Run: `uv run --project backend pytest backend/tests/factor/test_codegen.py backend/tests/execution/test_ast_validator.py -v`  
Expected: deterministic code passes; all dangerous examples fail.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/factor/codegen.py backend/src/factor_platform/execution/ast_validator.py backend/tests/factor/test_codegen.py backend/tests/execution/test_ast_validator.py
git commit -m "feat: generate and validate factor programs"
```

### Task 14: Add the no-secret filesystem job queue and Worker runtime

**Files:**
- Create: `backend/src/factor_platform/execution/job_store.py`
- Create: `backend/src/factor_platform/execution/runtime.py`
- Create: `backend/src/factor_platform/execution/worker.py`
- Create: `backend/tests/execution/test_job_store.py`
- Create: `backend/tests/execution/test_worker.py`

**Interfaces:**
- Produces: `JobStore.enqueue()`, `claim_next()`, `complete()`, `fail()` and CLI `factor-worker run-once|serve`.
- Worker input is an immutable manifest plus Parquet files; output is result Parquet and result JSON.

- [ ] **Step 1: Test atomic claim and secret-free Worker execution**

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
```

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/execution/test_job_store.py backend/tests/execution/test_worker.py -v`  
Expected: FAIL because queue and Worker are absent.

- [ ] **Step 3: Implement atomic directories and resource controls**

Create `pending`, `running`, `completed`, `failed` directories. Enqueue writes into a temporary directory and atomically renames it. Claim uses atomic rename. Worker builds a clean environment from `Settings.worker_environment`, applies CPU/memory/file-size limits, validates generated AST, executes the program, and writes a structured result before moving the job.

- [ ] **Step 4: Run unit tests and a real one-shot job**

Run:

```bash
uv run --project backend pytest backend/tests/execution/test_job_store.py backend/tests/execution/test_worker.py -v
uv run --project backend factor-worker run-once --job-root data/runtime/jobs
```

Expected: tests PASS; one fixture job moves from `pending` to `completed` and produces Parquet.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/execution/job_store.py backend/src/factor_platform/execution/runtime.py backend/src/factor_platform/execution/worker.py backend/tests/execution/test_job_store.py backend/tests/execution/test_worker.py
git commit -m "feat: execute factor jobs in isolated worker"
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

- [ ] **Step 1: Test plausible failures**

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
```

- [ ] **Step 2: Run tests red**

Run: `uv run --project backend pytest backend/tests/validation -v`  
Expected: FAIL because validators are absent.

- [ ] **Step 3: Implement exact validation rules**

Data: coverage, duplicate keys, missing rate, units, all-zero/constant, historical membership, announcement date and adjustment metadata. Formula: axis semantics, window, direction, weights, divide-by-zero, future price, financial availability and duplicate standardization. Result: distribution, daily coverage, Top/Bottom samples, duration, input rows and warnings.

- [ ] **Step 4: Run validation tests**

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
- Produces: `WorkflowService.create_session()`, `submit_message()`, `confirm_formula()`, `search_fields()`, `confirm_fields()`, `generate_code()`, `execute()`; every mutation returns the new `SessionSnapshot`.
- CLI command: `factor-platform run-case CASE_ID --real-wind`.

- [ ] **Step 1: Write a fake-adapter end-to-end test**

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
    session = await workflow.generate_code(session.id, expected_version=session.version)
    result = await workflow.execute(session.id, expected_version=session.version)
    assert result.state == "completed"
    assert result.artifact_uri.endswith("factor.parquet")
```

- [ ] **Step 2: Run the E2E test red**

Run: `uv run --project backend pytest backend/tests/orchestration/test_service.py backend/tests/e2e/test_cli_p0.py -v`  
Expected: FAIL because the service is absent.

- [ ] **Step 3: Implement orchestration with explicit commits between external effects**

Each public method checks state/version, emits a start event, performs one external operation, validates its result, then emits a completion or failure event. Never hold a SQLite transaction while calling the LLM, Wind or Worker.

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

- [ ] **Step 1: Write API contract tests**

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

- [ ] **Step 2: Run API tests red**

Run: `uv run --project backend pytest backend/tests/api/test_sessions.py backend/tests/api/test_events.py -v`  
Expected: FAIL because app and routers are absent.

- [ ] **Step 3: Implement thin handlers and SSE event IDs**

Map domain exceptions to stable JSON error codes. SSE reads persisted events after `Last-Event-ID`, then watches for new records with a bounded polling interval and heartbeat. `GET /health` reports database, Wind, active LLM provider, job queue and version without exposing credentials.

- [ ] **Step 4: Run API tests and launch smoke**

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

- [ ] **Step 1: Scaffold and write the shell test**

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

- [ ] **Step 2: Run the shell test red**

Run: `npm --prefix frontend test -- --run src/app/AppShell.test.tsx`  
Expected: FAIL because shell/routes are absent.

- [ ] **Step 3: Export OpenAPI and implement the typed client**

Add scripts `generate:api`, `test`, `typecheck`, and `build`. `apiClient` prepends `/api`, parses the stable error envelope, and sends `expected_version` on every mutation. Routes include workspace, reports, library, analysis, indices and admin.

- [ ] **Step 4: Generate types and verify frontend foundation**

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

- [ ] **Step 1: Test state-to-step mapping**

```ts
it("maps field confirmation to step six", () => {
  expect(toWorkflowView({ state: "waiting_field_confirmation", version: 8 }).activeStep).toBe(6)
})

it("keeps failed execution on the execution step", () => {
  expect(toWorkflowView({ state: "failed", failed_stage: "executing", version: 10 }).activeStep).toBe(9)
})
```

- [ ] **Step 2: Run view tests red**

Run: `npm --prefix frontend test -- --run src/features/workspace/sessionView.test.ts`  
Expected: FAIL because view mapping is absent.

- [ ] **Step 3: Implement the reference-layout shell**

Top controls: factor type, universe, start/end date, provider/model status, Wind switch and reset. Left column reserves formula/code/result tabs. Right column renders workflow steps, status banner, event log and research input. Reloading `/workspace/:id` fetches the snapshot before opening SSE.

- [ ] **Step 4: Run tests and type-check**

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

- [ ] **Step 1: Test that explicit confirmation payloads include the visible version**

```tsx
it("submits selected fields with current version", async () => {
  render(<FieldCandidateTable sessionVersion={8} candidates={candidates} onConfirm={onConfirm} />)
  await user.click(screen.getByRole("checkbox", { name: /后复权收盘价/ }))
  await user.click(screen.getByRole("button", { name: "确认字段" }))
  expect(onConfirm).toHaveBeenCalledWith({ expected_version: 8, selections: [expectedSelection] })
})
```

- [ ] **Step 2: Run confirmation tests red**

Run: `npm --prefix frontend test -- --run src/features/workspace/confirmations.test.tsx`  
Expected: FAIL because components are absent.

- [ ] **Step 3: Implement cards and panes**

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

- [ ] **Step 1: Test page evidence and file limits**

```python
def test_text_pdf_preserves_page_numbers(parser) -> None:
    report = parser.extract(FIXTURES / "text_zh.pdf")
    assert report.pages[0].page_number == 1
    assert "净资产收益率" in report.pages[0].text


def test_oversized_pdf_is_rejected(parser, oversized_pdf) -> None:
    with pytest.raises(ReportLimitError, match="file_size"):
        parser.extract(oversized_pdf)
```

- [ ] **Step 2: Run PDF tests red**

Run: `uv run --project backend pytest backend/tests/reports/test_pdf.py -v`  
Expected: FAIL because parser is absent.

- [ ] **Step 3: Implement MIME, size, page and timeout limits**

Validate `%PDF-` signature and MIME, cap the prototype at 50 MiB and 200 pages, reject encrypted PDFs without a supplied password, extract block text with PyMuPDF, normalize whitespace while retaining page and bounding box evidence, and never execute embedded content.

- [ ] **Step 4: Run PDF tests**

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

- [ ] **Step 1: Test evidence preservation and unknown values**

```python
async def test_report_extraction_never_invents_missing_rebalance(fake_llm, extractor) -> None:
    spec = await extractor.extract(report_without_rebalance())
    assert spec.rebalance_frequency is None
    assert "rebalance_frequency" in {item.field for item in spec.ambiguities}
    assert spec.source_evidence[0].page_number == 3
```

Frontend test verifies the upload response displays source quote and page number before “进入因子工作流” is enabled.

- [ ] **Step 2: Run backend and frontend tests red**

Run:

```bash
uv run --project backend pytest backend/tests/reports/test_extractor.py backend/tests/api/test_reports.py -v
npm --prefix frontend test -- --run src/features/reports/ReportsPage.test.tsx
```

Expected: FAIL because extractor/API/UI are absent.

- [ ] **Step 3: Implement retrieval-assisted extraction**

Rank report blocks for formula/variable/sample/preprocessing terms, send bounded blocks with page IDs to the provider, require evidence IDs in every extracted variable, and reject evidence IDs not present in the input. Upload API stores the file under a generated artifact ID, not the original filename.

- [ ] **Step 4: Run tests and browser-smoke one Chinese and one English report**

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

- [ ] **Step 1: Test OCR routing and page identity**

```python
def test_scanned_page_uses_ocr_and_keeps_page_number(parser, fake_ocr) -> None:
    report = parser.extract(FIXTURES / "scanned_bilingual.pdf")
    assert fake_ocr.calls == [1]
    assert report.pages[0].page_number == 1
    assert report.pages[0].source == "ocr"
```

- [ ] **Step 2: Run OCR tests red**

Run: `uv run --project backend pytest backend/tests/reports/test_ocr.py -v`  
Expected: FAIL because OCR engine is absent.

- [ ] **Step 3: Implement RapidOCR adapter and limits**

Render at 200 DPI, process at most 50 OCR pages per report, preserve bounding boxes and confidence, merge lines in reading order, and mark confidence below 0.60 for user review. Do not OCR pages whose extracted text exceeds the density threshold.

- [ ] **Step 4: Run tests and real OCR smoke**

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

- [ ] **Step 1: Test repair eligibility and cap**

```python
def test_field_not_found_is_not_repaired_as_formula(repair) -> None:
    decision = repair.classify(error(code="unknown_wind_field"))
    assert decision.repairable is False


async def test_third_repair_is_rejected(repair_service) -> None:
    with pytest.raises(RepairLimitError):
        await repair_service.propose(session_with_two_repairs(), formula_error())
```

- [ ] **Step 2: Run repair tests red**

Run: `uv run --project backend pytest backend/tests/orchestration/test_repair.py -v`  
Expected: FAIL because repair service is absent.

- [ ] **Step 3: Implement classified repair versions**

Only invalid parameter, formula type, divide-by-zero policy and alignment errors are repairable. LLM receives the confirmed spec, structured error and allowed operators; output must be a new `FactorSpec` version. The user must confirm the repaired formula before execution.

- [ ] **Step 4: Run repair tests and one browser repair scenario**

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

- [ ] **Step 1: Write deterministic metric tests**

```python
def test_perfect_factor_has_unit_ic() -> None:
    result = analyze_factor(perfect_factor(), matching_forward_returns(), groups=2)
    assert result.ic.mean == pytest.approx(1.0)
    assert result.rank_ic.mean == pytest.approx(1.0)


def test_turnover_uses_membership_changes() -> None:
    result = analyze_factor(rotating_factor(), forward_returns(), groups=2)
    assert result.turnover.iloc[1] == pytest.approx(0.5)
```

- [ ] **Step 2: Run metric tests red**

Run: `uv run --project backend pytest backend/tests/analysis/test_metrics.py -v`  
Expected: FAIL because analysis module is absent.

- [ ] **Step 3: Implement no-lookahead metrics**

Shift forward returns after factor observation, align by date/code, compute Pearson IC, Spearman Rank IC, quantile equal-weight returns, long-short spread, coverage and one-way membership turnover. Reject dates with insufficient cross-sectional observations.

- [ ] **Step 4: Run tests and render analysis UI**

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

- [ ] **Step 1: Test immutability and artifact hashes**

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

- [ ] **Step 2: Run library tests red**

Run: `uv run --project backend pytest backend/tests/library/test_service.py -v`  
Expected: FAIL because library is absent.

- [ ] **Step 3: Implement metadata rows and Parquet ownership**

Store factor identity, version, source session, spec JSON, field mappings, plan, code hash, result hash, artifact relative paths, creator and timestamps. Publishing a changed completed session creates version `N+1`; files are copied into a versioned immutable directory.

- [ ] **Step 4: Run migration, tests and UI type-check**

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

- [ ] **Step 1: Test weight invariants and rebalance output**

```python
def test_composite_normalizes_directions_before_weighting() -> None:
    result = combine_factors([quality(), valuation()], [0.6, 0.4], [1, -1])
    assert result.loc[DATE, "A"] > result.loc[DATE, "B"]


def test_index_weights_sum_to_one_per_rebalance() -> None:
    artifact = generate_index(composite(), IndexRule(top_n=2, weighting="equal", rebalance="monthly"))
    assert artifact.weights.groupby("date")["weight"].sum().eq(1.0).all()
```

- [ ] **Step 2: Run index tests red**

Run: `uv run --project backend pytest backend/tests/indices/test_service.py -v`  
Expected: FAIL because index service is absent.

- [ ] **Step 3: Implement the prototype boundaries**

Inputs must be completed library versions. Align dates/universes, orient direction, z-score each input, reject non-finite weights and normalize weights to one. Index rules support top N, equal or factor-score weights, monthly/weekly rebalance and CSV export; no live order execution.

- [ ] **Step 4: Run tests and export one index artifact**

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

- [ ] **Step 1: Test role enforcement and cookie properties**

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

- [ ] **Step 2: Run auth tests red**

Run: `uv run --project backend pytest backend/tests/auth/test_auth.py -v`  
Expected: FAIL because auth is absent.

- [ ] **Step 3: Implement password hashing and authorization**

Hash with Argon2 through `pwdlib`; sign random session IDs with `itsdangerous`; store only session hashes and expiry; rotate cookie on login; require CSRF token for cookie-authenticated mutations. Admin manages users/providers and sees provider health, call count, token cost and failure rate; researcher manages own sessions, reports, factors, analyses and indices.

- [ ] **Step 4: Run migration and auth tests**

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

- [ ] **Step 1: Write deployment contract test**

```python
def test_worker_has_no_network_and_no_secret_environment() -> None:
    compose = load_compose("deploy/compose.yaml")
    worker = compose["services"]["worker"]
    assert worker["network_mode"] == "none"
    assert "WIND_PASSWORD" not in worker.get("environment", {})
    assert "KIMI_METERED_API_KEY" not in worker.get("environment", {})
```

- [ ] **Step 2: Run deployment test red**

Run: `uv run --project backend pytest backend/tests/deploy/test_compose_contract.py -v`  
Expected: FAIL because Compose is absent.

- [ ] **Step 3: Implement images, health checks and volumes**

Backend binds inside the Compose network, frontend exposes the only host port, Nginx proxies `/api` and SSE with buffering disabled, Worker polls the shared filesystem queue with no network, SQLite/artifacts/jobs use named volumes, and only backend receives Wind/LLM secrets.

- [ ] **Step 4: Build and smoke the deployment**

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

- [ ] **Step 1: Run backend and frontend quality gates**

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

- [ ] **Step 2: Execute the exact real-data acceptance matrix**

Run and record artifact IDs for:

1. momentum factor on historical沪深300 members;
2. quality/value factor with blocking definition confirmation;
3. one intentionally invalid rolling window repaired once;
4. one Chinese text report;
5. one English text report;
6. one bilingual scanned report;
7. IC/Rank IC/group return/turnover on a completed factor;
8. a two-factor composite and generated monthly index weights.

Expected: every run is versioned, traceable to fields and source evidence, and produces non-empty artifacts or an explicitly accepted warning.

- [ ] **Step 3: Verify the complete UI in a browser**

Invoke `browser-control`; verify desktop layout at 1440×900 and a narrow 1024×768 layout. Exercise login, session resume, formula/field confirmation, SSE progress, code display, result charts, report evidence, factor library, analysis, index export and admin role enforcement. Check browser console and failed network requests.

Expected: all visible states match backend snapshots; no secret appears in DOM, network responses or logs.

- [ ] **Step 4: Write exact operating and security documentation**

`README.md` contains uv/npm setup and commands; `runbook.md` contains local and Compose start/stop, migration, backup and recovery; `demo-cases.md` lists inputs and expected checkpoints; `security.md` documents trust boundaries, secret locations, SQL restrictions, Worker isolation, upload limits and residual prototype risks. Do not include live hosts, users, passwords or API keys.

- [ ] **Step 5: Final secret and artifact checks**

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

- **End of Week 1:** Task 6 complete; CLI parses ten cases and blocks ambiguous inputs.
- **End of Week 2:** Task 16 complete; real Wind CLI P0 closes end to end. If not, freeze Tasks 23–29 until fixed.
- **Mid Week 3:** Task 20 browser acceptance complete; P0 is stable before report work expands.
- **End of Week 3:** Task 22 complete; Chinese and English text PDFs enter the shared workflow.
- **End of Week 4:** Task 30 complete; P2 prototypes run within documented limits and Compose is reproducible.

## Explicit Cut Order if P0 Slips

Freeze only in this order, without weakening P0 safety or correctness:

1. advanced step animations;
2. admin UI polish while retaining enforced backend roles;
3. OCR breadth beyond the single bilingual fixture;
4. index weighting modes beyond equal and factor-score weighting;
5. analysis chart polish while retaining metric output.

Never cut formula confirmation, field confirmation, real Wind data, announcement-date handling, generated-code validation, Worker secret isolation, result validation or end-to-end browser proof.
