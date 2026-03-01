import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppShell } from "./AppShell";
import { AccountsPage } from "../pages/accounts/AccountsPage";
import { DashboardPage } from "../pages/dashboard/DashboardPage";
import { ExpensesPage } from "../pages/expenses/ExpensesPage";
import { JournalPage } from "../pages/journal/JournalPage";
import { TradesPage } from "../pages/trades/TradesPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      {
        index: true,
        element: <DashboardPage />,
      },
      {
        path: "accounts",
        element: <AccountsPage />,
      },
      {
        path: "trades",
        element: <TradesPage />,
      },
      {
        path: "expenses",
        element: <ExpensesPage />,
      },
      {
        path: "journal",
        element: <JournalPage />,
      },
    ],
  },
  {
    path: "*",
    element: <Navigate to="/" replace />,
  },
]);
