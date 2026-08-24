# 真实 k3 自然语言案例

Provider：`kimi-coding-plan`  
Model：`k3`  
Fake：未使用

## 案例

| 类别 | 想法摘要 | 实际结果 |
| --- | --- | --- |
| 明确中文 | 过去 20 日动量 | 不阻塞；`rolling_return(close, window=20)` |
| 明确中文 | 低波动 | 不阻塞；`rolling_std(rolling_return(close, window=1), window=20)` |
| 明确中文 | ROE 横截面质量 | 不阻塞；`rank(roe_ttm)` |
| 歧义中文 | 盈利质量 | 正确阻塞，要求选择 ROE/ROA/CFO_TO_PROFIT 等口径 |
| 歧义中文 | 估值 | 正确阻塞，要求选择 PE/PB/PS 等口径 |
| 歧义中文 | 增长 | 正确阻塞，要求选择收入/净利润/营业利润增长口径 |
| 英文 | 20-day momentum | 不阻塞；AST 合法 |
| 复合 | 高 ROE + 低 PE | 不阻塞；`add(rank(roe_ttm), rank(negative(pe_ttm)))` |

## 汇总指标

| 指标 | 结果 |
| --- | ---: |
| FactorSpec parse success | 8/8，100% |
| AST 后端校验合法 | 8/8，100% |
| Blocking recall | 100% |
| Blocking precision | 100% |
| Unnecessary question rate | 0% |
| Retry | 0 |
| Average latency | 14,996.1 ms |
| P95 latency | 23,937 ms |
| Input tokens | 10,612 |
| Output tokens | 3,432 |
| Total tokens | 14,044 |

## 解释边界

8 个结果都能被后端校验并由 AST 确定性渲染 canonical formula。与 Golden 中某个唯一 AST 的逐节点完全相等只有 1/8；其余多为合法、可解释但不同的等价/近似表达。这一差异被如实记录，没有修改 Golden expected answer 来抬高指标。
