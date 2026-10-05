import { useState } from 'react';

const PREFIX = 'topsignal.simulation.workspace.v1.';
// Small presentation preferences live separately from exact engine checkpoints.
export function readPreference<T>(key: string, fallback: T, valid: (v: unknown) => v is T): T {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    if (raw && raw.length < 8000) { const value: unknown = JSON.parse(raw); if (valid(value)) return value; }
  } catch { /* Preferences are optional when browser storage is disabled. */ }
  return fallback;
}
export function writePreference(key: string, value: unknown) {
  try { localStorage.setItem(PREFIX + key, JSON.stringify(value)); } catch { /* Never interfere with the market. */ }
}
export function usePanelPreference(key: string, initial = true) {
  const [open, setOpen] = useState(() => readPreference(key, initial, (v): v is boolean => typeof v === 'boolean'));
  return [open, (value: boolean) => { setOpen(value); writePreference(key, value); }] as const;
}
