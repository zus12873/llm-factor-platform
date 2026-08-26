/**
 * Report page tests.
 *
 * The one that matters: the workflow gate stays shut until a human has supplied
 * or confirmed the formula. A low-confidence extraction looks exactly like a
 * good one on screen, so the button state is the only thing enforcing the rule.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { ReportsPage } from "./ReportsPage"

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>()
  return { ...actual, useNavigate: () => navigate }
})

/**
 * rc-picker's masked inputs do not accept native change events in jsdom.
 * The page still mounts the real RangePicker in the browser; tests drive the
 * same onChange contract with two labelled date fields.
 */
vi.mock("antd", async (importOriginal) => {
  const actual = await importOriginal<typeof import("antd")>()
  function RangePicker({
    onChange,
    placeholder,
  }: {
    onChange?: (
      dates: [{ format: (pattern: string) => string } | null, { format: (pattern: string) => string } | null] | null,
    ) => void
    placeholder?: [string, string]
  }) {
    const startPh = placeholder?.[0] ?? "开始日期"
    const endPh = placeholder?.[1] ?? "结束日期"
    const emit = (startVal: string, endVal: string) => {
      onChange?.([
        startVal ? { format: () => startVal } : null,
        endVal ? { format: () => endVal } : null,
      ])
    }
    return (
      <span>
        <input
          type="date"
          aria-label="开始日期"
          placeholder={startPh}
          onChange={(event) => {
            const end = event.currentTarget.parentElement?.querySelectorAll("input")[1] as HTMLInputElement
            emit(event.currentTarget.value, end?.value ?? "")
          }}
        />
        <input
          type="date"
          aria-label="结束日期"
          placeholder={endPh}
          onChange={(event) => {
            const start = event.currentTarget.parentElement?.querySelectorAll("input")[0] as HTMLInputElement
            emit(start?.value ?? "", event.currentTarget.value)
          }}
        />
      </span>
    )
  }
  function DatePickerProxy(props: Parameters<typeof actual.DatePicker>[0]) {
    return actual.DatePicker(props)
  }
  Object.assign(DatePickerProxy, actual.DatePicker, { RangePicker })
  return { ...actual, DatePicker: DatePickerProxy }
})

function uploadPayload(extraction: Record<string, unknown>) {
  return {
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
  }
}

function mockUpload(
  extraction: Record<string, unknown>,
  enter?: { status?: number; json?: Record<string, unknown> },
) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.includes("/sessions")) {
      const status = enter?.status ?? 201
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => enter?.json ?? { session_id: "s-report" },
      }
    }
    return {
      ok: true,
      status: 200,
      json: async () => uploadPayload(extraction),
    }
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

function renderPage() {
  return render(
    <MemoryRouter>
      <ReportsPage />
    </MemoryRouter>,
  )
}

async function uploadFile() {
  const user = userEvent.setup()
  renderPage()
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  await user.upload(input, new File(["%PDF-"], "note.pdf", { type: "application/pdf" }))
  return user
}

async function fillEnvelope() {
  fireEvent.change(await screen.findByLabelText("开始日期"), {
    target: { value: "2024-01-01" },
  })
  fireEvent.change(screen.getByLabelText("结束日期"), {
    target: { value: "2024-06-30" },
  })
}

function enterCall(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.find(([url]) => String(url).includes("/sessions"))
}

describe("ReportsPage", () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
    navigate.mockReset()
  })

  it("states the release's capability boundary up front", () => {
    renderPage()
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
    const proceed = await screen.findByRole("button", { name: "进入因子工作流" })
    expect(proceed).toBeDisabled()
    await fillEnvelope()
    expect(proceed).toBeEnabled()
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
    expect(proceed).toBeDisabled()
    await fillEnvelope()
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

  it("does not enter the workflow until the envelope and formula are present", async () => {
    mockUpload({
      status: "extracted",
      confidence: 0.92,
      source_pages: [3],
      extracted_text: "rank(ROE_TTM)",
      warning: "",
    })
    await uploadFile()
    const proceed = await screen.findByRole("button", { name: "进入因子工作流" })
    // Envelope dates are empty → still blocked, even though formula is extracted.
    expect(proceed).toBeDisabled()
  })

  it("posts the artifact id and navigates to the workbench", async () => {
    const fetchMock = mockUpload(
      {
        status: "extracted",
        confidence: 0.92,
        source_pages: [3],
        extracted_text: "rank(ROE_TTM)",
        warning: "",
      },
      { status: 201, json: { session_id: "s-report" } },
    )
    const user = await uploadFile()
    const proceed = await screen.findByRole("button", { name: "进入因子工作流" })
    await fillEnvelope()
    await user.click(proceed)

    await waitFor(() => {
      expect(enterCall(fetchMock)?.[0]).toBe("/api/reports/abc/sessions")
    })
    const body = JSON.parse((enterCall(fetchMock)?.[1] as RequestInit).body as string)
    expect(body.session_id).toMatch(/^s-/)
    expect(body.request).toMatchObject({
      asset_type: "stock",
      universe: "000300.SH",
      frequency: "daily",
      start_date: "2024-01-01",
      end_date: "2024-06-30",
      research_idea: "rank(ROE_TTM)",
    })
    expect(body.manual_formula).toBeUndefined()
    expect(navigate).toHaveBeenCalledWith("/workbench/s-report")
  })

  it("sends manual_formula when the extraction needed confirmation", async () => {
    const fetchMock = mockUpload(
      {
        status: "needs_manual_confirmation",
        confidence: 0.4,
        source_pages: [3],
        extracted_text: "rank(ROE?)",
        warning: "公式识别置信度低于阈值，需人工确认",
      },
      { status: 201, json: { session_id: "s-report" } },
    )
    const user = await uploadFile()
    await user.type(screen.getByLabelText("手动输入公式"), "rank(roe_ttm)")
    await fillEnvelope()
    await user.click(screen.getByRole("button", { name: "进入因子工作流" }))

    await waitFor(() => {
      expect(enterCall(fetchMock)).toBeTruthy()
    })
    const body = JSON.parse((enterCall(fetchMock)?.[1] as RequestInit).body as string)
    expect(body.manual_formula).toBe("rank(roe_ttm)")
  })
})
