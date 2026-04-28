import { lazy } from "react";
import { Navigate, createBrowserRouter } from "react-router-dom";
import { AppShell } from "./AppShell";
import { RouteErrorPage } from "./RouteErrorPage";

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
    errorElement: <RouteErrorPage fullScreen />,
    children: [
      {
        index: true,
        element: <DashboardPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "accounts",
        element: <AccountsPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "trades",
        element: <TradesPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "expenses",
        element: <ExpensesPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "journal",
        element: <JournalPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "bot",
        element: <BotPage />,
        errorElement: <RouteErrorPage />,
      },
    ],
  },
  {
    path: "*",
    element: <Navigate to="/" replace />,
  },
]);
