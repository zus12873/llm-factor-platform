/**
 * Deterministic mapping from backend session state to what the UI shows.
 *
 * A pure function on purpose. The backend state machine is the single source of
 * truth, and the alternative — components each deciding what a state means —
 * produces a UI that disagrees with itself: the step rail says "executing" while
 * the button says "run".
 *
 * The subtle case is failure. A failed session must stay on the step where it
 * failed, not jump to a generic error screen: the user needs to see *which*
 * stage broke to know whether to revise the formula or widen the date range.
 */
import type { SessionSnapshot } from "../../api/client"

export const WORKFLOW_STEPS = [
  { key: "created", label: "创建会话" },
  { key: "parsing_input", label: "解析想法" },
  { key: "needs_clarification", label: "澄清歧义" },
  { key: "waiting_formula_confirmation", label: "确认公式" },
  { key: "searching_fields", label: "检索字段" },
  { key: "waiting_field_confirmation", label: "确认字段" },
  { key: "planning_functions", label: "规划取数" },
  { key: "code_ready", label: "构建 manifest" },
  { key: "executing", label: "执行计算" },
  { key: "validating", label: "结果校验" },
  { key: "completed", label: "完成" },
] as const

export type StepStatus = "wait" | "process" | "finish" | "error"

export interface WorkflowView {
  activeStep: number
  status: StepStatus
  /** Shown verbatim; the user acts on this sentence. */
  message: string
  /** True when the workflow is waiting on the user rather than on the system. */
  awaitingUser: boolean
  canCancel: boolean
}

// Keyed by plain string: the backend state arrives as a string, and narrowing
// it here would only move the failure to a cast.
const STEP_INDEX: Record<string, number> = Object.fromEntries(
  WORKFLOW_STEPS.map((step, index) => [step.key, index]),
)

/** States where nothing moves until the user does something. */
const AWAITING_USER = new Set([
  "needs_clarification",
  "waiting_formula_confirmation",
  "waiting_field_confirmation",
])

const MESSAGES: Record<string, string> = {
  created: "填写研究想法后开始",
  parsing_input: "正在解析研究想法…",
  needs_clarification: "有阻塞性歧义待确认——平台不会替你猜口径",
  waiting_formula_confirmation: "请确认规范公式，确认后即为执行依据",
  searching_fields: "正在检索候选 Wind 字段…",
  waiting_field_confirmation: "请确认字段绑定",
  planning_functions: "正在规划取数步骤…",
  code_ready: "manifest 已构建，可以执行",
  executing: "正在隔离执行…",
  validating: "正在做数据、公式与结果三层校验…",
  completed: "已完成",
}

export function toWorkflowView(snapshot: SessionSnapshot): WorkflowView {
  const state = snapshot.state

  if (state === "failed") {
    // Stay on the stage that broke. A generic error screen loses the one piece
    // of information that decides what the user does next.
    const failedAt = inferFailedStep(snapshot)
    return {
      activeStep: failedAt,
      status: "error",
      message: snapshot.last_error?.message ?? "执行失败",
      awaitingUser: true,
      canCancel: false,
    }
  }

  const index = STEP_INDEX[state] ?? 0
  return {
    activeStep: index,
    status: state === "completed" ? "finish" : "process",
    message: MESSAGES[state] ?? state,
    awaitingUser: AWAITING_USER.has(state),
    canCancel: state === "executing",
  }
}

/**
 * Which step a failed session stopped on.
 *
 * Derived from what the snapshot contains rather than from a stage label,
 * because the reducer clears downstream artifacts on revision — so presence is
 * the reliable signal and a stored label could be stale.
 */
function inferFailedStep(snapshot: SessionSnapshot): number {
  if (snapshot.execution_result) return STEP_INDEX["validating"]!
  if (snapshot.code_sha256 || snapshot.plan) return STEP_INDEX["executing"]!
  if ((snapshot.field_selections ?? []).length > 0) return STEP_INDEX["planning_functions"]!
  if (snapshot.factor_spec) return STEP_INDEX["searching_fields"]!
  return STEP_INDEX["parsing_input"]!
}

/** Blocking questions the user must answer before anything else can proceed. */
export function blockingQuestions(snapshot: SessionSnapshot) {
  return (snapshot.clarifications ?? []).filter((q) => q.blocking)
}
