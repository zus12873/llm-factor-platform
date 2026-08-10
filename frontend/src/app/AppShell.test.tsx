/**
 * Shell tests.
 *
 * The navigation test is a smoke test. The health-banner tests are not: this
 * platform runs offline by design, and a researcher who cannot see that Wind is
 * disconnected will read a run on fake data as a real one. A single green light
 * would actively mislead, so the banner names what is off.
 */
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { AppShell, NAV_ITEMS } from "./AppShell"

function renderShell() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/workbench"]}>
        <AppShell />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockHealth(components: { name: string; status: string }[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "ok", version: "0.1.0", components }),
    }),
  )
}

describe("AppShell", () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it("renders every primary navigation entry", () => {
    mockHealth([{ name: "database", status: "ok" }])
    renderShell()
    for (const item of NAV_ITEMS) {
      expect(screen.getByText(item.label)).toBeInTheDocument()
    }
  })

  it("names the components that are offline rather than showing one status", async () => {
    mockHealth([
      { name: "database", status: "ok" },
      { name: "wind", status: "disabled" },
      { name: "llm", status: "unconfigured" },
    ])
    renderShell()
    expect(await screen.findByText(/离线模式：wind、llm/)).toBeInTheDocument()
  })

  it("reports everything ready only when every component is ok", async () => {
    mockHealth([
      { name: "database", status: "ok" },
      { name: "wind", status: "ok" },
    ])
    renderShell()
    expect(await screen.findByText("全部就绪")).toBeInTheDocument()
  })

  it("says the backend is unreachable rather than showing a stale status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")))
    renderShell()
    expect(await screen.findByText("后端不可达")).toBeInTheDocument()
  })
})
