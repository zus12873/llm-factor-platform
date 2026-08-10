/**
 * Report upload and extraction.
 *
 * The gate is the point: "进入因子工作流" stays disabled until a formula has
 * either been extracted with confidence or typed in by hand. A low-confidence
 * extraction reads exactly like a good one, so the only safe default is to
 * require the human step rather than to offer it.
 */
import { useState } from "react"
import { Alert, Button, Card, Input, Space, Tag, Typography, Upload } from "antd"
import { EvidenceViewer, type Evidence } from "./EvidenceViewer"

interface Extraction {
  factor_name: string
  hypothesis: string
  evidence: Evidence[]
  formula_extraction: {
    status: "extracted" | "needs_manual_confirmation"
    confidence: number
    source_pages: number[]
    extracted_text: string
    warning: string
  }
}

interface UploadResult {
  artifact_id: string
  display_name: string
  page_count: number
  scanned_pages: number[]
  extraction: Extraction
  capability_note: string
}

export function ReportsPage() {
  const [result, setResult] = useState<UploadResult | null>(null)
  const [manualFormula, setManualFormula] = useState("")
  const [error, setError] = useState<string | null>(null)

  const upload = async (file: File) => {
    setError(null)
    const body = new FormData()
    body.append("file", file)
    const response = await fetch("/api/reports/upload", { method: "POST", body })
    if (!response.ok) {
      const payload = await response.json().catch(() => null)
      setError(payload?.error?.message ?? `上传失败（HTTP ${response.status}）`)
      return
    }
    setResult(await response.json())
  }

  const extraction = result?.extraction.formula_extraction
  const needsManual = extraction?.status === "needs_manual_confirmation"
  // Confident extraction, or a formula the user typed. Nothing else proceeds.
  const canProceed = Boolean(
    extraction && (!needsManual || manualFormula.trim().length > 0),
  )

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card title="上传研报" size="small">
        <Space direction="vertical" style={{ width: "100%" }}>
          <Upload.Dragger
            accept=".pdf"
            maxCount={1}
            beforeUpload={(file) => {
              void upload(file)
              return false
            }}
          >
            <p>点击或拖拽 PDF 到此处</p>
          </Upload.Dragger>
          <Typography.Text type="secondary">
            首期支持正文可复制、变量定义明确的中英文文本型研报。图片公式、复杂数学
            排版与扫描版仅提取正文线索，公式需人工确认。
          </Typography.Text>
        </Space>
      </Card>

      {error && <Alert type="error" showIcon message={error} />}

      {result && (
        <>
          <Card title="抽取结果" size="small">
            <Space direction="vertical" style={{ width: "100%" }}>
              <Typography.Text>
                {result.display_name} · 共 {result.page_count} 页
                {result.scanned_pages.length > 0 && (
                  <Tag color="orange" style={{ marginLeft: 8 }}>
                    第 {result.scanned_pages.join("、")} 页疑似扫描件
                  </Tag>
                )}
              </Typography.Text>

              {needsManual ? (
                <Alert
                  type="warning"
                  showIcon
                  message="公式需人工确认"
                  description={
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <span>{extraction?.warning}</span>
                      {extraction?.extracted_text && (
                        <Typography.Text code>
                          {extraction.extracted_text}
                        </Typography.Text>
                      )}
                      <Input.TextArea
                        rows={2}
                        aria-label="手动输入公式"
                        placeholder="请依据证据手动输入或修正公式"
                        value={manualFormula}
                        onChange={(event) => setManualFormula(event.target.value)}
                      />
                    </Space>
                  }
                />
              ) : (
                <Alert
                  type="success"
                  showIcon
                  message={`已抽取公式（置信度 ${extraction?.confidence.toFixed(2)}）`}
                  description={
                    <Typography.Text code>{extraction?.extracted_text}</Typography.Text>
                  }
                />
              )}

              <Button type="primary" disabled={!canProceed}>
                进入因子工作流
              </Button>
            </Space>
          </Card>

          <EvidenceViewer evidence={result.extraction.evidence} />
        </>
      )}
    </Space>
  )
}
