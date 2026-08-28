# 指标口径人工确认记录

## 背景

- 下列指标此前在验收文档中的审核状态为 `unreviewed`。
- 用户已向带教老师完成人工确认；老师反馈当前整理的指标定义、Wind 字段映射和时间口径没有问题，可以按照当前定义使用。
- 本验收记录中的指标审核状态据此调整为 `reviewed`。
- 当前项目继续使用已有指标定义，本次不改动指标定义、字段映射、因子公式或计算逻辑。
- 本记录不添加未提供的老师姓名、签字、确认日期或审批信息。

## 已确认指标

| 指标 | 已确认 Wind 字段 | 审核状态 | 说明 |
|---|---|---|---|
| `ROE_TTM` | `AShareTTMHis.S_FA_ROE_TTM` | `reviewed` | 按当前净资产收益率（TTM）定义使用。 |
| `ROA_TTM` | `AShareTTMHis.S_FA_ROA_TTM` | `reviewed` | 按当前总资产收益率（TTM）定义使用。 |
| `CFO_TO_PROFIT` | 分子：`AShareCashFlow.NET_CASH_FLOWS_OPER_ACT`；分母：`AShareIncome.NET_PROFIT_EXCL_MIN_INT_INC` | `reviewed` | 按当前经营活动现金流量净额与利润的跨表计算定义使用。 |
| `PE_TTM` | `AShareEODDerivativeIndicator.S_VAL_PE_TTM` | `reviewed` | 按当前市盈率（TTM）定义使用。 |
| `PB` | `AShareEODDerivativeIndicator.S_VAL_PB_NEW` | `reviewed` | 按当前市净率定义使用。 |
| `PS_TTM` | `AShareEODDerivativeIndicator.S_VAL_PS_TTM` | `reviewed` | 按当前市销率（TTM）定义使用。 |
| `SALES_GROWTH_YOY` | `AShareFinancialIndicator.S_FA_YOY_OR` | `reviewed` | 对应已有营业收入同比增长口径。 |
| `PROFIT_GROWTH_YOY` | 候选：`AShareFinancialIndicator.S_FA_YOYNETPROFIT`、`AShareFinancialIndicator.S_FA_YOYOP` | `reviewed` | 当前利润增长指标口径及候选字段集合已经完成确认，不在本次文档更新中改选字段。 |
| `CFO_GROWTH_YOY` | `AShareFinancialIndicator.S_FA_YOYOCF` | `reviewed` | 按当前经营现金流同比增长定义使用。 |
| `S_DQ_VOLUME` | `AShareEODPrices.S_DQ_VOLUME` | `reviewed` | 按当前成交量字段及已有单位口径使用。 |

现有验收材料中的名称对应关系为：

- `SALES_GROWTH_YOY` 对应已有登记名称 `REVENUE_YOY`。
- `PROFIT_GROWTH_YOY` 对应已有的 `NET_PROFIT_YOY` / `OPERATING_PROFIT_YOY` 两类利润增长字段。

## 统一时间口径

- 财务指标按照实际公告可得时间使用。
- 不使用未来修订数据回填历史。
- 日行情和估值指标使用 T 日收盘后数据，最早用于 T+1。
- 跨表计算指标要求各组成字段属于同一报告期，并以较晚可用时间为准。

## 原始文档更新边界

本记录初次建立时仅更新了 `docs/acceptance` 下的审核记录，没有修改运行时指标注册文件；也没有填写老师姓名、签字、日期或其他未提供的审批信息。

## 后续运行时同步

在用户再次明确确认已取得带教老师授权后，运行时登记已同步：

- `backend/data/metric_definitions.yaml` 中原有的 9 个对应口径已由 `unreviewed` 更新为 `reviewed`；
- 新增登记 `CFO_GROWTH_YOY` 与 `S_DQ_VOLUME`，状态为 `reviewed`；
- `backend/data/wind_field_units.yaml` 中 `S_DQ_VOLUME` 的既有“手”单位口径已标记为已确认；
- `FLOAT_MV` 与 `CFO_WRONG_MAPPING` 是已知错误映射，不在本次老师确认清单内，继续保持 `disputed`；
- 未提供的老师姓名和确认日期仍不补造，运行时记录通过本文件追溯人工确认事实。

本次同步没有修改已确认的 Wind 字段、因子公式、计算逻辑、执行结果或历史验收结论。
