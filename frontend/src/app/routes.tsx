import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppShell } from "./AppShell";
import { AnalyticsPage } from "../pages/analytics/AnalyticsPage";
import { JournalPage } from "../pages/journal/JournalPage";
import { OverviewPage } from "../pages/overview/OverviewPage";
import { TradesPage } from "../pages/trades/TradesPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <Navigate to="/overview" replace />,
      },
      {
        path: "overview",
        element: <OverviewPage />,
      },
      {
        path: "trades",
        element: <TradesPage />,
      },
      {
        path: "analytics",
        element: <AnalyticsPage />,
      },
      {
        path: "journal",
        element: <JournalPage />,
      },
    ],
  },
]);
