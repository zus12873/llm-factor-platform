/**
 * Source evidence for an extracted factor.
 *
 * Every claim shows the page it came from and the passage it was read out of.
 * "The report says X" cannot be checked; "page 3, this paragraph" can — and the
 * reviewer checking it is the only thing standing between a misread formula and
 * a published factor.
 */
import { Card, List, Tag, Typography } from "antd"

export interface Evidence {
  evidence_id: string
  page_number: number
  text: string
}

export function EvidenceViewer({ evidence }: { evidence: Evidence[] }) {
  return (
    <Card title="来源证据" size="small">
      <List
        size="small"
        dataSource={evidence}
        locale={{ emptyText: "未找到相关段落" }}
        renderItem={(item) => (
          <List.Item>
            <Typography.Text>
              <Tag color="blue">第 {item.page_number} 页</Tag>
              {item.text}
            </Typography.Text>
          </List.Item>
        )}
      />
    </Card>
  )
}
