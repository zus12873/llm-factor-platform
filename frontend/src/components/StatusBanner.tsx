/**
 * The one sentence telling the user what is happening and what to do.
 *
 * Three cases, deliberately distinct:
 *
 * - **Conflict** is not a failure — someone else moved the session on. Showing it
 *   as an error teaches users to distrust a system that is working correctly, so
 *   it offers the action that actually resolves it: reload.
 * - **Request error** is what the last action returned. Swallowing it produces
 *   the worst possible UI: the user clicks, nothing changes, and they cannot tell
 *   whether it worked, is still running, or failed.
 * - Otherwise, the workflow's own state.
 */
import { Alert, Button } from "antd"
import type { ApiError } from "../api/client"
import type { WorkflowView } from "../features/workbench/sessionView"

interface Props {
  view: WorkflowView
  conflict?: boolean
  requestError?: ApiError | null
  onReload?: () => void
}

/** Codes worth explaining, because the raw message alone does not say what to do. */
const GUIDANCE: Record<string, string> = {
  llm_response_invalid:
    "模型未配置或返回了无法解析的结果。当前是离线模式，请在 .env 中配置模型接口后重试。",
  local_only_mode: "已启用全本地模式，外部模型调用被禁止。",
  disputed_metric: "该口径已被标记为有争议，平台拒绝执行。请改用其他口径。",
  planning_failed: "无法在不猜测的前提下生成取数计划，请补齐确认项。",
  outbound_blocked: "请求中包含不得外发的内容，已被数据出境边界拦截。",
}

export function StatusBanner({ view, conflict, requestError, onReload }: Props) {
  if (conflict) {
    return (
      <Alert
        type="warning"
        showIcon
        message="会话已被更新"
        description="这个会话在别处发生了变化。重新载入后再操作，以免覆盖对方的修改。"
        action={
          <Button size="small" onClick={onReload}>
            重新载入
          </Button>
        }
      />
    )
  }

  if (requestError) {
    return (
      <Alert
        type="error"
        showIcon
        message={GUIDANCE[requestError.code] ?? requestError.message}
        description={`错误码 ${requestError.code}`}
      />
    )
  }

  return (
    <Alert
      type={
        view.status === "error" ? "error" : view.awaitingUser ? "warning" : "info"
      }
      showIcon
      message={view.message}
    />
  )
}
