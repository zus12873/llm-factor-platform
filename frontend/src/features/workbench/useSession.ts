/**
 * Session state hook: fetch the snapshot first, then follow the event log.
 *
 * The order matters on reload. Opening the stream first would show events for a
 * session whose current state the UI does not yet know, and the user would watch
 * a step rail assemble itself from history. Fetching the snapshot first means a
 * reloaded page is immediately correct, and the stream only carries it forward.
 */
import { useCallback, useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { apiClient, ApiError, type SessionSnapshot } from "../../api/client"

export function useSession(sessionId: string | undefined) {
  const queryClient = useQueryClient()
  const [streamError, setStreamError] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ["session", sessionId],
    queryFn: () => apiClient.getSession(sessionId!),
    enabled: Boolean(sessionId),
  })

  const refresh = useCallback(() => {
    if (sessionId) {
      void queryClient.invalidateQueries({ queryKey: ["session", sessionId] })
    }
  }, [queryClient, sessionId])

  useEffect(() => {
    // Only follow once the snapshot has landed, so the stream extends a known
    // state instead of racing to establish one.
    if (!sessionId || !query.data) return
    const stop = apiClient.subscribe(sessionId, query.data.version, () => {
      setStreamError(null)
      refresh()
    })
    return stop
  }, [sessionId, query.data?.version, refresh, query.data])

  return {
    snapshot: query.data as SessionSnapshot | undefined,
    isLoading: query.isLoading,
    error: query.error instanceof ApiError ? query.error : null,
    streamError,
    refresh,
  }
}
