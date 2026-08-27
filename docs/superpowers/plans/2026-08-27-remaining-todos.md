# Remaining Offline Todos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish every remaining item this team can close without company network, Wind/Kimi credentials, Docker, or 口径 sign-off.

**Architecture:** Keep field-search → human confirmation → planner → `wind.get_price`. Ranking may reorder and label; it must not rewrite `wind_aliases.yaml` or skip confirmation. `get_price` `adjust_type` follows the confirmed Wind column. GitHub Actions run only offline gates on `ubuntu-latest` with no repository secrets.

**Tech Stack:** pytest, Vitest, ruff, mypy, GitHub Actions (`astral-sh/setup-uv@v4`, Node 22), existing FastAPI + React test fixtures.

**Baseline:** GitHub `main` `ce2f1c5`. Identifier catalog `backend/data/generated/wind_fields.jsonl` is tracked; `wind_metadata.jsonl` is not.

## Global Constraints

- No arbitrary SQL, no `eval` / `exec`, no silent `FakeLLMProvider` fallback.
- B4: never send a full report body or Wind raw data to an external model.
- Worker remains `network_mode: none` and holds only `MANIFEST_SIGNING_KEY` (verify, not sign).
- Formula confirmation and field confirmation cannot be skipped.
- Do **not** change `backend/data/metric_definitions.yaml` `review_status`.
- Do not commit `.env`, Wind credentials, API keys, parquet, `windquery/`, `imgs/`, or `backend/data/generated/wind_metadata.jsonl`.
- Do not claim Compose smoke, real Wind, or real Kimi passed unless those commands ran in this work.
- Python: `uv run --project backend …`. Frontend: `npm --prefix frontend …`.
- Public GitHub Actions: no `WIND_*`, no `KIMI_*`, no self-hosted runner.

## Scope

One sequenced plan (the audit listed these as one remaining-offline bundle). Three clusters that could split later: (A) dictionary test + handoff copy, (B) price semantics / planner / adapter, (C) CI + offline gate evidence + UI regression.

**Not in this plan:** YAML → `reviewed`; Docker compose up; real Wind/Kimi rerun; self-hosted runner; new independent blind set; VPN / DBA / Kimi quota.

---

## File Map

| File | Responsibility |
|---|---|
| `backend/tests/wind/test_metadata_catalog.py` | Dictionary size/quality assertions |
| `handoff.md` | Notebook credential copy |
| `backend/src/factor_platform/wind/price_semantics.py` | Reserve adj+raw before `limit` |
| `backend/tests/wind/test_price_semantics.py` | Crowded-pool limit test |
| `backend/src/factor_platform/wind/adapter.py` | `pre` reads `s_dq_adjclose_backward` |
| `backend/src/factor_platform/wind/planner.py` | Source map + non-price `adjust_type` |
| `backend/tests/wind/test_planner.py` | Forward-adj close + volume tests |
| `.github/workflows/offline-gates.yml` | Offline CI |
| `docs/acceptance/2026-08-27-offline-gates.md` | Written **after** commands run |
| `docs/acceptance/2026-08-27-ui-regression.md` | Written **after** UI is exercised |

---

### Task 1: Drop the dictionary field-count ceiling

**Files:**
- Modify: `backend/tests/wind/test_metadata_catalog.py:465-470`
- Test: same function

**Interfaces:**
- Consumes: `DictionaryBuilder(REAL_DICTIONARY).build() -> list[FieldMetadata]` (`REAL_DICTIONARY` = repo `windquery/windquery/references/wind字典`)
- Produces: same test name; **no maximum** on `len(records)`

- [ ] **Step 1: Replace the assertions**

Current failure on this machine: `AssertionError: got 12126 records` / `assert 12126 <= 9000`.

```python
@requires_real_dictionary
def test_real_dictionary_yields_metadata_for_thousands_of_fields() -> None:
    """A real WDS dump is large; never cap how many fields it may contain."""
    records = DictionaryBuilder(REAL_DICTIONARY).build()
    assert len(records) >= 6000, f"got {len(records)} records"
    assert all(record.metadata_source == "WDS" for record in records)
    by_key = {(record.table.lower(), record.field.lower()): record for record in records}
    close = by_key.get(("ashareeodprices", "s_dq_close"))
    adj = by_key.get(("ashareeodprices", "s_dq_adjclose"))
    assert close is not None, "s_dq_close missing from dictionary"
    assert adj is not None, "s_dq_adjclose missing from dictionary"
    assert close.name_zh
    assert close.frequency is Frequency.DAILY or close.frequency is None
```

Keep the `@requires_real_dictionary` skip when `windquery/.../wind字典` is absent (CI will skip; this laptop will run).

- [ ] **Step 2: Run**

```bash
uv run --project backend pytest backend/tests/wind/test_metadata_catalog.py::test_real_dictionary_yields_metadata_for_thousands_of_fields -v
```

Expected: PASS (`12126 >= 6000`) on a machine with the licensed dictionary; SKIP without it.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/wind/test_metadata_catalog.py
git commit -m "test: drop historic ceiling on Wind dictionary field count"
```

---

### Task 2: Correct the Notebook credential sentence in handoff

**Files:**
- Modify: `handoff.md:225`

**Interfaces:** none. Verification is a literal string check.

- [ ] **Step 1: Confirm the stale sentence is present**

```bash
rg -n "含非空 Wind 连接配置" handoff.md
```

Expected: a match on the Git 提交前安全提示 list.

- [ ] **Step 2: Replace that bullet with**

```markdown
- 仓库跟踪的 `Wind取数尝试.ipynb` **已脱敏**：连接参数来自 `os.environ.get("WIND_*")`，非空 host/password/user 字面量为 0。禁止再写入明文。该路径仍在 `.gitignore` 中，避免把填了凭据的本地副本重新纳入提交。
```

Leave the `.gitignore` coverage bullet that follows; it already lists `Wind取数尝试.ipynb`.

- [ ] **Step 3: Confirm the stale sentence is gone**

```bash
rg -n "含非空 Wind 连接配置" handoff.md
```

Expected: no matches.

- [ ] **Step 4: Commit**

```bash
git add handoff.md
git commit -m "docs: note Wind notebook is env-based and has no literal credentials"
```

---

### Task 3: Keep adj and raw close before applying `limit`

**Files:**
- Modify: `backend/src/factor_platform/wind/price_semantics.py:160-193` (`apply_price_semantics`)
- Test: `backend/tests/wind/test_price_semantics.py`

**Interfaces:**
- Consumes: `apply_price_semantics(candidates, requirement, use_adjusted_price=True, *, inject=None, limit=None) -> list[FieldCandidate]`
- Produces: same signature. When `classify_price_intent` sets `preferred_field`, every name in `(preferred_field, *also_list)` that is already in `candidates` or returned by `inject` **survives** `limit`. Extra BM25 rows fill remaining slots. If `limit` is smaller than the reserved set, return the reserved set anyway (do not drop raw close to hit `limit=5`).

- [ ] **Step 1: Write the failing test** at the bottom of `backend/tests/wind/test_price_semantics.py`

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
```

`classify_price_intent(..., "动量")` already sets `preferred_field="s_dq_adjclose"` and `also_list=("s_dq_close",)` in this file’s `test_momentum_infers_adjusted_close`.

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/wind/test_price_semantics.py::test_limit_does_not_drop_unadjusted_close_from_a_crowded_pool -v
```

Expected: FAIL — `s_dq_close` not in `fields`. Cause: `apply_price_semantics` boosts only `preferred_field` then `labelled[:limit]`, so eight `s_dq_other*` plus adj-close fill five slots and raw close (at the tail of `rest`) is dropped.

- [ ] **Step 3: Replace the slice at the end of `apply_price_semantics`**

Current (end of function):

```python
    labelled = [annotate_candidate(row, intent) for row in merged]
    if intent.preferred_field is not None:
        preferred = [row for row in labelled if row.field == intent.preferred_field]
        rest = [row for row in labelled if row.field != intent.preferred_field]
        labelled = preferred + rest
    if limit is not None:
        labelled = labelled[:limit]
    return labelled
```

Replace with:

```python
    labelled = [annotate_candidate(row, intent) for row in merged]
    reserved_names = tuple(
        name
        for name in (intent.preferred_field, *intent.also_list)
        if name is not None
    )
    reserved = [row for row in labelled if row.field in reserved_names]
    others = [row for row in labelled if row.field not in reserved_names]
    if intent.preferred_field is not None:
        preferred = [row for row in reserved if row.field == intent.preferred_field]
        also = [row for row in reserved if row.field != intent.preferred_field]
        reserved = preferred + also
    if limit is None:
        return reserved + others
    room = max(limit - len(reserved), 0)
    return reserved + others[:room]
```

Do not rewrite aliases. Do not auto-confirm.

- [ ] **Step 4: Run green**

```bash
uv run --project backend pytest backend/tests/wind/test_price_semantics.py backend/tests/wind/test_field_search.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/price_semantics.py backend/tests/wind/test_price_semantics.py
git commit -m "fix: keep adj and raw close candidates before applying search limit"
```

---

### Task 4: Serve 前复权 close through `get_price` with `adjust_type="pre"`

**Files:**
- Modify: `backend/src/factor_platform/wind/adapter.py:458-477` and `get_price` stock-field selection ~653-663 and value copy ~746-758
- Modify: `backend/src/factor_platform/wind/planner.py:88-92` (`_price_output_by_source`)
- Test: `backend/tests/wind/test_planner.py`

**Interfaces:**
- Consumes: `FieldSelection(logical_name="close", table="ashareeodprices", field="s_dq_adjclose_backward")`
- Produces: an `ExecutionStep` with `tool="wind.get_price"`, `arguments["fields"]==["close"]`, `arguments["adjust_type"]=="pre"`. Adapter, when `adjust_type in {"pre", "pre_volume"}`, reads column `s_dq_adjclose_backward` for the `close` output, not `s_dq_adjclose`.

Today `_price_output_by_source` is:

```python
self._price_output_by_source: dict[str, str] = {
    source.lower(): output
    for mapping in (PRICE_FIELD_MAP, ADJUSTED_PRICE_FIELD_MAP)
    for output, source in mapping.items()
}
```

`ADJUSTED_PRICE_FIELD_MAP["close"]` is `"s_dq_adjclose"` only, so `s_dq_adjclose_backward` is not a `get_price` source and `_retrieval_step` falls through to `execute_generic_query_plan`. `ADJUST_BY_FIELD["s_dq_adjclose_backward"] = "pre"` is unused. `get_price` treats `pre` and `post` as the same `ADJUSTED_CLOSE_TYPES` set and copies `s_dq_adjclose`.

- [ ] **Step 1: Failing planner test** (add next to the other price-adjustment tests; reuse `momentum_spec`, `request`, fixture `planner`)

```python
def test_confirmed_forward_adj_close_uses_get_price_pre(planner: WindPlanner) -> None:
    spec = momentum_spec()
    selection = [
        FieldSelection(
            logical_name="close",
            table="ashareeodprices",
            field="s_dq_adjclose_backward",
            time_role=FieldTimeRole.OBSERVATION,
        )
    ]
    plan = planner.plan(spec, selection, request())
    price_step = next(s for s in plan.steps if s.tool == "wind.get_price")
    assert price_step.arguments["adjust_type"] == "pre"
    assert price_step.arguments["fields"] == ["close"]
```

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/wind/test_planner.py::test_confirmed_forward_adj_close_uses_get_price_pre -v
```

Expected: FAIL — `StopIteration` (no `wind.get_price` step).

- [ ] **Step 3: Minimal wiring**

In `adapter.py` next to `ADJUSTED_PRICE_FIELD_MAP`:

```python
FORWARD_ADJUSTED_PRICE_FIELD_MAP = {
    "open": "s_dq_adjopen",   # keep existing if no dedicated forward OHLC columns
    "high": "s_dq_adjhigh",
    "low": "s_dq_adjlow",
    "close": "s_dq_adjclose_backward",
    "prev_close": "s_dq_adjpreclose",
}
PRE_ADJUST_TYPES = {"pre", "pre_volume"}
POST_ADJUST_TYPES = {"post", "post_volume"}
```

If Wind has no distinct 前复权 open/high/low columns in this replica, **only** change `close` (and leave other keys pointing at the existing adj columns). The required behavior is for `close`.

In `get_price`, replace the single `ADJUSTED_PRICE_FIELD_MAP` lookup:

```python
if adjust_type in PRE_ADJUST_TYPES:
    adj_map = FORWARD_ADJUSTED_PRICE_FIELD_MAP
elif adjust_type in POST_ADJUST_TYPES:
    adj_map = ADJUSTED_PRICE_FIELD_MAP
else:
    adj_map = {}
```

Use `adj_map` both when building `stock_fields` and when copying values (`adjusted_values = _numeric_column(raw, adj_map[field])`).

In `planner.py` `__init__`, include the forward map in `_price_output_by_source`:

```python
from factor_platform.wind.adapter import (
    ADJUSTED_PRICE_FIELD_MAP,
    FORWARD_ADJUSTED_PRICE_FIELD_MAP,
    PRICE_FIELD_MAP,
)
self._price_output_by_source = {
    source.lower(): output
    for mapping in (PRICE_FIELD_MAP, ADJUSTED_PRICE_FIELD_MAP, FORWARD_ADJUSTED_PRICE_FIELD_MAP)
    for output, source in mapping.items()
}
```

Export `FORWARD_ADJUSTED_PRICE_FIELD_MAP` from `adapter.py` `__all__` if the module has one; otherwise the import is enough.

- [ ] **Step 4: Run green**

```bash
uv run --project backend pytest backend/tests/wind/test_planner.py backend/tests/wind/test_adapter_contract.py -v
```

Expected: PASS. Existing `s_dq_adjclose` tests still expect `adjust_type=="post"` and `fields==["close"]`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/adapter.py backend/src/factor_platform/wind/planner.py backend/tests/wind/test_planner.py
git commit -m "fix: serve forward-adjusted close via get_price pre adjustment"
```

---

### Task 5: Volume and limit fields must not inherit price adjustment

**Files:**
- Modify: `backend/src/factor_platform/wind/planner.py:332-337` (`_adjust_type_for`)
- Modify: `backend/tests/wind/test_planner.py:416-438` (`test_unmapped_price_field_falls_back_to_data_rules` currently **expects** volume + `use_adjusted_price=True` → `adjust_type=="post"`; that expectation is the bug)

**Interfaces:**
- Consumes: `FieldSelection.field`, `ADJUST_BY_FIELD`, `PRICE_FIELD_MAP`
- Produces: `_adjust_type_for(selection, spec) -> str`. For `s_dq_volume`, `s_dq_amount`, `s_dq_limit`, `s_dq_stopping` always `"none"`. OHLC / prev_close still use `ADJUST_BY_FIELD`.

- [ ] **Step 1: Write a failing test and change the parametrize expectation**

Replace `test_unmapped_price_field_falls_back_to_data_rules` with:

```python
@pytest.mark.parametrize("use_adjusted_price", [True, False])
def test_confirmed_volume_does_not_inherit_price_adjustment(
    planner: WindPlanner, use_adjusted_price: bool
) -> None:
    spec = momentum_spec()
    spec.variables[0].logical_name = "volume"
    spec.formula_ast.args[0].args[0].name = "volume"
    spec.data_rules = DataRules(use_adjusted_price=use_adjusted_price)
    selection = [
        FieldSelection(
            logical_name="volume",
            table="ashareeodprices",
            field="s_dq_volume",
            time_role=FieldTimeRole.OBSERVATION,
        )
    ]
    plan = planner.plan(spec, selection, request())
    price_step = next(s for s in plan.steps if s.tool == "wind.get_price")
    assert price_step.arguments["adjust_type"] == "none"
    assert price_step.arguments["fields"] == ["volume"]
```

The formula_ast surgery matches the existing volume test in this file (rolling_return’s inner variable renamed to `volume`).

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/wind/test_planner.py::test_confirmed_volume_does_not_inherit_price_adjustment -v
```

Expected: FAIL when `use_adjusted_price=True` — `assert "post" == "none"`.

- [ ] **Step 3: Implementation**

```python
_NON_PRICE_GET_PRICE_FIELDS = frozenset(
    {
        PRICE_FIELD_MAP["volume"],
        PRICE_FIELD_MAP["total_turnover"],
        PRICE_FIELD_MAP["limit_up"],
        PRICE_FIELD_MAP["limit_down"],
    }
)


def _adjust_type_for(selection: FieldSelection, spec: FactorSpec) -> str:
    mapped = ADJUST_BY_FIELD.get(selection.field.lower())
    if mapped is not None:
        return mapped
    if selection.field.lower() in _NON_PRICE_GET_PRICE_FIELDS:
        return "none"
    return "post" if spec.data_rules.use_adjusted_price else "none"
```

Import `PRICE_FIELD_MAP` at the top of `planner.py` (already imported for the source map). `PRICE_FIELD_MAP` values are already lowercase (`s_dq_volume`, …).

- [ ] **Step 4: Run green**

```bash
uv run --project backend pytest backend/tests/wind/test_planner.py -v
```

Expected: PASS, including the raw/adj close tests from Task 6 of the P1/P2 work.

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
- Consumes: repo at `main` with no secrets
- Produces: workflow `offline-gates` on `push` and `pull_request` to `main`

- [ ] **Step 1: Add the workflow**

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

Do not add `env:` blocks with Wind or Kimi. Dictionary tests skip without `windquery/`. Compose contract tests run inside pytest.

- [ ] **Step 2: Confirm no secrets in the file**

```bash
rg -n "WIND_|KIMI_|PASSWORD|API_KEY|SECRET" .github/workflows/offline-gates.yml
```

Expected: no matches (`MANIFEST_SIGNING_KEY` must not appear either).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/offline-gates.yml
git commit -m "ci: add offline ruff mypy pytest vitest and frontend build gates"
```

Do not write “CI passed” in docs until the Actions run for the pushed SHA is green.

---

### Task 7: Rerun the full offline gate and record the SHA

**Files:**
- Create: `docs/acceptance/2026-08-27-offline-gates.md` (only after the commands run)
- Modify: `README.md` “当前状态” 门禁表, `handoff.md` 测试状态 B, `docs/使用说明.md` 安装验证段 — **one** latest-offline-bar paragraph, keep 2026-08-14-coding-final as the last real Wind/Kimi/browser bar

**Interfaces:** none. The evidence file is the deliverable.

- [ ] **Step 1: Run** (must be after Tasks 1–5 so the dictionary test and wind tests are green)

```bash
git rev-parse HEAD
uv run --project backend ruff check backend/src backend/tests
uv run --project backend mypy backend/src
uv run --project backend pytest backend/tests -q
npm --prefix frontend test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: every command exit 0. If pytest still has failures, **stop and fix**; do not write a pass.

- [ ] **Step 2: Write `docs/acceptance/2026-08-27-offline-gates.md`**

```markdown
# Offline gates

Date: 2026-08-27
SHA: <paste git rev-parse HEAD>

| Command | Exit | Summary |
|---|---|---|
| ruff | 0 | All checks passed |
| mypy | 0 | <paste "Success: no issues found in N source files"> |
| pytest backend/tests | 0 | <paste "N passed, M skipped"> |
| vitest --run | 0 | <paste "Tests N passed"> |
| npm run lint | 0 | tsc --noEmit clean |
| npm run build | 0 | production build ok |
```

Fill the table with **actual** numbers from Step 1. Do not copy 698/41 if the new run differs.

- [ ] **Step 3: Point README / handoff / 使用说明 at this file for “latest offline bar”, leave coding-final numbers for the real-component bar**

- [ ] **Step 4: Commit**

```bash
git add docs/acceptance/2026-08-27-offline-gates.md README.md handoff.md docs/使用说明.md
git commit -m "docs: record offline gate rerun after remaining-todo fixes"
```

---

### Task 8: Reports and library UI regression without live Wind

**Files:**
- Create: `docs/acceptance/2026-08-27-ui-regression.md`
- Existing tests (do not weaken): `frontend/src/features/reports/ReportsPage.test.tsx`, `frontend/src/features/library/LibraryPage.test.tsx`, `frontend/src/features/workbench/confirmations.test.tsx`

**Interfaces:**
- Consumes: fixture PDFs in `backend/tests/fixtures/reports/`; `APP_ENV=test` FakeLLM path
- Produces: evidence that (1) 「进入因子工作流」 stays disabled until formula gate + dates, (2) enabled path POSTs `/api/reports/{id}/sessions`, (3) LibraryPage is not 「待实现」, (4) completed ResultPane unreviewed copy is `入库将标注「未复核」` not `不得作为正式发布`

- [ ] **Step 1: Run the existing component tests**

```bash
npm --prefix frontend test -- --run src/features/reports/ReportsPage.test.tsx src/features/library/LibraryPage.test.tsx src/features/workbench/confirmations.test.tsx
```

Expected: PASS (already locking the four gates). If any fail, fix in this task.

- [ ] **Step 2: Write the evidence file** listing the four assertions, the command, and the pass counts. Optional: start `APP_ENV=test` backend + Vite and click through with browser-control; if used, save screenshots under `docs/acceptance/2026-08-27-ui-regression/` and confirm they contain no cookies or connection strings. Do **not** call real Kimi.

- [ ] **Step 3: Commit**

```bash
git add docs/acceptance/2026-08-27-ui-regression.md
git commit -m "docs: record reports and library UI regression without live Wind"
```

---

## Self-review

**Spec coverage (2026-08-27 audit “我们能直接解决”):**

| Audit item | Task |
|---|---|
| 同步本地到 `8818cea` | Already done (`ce2f1c5` is ahead; no pull task) |
| 字典测试上界 | 1 |
| handoff Notebook 过时描述 | 2 |
| `limit` 挤掉未复权收盘价 | 3 |
| `s_dq_adjclose_backward` 未接通 | 4 |
| 成交量继承 `adjust_type` | 5 |
| GitHub 离线 CI | 6 |
| P1/P2 后重跑离线门禁 | 7 |
| 研报/因子库浏览器回归 | 8 |
| 文档测试口径 | 7 |
| 隐藏集入库 | Done in `69f7b84` |

**Placeholder scan:** no TBD / “handle edge cases” / “similar to Task N”.

**Type consistency:** `apply_price_semantics(..., limit: int | None)` unchanged; `_adjust_type_for(selection, spec) -> str`; `FORWARD_ADJUSTED_PRICE_FIELD_MAP` only introduced in Task 4 and imported in the same task’s planner snippet.

## Verification matrix

| After | Command | Expected |
|---|---|---|
| Task 1 | dictionary test | PASS or SKIP; never FAIL on 12126 |
| Task 3 | `pytest backend/tests/wind/test_price_semantics.py` | PASS |
| Task 4–5 | `pytest backend/tests/wind/test_planner.py` | PASS |
| Task 6 | workflow exists; `rg WIND_\|KIMI_` empty | |
| Task 7 | all six commands exit 0 | evidence file with this SHA |
| Task 8 | frontend feature tests PASS | evidence file |
