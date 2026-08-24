# 本轮源码修改

修改遵循“先实测、后补线”的原则；没有重构整体架构，没有开放任意 SQL，也没有放宽 manifest、PIT 或 disputed 闸门。

## Coding Provider

- `llm/openai_compatible.py`：支持不发送 temperature；保证结构化 schema 在已有 system message 时仍附加。
- `llm/factory.py`：Coding Plan/k3 使用兼容参数；Metered Provider 能力保留。
- 专项测试覆盖 factory、key loading、health、解析、错误转换、Fake 隔离和 Secret 不泄漏。

## 澄清、状态与确定性边界

- `factor/clarification.py`：基于原始用户措辞检测歧义；人工答案确定性改写变量和解释。
- `orchestration/reducer.py`：持久化字段候选；上游修订正确使字段、计划、manifest 和结果失效。
- `orchestration/service.py`：补齐澄清解析、字段发现、实时 schema 验证、真实 Wind 执行和验证报告回写。
- 后端继续从 AST 渲染 canonical formula，不执行模型自由文本。

## 真实 Wind 接线

- `execution/real_wind.py`：已确认计划 → Wind Adapter → raw/aligned parquet → signed manifest → Worker → factor output → 三层校验。
- `wind/planner.py`：已注册的物理价格字段正确复用受控 `wind.get_price`，包括复权价映射。
- `execution/runtime.py`：预热窗口用于计算，但最终输出裁切到计划签名的开始/结束日期。
- Worker 不持有 Wind/LLM 凭据且不连接数据库；数据库连接在 Worker 启动前关闭。

## 研报

- `reports/extractor.py`：保留 bbox、direction、formula AST 和 typed variables；缺失可信字段或低置信度时进入人工确认；补充 AST 非空类型收窄。

## API 与前端

- 新增澄清解析、字段发现和真实 Wind 执行端点；OpenAPI 同步生成。
- Workbench 接通澄清、公式确认、真实字段、manifest、执行和结果页。
- 顶栏显示实际 provider/model 与真实 Wind 状态。
- ResultPane 显示 source、coverage、review status 和未复核警告。

## 安全不变量

- 没有 Fake 成功冒充真实组件；
- 没有任意 SQL 通道；
- 没有 `eval`/`exec`；
- 没有把 Wind、LLM 凭据交给 Worker；
- 没有把 Wind 原始数据发送给模型；
- 没有把任何 `unreviewed` 指标改成 `reviewed`；
- 没有 commit 或 push。
