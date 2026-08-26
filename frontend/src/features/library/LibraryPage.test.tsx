/**
 * Library page tests.
 *
 * The stored `review_status` is what the page must show. Recomputing it from
 * metric keys in the browser would describe a registry that has since changed,
 * not the factor that was published.
 */
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { LibraryPage } from "./LibraryPage"

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <LibraryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function libraryEntry(overrides: Record<string, unknown> = {}) {
  return {
    factor_id: "quality",
    version: 2,
    factor_name: "quality",
    review_status: "unreviewed",
    review_note: "ROE_TTM 未复核",
    manifest_sha256: "m".repeat(64),
    program_sha256: "p".repeat(64),
    result_sha256: "r".repeat(64),
    artifact_path: "quality/v2/result.parquet",
    session_id: "s1",
    created_at: "2026-08-24T00:00:00Z",
    spec: { canonical_formula: "rank(roe_ttm)", factor_name: "quality" },
    provenance: {
      manifest_sha256: "m".repeat(64),
      result_sha256: "r".repeat(64),
      inputs: [
        {
          input_artifact_sha256: "i".repeat(64),
          query_timestamp: "2026-08-24T01:00:00Z",
          source_database: "wind",
          source_table: "asharettmhis",
          source_fields: ["s_fa_roe_ttm"],
          row_count: 10,
        },
      ],
      versions: { code_commit: "abc123", runtime_version: "0.1.0" },
    },
    ...overrides,
  }
}

describe("LibraryPage", () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it("lists published factors and shows the unreviewed label", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [libraryEntry()],
    }))
    renderPage()
    expect(await screen.findByText("quality")).toBeInTheDocument()
    expect(screen.getByText(/未复核/)).toBeInTheDocument()
  })

  it("opens a version detail with formula, hashes, provenance fields, and artifact path", async () => {
    const entry = libraryEntry()
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input)
        if (url.includes("/v/")) {
          return { ok: true, json: async () => entry }
        }
        return { ok: true, json: async () => [entry] }
      }),
    )
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByText("quality"))
    expect(await screen.findByText("rank(roe_ttm)")).toBeInTheDocument()
    expect(screen.getByText("quality/v2/result.parquet")).toBeInTheDocument()
    expect(screen.getByText("m".repeat(64))).toBeInTheDocument()
    expect(screen.getByText(/asharettmhis/)).toBeInTheDocument()
    expect(screen.getAllByText(/未复核/).length).toBeGreaterThan(0)
  })
})
