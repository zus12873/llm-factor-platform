/**
 * Typed API client.
 *
 * Types come from the backend's own OpenAPI schema (`npm run gen:api`), so the
 * two sides cannot drift: a renamed field breaks the build here rather than
 * appearing as `undefined` in a component at runtime.
 *
 * Every mutation carries `expected_version`. Two browser tabs on one session is
 * the normal case, and without the version the second tab's confirmation
 * silently overwrites the first tab's revision.
 */
import type { components } from "./schema"

export type SessionSnapshot = components["schemas"]["SessionSnapshot"]

/**
 * FastAPI splits this into two schemas because the shapes genuinely differ: on
 * the way out every default is populated, on the way in they are optional. The
 * client keeps the distinction rather than collapsing it, so sending a snapshot
 * straight back is type-checked instead of hopeful.
 */
export type FactorSpecOut = components["schemas"]["FactorSpec-Output"]
export type FactorSpecIn = components["schemas"]["FactorSpec-Input"]
export type FieldSelection = components["schemas"]["FieldSelection"]
export type ResearchRequest = components["schemas"]["ResearchRequest"]
export type HealthReport = components["schemas"]["HealthReport"]

/** A domain error the backend gave a stable code to. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
    this.name = "ApiError"
  }

  /** The session moved on; re-read and retry rather than editing the payload. */
  get isStaleVersion(): boolean {
    return this.code === "stale_session_version"
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  })

  if (!response.ok) {
    // Branch on the code, never on the message: the wording is free to change.
    const body = await response.json().catch(() => null)
    const error = body?.error
    throw new ApiError(
      response.status,
      error?.code ?? "unknown_error",
      error?.message ?? `HTTP ${response.status}`,
    )
  }
  return (await response.json()) as T
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) })

export const apiClient = {
  health: () => request<HealthReport>("/api/health"),

  createSession: (sessionId: string) =>
    post<SessionSnapshot>("/api/sessions", { session_id: sessionId }),

  getSession: (sessionId: string) =>
    request<SessionSnapshot>(`/api/sessions/${sessionId}`),

  submitMessage: (sessionId: string, expectedVersion: number, req: ResearchRequest) =>
    post<SessionSnapshot>(`/api/sessions/${sessionId}/messages`, {
      expected_version: expectedVersion,
      request: req,
    }),

  confirmFormula: (sessionId: string, expectedVersion: number, spec: FactorSpecIn) =>
    post<SessionSnapshot>(`/api/sessions/${sessionId}/confirm-formula`, {
      expected_version: expectedVersion,
      factor_spec: spec,
    }),

  confirmFields: (
    sessionId: string,
    expectedVersion: number,
    selections: FieldSelection[],
  ) =>
    post<SessionSnapshot>(`/api/sessions/${sessionId}/confirm-fields`, {
      expected_version: expectedVersion,
      field_selections: selections,
    }),

  buildManifest: (sessionId: string, expectedVersion: number, req: ResearchRequest) =>
    post<SessionSnapshot>(`/api/sessions/${sessionId}/manifest`, {
      expected_version: expectedVersion,
      request: req,
    }),

  reviseFormula: (sessionId: string, expectedVersion: number, spec: FactorSpecIn) =>
    post<SessionSnapshot>(`/api/sessions/${sessionId}/revise-formula`, {
      expected_version: expectedVersion,
      factor_spec: spec,
    }),

  cancel: (sessionId: string, expectedVersion: number) =>
    post<SessionSnapshot>(`/api/sessions/${sessionId}/cancel`, {
      expected_version: expectedVersion,
    }),

  /**
   * Subscribe to the session log, resuming after `lastEventId`.
   *
   * The stream is a projection of a durable log, not a broadcast, so a tab that
   * slept reconnects with its last id and receives exactly what it missed.
   */
  subscribe: (
    sessionId: string,
    lastEventId: number | null,
    onEvent: (event: { sequence: number; event_type: string }) => void,
  ): (() => void) => {
    const url = `/api/sessions/${sessionId}/events`
    const source = new EventSource(
      lastEventId ? `${url}?after=${lastEventId}` : url,
    )
    source.onmessage = (message) => {
      try {
        onEvent(JSON.parse(message.data))
      } catch {
        // A malformed frame must not kill the subscription; the next event
        // still carries the session forward.
      }
    }
    return () => source.close()
  },
}
