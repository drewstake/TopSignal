import { useEffect } from "react";
import { NavLink, Outlet } from "react-router-dom";

type Theme = "light" | "dark";

function navClass(isActive: boolean) {
  return (
    "rounded-xl px-3 py-2 text-sm transition " +
    (isActive
      ? "bg-zinc-200 text-zinc-900 shadow-sm dark:bg-zinc-200"
      :
          "border border-zinc-300 bg-white text-zinc-700 shadow-sm hover:border-zinc-400 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-200")
  );
}

function getInitialTheme(): Theme {
  if (typeof window === "undefined") return "dark";

  const stored = localStorage.getItem("topsignal-theme");
  if (stored === "light" || stored === "dark") return stored;

  return "dark";
}

export default function Layout() {
  useEffect(() => {
    const theme = getInitialTheme();
    const root = document.documentElement;
    root.classList.toggle("dark", theme === "dark");
    root.setAttribute("data-theme", theme);
    localStorage.setItem("topsignal-theme", theme);
  }, []);

  return (
    <div className="min-h-screen bg-white text-zinc-900 transition-colors dark:bg-zinc-950 dark:text-zinc-100">
      <div className="mx-auto max-w-6xl px-4 py-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xl font-semibold">TopSignal</div>
            <div className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
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
