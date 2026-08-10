/**
 * The field confirmation table.
 *
 * Each row carries what the user needs to judge a binding: which tier proposed
 * it, its Chinese name, its unit, its time role, and whether the dictionary could
 * describe it at all. That last column matters — roughly 6% of fields have no
 * metadata, and a blank description means "we could not read the docs", not "this
 * field is meaningless".
 *
 * The unit column is not decoration either. Reading market cap in yuan rather
 * than ten-thousand yuan is a 10,000x error that no downstream check catches, so
 * the unit is placed where the person confirming the binding will see it.
 */
import { Button, Space, Table, Tag, Tooltip } from "antd"
import type { CheckboxProps } from "antd"
import { useState } from "react"
import type { FieldSelection } from "../../api/client"

export interface FieldCandidateRow {
  logical_name: string
  table: string
  field: string
  meaning_zh?: string | null
  unit?: string | null
  time_role?: string | null
  source_tier?: string
  metadata_source?: string | null
  schema_status?: string | null
}

interface Props {
  candidates: FieldCandidateRow[]
  sessionVersion: number
  submitting?: boolean
  onConfirm: (payload: {
    expected_version: number
    selections: FieldSelection[]
  }) => void
}

export function FieldCandidateTable({
  candidates,
  sessionVersion,
  submitting,
  onConfirm,
}: Props) {
  const [selectedKeys, setSelectedKeys] = useState<React.Key[]>([])

  const keyOf = (row: FieldCandidateRow) => `${row.table}.${row.field}`

  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      <Table<FieldCandidateRow>
        size="small"
        rowKey={keyOf}
        dataSource={candidates}
        pagination={false}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: setSelectedKeys,
          // aria-label, not name: a screen-reader user needs to know which
          // binding each checkbox selects, and `name` is not announced.
          getCheckboxProps: (row) =>
            ({
              "aria-label": row.meaning_zh ?? `${row.table}.${row.field}`,
            }) as Partial<CheckboxProps>,
        }}
        columns={[
          { title: "变量", dataIndex: "logical_name" },
          { title: "表", dataIndex: "table" },
          { title: "字段", dataIndex: "field" },
          {
            title: "含义",
            dataIndex: "meaning_zh",
            render: (value: string | null) =>
              value ? (
                value
              ) : (
                <Tooltip title="本地数据字典未能解析该字段的描述；字段本身可能完全有效">
                  <Tag>无元数据</Tag>
                </Tooltip>
              ),
          },
          {
            title: "单位",
            dataIndex: "unit",
            render: (value: string | null) =>
              value ? (
                <Tag color="blue">{value}</Tag>
              ) : (
                <Tooltip title="单位未登记。口径未确认前，量级错误无法被下游发现">
                  <Tag color="orange">单位未知</Tag>
                </Tooltip>
              ),
          },
          { title: "时间角色", dataIndex: "time_role" },
          {
            title: "来源",
            dataIndex: "source_tier",
            render: (value: string) => <Tag>{value}</Tag>,
          },
        ]}
      />
      <Button
        type="primary"
        loading={submitting}
        disabled={selectedKeys.length === 0}
        onClick={() =>
          onConfirm({
            expected_version: sessionVersion,
            selections: candidates
              .filter((row) => selectedKeys.includes(keyOf(row)))
              .map((row) => ({
                logical_name: row.logical_name,
                table: row.table,
                field: row.field,
                time_role: row.time_role,
              })) as FieldSelection[],
          })
        }
      >
        确认字段
      </Button>
    </Space>
  )
}
