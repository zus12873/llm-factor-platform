import { describe, expect, it } from "vitest"
import { blockingQuestions, toWorkflowView, WORKFLOW_STEPS } from "./sessionView"
import type { SessionSnapshot } from "../../api/client"

function snapshot(overrides: Partial<SessionSnapshot> = {}): SessionSnapshot {
  return {
    schema_version: 1,
    session_id: "s1",
    state: "created",
    version: 0,
    request: null,
    factor_spec: null,
    field_selections: [],
    plan: null,
    generated_code: null,
    code_sha256: null,
    execution_result: null,
    artifact_uri: null,
    last_error: null,
    ambiguities: [],
    clarifications: [],
    ...overrides,
  } as SessionSnapshot
}

const stepOf = (key: string) => WORKFLOW_STEPS.findIndex((s) => s.key === key)

describe("toWorkflowView", () => {
  it("maps each state to its own step", () => {
    for (const step of WORKFLOW_STEPS) {
      const view = toWorkflowView(snapshot({ state: step.key }))
      expect(view.activeStep).toBe(stepOf(step.key))
    }
  })

  it("marks the states that are waiting on the user", () => {
    expect(toWorkflowView(snapshot({ state: "waiting_field_confirmation" })).awaitingUser).toBe(true)
    expect(toWorkflowView(snapshot({ state: "executing" })).awaitingUser).toBe(false)
  })

  it("offers cancel only while something is actually running", () => {
    expect(toWorkflowView(snapshot({ state: "executing" })).canCancel).toBe(true)
    expect(toWorkflowView(snapshot({ state: "code_ready" })).canCancel).toBe(false)
  })

  it("keeps a failed execution on the execution step", () => {
    // A generic error screen would lose the one fact that decides what to do next.
    const view = toWorkflowView(
      snapshot({ state: "failed", plan: {} as never, factor_spec: {} as never }),
    )
    expect(view.activeStep).toBe(stepOf("executing"))
    expect(view.status).toBe("error")
  })

  it("keeps a failed parse on the parsing step", () => {
    const view = toWorkflowView(snapshot({ state: "failed" }))
    expect(view.activeStep).toBe(stepOf("parsing_input"))
  })

  it("shows the backend's own error message rather than a generic one", () => {
    const view = toWorkflowView(
      snapshot({
        state: "failed",
        last_error: { message: "字段 s_dq_close 在该区间无数据" } as never,
      }),
    )
    expect(view.message).toContain("s_dq_close")
  })

  it("says the platform will not guess when an ambiguity blocks", () => {
    const view = toWorkflowView(snapshot({ state: "needs_clarification" }))
    expect(view.message).toContain("不会替你猜")
  })
})

describe("blockingQuestions", () => {
  it("returns only the blocking ones", () => {
    const questions = blockingQuestions(
      snapshot({
        clarifications: [
          { question_id: "direction", blocking: true } as never,
          { question_id: "rebalance_frequency", blocking: false } as never,
        ],
      }),
    )
    expect(questions).toHaveLength(1)
    expect(questions[0]!.question_id).toBe("direction")
  })
})
