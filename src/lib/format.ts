export function fmtMoney(n: number) {
  const v = Number.isFinite(n) ? n : 0;
  return v.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

export function fmtNumber(n: number, digits = 2) {
  const v = Number.isFinite(n) ? n : 0;
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fmtPercent(x: number, digits = 1) {
  const v = Number.isFinite(x) ? x : 0;
  return `${(v * 100).toFixed(digits)}%`;
}

export function fmtInt(n: number) {
  const v = Number.isFinite(n) ? n : 0;
  return Math.round(v).toLocaleString();
}

export function fmtDuration(ms: number) {
  const v = Number.isFinite(ms) ? Math.max(0, ms) : 0;
  const totalSec = Math.floor(v / 1000);
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const hh = String(h).padStart(2, "0");
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}
