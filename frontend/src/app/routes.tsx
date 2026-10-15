import { lazy, Suspense } from "react";
import { Navigate, createBrowserRouter, redirect } from "react-router-dom";
import { AppShell } from "./AppShell";
import { RouteErrorPage } from "./RouteErrorPage";
import { AccountAccess } from "./AccountAccess";

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
const BotPage = lazy(() =>
  import("../pages/bot/BotPage").then((module) => ({ default: module.BotPage })),
);
const SimulationPage = lazy(() =>
  import("../pages/simulation/funded/TradingPage").then((module) => ({ default: module.TradingPage })),
);
const ThemePage = lazy(() =>
  import("../pages/themes/ThemePage").then((module) => ({ default: module.ThemePage })),
);

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Suspense fallback={<div className="min-h-screen bg-app-bg p-8 text-app-text">Opening TopSignal…</div>}><SimulationPage /></Suspense>,
    errorElement: <RouteErrorPage fullScreen />,
  },
  {
    path: "/simulation",
    element: <Suspense fallback={<div className="min-h-screen bg-app-bg p-8 text-app-text">Opening TopSignal…</div>}><SimulationPage /></Suspense>,
    errorElement: <RouteErrorPage fullScreen />,
  },
  {
    element: <AccountAccess><AppShell /></AccountAccess>,
    errorElement: <RouteErrorPage fullScreen />,
    children: [
      {
        path: "dashboard",
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
        loader: ({ request }) => redirect(`/dashboard${new URL(request.url).search}`),
      },
      {
        path: "bot",
        element: <BotPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "themes",
        element: <ThemePage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "data",
        loader: ({ request }) => redirect(`/dashboard${new URL(request.url).search}`),
      },
    ],
  },
  {
    path: "*",
    element: <Navigate to="/" replace />,
  },
]);
