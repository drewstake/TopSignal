import { NavLink, Outlet } from "react-router-dom";

function navClass(isActive: boolean) {
  return (
    "rounded-xl px-3 py-2 text-sm " +
    (isActive
      ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-200 dark:text-zinc-900"
      : "border border-zinc-200 bg-white text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200")
  );
}

export default function Layout() {
  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-900 dark:text-zinc-100">
      <div className="mx-auto max-w-6xl px-4 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xl font-semibold">TopSignal</div>
            <div className="mt-1 text-sm text-zinc-500 dark:text-zinc-600 dark:text-zinc-400">
              TopstepX metrics dashboard
            </div>
          </div>

          <div className="flex items-center gap-2">
            <NavLink to="/" end className={({ isActive }) => navClass(isActive)}>
              Dashboard
            </NavLink>
            <NavLink to="/accounts" className={({ isActive }) => navClass(isActive)}>
              Accounts
            </NavLink>
            <NavLink to="/trade" className={({ isActive }) => navClass(isActive)}>
              Trade
            </NavLink>
            <NavLink to="/settings" className={({ isActive }) => navClass(isActive)}>
              Settings
            </NavLink>
          </div>
        </div>

        <div className="mt-4">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
