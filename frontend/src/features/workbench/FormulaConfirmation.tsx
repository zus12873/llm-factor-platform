/**
 * The formula confirmation card.
 *
 * What the user confirms here is `canonical_formula` — rendered by the backend
 * from the AST that will actually run. The model's prose sits below it, visually
 * subordinate and labelled, because the one failure this card exists to prevent
 * is a user confirming the explanation instead of the formula.
 */
import { Button, Card, Descriptions, Space, Tag, Typography } from "antd"
import type { FactorSpecOut } from "../../api/client"

interface Props {
  spec: FactorSpecOut
  sessionVersion: number
  submitting?: boolean
  onConfirm: (spec: FactorSpecOut, expectedVersion: number) => void
  onRevise?: () => void
}

export function FormulaConfirmation({
  spec,
  sessionVersion,
  submitting,
  onConfirm,
  onRevise,
}: Props) {
  return (
    <Card
      title="确认公式"
      size="small"
      extra={<Tag>版本 {sessionVersion}</Tag>}
      actions={[
        <Button
          key="confirm"
          type="primary"
          loading={submitting}
          onClick={() => onConfirm(spec, sessionVersion)}
        >
          确认公式
        </Button>,
        <Button key="revise" onClick={onRevise}>
          修改
        </Button>,
      ]}
    >
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="正式公式">
          <Typography.Text code style={{ fontSize: 15 }}>
            {spec.canonical_formula}
          </Typography.Text>
          <div>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              由 AST 确定性渲染。你确认的就是执行的。
            </Typography.Text>
          </div>
        </Descriptions.Item>

        {spec.formula_explanation && (
          <Descriptions.Item label="模型说明">
            <Typography.Text type="secondary">
              {spec.formula_explanation}
            </Typography.Text>
            <div>
              <Tag color="default">仅供阅读，不参与执行</Tag>
            </div>
          </Descriptions.Item>
        )}

        <Descriptions.Item label="变量">
          <Space wrap>
            {(spec.variables ?? []).map((variable) => (
              <Tag key={variable.logical_name}>
                {variable.logical_name}
                {variable.meaning ? ` — ${variable.meaning}` : ""}
              </Tag>
            ))}
          </Space>
        </Descriptions.Item>

        <Descriptions.Item label="方向">
          {spec.direction === "higher_is_better" ? "数值越大越好" : "数值越小越好"}
        </Descriptions.Item>

        <Descriptions.Item label="时间口径">
          {spec.time_convention?.signal_date} 形成信号 →{" "}
          {spec.time_convention?.trade_date} 交易 → 按{" "}
          {spec.time_convention?.execution_price} 执行
        </Descriptions.Item>
      </Descriptions>
    </Card>
  )
}
