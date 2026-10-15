import { RouterProvider } from "react-router-dom";
import { router } from "./app/routes";
import { AccountAccess } from "./app/AccountAccess";

export default function App() {
  return <RouterProvider router={router} />;
}

// Compatibility harness for authentication isolation tests. Real route access
// is enforced by AccountAccess around the account workspace in routes.tsx.
export function AccountWorkspace() {
  return <AccountAccess><RouterProvider router={router} /></AccountAccess>;
}
