# Reports and library UI regression (component tests)

Date: 2026-08-27
Parent SHA: 19de7d743da46ae2c236557caa15c8c5e517467a (`docs: record offline gate rerun after remaining-todo fixes`)

Evidence from Vitest feature tests only. No backend, Vite, browser click-through, Compose smoke, real Wind, real Kimi, or GitHub Actions were run for this file.

## Command

```bash
npm --prefix frontend test -- --run src/features/reports/ReportsPage.test.tsx src/features/library/LibraryPage.test.tsx src/features/workbench/confirmations.test.tsx
```

| Result | Value |
|---|---|
| Exit code | 0 |
| Test files | 3 passed (3) |
| Tests | 29 passed (29) |
| Duration | ~4.9s |

jsdom stderr (`window.getComputedStyle` / Ant Design table scrollbar) appeared during Library and FieldCandidateTable renders; it did not fail any assertion.

## Four assertions locked by the suite

| # | Assertion | Where |
|---|---|---|
| 1 | 「进入因子工作流」 stays disabled until formula gate + dates | `ReportsPage.test.tsx` — disabled after upload with empty envelope; remains disabled for low-confidence formula until typed confirmation + envelope; enabled only after both |
| 2 | Enabled path POSTs `/api/reports/{id}/sessions` | `ReportsPage.test.tsx` — after fill + click, `enterCall` URL is `/api/reports/abc/sessions` |
| 3 | LibraryPage is not 「待实现」 | `LibraryPage.test.tsx` — renders real list (factor name + 未复核) and version detail (formula, hashes, provenance, artifact path); no 「待实现」 copy in frontend |
| 4 | Completed ResultPane unreviewed copy is `入库将标注「未复核」`, not `不得作为正式发布` | `confirmations.test.tsx` — completed + unreviewed shows `/入库将标注「未复核」/`; `queryByText(/不得作为正式发布/)` is null |

## Explicit non-claims

This evidence does **not** claim Compose smoke, real Wind, real Kimi, GitHub Actions, or a live browser click-through.
