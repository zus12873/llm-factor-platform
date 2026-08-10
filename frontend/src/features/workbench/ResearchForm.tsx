/**
 * The research input: a trusted envelope plus one free-text idea.
 *
 * The split is the point. Universe, dates and asset type are structured fields
 * the user picks — the model never invents them, so they cannot be hallucinated
 * into a factor. Only the idea is free text, and only the idea goes to a model.
 */
import { Button, DatePicker, Form, Input, Select, Space } from "antd"
import type { ResearchRequest } from "../../api/client"

interface Props {
  disabled?: boolean
  submitting?: boolean
  onSubmit: (request: ResearchRequest) => void
}

export function ResearchForm({ disabled, submitting, onSubmit }: Props) {
  const [form] = Form.useForm()

  return (
    <Form
      form={form}
      layout="vertical"
      disabled={disabled}
      initialValues={{ asset_type: "stock", universe: "000300.SH", frequency: "daily" }}
      onFinish={(values) => {
        const [start, end] = values.range ?? []
        onSubmit({
          asset_type: values.asset_type,
          universe: values.universe,
          frequency: values.frequency,
          start_date: start?.format("YYYY-MM-DD"),
          end_date: end?.format("YYYY-MM-DD"),
          research_idea: values.research_idea,
        } as ResearchRequest)
      }}
    >
      <Space wrap>
        <Form.Item name="asset_type" label="资产类型">
          <Select
            style={{ width: 120 }}
            options={[{ value: "stock", label: "股票" }]}
          />
        </Form.Item>
        <Form.Item name="universe" label="股票池">
          <Select
            style={{ width: 160 }}
            options={[
              { value: "000300.SH", label: "沪深300" },
              { value: "000905.SH", label: "中证500" },
              { value: "000016.SH", label: "上证50" },
            ]}
          />
        </Form.Item>
        <Form.Item name="frequency" label="频率">
          <Select style={{ width: 100 }} options={[{ value: "daily", label: "日频" }]} />
        </Form.Item>
        <Form.Item name="range" label="区间" rules={[{ required: true }]}>
          <DatePicker.RangePicker />
        </Form.Item>
      </Space>

      <Form.Item
        name="research_idea"
        label="研究想法"
        rules={[{ required: true, message: "请描述你的研究想法" }]}
        extra="只有这一段会发给模型；上面的结构化字段是可信信封，模型不会改动"
      >
        <Input.TextArea
          rows={4}
          placeholder="例如：ROE 高的股票未来收益更好"
          aria-label="研究想法"
        />
      </Form.Item>

      <Button type="primary" htmlType="submit" loading={submitting}>
        开始解析
      </Button>
    </Form>
  )
}
