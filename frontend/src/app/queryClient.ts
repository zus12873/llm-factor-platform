import { QueryClient } from "@tanstack/react-query"
import { ApiError } from "../api/client"

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      retry: (failureCount, error) => {
        // A stale version or a refusal will not become true by retrying, and a
        // retried refusal reads to the user as a flaky server.
        if (error instanceof ApiError && error.status < 500) return false
        return failureCount < 2
      },
    },
  },
})
