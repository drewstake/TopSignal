import { useEffect, useState } from "react";

const ACTIVE_KEY = "topsignal.topstep.activeAccountId.v1";
const EVENT_NAME = "topsignal:activeAccountChanged";

export function getActiveAccountId(): number | null {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  } catch {
    return null;
  }
}

export function setActiveAccountId(id: number) {
  localStorage.setItem(ACTIVE_KEY, String(id));
  window.dispatchEvent(new Event(EVENT_NAME));
}

export function useActiveAccountId() {
  const [id, setId] = useState<number | null>(() => getActiveAccountId());

  useEffect(() => {
    const onChange = () => setId(getActiveAccountId());
    window.addEventListener(EVENT_NAME, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(EVENT_NAME, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  return id;
}
