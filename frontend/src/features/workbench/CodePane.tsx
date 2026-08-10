/**
 * The exported `factor.py` pane.
 *
 * Read-only, and the banner says why: this file is a deliverable, not the thing
 * that runs. The platform executes a signed manifest. A user who edits this text
 * expecting the next run to change would be wrong, and an editable box would be
 * an invitation to believe that.
 *
 * The manifest hash is shown alongside so a reviewer holding only the downloaded
 * file can tell which run it describes.
 */
import { Alert, Button, Card, Space, Typography } from "antd"

interface Props {
  source: string
  manifestSha256: string
}

export function CodePane({ source, manifestSha256 }: Props) {
  const download = () => {
    const blob = new Blob([source], { type: "text/x-python" })
    const url = URL.createObjectURL(blob)
    const link = document.createElement("a")
    link.href = url
    link.download = "factor.py"
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <Card
      title="导出代码"
      size="small"
      extra={
        <Space>
          <Button size="small" onClick={() => navigator.clipboard?.writeText(source)}>
            复制
          </Button>
          <Button size="small" onClick={download}>
            下载 .py
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" style={{ width: "100%" }}>
        <Alert
          type="info"
          showIcon
          message="这不是平台实际执行的对象"
          description={
            <>
              平台执行的是签名 manifest（
              <Typography.Text code>{manifestSha256.slice(0, 16)}…</Typography.Text>
              ）。本文件由同一份 manifest 渲染，供你阅读、复制与在自己的环境中复现；
              修改它不会影响平台的任何一次运行。
            </>
          }
        />
        <pre
          style={{
            maxHeight: 420,
            overflow: "auto",
            background: "#fafafa",
            padding: 12,
            fontSize: 12,
          }}
        >
          {source}
        </pre>
      </Space>
    </Card>
  )
}
