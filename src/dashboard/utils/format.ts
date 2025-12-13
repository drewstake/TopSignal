export function fmtPct(x: number) {
  if (!Number.isFinite(x)) return "0.0%";
  return `${(x * 100).toFixed(1)}%`;
}

export function fmtPF(x: number) {
  if (x === Infinity) return "∞";
  if (!Number.isFinite(x)) return "0.00";
  return x.toFixed(2);
}

export function fmtDays(x: number) {
  if (!Number.isFinite(x)) return "0.0";
  return `${x.toFixed(1)}d`;
}

export function fmtDuration(msValue: number) {
  if (!Number.isFinite(msValue) || msValue <= 0) return "0s";
  const totalSeconds = Math.round(msValue / 1000);

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const parts: string[] = [];
  if (hours) parts.push(`${hours}h`);
  if (hours || minutes) parts.push(`${minutes}m`);
  parts.push(`${seconds}s`);

  return parts.join(" ");
}
