import type { ReactNode } from "react";

export function AccountTableScrollArea({ children }: { children: ReactNode }) {
  return (
    <div
      className="max-w-full overflow-x-auto rounded-xl border border-slate-800/80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/55"
      role="region"
      aria-label="Trading accounts table"
      tabIndex={0}
    >
      {children}
    </div>
  );
}

export function AccountSelectionButton({
  accountName,
  active,
  disabled = false,
  onSelect,
}: {
  accountName: string;
  active: boolean;
  disabled?: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className="inline-flex min-h-11 min-w-0 items-center truncate rounded-md text-left font-medium text-slate-100 underline-offset-4 hover:text-cyan-100 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/55 sm:min-h-0"
      aria-label={`Select ${accountName} account`}
      aria-pressed={active}
      disabled={disabled}
      onClick={onSelect}
    >
      {accountName}
    </button>
  );
}
