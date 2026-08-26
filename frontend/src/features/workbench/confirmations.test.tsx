/**
 * Confirmation-component tests.
 *
 * The property they all share: a confirmation must carry the version the user
 * was looking at. Sending the latest version instead would make every
 * confirmation succeed — including the one made against a screen that is now
 * three revisions out of date, which is exactly the silent overwrite the version
 * check exists to prevent.
 */
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ClarificationCard } from "./ClarificationCard"
import { FieldCandidateTable } from "./FieldCandidateTable"
import { FormulaConfirmation } from "./FormulaConfirmation"
import { CodePane } from "./CodePane"
import { ValidationFindings } from "./ValidationFindings"
import { ResultPane } from "./ResultPane"

const CANDIDATES = [
  {
    logical_name: "close",
    table: "ashareeodprices",
    field: "s_dq_adjclose",
    meaning_zh: "后复权收盘价",
    unit: "cny",
    time_role: "observation",
    source_tier: "alias",
    metadata_source: "WDS",
  },
  {
    logical_name: "close",
    table: "obscuretable",
    field: "rare_field",
    meaning_zh: null,
    unit: null,
    time_role: "observation",
    source_tier: "bm25",
    metadata_source: null,
  },
]

describe("FieldCandidateTable", () => {
  it("submits the selected binding with the version the user saw", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <FieldCandidateTable
        candidates={CANDIDATES}
        sessionVersion={8}
        onConfirm={onConfirm}
      />,
    )

    await user.click(screen.getByRole("checkbox", { name: /后复权收盘价/ }))
    await user.click(screen.getByRole("button", { name: "确认字段" }))

    expect(onConfirm).toHaveBeenCalledWith({
      expected_version: 8,
      selections: [
        {
          logical_name: "close",
          table: "ashareeodprices",
          field: "s_dq_adjclose",
          time_role: "observation",
        },
      ],
    })
  })

  it("cannot confirm nothing", () => {
    render(
      <FieldCandidateTable candidates={CANDIDATES} sessionVersion={1} onConfirm={vi.fn()} />,
    )
    expect(screen.getByRole("button", { name: "确认字段" })).toBeDisabled()
  })

  it("marks a field the dictionary could not describe, rather than hiding it", () => {
    render(
      <FieldCandidateTable candidates={CANDIDATES} sessionVersion={1} onConfirm={vi.fn()} />,
    )
    expect(screen.getByText("无元数据")).toBeInTheDocument()
  })

  it("flags an unknown unit, because a unit error is invisible downstream", () => {
    render(
      <FieldCandidateTable candidates={CANDIDATES} sessionVersion={1} onConfirm={vi.fn()} />,
    )
    expect(screen.getByText("单位未知")).toBeInTheDocument()
  })
})

const SPEC = {
  canonical_formula: "rank(rolling_return(close, window=20))",
  formula_explanation: "对过去20日收益做横截面排名",
  direction: "higher_is_better",
  variables: [{ logical_name: "close", meaning: "后复权收盘价" }],
  time_convention: {
    signal_date: "T",
    trade_date: "T+1",
    execution_price: "NEXT_OPEN",
  },
} as never

describe("FormulaConfirmation", () => {
  it("confirms with the version shown on the card", async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    render(
      <FormulaConfirmation spec={SPEC} sessionVersion={5} onConfirm={onConfirm} />,
    )
    await user.click(screen.getByRole("button", { name: "确认公式" }))
    expect(onConfirm).toHaveBeenCalledWith(SPEC, 5)
  })

  it("labels the model's prose so it cannot be mistaken for the formula", () => {
    render(<FormulaConfirmation spec={SPEC} sessionVersion={1} onConfirm={vi.fn()} />)
    expect(screen.getByText("rank(rolling_return(close, window=20))")).toBeInTheDocument()
    expect(screen.getByText("仅供阅读，不参与执行")).toBeInTheDocument()
  })
})

const QUESTIONS = [
  {
    question_id: "profitability_definition",
    question: "请明确「盈利质量」的具体口径。",
    options: ["ROE_TTM", "ROA_TTM", "CFO_TO_PROFIT"],
    blocking: true,
  },
] as never[]

describe("ClarificationCard", () => {
  it("cannot be submitted until every question is answered", async () => {
    const user = userEvent.setup()
    const onResolve = vi.fn()
    render(
      <ClarificationCard
        questions={QUESTIONS}
        sessionVersion={3}
        onResolve={onResolve}
      />,
    )
    expect(screen.getByRole("button", { name: "确认口径" })).toBeDisabled()

    await user.click(screen.getByRole("radio", { name: "ROE_TTM" }))
    await user.click(screen.getByRole("button", { name: "确认口径" }))

    expect(onResolve).toHaveBeenCalledWith(
      { profitability_definition: "ROE_TTM" },
      3,
    )
  })

  it("offers no default, because a default here is the guess we refuse to make", () => {
    render(
      <ClarificationCard questions={QUESTIONS} sessionVersion={1} onResolve={vi.fn()} />,
    )
    expect(screen.queryByText(/跳过|使用默认/)).toBeNull()
  })
})

describe("CodePane", () => {
  it("says plainly that this file is not what runs", () => {
    render(<CodePane source="print('hi')" manifestSha256={"a".repeat(64)} />)
    expect(screen.getByText("这不是平台实际执行的对象")).toBeInTheDocument()
  })

  it("shows the manifest hash so the file can be traced to a run", () => {
    render(<CodePane source="print('hi')" manifestSha256={"a".repeat(64)} />)
    expect(screen.getByText(/aaaaaaaaaaaaaaaa…/)).toBeInTheDocument()
  })
})

describe("ValidationFindings", () => {
  it("separates blocking errors from advisory warnings", () => {
    render(
      <ValidationFindings
        findings={
          [
            { severity: "error", code: "duplicate_key", message: "重复键" },
            { severity: "warning", code: "unreviewed_metric", message: "未复核口径" },
          ] as never
        }
      />,
    )
    expect(screen.getByText("1 项阻塞问题")).toBeInTheDocument()
    expect(screen.getByText("1 项提示")).toBeInTheDocument()
  })
})

describe("ResultPane", () => {
  it("keeps the real source and unreviewed status attached to the result", () => {
    render(
      <ResultPane
        result={
          {
            status: "completed",
            resource_stats: {
              source: "real_wind",
              rows: 10,
              non_null_rate: 0.8,
              metric_review_status: { ADJ_CLOSE: "unreviewed" },
            },
          } as never
        }
      />,
    )
    expect(screen.getByText("real_wind")).toBeInTheDocument()
    expect(screen.getByText("ADJ_CLOSE: unreviewed")).toBeInTheDocument()
    expect(screen.getByText(/含未复核口径/)).toBeInTheDocument()
  })
})

describe("publish from a completed result", () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function renderCompleted(result: Record<string, unknown> = { status: "completed" }) {
    return render(
      <MemoryRouter>
        <ResultPane
          result={result as never}
          sessionId="s1"
          sessionState="completed"
        />
      </MemoryRouter>,
    )
  }

  it("shows a publish button on a completed snapshot", () => {
    renderCompleted()
    expect(screen.getByRole("button", { name: "发布到因子库" })).toBeEnabled()
  })

  it("POSTs /api/library when publish is clicked and then links to the library", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ factor_id: "quality", version: 1 }),
    })
    vi.stubGlobal("fetch", fetchMock)
    const user = userEvent.setup()
    renderCompleted()
    await user.click(screen.getByRole("button", { name: "发布到因子库" }))
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/library",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ session_id: "s1" }),
      }),
    )
    expect(await screen.findByRole("link", { name: /因子库/ })).toHaveAttribute(
      "href",
      "/library",
    )
  })

  it("disables publish when result findings include disputed", () => {
    renderCompleted({
      status: "completed",
      result_validation: {
        findings: [{ severity: "error", code: "disputed", message: "有争议口径" }],
      },
    })
    expect(screen.getByRole("button", { name: "发布到因子库" })).toBeDisabled()
  })

  it("disables publish when the result status is not completed", () => {
    renderCompleted({ status: "failed" })
    expect(screen.getByRole("button", { name: "发布到因子库" })).toBeDisabled()
  })

  it("does not offer publish unless the session is completed", () => {
    render(
      <ResultPane
        result={{ status: "completed" } as never}
        sessionId="s1"
        sessionState="code_ready"
      />,
    )
    expect(screen.queryByRole("button", { name: "发布到因子库" })).toBeNull()
  })
})
