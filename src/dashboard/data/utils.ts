import type { TimeBlock } from "../../types/trades";

export function safeDiv(a: number, b: number) {
  return b === 0 ? 0 : a / b;
}

export function median(xs: number[]) {
  if (!xs.length) return 0;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

export function fmtISO(d: Date) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${dd}`;
}

export function weekdayName(dateISO: string) {
  const d = new Date(dateISO + "T00:00:00");
  const names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  return names[d.getDay()];
}

export function toMinutes(hhmm: string) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function timeBlock(entryTime: string): TimeBlock {
  const mins = toMinutes(entryTime);
  const pre = 9 * 60 + 30;
  const openEnd = 11 * 60;
  const midEnd = 14 * 60;
  const closeEnd = 16 * 60;

  if (mins < pre) return "Pre";
  if (mins < openEnd) return "Open";
  if (mins < midEnd) return "Mid";
  if (mins < closeEnd) return "Close";
  return "After";
}

export function bucketDuration(min: number) {
  if (min < 5) return "0-5m";
  if (min < 15) return "5-15m";
  if (min < 30) return "15-30m";
  if (min < 60) return "30-60m";
  if (min < 120) return "1-2h";
  return "2h+";
}

export function bucketR(r: number) {
  if (r < -2) return "< -2R";
  if (r < -1) return "-2R to -1R";
  if (r < -0.5) return "-1R to -0.5R";
  if (r < 0) return "-0.5R to 0R";
  if (r < 0.5) return "0R to 0.5R";
  if (r < 1) return "0.5R to 1R";
  if (r < 2) return "1R to 2R";
  return ">= 2R";
}

export function seededRand(seed: number) {
  let t = seed % 2147483647;
  if (t <= 0) t += 2147483646;
  return () => {
    t = (t * 16807) % 2147483647;
    return (t - 1) / 2147483646;
  };
}
