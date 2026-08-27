# Remaining P1/P2 Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four remaining product gaps after `32ce5b3` / `c37fa4a`: report extraction enters the shared factor workflow, the factor library is listable and publishable, price-adjustment semantics are explicit at field confirmation, and Compose smoke is gated on a machine that actually has Docker.

**Architecture:** Reuse the existing P0 session state machine rather than adding a second workflow. The report page becomes a second *source* of a `ResearchRequest` + draft `FactorSpec`; after `POST /api/reports/{artifact_id}/sessions` the session is a normal workbench session at `needs_clarification` or `waiting_formula_confirmation`. The library stays a filesystem-backed immutable store (`FactorLibrary`) with a thin API; it only consumes `COMPLETED` sessions. Field semantics are a ranking + labelling layer in `FieldSearch` plus planner alignment with the *confirmed* field — never a silent remap and never a bypass of confirmation. Compose files and contract tests already exist; real `docker compose up` is a gated smoke, not a redesign.

**Tech Stack:** FastAPI + Pydantic v2, SQLite session store, filesystem library, React 18 + Vite + Ant Design + TanStack Query, Vitest / pytest, Docker Compose.

**Baseline:** GitHub `main` at `c37fa4a` (stable product commit `32ce5b3`). Do not reopen the real-Wind / Kimi Coding Plan loop.

## Global Constraints

- No arbitrary SQL, no `eval` / `exec`, no silent `FakeLLMProvider` fallback.
- Trust boundary B4: never send a full report body or Wind raw data to an external model. Report excerpts remain bounded and already-scored.
- Worker remains `network_mode: none` and holds only `MANIFEST_SIGNING_KEY` (verify, not sign).
- Every session mutation carries `expected_version`.
- Formula confirmation and field confirmation cannot be skipped. Low-confidence extraction cannot enter execution.
- `disputed` metrics cannot be published; `unreviewed` may publish only with a stored label.
- Python via `uv run --project backend`; frontend types from `backend/scripts/export_openapi.py` then `npm --prefix frontend run gen:api`.
- Do not commit `.env`, Wind credentials, API keys, parquet, or `Wind取数尝试.ipynb`.
- Do not add Alembic `0002_factor_library`: Task 26 sketched SQLite, but the live `FactorLibrary` is filesystem-only. Do not introduce a second store.

## Current Gaps (verified in code)

| Gap | What exists | What is missing |
|---|---|---|
| Report → workflow | Upload, extract, evidence, disabled gate on 「进入因子工作流」 | Button has no `onClick`. Extraction is not persisted (only `{artifact_id}.pdf`). No mapping to a session. `ResearchRequest.report_artifact_id` is unused. |
| Factor library | `FactorLibrary.publish/list/get/replace` + unit tests; `/library` route | No `api/library.py`. `LibraryPage` is `<Empty description="待实现" />`. Workbench has no publish action. |
| Field semantics | Alias `收盘价` → `s_dq_close`; alias `后复权收盘价` → `s_dq_adjclose`; `DataRules.use_adjusted_price` defaults `True`; planner `get_price` uses that flag independently of the confirmed field | Query 「收盘价」/「close」 ranks unadjusted first. Confirmed `s_dq_close` can still be fetched as post-adjusted. UI has no 复权 column. |
| Compose smoke | `deploy/compose.yaml` + 13 contract tests | No real image build / `compose up` / health smoke. Original machine had no Docker. |

## File Map

**Create**

- `backend/src/factor_platform/api/library.py` — list / get / publish
- `backend/tests/api/test_library.py`
- `backend/tests/api/test_reports_workflow.py`
- `backend/src/factor_platform/wind/price_semantics.py` — family classifier + adjustment labels
- `backend/tests/wind/test_price_semantics.py`
- `frontend/src/features/library/LibraryPage.test.tsx`

**Modify**

- `backend/src/factor_platform/api/reports.py` — persist extraction JSON; `POST /{artifact_id}/sessions`
- `backend/src/factor_platform/orchestration/service.py` — `enter_from_report(...)`
- `backend/src/factor_platform/domain/errors.py` + `api/errors.py` — `report_formula_unconfirmed`, `report_artifact_not_found`, `session_not_completed`, plus existing `PublishRefusedError` / `ImmutableArtifactError`
- `backend/src/factor_platform/domain/models.py` — optional `price_adjustment` on `FieldCandidate` / `FieldCandidateBinding`
- `backend/src/factor_platform/main.py` — wire library router + `FactorLibrary`
- `backend/src/factor_platform/settings.py` — `library_root`
- `backend/src/factor_platform/wind/field_search.py` — semantic rerank
- `backend/src/factor_platform/wind/planner.py` — `adjust_type` from confirmed field
- `frontend/src/features/reports/ReportsPage.tsx` + `.test.tsx`
- `frontend/src/features/library/LibraryPage.tsx`
- `frontend/src/features/workbench/WorkbenchPage.tsx` + `ResultPane.tsx` + `FieldCandidateTable.tsx`
- `frontend/src/api/client.ts` + regen `openapi.json` / `schema.d.ts`
- `docs/使用说明.md`, `handoff.md`

**Do not touch**

- Worker Dockerfile network/credentials, compose worker `network_mode`, B4 outbound filter, metric registry review statuses (doc-only `reviewed` must not be copied into `metric_definitions.yaml` without explicit authorization).

---

## Approach (locked)

### Report → workflow

Two rejected alternatives:

1. **Client posts the extraction blob into `POST /api/sessions`** — the client can forge `status: extracted` and skip the human gate.
2. **Re-extract the PDF on enter** — extra model call, non-deterministic, and the user already confirmed what they saw.

Chosen path: persist `{artifact_id}.extraction.json` next to the PDF at upload time. Enter-workflow loads *server* state, re-checks the gate, seeds a session, and still requires formula confirmation in the workbench.

High-confidence extraction with a validated `formula_ast`: no extra LLM call; build `FactorSpec` from stored extraction + envelope; run `ClarificationEngine`.

`needs_manual_confirmation`: require `manual_formula`; call existing `FactorParser.parse` with `research_idea = manual_formula` (envelope only — not the report body); attach stored `source_evidence`.

### Factor library

Rejected: SQLite table from original Task 26. The live service already copies parquet into `library_root/{factor_id}/v{n}/`. API is a thin wrapper. Publish is initiated from a completed workbench session, not from a free-form upload.

### Field semantics

Rejected: silently rewriting `close` → `adj_close`. That would hide a 口径 change. Ranking may *prefer* 后复权 for momentum/return/volatility, but both candidates stay visible, labelled, and user-confirmed. Planner `adjust_type` follows the confirmed field.

### Compose

Contract tests stay. Real smoke is a separate gated task that no-ops with a written skip if `docker` is absent.

---

### Task 1: Persist extraction and seed a session from a report

**Files:**
- Modify: `backend/src/factor_platform/api/reports.py`
- Modify: `backend/src/factor_platform/orchestration/service.py`
- Modify: `backend/src/factor_platform/domain/errors.py`
- Modify: `backend/src/factor_platform/api/errors.py`
- Create: `backend/tests/api/test_reports_workflow.py`
- Test: `backend/tests/reports/test_extractor.py` (keep existing; do not weaken the confidence gate)

**Interfaces:**
- Consumes: `ExtractedFactor`, `ResearchRequest`, `WorkflowService.create_session`, `FactorParser.parse`, `ClarificationEngine.questions`, `render_canonical_formula`
- Produces:
  - `upload_report` writes `{upload_root}/{artifact_id}.extraction.json`
  - `POST /api/reports/{artifact_id}/sessions` → `SessionSnapshot`
  - `WorkflowService.enter_from_report(session_id, artifact_id, request, manual_formula, expected_version) -> SessionSnapshot`

- [ ] **Step 1: Write the failing API tests**

```python
# backend/tests/api/test_reports_workflow.py
import json
from pathlib import Path

from httpx import AsyncClient

from factor_platform.domain.formula import FormulaNode
from factor_platform.reports.extractor import ExtractedFactor, FormulaExtraction, FormulaExtractionStatus

ENVELOPE = {
    "asset_type": "stock",
    "universe": "000300.SH",
    "start_date": "2024-01-01",
    "end_date": "2024-06-30",
    "research_idea": "研报抽取",
}

AST = {
    "type": "call",
    "op": "rank",
    "args": [{"type": "variable", "name": "roe_ttm"}],
}


def _extracted(tmp_path: Path, artifact_id: str, status: str, text: str = "rank(roe_ttm)") -> None:
    extraction = ExtractedFactor.model_validate({
        "factor_name": "quality",
        "hypothesis": "ROE 高的股票更好",
        "direction": "higher_is_better",
        "variables": [{"logical_name": "roe_ttm", "meaning": "净资产收益率TTM"}],
        "evidence": [{
            "evidence_id": "p3b1",
            "page_number": 3,
            "text": "因子定义：对 ROE_TTM 做横截面排名。",
            "score": 5.0,
            "bbox": [0, 0, 1, 1],
        }],
        "formula_extraction": {
            "status": status,
            "confidence": 0.92 if status == "extracted" else 0.4,
            "source_pages": [3],
            "extracted_text": text,
            "formula_ast": AST if status == "extracted" else None,
            "warning": "" if status == "extracted" else "需人工确认",
        },
    })
    (tmp_path / f"{artifact_id}.extraction.json").write_text(
        extraction.model_dump_json(), encoding="utf-8"
    )
    (tmp_path / f"{artifact_id}.pdf").write_bytes(b"%PDF-")


async def test_unconfirmed_extraction_cannot_enter_workflow(client, upload_root):
    _extracted(upload_root, "abc", "needs_manual_confirmation")
    response = await client.post(
        "/api/reports/abc/sessions",
        json={"session_id": "s-report", "request": ENVELOPE},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "report_formula_unconfirmed"


async def test_extracted_report_opens_a_normal_session_waiting_formula(client, upload_root):
    _extracted(upload_root, "abc", "extracted")
    response = await client.post(
        "/api/reports/abc/sessions",
        json={"session_id": "s-report", "request": ENVELOPE},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == "s-report"
    assert body["request"]["report_artifact_id"] == "abc"
    assert body["state"] in {"needs_clarification", "waiting_formula_confirmation"}
    assert body["factor_spec"]["canonical_formula"]
    assert body["factor_spec"]["source_evidence"][0]["page_number"] == 3
    # Must not skip formula confirmation.
    assert body["state"] != "searching_fields"


async def test_manual_formula_is_required_and_then_parsed(client, upload_root):
    _extracted(upload_root, "abc", "needs_manual_confirmation")
    response = await client.post(
        "/api/reports/abc/sessions",
        json={
            "session_id": "s-manual",
            "request": ENVELOPE,
            "manual_formula": "rank(roe_ttm)",
        },
    )
    assert response.status_code == 201
    assert response.json()["request"]["research_idea"] == "rank(roe_ttm)"


async def test_missing_artifact_is_404(client):
    response = await client.post(
        "/api/reports/missing/sessions",
        json={"session_id": "s-x", "request": ENVELOPE},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_artifact_not_found"
```

Wire `upload_root` in the existing `client` fixture (or a dedicated one) via `app.dependency_overrides[reports.get_upload_root]`. FakeLLMProvider must still be enqueued for the manual-formula path.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run --project backend pytest backend/tests/api/test_reports_workflow.py -v
```

Expected: FAIL — route `/api/reports/{id}/sessions` does not exist.

- [ ] **Step 3: Minimal implementation**

1. New errors:

```python
class ReportFormulaUnconfirmedError(DomainError):
    """Low-confidence extraction cannot enter the workflow without a typed formula."""

class ReportArtifactNotFoundError(DomainError):
    """Upload id is unknown or its extraction record is missing."""
```

Map them in `ERROR_MAP` to `(422, "report_formula_unconfirmed")` and `(404, "report_artifact_not_found")`.

2. `upload_report` after `extractor.extract`: write `target.with_suffix(".extraction.json")`.

3. `EnterWorkflowBody`:

```python
class EnterWorkflowBody(BaseModel):
    session_id: str
    request: ResearchRequest
    manual_formula: str | None = None
```

4. `WorkflowService.enter_from_report`:
   - `create_session(session_id)` if needed (or require empty `created` session — prefer: create here, 409 if id exists).
   - Load extraction JSON. Missing → `ReportArtifactNotFoundError`.
   - If `status == needs_manual_confirmation` and not `manual_formula.strip()` → `ReportFormulaUnconfirmedError`.
   - Set `request.report_artifact_id = artifact_id`.
   - If extracted + `formula_ast` present: build `FactorSpec` from extraction (envelope from `request`; `canonical_formula = render_canonical_formula(ast)`; `source_evidence` from excerpts). Append `PARSE_STARTED` then `FORMULA_PROPOSED` or `CLARIFICATION_REQUESTED`. **Do not call the parser.** **Do not append `FORMULA_CONFIRMED`.**
   - If manual: `request.research_idea = manual_formula.strip()`, then reuse `submit_message` so the existing parser + clarifier path runs. After parse, merge `source_evidence` from the stored extraction onto the spec (extra event or include in `FORMULA_PROPOSED` payload). Still no full report in the prompt.
   - Return snapshot.

5. Handler stays thin: load path, call one service method, return snapshot, status 201.

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run --project backend pytest backend/tests/api/test_reports_workflow.py backend/tests/reports/test_extractor.py backend/tests/api/test_api.py -v
```

Expected: PASS. Existing extractor confidence tests still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/api/reports.py \
  backend/src/factor_platform/orchestration/service.py \
  backend/src/factor_platform/domain/errors.py \
  backend/src/factor_platform/api/errors.py \
  backend/tests/api/test_reports_workflow.py
git commit -m "feat: seed factor sessions from persisted report extractions"
```

---

### Task 2: Wire 「进入因子工作流」 and collect the trusted envelope

**Files:**
- Modify: `frontend/src/features/reports/ReportsPage.tsx`
- Modify: `frontend/src/features/reports/ReportsPage.test.tsx`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/app/router.tsx` is already fine (`/workbench/:sessionId`)
- Regen: `frontend/src/api/openapi.json`, `frontend/src/api/schema.d.ts`

**Interfaces:**
- Consumes: `POST /api/reports/{artifact_id}/sessions`
- Produces: `apiClient.enterReportWorkflow(artifactId, body)`; navigation to `/workbench/{sessionId}`

- [ ] **Step 1: Export OpenAPI and generate types**

```bash
uv run --project backend python backend/scripts/export_openapi.py frontend/src/api/openapi.json
npm --prefix frontend run gen:api
```

Add to `client.ts`:

```ts
enterReportWorkflow: (
  artifactId: string,
  body: {
    session_id: string
    request: ResearchRequest
    manual_formula?: string
  },
) => post<SessionSnapshot>(`/api/reports/${artifactId}/sessions`, body),
```

- [ ] **Step 2: Write the failing frontend tests**

Keep the existing disable/enable tests. Add:

```tsx
it("does not enter the workflow until the envelope and formula are present", async () => {
  mockUpload({ status: "extracted", confidence: 0.92, source_pages: [3], extracted_text: "rank(ROE_TTM)", warning: "" })
  const user = await uploadFile()
  const proceed = await screen.findByRole("button", { name: "进入因子工作流" })
  // Envelope dates are empty → still blocked, even though formula is extracted.
  expect(proceed).toBeDisabled()
})

it("posts the artifact id and navigates to the workbench", async () => {
  // mock upload + enter-workflow 201 with session_id "s-report"
  // fill universe (default) and date range
  // click 进入因子工作流
  // expect fetch POST /api/reports/abc/sessions
  // expect navigate to /workbench/s-report
})

it("sends manual_formula when the extraction needed confirmation", async () => {
  // status needs_manual_confirmation, type formula, fill dates, click
  // body.manual_formula === "rank(roe_ttm)"
})
```

Wrap the page in `MemoryRouter` + a test-double `navigate` (or `createMemoryRouter` with `/reports` and `/workbench/:sessionId`).

- [ ] **Step 3: Run frontend tests red**

```bash
npm --prefix frontend test -- --run src/features/reports/ReportsPage.test.tsx
```

Expected: FAIL — button still has no `onClick`; no envelope fields.

- [ ] **Step 4: Implement the page**

Reuse the same envelope fields as `ResearchForm` (asset type, universe, frequency, date range). Do **not** send a free-text idea when status is `extracted`; the backend seeds the spec from stored AST. When status is `needs_manual_confirmation`, send `manualFormula`.

`canProceed` becomes: formula gate (existing) **and** both dates present.

On success: `navigate(/workbench/${snapshot.session_id})`. On `ApiError`, show the backend `error.message` (same pattern as workbench). Session id: `s-${Date.now().toString(36)}` matching workbench.

Do not call `createSession` + `submitMessage` from the client for this path — that would re-parse and drop evidence.

- [ ] **Step 5: Run frontend tests green + typecheck**

```bash
npm --prefix frontend test -- --run src/features/reports/ReportsPage.test.tsx src/features/workbench/confirmations.test.tsx src/app/AppShell.test.tsx
npm --prefix frontend run lint
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/reports/ReportsPage.tsx \
  frontend/src/features/reports/ReportsPage.test.tsx \
  frontend/src/api/client.ts \
  frontend/src/api/openapi.json \
  frontend/src/api/schema.d.ts
git commit -m "feat: connect report extraction to the shared workbench session"
```

---

### Task 3: Factor library HTTP API

**Files:**
- Create: `backend/src/factor_platform/api/library.py`
- Create: `backend/tests/api/test_library.py`
- Modify: `backend/src/factor_platform/main.py`
- Modify: `backend/src/factor_platform/settings.py` (`library_root: str = "./data/artifacts/library"`)
- Modify: `backend/src/factor_platform/api/errors.py` — map `PublishRefusedError` → `(422, "publish_refused")`, `ImmutableArtifactError` → `(409, "immutable_artifact")`, new `SessionNotCompletedError` → `(409, "session_not_completed")`

**Interfaces:**
- Consumes: `FactorLibrary.publish/list_factors/get_version`, `SessionRepository.get_snapshot`
- Produces:
  - `GET /api/library` → `list[LibraryEntry]` (latest version per factor)
  - `GET /api/library/{factor_id}/v/{version}` → `LibraryEntry`
  - `POST /api/library` `{ session_id, factor_id? }` → `LibraryEntry` (201)
  - `WorkflowService.publish_to_library(session_id, factor_id | None) -> LibraryEntry`

Publish rules (already in `FactorLibrary`; API must not weaken them):

- Session `state == completed` and `execution_result.status == completed` and `artifact_uri` points at an existing parquet.
- Copy the result file; do not store the run path.
- `program_source` = `snapshot.generated_code or ""`.
- `manifest_sha256` = `snapshot.code_sha256` if that is the manifest hash; if `code_sha256` is the export hash, read the run's signed manifest sha from `resource_stats` or the sibling `manifest` file. Prefer the hash already stored on the snapshot; if absent, refuse with `session_not_completed` rather than inventing one.
- `metric_keys` from `execution_result.resource_stats["metric_review_status"].keys()`.
- `factor_id` defaults to a slug of `spec.factor_name` (lowercase, `[a-z0-9_]+`); collisions publish `vN+1` of the same id (that is the versioning model).
- `disputed` → `PublishRefusedError`. Do not catch-and-ignore.

- [ ] **Step 1: Write failing API tests**

```python
async def test_publish_refuses_an_incomplete_session(client):
    await client.post("/api/sessions", json={"session_id": "s1"})
    response = await client.post("/api/library", json={"session_id": "s1"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_not_completed"


async def test_publish_copies_the_artifact_and_lists_latest(client, completed_session, tmp_path):
    response = await client.post("/api/library", json={"session_id": completed_session, "factor_id": "quality"})
    assert response.status_code == 201
    entry = response.json()
    assert entry["version"] == 1
    assert entry["review_status"] in {"unreviewed", "reviewed"}
    listed = await client.get("/api/library")
    assert any(item["factor_id"] == "quality" for item in listed.json())
    got = await client.get("/api/library/quality/v/1")
    assert got.status_code == 200
    # Delete the run artifact; library copy must still verify.
```

Build `completed_session` by writing a parquet under a temp artifact root and injecting a snapshot in `COMPLETED` (prefer driving the fake workflow to completed if the existing API tests already have a helper; otherwise seed the event log).

Also assert `PublishRefusedError` for `FLOAT_MV` still returns 422.

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/api/test_library.py -v
```

Expected: FAIL — no library router.

- [ ] **Step 3: Implement thin handlers + `publish_to_library`**

`get_library` dependency overridden in `create_app` with `FactorLibrary(Path(settings.library_root))`. Do not put publish logic in the handler.

- [ ] **Step 4: Run green, including existing library unit tests**

```bash
uv run --project backend pytest backend/tests/api/test_library.py backend/tests/library/test_service.py -v
```

Expected: PASS. Immutability / copy-not-reference / disputed gate still pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/api/library.py \
  backend/src/factor_platform/main.py \
  backend/src/factor_platform/settings.py \
  backend/src/factor_platform/api/errors.py \
  backend/src/factor_platform/orchestration/service.py \
  backend/src/factor_platform/domain/errors.py \
  backend/tests/api/test_library.py
git commit -m "feat: expose immutable factor library over HTTP"
```

---

### Task 4: Library page and publish from a completed run

**Files:**
- Modify: `frontend/src/features/library/LibraryPage.tsx`
- Create: `frontend/src/features/library/LibraryPage.test.tsx`
- Modify: `frontend/src/features/workbench/ResultPane.tsx` and/or `WorkbenchPage.tsx`
- Modify: `frontend/src/features/workbench/confirmations.test.tsx` (or a small ResultPane test)
- Modify: `frontend/src/api/client.ts` + regen OpenAPI

**Interfaces:**
- `apiClient.listLibrary()`, `getLibraryVersion(id, version)`, `publishSession(sessionId, factorId?)`
- Library page: table of latest versions (name, version, review_status, created_at, session_id); click → detail with canonical formula, hashes, provenance diff fields, artifact path, 「未复核」 tag
- Workbench: when `snapshot.state === "completed"`, primary button 「发布到因子库」. Disabled when result findings include `disputed` or status is not completed. After success, link to `/library`.

- [ ] **Step 1: Regen types + failing tests**

Library empty-state test today expects 「待实现」. Replace with:

```tsx
it("lists published factors and shows the unreviewed label", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => [{
      factor_id: "quality",
      version: 2,
      factor_name: "quality",
      review_status: "unreviewed",
      review_note: "ROE_TTM 未复核",
      manifest_sha256: "m".repeat(64),
      session_id: "s1",
    }],
  }))
  render(<LibraryPage />)
  expect(await screen.findByText("quality")).toBeInTheDocument()
  expect(screen.getByText(/未复核/)).toBeInTheDocument()
})
```

Workbench: completed snapshot shows 「发布到因子库」; click POSTs `/api/library`.

- [ ] **Step 2: Run red**

```bash
npm --prefix frontend test -- --run src/features/library/LibraryPage.test.tsx
```

- [ ] **Step 3: Implement**

Frontend only renders backend fields. Do not recompute `review_status` in the browser. Do not fetch parquet into the page; show path + hashes.

- [ ] **Step 4: Run green**

```bash
npm --prefix frontend test -- --run src/features/library/LibraryPage.test.tsx src/features/workbench
npm --prefix frontend run lint
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/library frontend/src/features/workbench/ResultPane.tsx \
  frontend/src/features/workbench/WorkbenchPage.tsx frontend/src/api
git commit -m "feat: render factor library and publish completed sessions"
```

---

### Task 5: Price-adjustment semantics at field search

**Files:**
- Create: `backend/src/factor_platform/wind/price_semantics.py`
- Create: `backend/tests/wind/test_price_semantics.py`
- Modify: `backend/src/factor_platform/wind/field_search.py`
- Modify: `backend/src/factor_platform/domain/models.py` (`FieldCandidate.price_adjustment: Literal["none","forward","backward"] | None = None`, `semantic_note: str | None = None`)
- Modify: `backend/tests/wind/test_field_search.py`
- Modify: `frontend/src/features/workbench/FieldCandidateTable.tsx` + `confirmations.test.tsx`

**Interfaces:**
- `classify_price_intent(requirement: DataRequirement, use_adjusted_price: bool) -> PriceIntent`
- `annotate_candidate(candidate, intent) -> FieldCandidate`
- `FieldSearch.search` still returns a list the user must confirm. Semantic tier only reorders and labels.

Rules (exact):

| Query / meaning | Family | Preferred field | Also listed |
|---|---|---|---|
| 后复权收盘价 / adj close | explicit backward | `s_dq_adjclose` (alias, unchanged) | `s_dq_close` labelled 未复权 |
| 前复权收盘价 | explicit forward | `s_dq_adjclose_backward` | `s_dq_close` |
| 不复权 / 原始收盘价 | explicit none | `s_dq_close` | adj variants |
| 收盘价 / close / 动量 / 收益率 / 波动 / momentum / return / volatility (no 不复权) | inferred return | **boost** `s_dq_adjclose` to front with `source_tier` kept; add `semantic_note="动量/收益类默认推荐后复权收盘价，close ≠ adj_close"` | keep `s_dq_close` with `semantic_note="未复权收盘价，不等于复权收盘价"` |

`close` must never be treated as an alias of `s_dq_adjclose`. Alias YAML for `收盘价` stays pointing at `s_dq_close` — the semantic layer adds the adj candidate rather than rewriting the alias file. Rewriting the alias would make 「收盘价」 lie.

- [ ] **Step 1: Failing tests**

```python
def test_plain_close_does_not_alias_to_adjclose():
    intent = classify_price_intent(DataRequirement(logical_name="close", meaning="收盘价"), True)
    assert intent.preferred_field == "s_dq_adjclose"
    assert intent.explicit is False


def test_search_for_close_lists_unadjusted_and_prefers_adjusted(search):
    hits = search.search(DataRequirement(logical_name="close", meaning="收盘价"), limit=5)
    fields = [h.field for h in hits]
    assert "s_dq_adjclose" in fields
    assert "s_dq_close" in fields
    assert hits[0].field == "s_dq_adjclose"
    close = next(h for h in hits if h.field == "s_dq_close")
    assert close.price_adjustment == "none"
    assert "不等于" in (close.semantic_note or "")


def test_explicit_unadjusted_keeps_s_dq_close_first(search):
    hits = search.search(DataRequirement(logical_name="close", meaning="不复权收盘价"), limit=5)
    assert hits[0].field == "s_dq_close"
```

Frontend: candidate table renders a 「复权」 column and the semantic note.

- [ ] **Step 2: Run red**

```bash
uv run --project backend pytest backend/tests/wind/test_price_semantics.py backend/tests/wind/test_field_search.py -v
```

- [ ] **Step 3: Implement classifier + rerank inside `FieldSearch.search` after merge, before `limit`.** Pass `use_adjusted_price` only as a ranking prior (default True matches `DataRules`). Do not drop unadjusted rows. Do not auto-confirm.

- [ ] **Step 4: Run green + existing planner/sample tests**

```bash
uv run --project backend pytest backend/tests/wind -v
npm --prefix frontend test -- --run src/features/workbench/confirmations.test.tsx
```

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/price_semantics.py \
  backend/src/factor_platform/wind/field_search.py \
  backend/src/factor_platform/domain/models.py \
  backend/tests/wind \
  frontend/src/features/workbench/FieldCandidateTable.tsx \
  frontend/src/features/workbench/confirmations.test.tsx
git commit -m "feat: rank and label adjusted vs raw close without skipping confirmation"
```

---

### Task 6: Planner `adjust_type` follows the confirmed field

**Files:**
- Modify: `backend/src/factor_platform/wind/planner.py` (`_retrieval_step`)
- Modify: `backend/tests/wind/test_planner.py`

**Why:** `_retrieval_step` currently sets `"adjust_type": "post" if spec.data_rules.use_adjusted_price else "none"` whenever the field is served by `wind.get_price`. A user who confirmed `s_dq_close` still receives post-adjusted prices because `use_adjusted_price` defaults True. That is the silent `close ≡ adj_close` bug.

**Rule:**

```python
ADJUST_BY_FIELD = {
    "s_dq_adjclose": "post",
    "s_dq_adjopen": "post",
    "s_dq_adjhigh": "post",
    "s_dq_adjlow": "post",
    "s_dq_adjclose_backward": "pre",
    "s_dq_close": "none",
    "s_dq_open": "none",
    "s_dq_high": "none",
    "s_dq_low": "none",
}
```

If `selection.field` is in the map, use it. Otherwise fall back to `data_rules.use_adjusted_price`. Never invert a confirmed unadjusted field.

- [ ] **Step 1: Failing test**

```python
def test_confirmed_raw_close_does_not_request_post_adjustment():
    spec = make_spec(data_rules=DataRules(use_adjusted_price=True))
    selection = FieldSelection(logical_name="close", table="ashareeodprices", field="s_dq_close")
    plan = planner.plan(spec, [selection], request)
    price_step = next(s for s in plan.steps if s.tool.endswith("get_price"))
    assert price_step.arguments["adjust_type"] == "none"
    assert price_step.arguments["fields"] == ["close"]  # or the mapped output name already used


def test_confirmed_adjclose_requests_post_adjustment():
    selection = FieldSelection(logical_name="close", table="ashareeodprices", field="s_dq_adjclose")
    plan = planner.plan(spec, [selection], request)
    price_step = next(s for s in plan.steps if s.tool.endswith("get_price"))
    assert price_step.arguments["adjust_type"] == "post"
```

Match the existing `test_planner.py` helpers (`make_spec`, fixture `planner`) rather than inventing new ones. Read that file first and copy its style.

- [ ] **Step 2–4: red → implement → green** (`uv run --project backend pytest backend/tests/wind/test_planner.py -v`)

- [ ] **Step 5: Commit**

```bash
git add backend/src/factor_platform/wind/planner.py backend/tests/wind/test_planner.py
git commit -m "fix: derive Wind price adjustment from the confirmed field"
```

---

### Task 7: Gated Docker Compose smoke (no security-contract changes)

**Files:**
- Create: `docs/acceptance/compose-smoke.md` (commands + expected output; fill in only after a real run)
- Modify: `handoff.md` and `docs/使用说明.md` only if smoke actually ran
- Do **not** modify `deploy/compose.yaml` worker `network_mode`, ports, or secret interpolation unless a contract test fails for a real bug

**Gate:** if `docker info` fails, write the skip in `docs/acceptance/compose-smoke.md` with the exact error and stop. Do not fake a pass.

- [ ] **Step 1: Contract tests still pass**

```bash
uv run --project backend pytest backend/tests/deploy/test_compose_contract.py -v
```

Expected: PASS (already 13 tests).

- [ ] **Step 2: If Docker exists, smoke**

```bash
docker compose --env-file deploy/compose.env -f deploy/compose.yaml up --build -d
curl -fsS http://127.0.0.1:8080/api/health
# worker has no published port and docker inspect NetworkMode == none
docker compose -f deploy/compose.yaml down
```

Use empty/local-only env from `deploy/compose.env.example` (copy to an untracked `deploy/compose.env`). `WIND_ENABLED=false`. Health may report Wind/LLM as down; that is acceptable. Frontend loopback bind `127.0.0.1:8080` must remain.

- [ ] **Step 3: Record evidence, then commit only the markdown**

```bash
git add docs/acceptance/compose-smoke.md handoff.md docs/使用说明.md
git commit -m "docs: record Compose smoke result or explicit skip"
```

---

### Task 8: Docs + handoff sync (after Tasks 1–6)

**Files:** `docs/使用说明.md` (研报入口 §6, 因子库), `handoff.md` (remove completed items; keep field-semantics residual only if any), `docs/工程优化记录.md` if you add a short note.

State literally what was verified. Do not claim Compose smoke or real Wind re-run unless those commands were executed in this work.

- [ ] **Step 1: Update remaining-work list in `handoff.md`**
- [ ] **Step 2: Commit**

```bash
git add docs/使用说明.md handoff.md docs/工程优化记录.md
git commit -m "docs: mark report workflow and library as wired"
```

---

## Out of scope

- Multi-factor index UI (`indices` service already has unit tests; no page in this plan).
- Changing `metric_definitions.yaml` review_status (handoff: docs-only review ≠ runtime registry).
- Replacing BM25 with embeddings.
- Publishing the frontend beyond loopback.
- Hidden golden-set rebuild.
- Git force-push / auto-push.

## Verification matrix (run before claiming done)

| After | Command | Expected |
|---|---|---|
| Task 1 | `uv run --project backend pytest backend/tests/api/test_reports_workflow.py backend/tests/reports -v` | PASS |
| Task 2 | `npm --prefix frontend test -- --run src/features/reports` | PASS |
| Task 3 | `uv run --project backend pytest backend/tests/api/test_library.py backend/tests/library -v` | PASS |
| Task 4 | `npm --prefix frontend test -- --run src/features/library src/features/workbench` | PASS |
| Task 5–6 | `uv run --project backend pytest backend/tests/wind -v` | PASS |
| Task 7 | compose contract + optional real smoke | PASS or documented skip |
| Any API change | export OpenAPI + `npm --prefix frontend run gen:api` + `npm --prefix frontend run lint` | clean |
| Do not regress | `uv run --project backend pytest backend/tests -q` and `npm --prefix frontend test -- --run` before the docs commit | last known bar was 647 passed / 12 skipped backend, 29 frontend — new tests add to this, do not drop it |

## Spec coverage (self-review)

| Remaining item in `handoff.md` | Tasks |
|---|---|
| 研报提取结果进入因子工作流闭环 | 1, 2, 8 |
| 因子库页面 + API | 3, 4, 8 |
| 字段语义增强（复权 vs close） | 5, 6, 8 |
| Docker/Compose 真实冒烟 | 7 |
| B4 / no full-report outbound | Task 1 manual path uses `FactorParser` on the typed formula only |
| Confirmation not skipped | Task 1 asserts state is not `searching_fields` |
| Library copy-not-reference / disputed gate | Task 3 reuses `FactorLibrary` |
| Worker still credential-less | Task 7 must not edit worker env |

No TBD / “handle edge cases later” left in the tasks above.
