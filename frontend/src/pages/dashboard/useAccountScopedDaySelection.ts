import { useCallback, useMemo, useState } from "react";

interface AccountScopedDaySelection {
  scope: object;
  date: string;
}

/** Prevent a calendar drill-down from leaking or resurfacing across accounts. */
export function useAccountScopedDaySelection(accountId: number | null) {
  const accountScope = useMemo(() => ({ accountId }), [accountId]);
  const [selection, setSelection] = useState<AccountScopedDaySelection | null>(null);

  const setSelectedDate = useCallback(
    (date: string | null) => {
      setSelection(date !== null && accountId !== null ? { scope: accountScope, date } : null);
    },
    [accountId, accountScope],
  );

  return {
    selectedDate: selection?.scope === accountScope ? selection.date : null,
    setSelectedDate,
  };
}
