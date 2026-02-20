import { NavLink } from "react-router-dom";
import { cn } from "./cn";

export interface TabItem {
  label: string;
  to: string;
}

export interface TabsProps {
  items: TabItem[];
}

export function Tabs({ items }: TabsProps) {
  return (
    <nav className="flex w-full items-center gap-2 overflow-x-auto rounded-xl border border-slate-800 bg-slate-900/50 p-1">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            cn(
              "rounded-lg px-3 py-2 text-sm font-medium transition duration-200",
              isActive
                ? "bg-gradient-to-r from-cyan-500/20 to-violet-500/25 text-cyan-100"
                : "text-slate-400 hover:bg-slate-800/80 hover:text-slate-200",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
