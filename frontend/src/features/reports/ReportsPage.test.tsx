/**
 * Report page tests.
 *
 * The one that matters: the workflow gate stays shut until a human has supplied
 * or confirmed the formula. A low-confidence extraction looks exactly like a
 * good one on screen, so the button state is the only thing enforcing the rule.
 */
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ReportsPage } from "./ReportsPage"

function mockUpload(extraction: Record<string, unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        artifact_id: "abc",
        display_name: "note.pdf",
        page_count: 3,
        scanned_pages: [],
        capability_note: "…",
        extraction: {
          factor_name: "quality",
          hypothesis: "",
          evidence: [
            { evidence_id: "p3b1", page_number: 3, text: "因子定义：对 ROE_TTM 做横截面排名。" },
          ],
          formula_extraction: extraction,
        },
      }),
    }),
  )
}

async function uploadFile() {
  const user = userEvent.setup()
  render(<ReportsPage />)
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await user.upload(input, new File(["%PDF-"], "note.pdf", { type: "application/pdf" }))
  return user
}

describe("ReportsPage", () => {
  beforeEach(() => vi.unstubAllGlobals())

  it("states the release's capability boundary up front", () => {
    render(<ReportsPage />)
    expect(screen.getByText(/图片公式、复杂数学/)).toBeInTheDocument()
  })

  it("shows the source page before offering to continue", async () => {
    mockUpload({
      status: "extracted",
      confidence: 0.92,
      source_pages: [3],
      extracted_text: "rank(ROE_TTM)",
      warning: "",
    })
    await uploadFile()
    expect(await screen.findByText("第 3 页")).toBeInTheDocument()
    expect(await screen.findByRole("button", { name: "进入因子工作流" })).toBeEnabled()
  })

  it("blocks the workflow until a low-confidence formula is typed in", async () => {
    mockUpload({
      status: "needs_manual_confirmation",
      confidence: 0.4,
      source_pages: [3],
      extracted_text: "rank(ROE?)",
      warning: "公式识别置信度低于阈值，需人工确认",
    })
    const user = await uploadFile()

    const proceed = await screen.findByRole("button", { name: "进入因子工作流" })
    expect(proceed).toBeDisabled()

    await user.type(screen.getByLabelText("手动输入公式"), "rank(roe_ttm)")
    expect(proceed).toBeEnabled()
  })

  it("still shows the evidence when extraction needs confirmation", async () => {
    mockUpload({
      status: "needs_manual_confirmation",
      confidence: 0.2,
      source_pages: [3],
      extracted_text: "",
      warning: "多栏版式，正文顺序可能错乱",
    })
    await uploadFile()
    // A failed extraction that also hides the evidence leaves the user with nothing.
    expect(await screen.findByText(/因子定义：对 ROE_TTM/)).toBeInTheDocument()
    expect(screen.getByText(/多栏版式/)).toBeInTheDocument()
  })

  it("surfaces an upload rejection rather than failing silently", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 413,
        json: async () => ({ error: { code: "report_limit_exceeded", message: "文件过大" } }),
      }),
    )
    await uploadFile()
    expect(await screen.findByText("文件过大")).toBeInTheDocument()
  })
})
