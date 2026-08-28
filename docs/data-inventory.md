# 仓库数据资产清单

日期：2026-08-27  
原则：公开仓库只收**无密钥、无商业授权正文、无运行时产物**的材料。

## 已在 GitHub `main`

| 路径 | 内容 |
|---|---|
| `backend/data/golden_cases/` | 37 个黄金案例 |
| `backend/data/hidden_cases/` | 10 个历史案例（2026-08-27 入库；**不再是盲测**） |
| `backend/data/metric_definitions.yaml` | 口径注册表（老师确认清单对应的 11 项为 `reviewed`；两个已知错误映射为 `disputed`） |
| `backend/data/wind_aliases.yaml` | 手写中文别名 → Wind 表.字段 |
| `backend/data/wind_field_units.yaml` | 单位 overlay（`s_dq_volume` 已确认；其余条目仍多为 `verified: false`） |
| `backend/data/generated/wind_fields.jsonl` | **仅** `table` + `field` 标识符，无中文释义 |
| `backend/tests/fixtures/reports/` | 合法测试 PDF |
| `docs/acceptance/` | 验收文档与脱敏截图 |

## 本机有、故意不入库

| 路径 | 原因 |
|---|---|
| `windquery/`、`windquery.rar` | Wind 数据字典受商业授权，不可再分发 |
| `backend/data/generated/wind_metadata.jsonl` | 含 WDS 中文名称与描述，属字典衍生物 |
| `imgs/` | 内部需求文档与 UI 参考图，README 写明不随仓库分发 |
| `.env`、`deploy/compose.env` | 密钥 |
| `data/runtime/`、`data/artifacts/`、`*.parquet`、`*.db` | 运行时产物，可能含行情 |
| `.superpowers/` | SDD 过程草稿，不是产品资产 |

## 已跟踪但仍列在 `.gitignore` 以免覆盖

`Wind取数尝试.ipynb` 仓库内版本已脱敏（`os.environ.get("WIND_*")`）。gitignore 防止把填了明文的本地副本再次提交。
