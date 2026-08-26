/**
 * Published factor versions.
 *
 * `review_status` is stored on the entry at publish time and rendered as-is.
 * Recomputing it from the live metric registry would describe a different
 * object than the one that was saved. The parquet is not fetched: the page
 * shows the stored path and hashes so a reviewer can locate the file without
 * pulling it into the browser.
 */
import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Alert, Descriptions, Drawer, Empty, Spin, Table, Tag, Typography } from "antd"
import { apiClient, type LibraryEntry } from "../../api/client"

const REVIEW_LABEL: Record<string, { text: string; color: string }> = {
  unreviewed: { text: "未复核", color: "orange" },
  reviewed: { text: "已复核", color: "green" },
  disputed: { text: "有争议", color: "red" },
}

function ReviewTag({ status }: { status: string }) {
  const mapped = REVIEW_LABEL[status]
  return <Tag color={mapped?.color}>{mapped?.text ?? status}</Tag>
}

export function LibraryPage() {
  const [selected, setSelected] = useState<{
    factorId: string
    version: number
  } | null>(null)

  const list = useQuery({
    queryKey: ["library"],
    queryFn: () => apiClient.listLibrary(),
  })
  const detail = useQuery({
    queryKey: ["library", selected?.factorId, selected?.version],
    queryFn: () => apiClient.getLibraryVersion(selected!.factorId, selected!.version),
    enabled: Boolean(selected),
  })

  return (
    <div>
      <Typography.Title level={4}>因子库</Typography.Title>
      {list.isError && <Alert type="error" showIcon message="无法载入因子库" />}
      {list.isLoading ? (
        <Spin />
      ) : (list.data ?? []).length === 0 ? (
        <Empty description="暂无已发布因子" />
      ) : (
        <Table<LibraryEntry>
          rowKey={(row) => `${row.factor_id}-v${row.version}`}
          dataSource={list.data}
          pagination={false}
          onRow={(row) => ({
            onClick: () =>
              setSelected({ factorId: row.factor_id, version: row.version }),
            style: { cursor: "pointer" },
          })}
          columns={[
            { title: "名称", dataIndex: "factor_name" },
            { title: "版本", dataIndex: "version" },
            {
              title: "复核",
              dataIndex: "review_status",
              render: (status: string) => <ReviewTag status={status} />,
            },
            { title: "创建时间", dataIndex: "created_at" },
            { title: "会话", dataIndex: "session_id" },
          ]}
        />
      )}
      <Drawer
        title={
          detail.data
            ? `${detail.data.factor_name} v${detail.data.version}`
            : "因子版本"
        }
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={560}
      >
        {detail.isLoading && <Spin />}
        {detail.isError && <Alert type="error" showIcon message="无法载入该版本" />}
        {detail.data && <LibraryDetail entry={detail.data} />}
      </Drawer>
    </div>
  )
}

function LibraryDetail({ entry }: { entry: LibraryEntry }) {
  const provenance = entry.provenance
  const versionFields = provenance?.versions
    ? Object.entries(provenance.versions).filter(
        ([, value]) => value !== "" && value !== undefined && value !== null,
      )
    : []

  return (
    <>
      <ReviewTag status={entry.review_status} />
      {entry.review_note && (
        <Typography.Paragraph type="secondary">{entry.review_note}</Typography.Paragraph>
      )}
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="正式公式">
          <Typography.Text code>{entry.spec.canonical_formula}</Typography.Text>
        </Descriptions.Item>
        <Descriptions.Item label="工件路径">{entry.artifact_path}</Descriptions.Item>
        <Descriptions.Item label="manifest">{entry.manifest_sha256}</Descriptions.Item>
        <Descriptions.Item label="program">{entry.program_sha256}</Descriptions.Item>
        <Descriptions.Item label="result">{entry.result_sha256}</Descriptions.Item>
        <Descriptions.Item label="会话">{entry.session_id}</Descriptions.Item>
        {(provenance?.inputs ?? []).map((input, index) => (
          <Descriptions.Item key={`${input.source_table}-${index}`} label="输入">
            {input.source_table}
            {input.source_fields?.length ? ` · ${input.source_fields.join(", ")}` : ""}
            {input.query_timestamp ? ` · ${input.query_timestamp}` : ""}
            {input.input_artifact_sha256
              ? ` · ${input.input_artifact_sha256}`
              : ""}
          </Descriptions.Item>
        ))}
        {versionFields.map(([key, value]) => (
          <Descriptions.Item key={key} label={key}>
            {String(value)}
          </Descriptions.Item>
        ))}
      </Descriptions>
    </>
  )
}
