/** The end-to-end factor workbench, driven solely by the backend session state. */
import { useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useMutation } from "@tanstack/react-query"
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  InputNumber,
  Modal,
  Row,
  Space,
  Tag,
  Typography,
} from "antd"
import {
  apiClient,
  ApiError,
  type FactorSpecIn,
  type FieldSelection,
  type ResearchRequest,
} from "../../api/client"
import { StatusBanner } from "../../components/StatusBanner"
import { ClarificationCard } from "./ClarificationCard"
import { FieldCandidateTable } from "./FieldCandidateTable"
import { FormulaConfirmation } from "./FormulaConfirmation"
import { ResearchForm } from "./ResearchForm"
import { ResultPane } from "./ResultPane"
import { useSession } from "./useSession"
import { blockingQuestions, toWorkflowView } from "./sessionView"
import { WorkflowSteps } from "./WorkflowSteps"

export function WorkbenchPage() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const { snapshot, refresh } = useSession(sessionId)
  const [conflict, setConflict] = useState(false)
  const [requestError, setRequestError] = useState<ApiError | null>(null)
  const [revisionOpen, setRevisionOpen] = useState(false)
  const [revisionWindow, setRevisionWindow] = useState<number | null>(null)

  const mutationSucceeded = () => {
    setConflict(false)
    setRequestError(null)
    refresh()
  }
  const mutationFailed = (error: unknown) => {
    if (error instanceof ApiError && error.isStaleVersion) {
      setConflict(true)
      setRequestError(null)
    } else {
      setRequestError(
        error instanceof ApiError
          ? error
          : new ApiError(0, "client_error", "操作失败，请查看后端状态"),
      )
    }
  }

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
    onError: mutationFailed,
  })

  const resolveClarification = useMutation({
    mutationFn: ({ answers, version }: { answers: Record<string, string>; version: number }) =>
      apiClient.resolveClarification(snapshot!.session_id, version, answers),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const confirmFormula = useMutation({
    mutationFn: ({ spec, version }: { spec: FactorSpecIn; version: number }) =>
      apiClient.confirmFormula(snapshot!.session_id, version, spec),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const discoverFields = useMutation({
    mutationFn: (version: number) =>
      apiClient.discoverFields(snapshot!.session_id, version),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const confirmFields = useMutation({
    mutationFn: ({
      expected_version,
      selections,
    }: {
      expected_version: number
      selections: FieldSelection[]
    }) => apiClient.confirmFields(snapshot!.session_id, expected_version, selections),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const buildManifest = useMutation({
    mutationFn: (version: number) =>
      apiClient.buildManifest(
        snapshot!.session_id,
        version,
        snapshot!.request as ResearchRequest,
      ),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const executeRealWind = useMutation({
    mutationFn: (version: number) =>
      apiClient.executeRealWind(snapshot!.session_id, version),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const reviseFormula = useMutation({
    mutationFn: ({ spec, version }: { spec: FactorSpecIn; version: number }) =>
      apiClient.reviseFormula(snapshot!.session_id, version, spec),
    onSuccess: () => {
      setRevisionOpen(false)
      mutationSucceeded()
    },
    onError: mutationFailed,
  })
  const reviseFields = useMutation({
    mutationFn: (version: number) =>
      apiClient.reviseFields(
        snapshot!.session_id,
        version,
        snapshot!.field_selections ?? [],
      ),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })
  const cancel = useMutation({
    mutationFn: (version: number) => apiClient.cancel(snapshot!.session_id, version),
    onSuccess: mutationSucceeded,
    onError: mutationFailed,
  })

  const openFormulaRevision = () => {
    const window = firstRollingWindow(snapshot?.factor_spec?.formula_ast)
    setRevisionWindow(window ?? 20)
    setRevisionOpen(true)
  }
  const applyFormulaRevision = () => {
    if (!snapshot?.factor_spec || revisionWindow === null) return
    const revised = JSON.parse(JSON.stringify(snapshot.factor_spec)) as FactorSpecIn
    if (!replaceFirstRollingWindow(revised.formula_ast, revisionWindow)) {
      setRequestError(
        new ApiError(0, "formula_not_revisionable", "当前公式没有可调整的滚动窗口"),
      )
      return
    }
    reviseFormula.mutate({ spec: revised, version: snapshot.version })
  }

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
  const mayRevise =
    snapshot &&
    [
      "waiting_field_confirmation",
      "planning_functions",
      "code_ready",
      "completed",
      "failed",
    ].includes(snapshot.state)

  return (
    <>
      <Row gutter={16}>
        <Col span={15}>
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Card title="研究想法" size="small">
              <ResearchForm
                submitting={start.isPending}
                onSubmit={(request) => start.mutate(request)}
              />
            </Card>

            {snapshot?.state === "waiting_formula_confirmation" && snapshot.factor_spec && (
              <FormulaConfirmation
                spec={snapshot.factor_spec}
                sessionVersion={snapshot.version}
                submitting={confirmFormula.isPending}
                onConfirm={(spec, version) => confirmFormula.mutate({ spec, version })}
                onRevise={openFormulaRevision}
              />
            )}

            {snapshot?.factor_spec && snapshot.state !== "waiting_formula_confirmation" && (
              <Card
                title="因子定义"
                size="small"
                extra={mayRevise ? <Button onClick={openFormulaRevision}>修改公式</Button> : null}
              >
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
                      <div><Tag>仅供阅读，不参与执行</Tag></div>
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

            {snapshot?.state === "searching_fields" && (
              <Card title="Wind 字段发现" size="small">
                <Button
                  type="primary"
                  loading={discoverFields.isPending}
                  onClick={() => discoverFields.mutate(snapshot.version)}
                >
                  检索并验证真实 Wind 字段
                </Button>
              </Card>
            )}

            {snapshot?.state === "waiting_field_confirmation" && (
              <Card title="确认 Wind 字段" size="small">
                {(snapshot.field_candidates ?? []).length > 0 ? (
                  <FieldCandidateTable
                    key={snapshot.version}
                    candidates={snapshot.field_candidates ?? []}
                    sessionVersion={snapshot.version}
                    submitting={confirmFields.isPending}
                    onConfirm={(payload) => confirmFields.mutate(payload)}
                  />
                ) : (
                  <Alert type="error" showIcon message="没有找到受控字段候选，不能继续" />
                )}
              </Card>
            )}

            {snapshot?.state === "planning_functions" && snapshot.request && (
              <Card title="构建执行计划" size="small">
                <Button
                  type="primary"
                  loading={buildManifest.isPending}
                  onClick={() => buildManifest.mutate(snapshot.version)}
                >
                  构建签名 manifest
                </Button>
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

            {snapshot?.state === "code_ready" && (
              <Card title="真实执行" size="small">
                <Space>
                  <Button
                    type="primary"
                    loading={executeRealWind.isPending}
                    onClick={() => executeRealWind.mutate(snapshot.version)}
                  >
                    执行真实 Wind 闭环
                  </Button>
                  <Button
                    loading={reviseFields.isPending}
                    onClick={() => reviseFields.mutate(snapshot.version)}
                  >
                    重新确认字段
                  </Button>
                </Space>
              </Card>
            )}

            {snapshot?.state === "completed" && (
              <Card title="修订已完成结果" size="small">
                <Button
                  loading={reviseFields.isPending}
                  onClick={() => reviseFields.mutate(snapshot.version)}
                >
                  修改字段并使旧计划失效
                </Button>
              </Card>
            )}

            {snapshot?.execution_result && (
              <ResultPane
                result={snapshot.execution_result}
                artifactUri={snapshot.artifact_uri}
                sessionId={snapshot.session_id}
                sessionState={snapshot.state}
              />
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

            {blocking.length > 0 && snapshot && (
              <ClarificationCard
                questions={blocking}
                sessionVersion={snapshot.version}
                submitting={resolveClarification.isPending}
                onResolve={(answers, version) =>
                  resolveClarification.mutate({ answers, version })
                }
              />
            )}

            <Card title="流程状态" size="small">
              <WorkflowSteps view={view} />
              {view.canCancel && snapshot && (
                <Button
                  danger
                  size="small"
                  loading={cancel.isPending}
                  onClick={() => cancel.mutate(snapshot.version)}
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

      <Modal
        title="修改公式参数"
        open={revisionOpen}
        okText="应用并使下游失效"
        cancelText="取消"
        confirmLoading={reviseFormula.isPending}
        onOk={applyFormulaRevision}
        onCancel={() => setRevisionOpen(false)}
      >
        <Space direction="vertical">
          <Typography.Text>
            修改 AST 中第一个滚动算子的窗口。后端会重新渲染正式公式，并清除旧字段、计划和结果。
          </Typography.Text>
          <InputNumber
            aria-label="滚动窗口"
            min={1}
            precision={0}
            value={revisionWindow}
            onChange={setRevisionWindow}
          />
        </Space>
      </Modal>
    </>
  )
}

interface AstNode {
  op?: string | null
  params?: Record<string, string | number>
  args?: AstNode[]
}

function firstRollingWindow(node: unknown): number | null {
  const current = node as AstNode | null
  if (!current) return null
  if (current.op?.startsWith("rolling_") && typeof current.params?.window === "number") {
    return current.params.window
  }
  for (const child of current.args ?? []) {
    const found = firstRollingWindow(child)
    if (found !== null) return found
  }
  return null
}

function replaceFirstRollingWindow(node: unknown, window: number): boolean {
  const current = node as AstNode | null
  if (!current) return false
  if (current.op?.startsWith("rolling_")) {
    current.params = { ...(current.params ?? {}), window }
    return true
  }
  return (current.args ?? []).some((child) => replaceFirstRollingWindow(child, window))
}
