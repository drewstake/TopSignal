export interface AppliedCustomDateRange {
  startDate: string;
  endDate: string;
}

export function getAppliedCustomDateRange(startDate: string, endDate: string): AppliedCustomDateRange | null {
  if (!startDate || !endDate || startDate > endDate) {
    return null;
  }
  return { startDate, endDate };
}

export interface CustomDateRangeDraftResolution {
  appliedRange: AppliedCustomDateRange | null;
  nextMode: "CUSTOM" | "ALL" | "UNCHANGED";
}

export function resolveCustomDateRangeDraft(
  startDate: string,
  endDate: string,
  customModeActive: boolean,
): CustomDateRangeDraftResolution {
  const appliedRange = getAppliedCustomDateRange(startDate, endDate);
  if (appliedRange) {
    return { appliedRange, nextMode: "CUSTOM" };
  }
  return {
    appliedRange: null,
    nextMode: customModeActive ? "ALL" : "UNCHANGED",
  };
}
