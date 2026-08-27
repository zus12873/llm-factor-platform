# Remaining Offline Todos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining items this team can finish without company network, Wind/Kimi credentials, Docker, or business-signoff: dictionary-test bound, handoff notebook copy, three price-semantic residuals, offline GitHub Actions, a full offline gate rerun, and a reports/library browser regression.

**Architecture:** Keep the existing field-search → confirmation → planner → `get_price` path. Ranking still must not rewrite aliases or skip confirmation. CI runs only offline gates on GitHub-hosted runners; it must never receive Wind/Kimi secrets or talk to the company MySQL.

**Tech Stack:** pytest, Vitest, ruff, mypy, GitHub Actions (`uv` + Node 22), Playwright/browser-control for the UI regression.

**Baseline:** GitHub `main` at `69f7b84` (hidden cases archived). Local = remote.

## Global Constraints

- No arbitrary SQL, no `eval` / `exec`, no silent `FakeLLMProvider` fallback.
- B4: never send a full report body or Wind raw data to an external model.
- Worker remains `network_mode: none` and holds only `MANIFEST_SIGNING_KEY` (verify, not sign).
- Formula confirmation and field confirmation cannot be skipped.
- `disputed` metrics cannot be published; do **not** change `metric_definitions.yaml` `review_status` in this plan.
- Do not commit `.env`, Wind credentials, API keys, parquet, `backend/data/generated/`, or `windquery/`.
- Do not claim Compose smoke, real Wind, or real Kimi passed unless those commands ran.
- Python via `uv run --project backend`. Frontend: `npm --prefix frontend`.
- Public GitHub Actions: no Wind host, no Kimi keys, no self-hosted runner in this plan.

## Out of this plan (external / not developer-decidable)

Recorded so implementers do not “helpfully” do them:

- YAML 口径改为 `reviewed`（须带教老师书面授权 + `PROFIT_GROWTH_YOY` 最终字段）。
- Docker/Compose 实机冒烟（须有 Docker daemon 的机器）。
- P1/P2 后真实 Wind / 真实 Kimi / 老师现场验收。
- Self-hosted runner 跑真实组件。
- 新建独立盲测集冒充 2026-08-10 原隐藏验收。
- 轮换 Wind 密码、开通 VPN、增加 Kimi 额度。

---

## File Map

**Create**

- `.github/workflows/offline-gates.yml`
- `docs/acceptance/2026-08-27-offline-gates.md` (filled only after Task 7 actually runs)
- `docs/acceptance/2026-08-27-ui-regression.md` (filled only after Task 8 actually runs)

**Modify**

- `backend/tests/wind/test_metadata_catalog.py` — drop `<= 9000`
- `handoff.md` — Notebook 已脱敏
- `backend/src/factor_platform/wind/price_semantics.py` — reserve preferred + also_list before `limit`
- `backend/tests/wind/test_price_semantics.py`
- `backend/src/factor_platform/wind/adapter.py` — map 前复权 close
- `backend/src/factor_platform/wind/planner.py` — volume/limit not inherit price adjust
- `backend/tests/wind/test_planner.py`
- `README.md` / `docs/使用说明.md` — after Task 7, one test-bar paragraph

**Do not touch**

- `backend/data/metric_definitions.yaml` review statuses
- `deploy/compose.yaml` worker `network_mode`
- `backend/data/generated/`, `windquery/`

---

### Task 1: Dictionary size test — floor and quality, no historic ceiling

**Files:**
- Modify: `backend/tests/wind/test_metadata_catalog.py` (`test_real_dictionary_yields_metadata_for_thousands_of_fields`)
- Test: same file

**Interfaces:**
- Consumes: `DictionaryBuilder(REAL_DICTIONARY).build() -> list[FieldMetadata]`
- Produces: assertion with a **minimum** count, required fields present, parseable name/unit/frequency; **no maximum**.

- [ ] **Step 1: Write the failing replacement assertions** (keep `@requires_real_dictionary`)

```python
@requires_real_dictionary
def test_real_dictionary_yields_metadata_for_thousands_of_fields() -> None:
    """A real WDS dump is large; never cap how many fields it may contain."""
    records = DictionaryBuilder(REAL_DICTIONARY).build()
    assert len(records) >= 6000, f"got {len(records)} records"
    by_key = {(r.table.lower(), r.field.lower()): r for r in records}
    assert ("ashareeodprices", "s_dq_close") in by_key
    assert ("ashareeodprices", "s_dq_adjclose") in by_key
    close = by_key[("ashareeodprices", "s_dq_close")]
    assert close.metadata_source == "WDS"
    assert close.name_zh
```

Delete `<= 9000`. Keep the existing `assert all(record.metadata_source == "WDS")` or fold it in.

- [ ] **Step 2: Run it**

```bash
uv run --project backend pytest backend/tests/wind/test_metadata_catalog.py::test_real_dictionary_yields_metadata_for_thousands_of_fields -v
```

Expected: PASS on a machine with `windquery/.../wind字典` (12126 >= 6000). If the dictionary is absent: SKIP, same as today. Do not skip when the dictionary is present.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/wind/test_metadata_catalog.py
git commit -m "test: drop historic ceiling on Wind dictionary field count"
```

---

### Task 2: Handoff Notebook copy

**Files:**
- Modify: `handoff.md` Git 提交前安全提示

**Interfaces:** none.

- [ ] **Step 1: Replace the stale sentence**

Old: `Wind取数尝试.ipynb 含非空 Wind 连接配置`

New (exact):

```markdown
- 仓库跟踪的 `Wind取数尝试.ipynb` **已脱敏**：连接参数来自 `os.environ.get("WIND_*")`，非空 host/password/user 字面量为 0。禁止再写入明文。该路径仍在 `.gitignore` 中，避免把填了凭据的本地副本重新纳入提交。
```

- [ ] **Step 2: Commit**

```bash
git add handoff.md
git commit -m "docs: note Wind notebook is env-based and contains no literal credentials"
```

---

### Task 3: Reserve adj + raw close before applying `limit`

**Files:**
- Modify: `backend/src/factor_platform/wind/price_semantics.py` (`apply_price_semantics`)
- Modify: `backend/tests/wind/test_price_semantics.py`

**Interfaces:**
- Consumes: `PriceIntent.preferred_field`, `PriceIntent.also_list`, `limit: int | None`
- Produces: same function signature; when `intent.preferred_field` is set, the returned list of length `limit` **must still contain** `preferred_field` and every `also_list` field that was injected or already present, unless `limit` is smaller than that reserved set (then keep the reserved set and do not pad).

- [ ] **Step 1: Failing test** (tiny in-memory search / fake candidates, no licensed catalog)

```python
def test_limit_does_not_drop_unadjusted_close_from_a_crowded_pool() -> None:
    req = DataRequirement(logical_name="close", meaning="动量")
    crowded = [
        FieldCandidate(table="ashareeodprices", field=f"s_dq_other{i}")
        for i in range(8)
    ] + [
        FieldCandidate(table="ashareeodprices", field="s_dq_adjclose"),
        FieldCandidate(table="ashareeodprices", field="s_dq_close"),
    ]
    out = apply_price_semantics(crowded, req, use_adjusted_price=True, limit=5)
    fields = [row.field for row in out]
    assert "s_dq_adjclose" in fields
    assert "s_dq_close" in fields
    assert len(out) <= 5 or set(fields) >= {"s_dq_adjclose", "s_dq_close"}
```

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/wind/test_price_semantics.py::test_limit_does_not_drop_unadjusted_close_from_a_crowded_pool -v
```

Expected: FAIL — `s_dq_close` missing after `[:5]` because it sits at the tail of `rest`.

- [ ] **Step 3: Implementation**

After labelling and boosting `preferred`, partition:

```python
reserved_names = tuple(
    n for n in (intent.preferred_field, *intent.also_list) if n is not None
)
reserved = [row for row in labelled if row.field in reserved_names]
others = [row for row in labelled if row.field not in reserved_names]
if limit is None:
    return reserved + others
room = max(limit - len(reserved), 0)
return reserved + others[:room]
```

Do not drop a reserved field to satisfy `len == limit`. If `limit=1` and two reserved names exist, return both reserved rows (the floor is the reserved set).

- [ ] **Step 4: Run green** including existing price-semantics tests.

```bash
uv run --project backend pytest backend/tests/wind/test_price_semantics.py backend/tests/wind/test_field_search.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/price_semantics.py backend/tests/wind/test_price_semantics.py
git commit -m "fix: keep adj and raw close candidates before applying search limit"
```

---

### Task 4: Map `s_dq_adjclose_backward` through `get_price`

**Files:**
- Modify: `backend/src/factor_platform/wind/adapter.py` (`ADJUSTED_PRICE_FIELD_MAP` or a dedicated forward-adjust map)
- Modify: `backend/src/factor_platform/wind/planner.py` (`_price_output_by_source` construction)
- Modify: `backend/tests/wind/test_planner.py`

**Why:** `ADJUST_BY_FIELD["s_dq_adjclose_backward"] = "pre"` is dead: `_price_output_by_source` is built only from `PRICE_FIELD_MAP` and `ADJUSTED_PRICE_FIELD_MAP`, which map `close` → `s_dq_adjclose`, not the backward column. Confirmed 前复权 close currently plans `execute_generic_query_plan`.

**Interfaces:**
- Consumes: `FieldSelection(field="s_dq_adjclose_backward", table="ashareeodprices")`
- Produces: `wind.get_price` step with `fields=["close"]` and `adjust_type="pre"` **and** adapter actually reading `s_dq_adjclose_backward` when `adjust_type == "pre"`.

Read `get_price` in `adapter.py` before editing. Today `pre` and `post` are both in `ADJUSTED_CLOSE_TYPES` and both read `s_dq_adjclose`. **That is the bug.** Split:

- `adjust_type in {"post", "post_volume"}` → `s_dq_adjclose`
- `adjust_type in {"pre", "pre_volume"}` → `s_dq_adjclose_backward`

- [ ] **Step 1: Failing planner test** (copy `momentum_spec` / `confirmed_close` style)

```python
def test_confirmed_forward_adj_close_uses_get_price_pre() -> None:
    spec = momentum_spec()
    selection = FieldSelection(
        logical_name="close",
        table="ashareeodprices",
        field="s_dq_adjclose_backward",
        time_role=FieldTimeRole.OBSERVATION,
    )
    plan = planner.plan(spec, [selection], request())
    price_step = next(s for s in plan.steps if s.tool.endswith("get_price"))
    assert price_step.arguments["adjust_type"] == "pre"
    assert price_step.arguments["fields"] == ["close"]
```

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/wind/test_planner.py::test_confirmed_forward_adj_close_uses_get_price_pre -v
```

Expected: FAIL — no `get_price` step (generic query instead).

- [ ] **Step 3: Wire source map + adapter column**

Add `s_dq_adjclose_backward` to the planner source map as output `"close"`. In `get_price`, when `adjust_type` is `pre` / `pre_volume`, select `s_dq_adjclose_backward` not `s_dq_adjclose`. Add/adjust an adapter unit test if one already pins `pre` → `s_dq_adjclose`.

- [ ] **Step 4: Run**

```bash
uv run --project backend pytest backend/tests/wind/test_planner.py backend/tests/wind/test_adapter_contract.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/adapter.py backend/src/factor_platform/wind/planner.py backend/tests/wind/test_planner.py
git commit -m "fix: serve forward-adjusted close via get_price pre adjustment"
```

---

### Task 5: Non-price `get_price` fields must not inherit price adjustment

**Files:**
- Modify: `backend/src/factor_platform/wind/planner.py` (`_adjust_type_for`)
- Modify: `backend/tests/wind/test_planner.py`

**Interfaces:**
- Consumes: `selection.field`, `ADJUST_BY_FIELD`, `PRICE_FIELD_MAP` keys that are not OHLC/prev_close
- Produces: `volume` / `total_turnover` / `limit_up` / `limit_down` source fields get `adjust_type="none"` even when `DataRules.use_adjusted_price is True`. OHLC/prev_close still use `ADJUST_BY_FIELD`.

- [ ] **Step 1: Failing test**

```python
def test_confirmed_volume_does_not_inherit_post_adjustment() -> None:
    spec = momentum_spec()
    spec.data_rules = DataRules(use_adjusted_price=True)
    selection = FieldSelection(
        logical_name="volume",
        table="ashareeodprices",
        field="s_dq_volume",
        time_role=FieldTimeRole.OBSERVATION,
    )
    plan = planner.plan(spec, [selection], request())
    price_step = next(s for s in plan.steps if s.tool.endswith("get_price"))
    assert price_step.arguments["adjust_type"] == "none"
    assert price_step.arguments["fields"] == ["volume"]
```

Match the existing output name for volume in `_price_output_by_source` if it is not `"volume"`.

- [ ] **Step 2: Run red** — expect `adjust_type == "post"`.

- [ ] **Step 3: Implementation**

```python
_PRICE_ADJUST_FIELDS = frozenset(ADJUST_BY_FIELD)

def _adjust_type_for(selection: FieldSelection, spec: FactorSpec) -> str:
    mapped = ADJUST_BY_FIELD.get(selection.field.lower())
    if mapped is not None:
        return mapped
    if selection.field.lower() in {"s_dq_volume", "s_dq_amount", "s_dq_limit", "s_dq_stopping"}:
        return "none"
    return "post" if spec.data_rules.use_adjusted_price else "none"
```

Prefer deriving the non-price set from `PRICE_FIELD_MAP` values minus OHLC/prev_close rather than a second literal list, if that stays readable.

- [ ] **Step 4: Run** `uv run --project backend pytest backend/tests/wind/test_planner.py -v`

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/planner.py backend/tests/wind/test_planner.py
git commit -m "fix: do not apply price adjustment to volume and limit fields"
```

---

### Task 6: Offline GitHub Actions

**Files:**
- Create: `.github/workflows/offline-gates.yml`

**Interfaces:**
- Produces: on `push` / `pull_request` to `main`, one job (or backend + frontend jobs) that uses **no repository secrets**.

- [ ] **Step 1: Workflow file** (exact shape)

```yaml
name: offline-gates
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: .
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - name: Backend gates
        run: |
          uv sync --project backend --group dev
          uv run --project backend ruff check backend/src backend/tests
          uv run --project backend mypy backend/src
          uv run --project backend pytest backend/tests -q
  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Frontend gates
        working-directory: frontend
        run: |
          npm ci
          npm test -- --run
          npm run lint
          npm run build
```

Golden cases run as part of `pytest backend/tests`. Do **not** set `WIND_*` or `KIMI_*`. Real-dictionary tests skip without `windquery/`. Compose contract tests run as part of pytest.

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/offline-gates.yml
git commit -m "ci: add offline ruff mypy pytest vitest and frontend build gates"
```

Push happens when the user asks, or with the rest of this plan’s last push. The first green run on GitHub is the evidence; do not invent a pass in docs before the Actions tab shows it.

---

### Task 7: Full offline gate rerun on this tree

**Files:**
- Create: `docs/acceptance/2026-08-27-offline-gates.md` **only after commands run**

- [ ] **Step 1: Run and paste actual output**

```bash
uv run --project backend ruff check backend/src backend/tests
uv run --project backend mypy backend/src
uv run --project backend pytest backend/tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
uv run --project backend pytest backend/tests/golden -q
```

Expected after Task 1: backend has **0 failures** (dictionary ceiling gone). If something else fails, **stop and fix**; do not write a fake pass.

- [ ] **Step 2: Write the evidence file** with date, SHA (`git rev-parse HEAD`), and each command’s exit code / summary line. Update `README.md` / `handoff.md` / `docs/使用说明.md` **one** “latest offline bar” paragraph to this SHA. Keep the 2026-08-14-coding-final numbers as the last **real Wind/Kimi/browser** bar.

- [ ] **Step 3: Commit**

```bash
git add docs/acceptance/2026-08-27-offline-gates.md README.md handoff.md docs/使用说明.md
git commit -m "docs: record offline gate rerun after remaining-todo fixes"
```

---

### Task 8: Reports + library UI regression (no real Wind)

**Files:**
- Create: `docs/acceptance/2026-08-27-ui-regression.md`
- Existing tests already cover much of this; extend if a hole is real:

  - `frontend/src/features/reports/ReportsPage.test.tsx`
  - `frontend/src/features/library/LibraryPage.test.tsx`

**Procedure:**

1. `APP_ENV=test` backend + `npm --prefix frontend run dev` (or existing test app).
2. Upload `backend/tests/fixtures/reports/` a legal PDF; envelope dates required; 「进入因子工作流」 disabled until formula gate + dates; enabled path POSTs `/api/reports/{id}/sessions` not `POST /api/sessions`.
3. Library page lists stored `review_status`; does not show 「待实现」.
4. Workbench completed ResultPane: unreviewed does **not** say 「不得作为正式发布」; 「发布到因子库」 present.

Use `browser-control` if driving a real browser; Vitest + Playwright-in-CI is enough for this task if the existing component tests already lock the gates. Do **not** call real Kimi. Fake provider / fixture upload is correct.

If browser-control is used, record screenshots under `docs/acceptance/2026-08-27-ui-regression/` and list them in the markdown. Strip any cookie/credential from shots.

- [ ] **Step 1: Exercise the two pages; write evidence**
- [ ] **Step 2: Commit only evidence + any real test gap fill**

```bash
git add docs/acceptance/2026-08-27-ui-regression.md frontend/src/features/reports frontend/src/features/library
git commit -m "test: record reports and library UI regression without live Wind"
```

---

## Verification matrix

| After | Command | Expected |
|---|---|---|
| Task 1 | dictionary test | PASS or SKIP (no local 字典); never FAIL on 12126 |
| Task 3–5 | `pytest backend/tests/wind` | PASS |
| Task 6 | workflow file present; no secrets in YAML | |
| Task 7 | all offline commands exit 0 | documented with SHA |
| Task 8 | reports enter-workflow + library list | evidence file |

## Spec coverage

| Remaining doable item from the 2026-08-27 audit | Task |
|---|---|
| 本地已与 `8818cea`/`69f7b84` 同步 | 无需 pull；本计划在最新 `main` 上做 |
| 字典测试上界 | 1 |
| handoff Notebook 过时描述 | 2 |
| `limit` 挤掉未复权收盘价 | 3 |
| `s_dq_adjclose_backward` 未接通 | 4 |
| 成交量继承 `adjust_type` | 5 |
| GitHub 离线 CI | 6 |
| P1/P2 后重跑离线门禁 | 7 |
| 研报/因子库浏览器回归 | 8 |
| 文档测试口径 | 7 |
| 隐藏集入库 | **已在 `69f7b84` 完成** |
