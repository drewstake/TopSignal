const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const pnlFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});

export function formatCurrency(value: number) {
  return currencyFormatter.format(value);
}

export function formatPnl(value: number) {
  const prefix = value > 0 ? "+" : "";
  return `${prefix}${pnlFormatter.format(value)}`;
}

export function formatPercent(value: number, digits = 2) {
  return `${value.toFixed(digits)}%`;
}

export function formatNumber(value: number, digits = 2) {
  return value.toFixed(digits);
}

export function formatInteger(value: number) {
  return Math.round(value).toLocaleString("en-US");
}

export function formatMinutes(value: number) {
  const safeMinutes = Number.isFinite(value) ? Math.max(0, value) : 0;
  const totalSeconds = Math.round(safeMinutes * 60);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes} min ${seconds} sec`;
}

