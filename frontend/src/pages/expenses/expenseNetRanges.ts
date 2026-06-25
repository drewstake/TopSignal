export interface NetRangeDateRange {
  startDate?: string;
  endDate?: string;
}

export interface NetRangeOption {
  key: string;
  label: string;
  dateRange: NetRangeDateRange;
}

export function formatLocalIsoDate(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function parseLocalIsoDate(value: string) {
  const [yearText, monthText, dayText] = value.split("-");
  return new Date(Number(yearText), Number(monthText) - 1, Number(dayText));
}

function addLocalDays(date: Date, days: number) {
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  target.setDate(target.getDate() + days);
  return target;
}

function subtractLocalMonths(date: Date, months: number) {
  const target = new Date(date.getFullYear(), date.getMonth() - months, 1);
  const lastDayOfTargetMonth = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  target.setDate(Math.min(date.getDate(), lastDayOfTargetMonth));
  return target;
}

function addLocalYears(date: Date, years: number) {
  const target = new Date(date.getFullYear() + years, date.getMonth(), 1);
  const lastDayOfTargetMonth = new Date(target.getFullYear(), target.getMonth() + 1, 0).getDate();
  target.setDate(Math.min(date.getDate(), lastDayOfTargetMonth));
  return target;
}

function getTrailingDateRange(months: number, end = new Date()) {
  return {
    startDate: formatLocalIsoDate(subtractLocalMonths(end, months)),
    endDate: formatLocalIsoDate(end),
  };
}

function getYearToDateRange(end = new Date()) {
  return {
    startDate: `${end.getFullYear()}-01-01`,
    endDate: formatLocalIsoDate(end),
  };
}

function getEarlierIsoDate(left: string, right: string) {
  return left <= right ? left : right;
}

function buildStaticNetRangeOptions(today = new Date()): NetRangeOption[] {
  return [
    { key: "one_month", label: "1 Month", dateRange: getTrailingDateRange(1, today) },
    { key: "three_months", label: "3 Months", dateRange: getTrailingDateRange(3, today) },
    { key: "six_months", label: "6 Months", dateRange: getTrailingDateRange(6, today) },
    { key: "year_to_date", label: "YTD", dateRange: getYearToDateRange(today) },
    { key: "one_year", label: "1 Year", dateRange: getTrailingDateRange(12, today) },
    { key: "all_time", label: "All Time", dateRange: {} },
  ];
}

export function getEarliestIsoDate(values: Array<string | null>) {
  let earliest: string | null = null;
  for (const value of values) {
    if (value === null) {
      continue;
    }
    if (earliest === null || value < earliest) {
      earliest = value;
    }
  }
  return earliest;
}

export function buildAnniversaryYearRangeOptions(firstCashFlowDate: string | null, today = new Date()): NetRangeOption[] {
  if (firstCashFlowDate === null) {
    return [];
  }

  const todayIso = formatLocalIsoDate(today);
  if (firstCashFlowDate > todayIso) {
    return [];
  }

  const firstDate = parseLocalIsoDate(firstCashFlowDate);
  const options: NetRangeOption[] = [];

  for (let yearIndex = 0; yearIndex < 100; yearIndex += 1) {
    const startDate = addLocalYears(firstDate, yearIndex);
    const startDateIso = formatLocalIsoDate(startDate);
    if (startDateIso > todayIso) {
      break;
    }

    const nextYearStartDate = addLocalYears(firstDate, yearIndex + 1);
    const fullYearEndDateIso = formatLocalIsoDate(addLocalDays(nextYearStartDate, -1));
    options.push({
      key: `anniversary_year_${yearIndex + 1}`,
      label: `Year ${yearIndex + 1}`,
      dateRange: {
        startDate: startDateIso,
        endDate: getEarlierIsoDate(fullYearEndDateIso, todayIso),
      },
    });
  }

  return options;
}

export function buildNetRangeOptions(firstCashFlowDate: string | null, today = new Date()): NetRangeOption[] {
  const staticOptions = buildStaticNetRangeOptions(today);
  const allTimeOption = staticOptions[staticOptions.length - 1];
  if (!allTimeOption) {
    return staticOptions;
  }
  return [
    ...staticOptions.slice(0, -1),
    ...buildAnniversaryYearRangeOptions(firstCashFlowDate, today),
    allTimeOption,
  ];
}
