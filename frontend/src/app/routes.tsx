import { lazy } from "react";
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
const BotForwardResultsPage = lazy(() =>
  import("../pages/bot/BotForwardResultsPage").then((module) => ({ default: module.BotForwardResultsPage })),
);
const SimulationPage = lazy(() =>
  import("../pages/simulation/funded/TradingPage").then((module) => ({ default: module.TradingPage })),
);
const AppearancePage = lazy(() =>
  import("../pages/themes/ThemePage").then((module) => ({ default: module.AppearancePage })),
);

export const router = createBrowserRouter([
  {
    path: "/bot-results/:botId",
    element: <AccountAccess><BotForwardResultsPage /></AccountAccess>,
    errorElement: <RouteErrorPage fullScreen />,
  },
  {
    path: "/",
    element: <AccountAccess><AppShell /></AccountAccess>,
    errorElement: <RouteErrorPage fullScreen />,
    children: [
      {
        index: true,
        element: <DashboardPage />,
        errorElement: <RouteErrorPage />,
      },
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
        path: "simulation",
        element: <SimulationPage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "appearance",
        element: <AppearancePage />,
        errorElement: <RouteErrorPage />,
      },
      {
        path: "themes",
        loader: ({ request }) => redirect(`/appearance${new URL(request.url).search}`),
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
