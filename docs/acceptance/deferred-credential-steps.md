# 延后的凭据依赖步骤

> 用户目标（2026-08-10）：先离线完成全部开发，凭据在开发结束后统一索取。
> 本文件是那份清单。**2026-08-27 已按仓库现存证据改成「已完成 / 仍未完成」**，不要凭旧段落推断 `run-case --real-wind` 仍未接线。

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
| `KIMI_CODING_BASE_URL` / `KIMI_CODING_API_KEY` | Coding Plan 端点 |
| `KIMI_METERED_API_KEY` / `KIMI_MODEL` | 按量 API（本仓库无专项通过证据） |

## 已完成（有仓库内证据）

### Task 16 — P0 端到端真实取数

```bash
uv run --project backend factor-platform run-case momentum_20d --real-wind
```

**已接线并真实跑通**（动量等案例）。不是「取数阶段尚未接线」。证据：[`2026-08-14-coding-final/README.md`](2026-08-14-coding-final/README.md)（真实 Adapter、signed manifest、隔离 Worker、三层校验）。

离线全链仍在：`backend/tests/e2e/test_cli_p0.py`。

### Task 4.5 — Coding Plan 实测

**Coding Plan / k3 已真实验收**（鉴权、Chat、FactorSpec、工具调用、自然语言 / 研报 / 浏览器）。证据同上。

按量 / Open Platform 专项报告不在本仓库。不要把断裂路径 `docs/acceptance/2026-08-14/` 当成 live 状态。

### Task 10.5 — 口径抽样确认（文档层）

带教老师已确认当前指标定义可用。记录：[`metric-review-evidence.md`](metric-review-evidence.md)。

**运行时 YAML 未改**：`backend/data/metric_definitions.yaml` 里登记口径仍是 `unreviewed`（另有 `FLOAT_MV`、`CFO_WRONG_MAPPING` 为 `disputed`）。文档 `reviewed` ≠ 代码闸门。若要运行时显示已复核，需另行授权改注册表。

## 仍未完成

### Task 10 — 字段验证（三种形态各一，本机定向命令）

```bash
uv run --project backend factor-platform verify-field ashareeodprices s_dq_close \
  --shape point_range
uv run --project backend factor-platform verify-field asharefinancialindicator s_fa_roe \
  --shape report_period
uv run --project backend factor-platform verify-field aindexmembers s_con_indate \
  --shape interval_overlap
```

离线：fake executor 单测已过。Coding Plan 终验覆盖了点时 / 样本边界，但本文件这三条 CLI **没有**单独的本机通过记录。不要把终验 PASS 改写成「这三条命令已贴出输出」。

### Task 9.5 — Wind 元数据核对

`backend/data/wind_field_units.yaml` 中条目仍为 `verified: false`（以文件为准）。接入真实库后仍应对以下两项做实测核对，它们各自都是 10,000 倍量级的静默错误来源：

- `ashareeodprices.s_dq_volume` —— 单位是股还是万股（直接影响换手率口径）
- 财报科目按元 vs 行情表按万元 —— 混用会差 10,000 倍

老师文档确认含 `S_DQ_VOLUME`（见 metric-review-evidence），**没有**改单位 YAML。

### Docker / Compose 真实冒烟

契约 13 条已过。真实 `compose up`：**SKIPPED**（本机无 `docker` CLI，exit 127）。证据：[`compose-smoke.md`](compose-smoke.md)。契约通过 ≠ 冒烟通过。
