import { lazy } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppShell } from "./AppShell";

const DashboardPage = lazy(() =>
  import("../pages/dashboard/DashboardPage").then((module) => ({ default: module.DashboardPage })),
);
const AccountsPage = lazy(() =>
  import("../pages/accounts/AccountsPage").then((module) => ({ default: module.AccountsPage })),
);
const TradesPage = lazy(() =>
  import("../pages/trades/TradesPage").then((module) => ({ default: module.TradesPage })),
);
const ExpensesPage = lazy(() =>
  import("../pages/expenses/ExpensesPage").then((module) => ({ default: module.ExpensesPage })),
);
const JournalPage = lazy(() =>
  import("../pages/journal/JournalPage").then((module) => ({ default: module.JournalPage })),
);
const BotPage = lazy(() =>
  import("../pages/bot/BotPage").then((module) => ({ default: module.BotPage })),
);

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
      {
        path: "bot",
        element: <BotPage />,
      },
    ],
  },
  {
    path: "*",
    element: <Navigate to="/" replace />,
  },
]);
