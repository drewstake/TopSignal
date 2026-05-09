import { useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { cn } from "./cn";

export interface TabItem {
  label: string;
  to: string;
}

export interface TabsProps {
  items: TabItem[];
}

export function Tabs({ items }: TabsProps) {
  const location = useLocation();
  const navRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const activeLink = navRef.current?.querySelector<HTMLAnchorElement>("[aria-current='page']");
    activeLink?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [location.pathname, location.search]);

  return (
    <nav
      ref={navRef}
      className="flex w-full items-center gap-2 overflow-x-auto rounded-xl border border-app-border bg-app-surface/50 p-1"
    >
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            cn(
              "rounded-lg px-3 py-2 text-sm font-medium transition duration-200",
              isActive
                ? "bg-gradient-to-r from-app-accent/20 to-app-highlight/25 text-app-accent"
                : "text-app-muted hover:bg-app-raised/80 hover:text-app-text-soft",
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}
