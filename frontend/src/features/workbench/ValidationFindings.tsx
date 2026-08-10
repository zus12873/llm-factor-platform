/**
 * Validation findings, grouped by severity.
 *
 * Errors and warnings are shown apart rather than in one list. Mixed together,
 * a wall of yellow trains people to scroll past the red — and the red is what
 * blocks publication. Warnings that carry review status (`unreviewed_metric`)
 * are the ones that must travel with the result rather than be dismissed.
 */
import { Alert, Empty, Space, Tag, Typography } from "antd"
import type { components } from "../../api/schema"

type Finding = components["schemas"]["ValidationFinding"]

export function ValidationFindings({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return <Empty description="无校验发现" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  const errors = findings.filter((f) => f.severity === "error")
  const warnings = findings.filter((f) => f.severity === "warning")

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {errors.length > 0 && (
        <Alert
          type="error"
          showIcon
          message={`${errors.length} 项阻塞问题`}
          description={
            <Space direction="vertical">
              {errors.map((finding, index) => (
                <div key={index}>
                  <Tag color="red">{finding.code}</Tag>
                  <Typography.Text>{finding.message}</Typography.Text>
                </div>
              ))}
            </Space>
          }
        />
      )}
      {warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`${warnings.length} 项提示`}
          description={
            <Space direction="vertical">
              {warnings.map((finding, index) => (
                <div key={index}>
                  <Tag color="orange">{finding.code}</Tag>
                  <Typography.Text>{finding.message}</Typography.Text>
                </div>
              ))}
            </Space>
          }
        />
      )}
    </Space>
  )
}
