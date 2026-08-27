# 执行命令与结果

所有凭据均只在临时进程环境中注入；下列记录不含 Key、Authorization、Wind 密码或完整 DSN。临时诊断脚本在完成后已删除。

## 真实组件

| 命令/动作 | 退出码或状态 | 关键非敏感结果 |
| --- | ---: | --- |
| `GET https://api.kimi.com/coding/v1/models`（httpx） | HTTP 200 | `k3` 可用 |
| `POST https://api.kimi.com/coding/v1/chat/completions`（httpx，model=`k3`） | HTTP 200 | completion 可解析，finish reason=`stop` |
| `uv run --project backend python %TEMP%\coding_plan_project_probe_20260814.py` | 0 | 项目真实 Provider=`kimi-coding-plan` / `k3` |
| `uv run --project backend python %TEMP%\coding_provider_post_fix_and_tools_20260814.py` | 0 | 项目响应与真实 tool call 均通过 |
| `uv run --project backend python %TEMP%\coding_real_factor_cases_20260814.py` | 0 | 8 个真实 k3 自然语言案例完成 |
| `uv run --project backend python %TEMP%\coding_real_report_cases_20260814.py` | 0 | 中英文 PDF 的真实 k3 抽取完成 |
| `uv run --project backend factor-platform run-case momentum_20d --real-wind` | 0 | real Wind → manifest → Worker → validators → result |
| 浏览器经 React UI 完成五场景 | PASS | 顶栏、确认、级联失效、真实字段与真实结果均实际检查 |

## 最终自动化回归

| 命令 | 退出码 | 结果 |
| --- | ---: | --- |
| `uv run --project backend ruff check backend/src backend/tests` | 0 | All checks passed |
| `uv run --project backend mypy backend/src` | 0 | 85 source files，无问题 |
| `uv run --project backend pytest backend/tests -q` | 0 | 647 passed，12 skipped |
| `npm --prefix frontend test -- --run` | 0 | 29 passed |
| `npm --prefix frontend run build` | 0 | production build 成功；只有 chunk-size 提示 |
| `npm --prefix frontend audit --audit-level=low` | 0 | 0 vulnerabilities |
| `uv run --project backend factor-platform run-case-suite --set golden` | 0 | 37/37 passed |
| `docker --version` | 1 | 命令不存在，`DEPLOYMENT_ENVIRONMENT_PENDING` |

第一次完整 mypy 门禁曾以 exit 1 发现 `reports/extractor.py` 对可空 AST 的类型收窄缺失；加入确定性非空断言后，重新执行全部门禁并得到上表最终结果。

终验当时原始隐藏案例未入库。2026-08-27 已归档本机 10 个 `backend/data/hidden_cases/*.json`；它们不再是盲测。`docs/acceptance/2026-08-10/hidden.json` 仍是历史运行报告而非输入。

当前目录没有 `.git`；`git status` 返回“not a git repository”。本轮没有 commit、push 或 PR，也不声明分支或远端一致性。
