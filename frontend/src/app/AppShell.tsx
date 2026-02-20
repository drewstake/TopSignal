import { useMemo, useState } from "react";
import { Outlet } from "react-router-dom";
import { Input } from "../components/ui/Input";
import { Select } from "../components/ui/Select";
import { Tabs } from "../components/ui/Tabs";

const navItems = [
  { label: "Overview", to: "/overview" },
  { label: "Trades", to: "/trades" },
  { label: "Analytics", to: "/analytics" },
  { label: "Journal", to: "/journal" },
];

const dateRanges = [
  { label: "Last 7 days", value: "7d" },
  { label: "Last 30 days", value: "30d" },
  { label: "Quarter to date", value: "qtd" },
  { label: "Year to date", value: "ytd" },
];

export function AppShell() {
  const [range, setRange] = useState("30d");
  const [search, setSearch] = useState("");

  const activeRangeLabel = useMemo(
    () => dateRanges.find((item) => item.value === range)?.label ?? "Last 30 days",
    [range],
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-30 border-b border-slate-800/80 bg-slate-950/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-[1440px] flex-col gap-4 px-4 py-4 lg:px-8">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-500 to-violet-500 text-sm font-semibold text-slate-950 shadow-panel">
                TS
              </div>
              <div>
                <p className="text-sm font-semibold text-slate-200">TopSignal</p>
                <p className="text-xs text-slate-400">Trading Dashboard</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Select
                aria-label="Date range"
                value={range}
                onChange={(event) => setRange(event.target.value)}
                className="w-44"
              >
                {dateRanges.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </Select>
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search symbol or note"
                aria-label="Search"
                className="w-52"
              />
              <button
                type="button"
                className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-700 bg-slate-900/70 text-sm font-semibold text-slate-200 transition hover:border-cyan-400/70 hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"
                aria-label="Profile"
              >
                AW
              </button>
            </div>
          </div>
          <div className="flex items-center justify-between gap-3">
            <Tabs items={navItems} />
            <p className="hidden text-xs text-slate-400 md:block">Range: {activeRangeLabel}</p>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1440px] px-4 py-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  );
}
