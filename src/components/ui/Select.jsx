import { ChevronDown } from "lucide-react";

export default function Select({ value, onChange, children }) {
  return (
    <div className="relative">
      <select
        value={value}
        onChange={onChange}
        className="w-full appearance-none rounded-xl bg-zinc-900/60 border border-white/10 px-4 py-3 pr-10 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
      >
        {children}
      </select>
      <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 h-4 w-4 text-zinc-400 pointer-events-none" />
    </div>
  );
}
