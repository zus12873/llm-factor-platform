/**
 * The result pane.
 *
 * Coverage is shown next to the numbers, not below them. A factor computed on
 * 30% of the universe is a different object from one computed on all of it, and
 * a distribution chart alone hides that completely.
 *
 * Review status travels with the result: a factor built on an unreviewed metric
 * says so here, because the label is only useful if it is attached to the number
 * rather than remembered.
 */
import { Card, Col, Descriptions, Row, Statistic, Tag } from "antd"
import type { components } from "../../api/schema"
import { ValidationFindings } from "./ValidationFindings"

type ExecutionResult = components["schemas"]["ExecutionResult"]

interface Props {
  result: ExecutionResult
  artifactUri?: string | null
}

export function ResultPane({ result, artifactUri }: Props) {
  const findings = [
    ...(result.data_validation?.findings ?? []),
    ...(result.formula_validation?.findings ?? []),
    ...(result.result_validation?.findings ?? []),
  ]
  const unreviewed = findings.some((f) => f.code === "unreviewed_metric")

  return (
    <Card
      title="计算结果"
      size="small"
      extra={
        unreviewed ? <Tag color="orange">含未复核口径，不得作为正式发布</Tag> : null
      }
    >
      <Row gutter={16}>
        <Col span={8}>
          <Statistic title="状态" value={result.status} />
        </Col>
        <Col span={8}>
          <Statistic
            title="非空覆盖率"
            value={
              typeof result.resource_stats?.non_null_rate === "number"
                ? (result.resource_stats.non_null_rate as number) * 100
                : 0
            }
            precision={1}
            suffix="%"
          />
        </Col>
        <Col span={8}>
          <Statistic
            title="结果行数"
            value={(result.resource_stats?.rows as number) ?? 0}
          />
        </Col>
      </Row>

      <Descriptions column={1} size="small" style={{ marginTop: 16 }}>
        {artifactUri && (
          <Descriptions.Item label="工件">{artifactUri}</Descriptions.Item>
        )}
        {result.log_summary && (
          <Descriptions.Item label="日志">{result.log_summary}</Descriptions.Item>
        )}
      </Descriptions>

      <div style={{ marginTop: 16 }}>
        <ValidationFindings findings={findings} />
      </div>
    </Card>
  )
}
