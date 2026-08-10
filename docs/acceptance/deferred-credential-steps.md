# 延后的凭据依赖步骤

> 用户目标（2026-08-10）：先离线完成全部开发，凭据在开发结束后统一索取。
> 本文件是那份清单，随开发推进持续追加。**不要凭设计文档推断这些已经通过。**

## 前置：凭据轮换

**在执行下列任何一步之前**，必须由具备数据库管理权限的人轮换已暴露的 Wind 凭据，
新值只写入本地 `.env`（仓库为公开仓库，`.env` 已 gitignore）。

⚠️ 不要把凭据值粘贴进对话、日志、测试输出、提交信息或任何文档。

需要写入 `.env` 的项：

| 变量 | 用途 |
|---|---|
| `WIND_ENABLED=true` | 打开真实取数 |
| `WIND_HOST` / `WIND_PORT` / `WIND_USER` / `WIND_PASSWORD` / `WIND_DATABASE` | Wind MySQL 只读副本 |
| `SESSION_COOKIE_SECRET` | 32+ 字节随机串，非测试环境必填 |
| `KIMI_CODING_BASE_URL` / `KIMI_CODING_API_KEY` | Coding Plan 端点（优先） |
| `KIMI_METERED_API_KEY` / `KIMI_MODEL` | 按量 API 兜底 |

## 待执行清单

### Task 10 — 字段验证（三种形态各一）

```bash
uv run --project backend factor-platform verify-field ashareeodprices s_dq_close \
  --shape point_range
uv run --project backend factor-platform verify-field asharefinancialindicator s_fa_roe \
  --shape report_period
uv run --project backend factor-platform verify-field aindexmembers s_con_indate \
  --shape interval_overlap
```

预期：三种形态各自返回正确状态与对应采样统计。重点确认
`SCHEMA_VALID_NO_DATA_IN_SAMPLE` 在真实空区间上确实**不阻塞**。

离线状态：21 条单测全过（fake executor）。

### Task 9.5 — Wind 元数据核对

`backend/data/wind_field_units.yaml` 中全部条目当前为 `verified: false`。
接入真实库后需对以下两项做实测核对，它们各自都是 10,000 倍量级的静默错误来源：

- `ashareeodprices.s_dq_volume` —— 单位是股还是万股（直接影响换手率口径）
- 财报科目按元 vs 行情表按万元 —— 混用会差 10,000 倍

### Task 4.5 — 大模型接口实测

目前全部测试走 `FakeLLMProvider`，真实模型的结构化输出稳定性、成本、延迟均无实测数据。
接入后需确认 B4 出境过滤器不会误伤真实 prompt（离线已用 37 个黄金案例验证过）。

### Task 10.5 — 口径抽样确认（需带教老师，不需凭据）

`backend/data/metric_definitions.yaml` 中 9 条口径全部为 `review_status: unreviewed`。
每条已附 `reference_check` 说明如何核对。需老师逐条确认后改为 `reviewed` 并填写
`reviewer` / `reviewed_at` / `review_comment`。

在此之前，用这些口径算出的结果**可用于试算但不得作为正式发布**，界面必须标注「未复核」。

优先级最高的三条（各自都是量级或口径级的静默错误来源）：

| 口径 | 待确认 |
|---|---|
| `ROE_TTM` | Wind 存的是百分数还是比值（差 100 倍） |
| `REVENUE_YOY` | 单季度同比还是累计同比（两个口径） |
| `NET_PROFIT_YOY` | 去年同期为负时 Wind 如何处理（同比无经济意义） |

另有两条已登记为 `disputed`、平台直接拒绝，无需确认：`FLOAT_MV`、`CFO_WRONG_MAPPING`。

---

*后续任务的延后项在此继续追加。*

### Task 16 — P0 端到端真实取数

```bash
uv run --project backend factor-platform run-case momentum_20d --real-wind
uv run --project backend factor-platform run-case roe_ttm_rank --real-wind
```

`--real-wind` 的取数阶段尚未接线（当前会明确报错并指向本文件）。接线后需验证：
计划中的四个步骤按序执行、历史成分按调整日回溯、预热区间真实取到数据、
结果通过三层校验。

离线状态：全链已通（`backend/tests/e2e/test_cli_p0.py`），Wind 与模型为 fake，
但组件之间的每一道接缝都是真实的。
