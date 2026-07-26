import { useEffect, useState } from "react";

export const COMPACT_MODE_CHANGED_EVENT = "topsignal:compact-mode-changed";

const COMPACT_MODE_STORAGE_KEY = "topsignal.compactMode";

interface CompactModeChangeDetail {
  enabled: boolean;
}

export interface CompactModeController {
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
}

function parseStoredPreference(value: string | null): boolean | null {
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  return null;
}

export function isCompactModeEnabled(): boolean {
  try {
    if (typeof localStorage === "undefined") {
      return false;
    }
    return parseStoredPreference(localStorage.getItem(COMPACT_MODE_STORAGE_KEY)) ?? false;
  } catch {
    return false;
  }
}

function emitCompactModeChanged(enabled: boolean) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") {
    return;
  }

  if (typeof CustomEvent === "function") {
    window.dispatchEvent(
      new CustomEvent<CompactModeChangeDetail>(COMPACT_MODE_CHANGED_EVENT, {
        detail: { enabled },
      }),
    );
    return;
  }

  window.dispatchEvent(new Event(COMPACT_MODE_CHANGED_EVENT));
}

export function setCompactModeEnabled(enabled: boolean) {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(COMPACT_MODE_STORAGE_KEY, enabled ? "true" : "false");
    }
  } catch {
    // The current tab still receives the preference event when storage is unavailable.
  }
  emitCompactModeChanged(enabled);
}

export function useCompactMode(): CompactModeController {
  const [enabled, setEnabled] = useState(() => isCompactModeEnabled());

  useEffect(() => {
    function handleCompactModeChanged(event: Event) {
      const nextEnabled = (event as CustomEvent<CompactModeChangeDetail>).detail?.enabled;
      setEnabled(typeof nextEnabled === "boolean" ? nextEnabled : isCompactModeEnabled());
    }

    function handleStorage(event: StorageEvent) {
      if (event.key === COMPACT_MODE_STORAGE_KEY || event.key === null) {
        setEnabled(isCompactModeEnabled());
      }
    }

    window.addEventListener(COMPACT_MODE_CHANGED_EVENT, handleCompactModeChanged);
    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener(COMPACT_MODE_CHANGED_EVENT, handleCompactModeChanged);
      window.removeEventListener("storage", handleStorage);
    };
  }, []);

  return {
    enabled,
    setEnabled: setCompactModeEnabled,
  };
}
