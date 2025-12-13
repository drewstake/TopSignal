import { createBrowserRouter } from "react-router-dom";

import Layout from "../dashboard/components/Layout";
import DashboardPage from "../dashboard/views/DashboardPage";
import AccountsPage from "../dashboard/views/AccountsPage";
import SettingsPage from "../dashboard/views/SettingsPage";
import NotFoundPage from "../dashboard/views/NotFoundPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "accounts", element: <AccountsPage /> },
      { path: "settings", element: <SettingsPage /> },
    ],
  },
  { path: "*", element: <NotFoundPage /> },
]);
