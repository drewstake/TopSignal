import type { ReactNode } from "react";

export default function KpiCard(props: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-950/30 p-4">
      <div className="text-xs text-zinc-400">{props.label}</div>
      <div className="mt-1 text-xl font-semibold text-zinc-100">{props.value}</div>
      {props.sub ? <div className="mt-1 text-xs text-zinc-400">{props.sub}</div> : null}
    </div>
  );
}
