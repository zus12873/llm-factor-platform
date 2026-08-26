/**
 * The result pane.
 *
 * Coverage is shown next to the numbers, not below them. A factor computed on
 * 30% of the universe is a different object from one computed on all of it, and
 * a distribution chart alone hides that completely.
 *
 * Review status travels with the result: a factor built on an unreviewed metric
 * says so here, because the label is only useful if it is attached to the number
 * rather than remembered. Publish is offered only on a completed session;
 * `disputed` findings (and a non-completed result) disable it. Unreviewed may
 * still publish — the library stores that label.
 */
import { useState } from "react"
import { Link } from "react-router-dom"
import { Alert, Button, Card, Col, Descriptions, Row, Space, Statistic, Tag } from "antd"
import { apiClient, ApiError } from "../../api/client"
import type { components } from "../../api/schema"
import { ValidationFindings } from "./ValidationFindings"

type ExecutionResult = components["schemas"]["ExecutionResult"]
type Finding = components["schemas"]["ValidationFinding"]

interface Props {
  result: ExecutionResult
  artifactUri?: string | null
  sessionId?: string
  sessionState?: string
}

function collectFindings(result: ExecutionResult): Finding[] {
  return [
    ...(result.data_validation?.findings ?? []),
    ...(result.formula_validation?.findings ?? []),
    ...(result.result_validation?.findings ?? []),
  ]
}

function hasDisputed(result: ExecutionResult, findings: Finding[]): boolean {
  if (findings.some((finding) => finding.code.includes("disputed"))) {
    return true
  }
  const statuses = result.resource_stats?.metric_review_status
  if (statuses && typeof statuses === "object") {
    return Object.values(statuses as Record<string, string>).includes("disputed")
  }
  return false
}

export function ResultPane({ result, artifactUri, sessionId, sessionState }: Props) {
  const findings = collectFindings(result)
  const reviewStatuses = Object.entries(
    (result.resource_stats?.metric_review_status as Record<string, string> | undefined) ?? {},
  )
  const unreviewed =
    findings.some((f) => f.code === "unreviewed_metric") ||
    reviewStatuses.some(([, status]) => status === "unreviewed")
  const canOfferPublish = sessionState === "completed" && Boolean(sessionId)
  const publishBlocked = result.status !== "completed" || hasDisputed(result, findings)
  const [publishing, setPublishing] = useState(false)
  const [published, setPublished] = useState(false)
  const [publishError, setPublishError] = useState<ApiError | null>(null)

  const publish = async () => {
    if (!sessionId || publishBlocked) return
    setPublishing(true)
    setPublishError(null)
    try {
      await apiClient.publishSession(sessionId)
      setPublished(true)
    } catch (error) {
      setPublishError(
        error instanceof ApiError
          ? error
          : new ApiError(0, "client_error", "发布失败"),
      )
    } finally {
      setPublishing(false)
    }
  }

  return (
    <Card
      title="计算结果"
      size="small"
      extra={
        unreviewed ? <Tag color="orange">含未复核口径，入库将标注「未复核」</Tag> : null
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
        {typeof result.resource_stats?.source === "string" && (
          <Descriptions.Item label="数据源">
            <Tag color="green">{result.resource_stats.source}</Tag>
          </Descriptions.Item>
        )}
        {reviewStatuses.length > 0 && (
          <Descriptions.Item label="口径复核">
            {reviewStatuses.map(([metric, status]) => (
              <Tag key={metric} color={status === "reviewed" ? "green" : "orange"}>
                {metric}: {status}
              </Tag>
            ))}
          </Descriptions.Item>
        )}
      </Descriptions>

      <div style={{ marginTop: 16 }}>
        <ValidationFindings findings={findings} />
      </div>

      {canOfferPublish && (
        <div style={{ marginTop: 16 }}>
          <Space>
            <Button
              type="primary"
              loading={publishing}
              disabled={publishBlocked}
              onClick={() => void publish()}
            >
              发布到因子库
            </Button>
            {published && <Link to="/library">前往因子库</Link>}
          </Space>
          {publishError && (
            <Alert
              type="error"
              showIcon
              message={publishError.message}
              style={{ marginTop: 8 }}
            />
          )}
        </div>
      )}
    </Card>
  )
}
