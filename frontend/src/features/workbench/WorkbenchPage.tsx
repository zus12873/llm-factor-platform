/**
 * The factor workbench.
 *
 * Left column: what the platform produced — the canonical formula, the plan, the
 * result. Right column: where the session is and what it needs from you.
 *
 * The canonical formula is displayed as the confirmed artifact, and the model's
 * prose sits beneath it labelled as explanation. They are not two formulas: one
 * is what runs, the other is commentary, and the layout has to make that
 * obvious or a user will confirm the prose.
 */
import { useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation } from "@tanstack/react-query"
import { Alert, Button, Card, Col, Descriptions, Row, Space, Tag, Typography } from "antd"
import { apiClient, ApiError, type ResearchRequest } from "../../api/client"
import { StatusBanner } from "../../components/StatusBanner"
import { ResearchForm } from "./ResearchForm"
import { useSession } from "./useSession"
import { blockingQuestions, toWorkflowView } from "./sessionView"
import { WorkflowSteps } from "./WorkflowSteps"

export function WorkbenchPage() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const { snapshot, refresh } = useSession(sessionId)
  const [conflict, setConflict] = useState(false)
  const [requestError, setRequestError] = useState<ApiError | null>(null)

  const start = useMutation({
    mutationFn: async (request: ResearchRequest) => {
      const id = sessionId ?? `s-${Date.now().toString(36)}`
      const created = sessionId ? snapshot! : await apiClient.createSession(id)
      return apiClient.submitMessage(id, created.version, request)
    },
    onSuccess: (result) => {
      setConflict(false)
      setRequestError(null)
      if (!sessionId) navigate(`/workbench/${result.session_id}`)
      else refresh()
    },
    onError: (error) => {
      // A conflict is not a failure; someone else moved the session on. Anything
      // else must be shown — a click that changes nothing is the worst outcome,
      // because the user cannot tell success from silence.
      if (error instanceof ApiError && error.isStaleVersion) {
        setConflict(true)
        setRequestError(null)
      } else {
        setRequestError(error instanceof ApiError ? error : null)
      }
    },
  })

  const view = snapshot
    ? toWorkflowView(snapshot)
    : {
        activeStep: 0,
        status: "wait" as const,
        message: "填写研究想法后开始",
        awaitingUser: true,
        canCancel: false,
      }
  const blocking = snapshot ? blockingQuestions(snapshot) : []

  return (
    <Row gutter={16}>
      <Col span={15}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <Card title="研究想法" size="small">
            <ResearchForm
              submitting={start.isPending}
              onSubmit={(request) => start.mutate(request)}
            />
          </Card>

          {snapshot?.factor_spec && (
            <Card title="因子定义" size="small">
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="正式公式">
                  <Typography.Text code>
                    {snapshot.factor_spec.canonical_formula}
                  </Typography.Text>
                  <div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      由 AST 确定性渲染，这就是执行依据
                    </Typography.Text>
                  </div>
                </Descriptions.Item>
                {snapshot.factor_spec.formula_explanation && (
                  <Descriptions.Item label="模型说明">
                    <Typography.Text type="secondary">
                      {snapshot.factor_spec.formula_explanation}
                    </Typography.Text>
                    <div>
                      <Tag>仅供阅读，不参与执行</Tag>
                    </div>
                  </Descriptions.Item>
                )}
                <Descriptions.Item label="时间口径">
                  {snapshot.factor_spec.time_convention?.signal_date} 形成信号 →{" "}
                  {snapshot.factor_spec.time_convention?.trade_date} 交易 →{" "}
                  {snapshot.factor_spec.time_convention?.execution_price}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          )}

          {snapshot?.plan && (
            <Card title="取数计划" size="small">
              <Space direction="vertical">
                {(snapshot.plan.steps ?? []).map((step, index) => (
                  <Typography.Text key={index}>
                    {index + 1}. <Typography.Text code>{step.tool}</Typography.Text>{" "}
                    <Typography.Text type="secondary">{step.purpose}</Typography.Text>
                  </Typography.Text>
                ))}
                <Typography.Text type="secondary">
                  预热区间自 {snapshot.plan.warmup_start} 起
                </Typography.Text>
              </Space>
            </Card>
          )}
        </Space>
      </Col>

      <Col span={9}>
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          <StatusBanner
            view={view}
            conflict={conflict}
            requestError={requestError}
            onReload={refresh}
          />

          {blocking.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="需要你确认口径"
              description={
                <Space direction="vertical">
                  {blocking.map((question) => (
                    <div key={question.question_id}>
                      <div>{question.question}</div>
                      <Space wrap>
                        {(question.options ?? []).map((option) => (
                          <Tag key={option}>{option}</Tag>
                        ))}
                      </Space>
                    </div>
                  ))}
                </Space>
              }
            />
          )}

          <Card title="流程状态" size="small">
            <WorkflowSteps view={view} />
            {view.canCancel && snapshot && (
              <Button
                danger
                size="small"
                onClick={() => apiClient.cancel(snapshot.session_id, snapshot.version)}
              >
                取消执行
              </Button>
            )}
          </Card>

          {snapshot && (
            <Card title="会话" size="small">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="ID">{snapshot.session_id}</Descriptions.Item>
                <Descriptions.Item label="版本">{snapshot.version}</Descriptions.Item>
              </Descriptions>
            </Card>
          )}
        </Space>
      </Col>
    </Row>
  )
}
