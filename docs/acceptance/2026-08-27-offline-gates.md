# Offline gates

Two bars, do not mix the pytest counts.

The last real Wind / Kimi Coding Plan / browser bar remains [`2026-08-14-coding-final/README.md`](2026-08-14-coding-final/README.md). This file does **not** claim Compose smoke, real Wind, real Kimi, or the browser five-scenario.

## Local six-command bar

Date: 2026-08-27
SHA: `b6397f3e5855fde381adf1c9d30f7683cafd1366` (product code; later commits through `8d771b9` are docs-only)

| Command | Exit | Summary |
|---|---|---|
| ruff | 0 | All checks passed |
| mypy | 0 | Success: no issues found in 87 source files |
| pytest backend/tests | 0 | 701 passed |
| vitest --run | 0 | Tests 41 passed |
| npm run lint | 0 | tsc --noEmit clean |
| npm run build | 0 | production build ok |

Post-merge verify on `main` `8d771b9` (2026-08-28): `pytest backend/tests -q` → **701 passed**; `npm --prefix frontend test -- --run` → **41 passed**. ruff / mypy / lint / build were not re-run locally on that SHA.

Local 701 includes licensed-dictionary tests present on the development machine.

## GitHub Actions (`offline-gates`)

Date: 2026-08-27 (run UTC); recorded 2026-08-28
SHA: `8d771b9739a0c87d6112f563e9cb7bbf81d8ac8b`
Event: `push` to `main`
Run: https://github.com/zus12873/llm-factor-platform/actions/runs/33098063707
Conclusion: **success** (jobs `backend` and `frontend`)

| Step | Exit | Summary |
|---|---|---|
| ruff | 0 | All checks passed |
| mypy | 0 | Success: no issues found in 87 source files |
| pytest backend/tests | 0 | **697 passed, 4 skipped**, 3 warnings in 17.51s |
| vitest --run | 0 | Tests 41 passed (41) |
| npm run lint | 0 | `tsc --noEmit` (job success) |
| npm run build | 0 | ✓ built in 5.27s |

4 skipped on Actions is expected: `ubuntu-latest` has no licensed `windquery/`. Do not write 701 as the Actions count.
