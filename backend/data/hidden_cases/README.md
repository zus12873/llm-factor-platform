# Hidden cases (archived)

This directory holds **10 historical cases** that used to be gitignored as a
blind acceptance set. They were recovered from a local working copy on
2026-08-27 and committed so they are not lost with the machine.

They are **not** a blind set anymore: anyone with the repo can read them.
Do not treat a suite run against these files as the original 2026-08-10
blind acceptance. A new blind set, if needed, must be authored by someone
who did not tune the implementation on it.

| File | `case_id` |
|---|---|
| `h_current_ratio.json` | `h_current_ratio` |
| `h_ep_ratio.json` | `h_ep_ratio` |
| `h_illiq_ratio.json` | `h_illiq_ratio` |
| `h_oper_profit_growth.json` | `h_oper_profit_growth` |
| `h_pit_revenue.json` | `h_pit_revenue` |
| `h_roa_ttm.json` | `h_roa_ttm` |
| `h_sse50_members.json` | `h_sse50_members` |
| `h_triple_composite.json` | `h_triple_composite` |
| `h_vague_momentum_window.json` | `h_vague_momentum_window` |
| `h_vague_valuation_en.json` | `h_vague_valuation_en` |

Schema matches `backend/data/golden_cases/`. IDs are disjoint from the
golden set. `docs/acceptance/2026-08-10/hidden.json` is a historical run
report, not these inputs.
