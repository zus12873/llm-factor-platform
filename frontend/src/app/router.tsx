import { createBrowserRouter, Navigate } from "react-router-dom"
import { AppShell } from "./AppShell"
import { WorkbenchPage } from "../features/workbench/WorkbenchPage"
import { ReportsPage } from "../features/reports/ReportsPage"
import { LibraryPage } from "../features/library/LibraryPage"

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <Navigate to="/workbench" replace /> },
      { path: "workbench", element: <WorkbenchPage /> },
      { path: "workbench/:sessionId", element: <WorkbenchPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "library", element: <LibraryPage /> },
    ],
  },
])
