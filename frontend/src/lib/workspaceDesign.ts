import { useSyncExternalStore } from "react";

export const WORKSPACE_DESIGNS = [
  { id: "signal-deck", name: "Signal Deck", description: "A full sidebar with compact sections and aligned data rows." },
  { id: "terminal", name: "Terminal", description: "A slim tool rail, compact panels, and monospaced type for scanning data." },
  { id: "editorial", name: "Editorial", description: "A masthead, horizontal navigation, and serif headings for reviewing a session." },
] as const;

type DesignId = (typeof WORKSPACE_DESIGNS)[number]["id"];
const defaultDesign = WORKSPACE_DESIGNS[0].id;
export const DESIGN_STORAGE_KEY = "topsignal.workspaceDesign";
const changeEvent = "topsignal-workspace-design-changed";

function normalizeDesign(value: string | null | undefined): DesignId {
  return WORKSPACE_DESIGNS.find(design => design.id === value)?.id ?? defaultDesign;
}

function applyDesign(id: DesignId) {
  document.documentElement.dataset.workspaceDesign = id;
  window.dispatchEvent(new Event(changeEvent));
}

export function selectWorkspaceDesign(id: DesignId) {
  try {
    window.localStorage.setItem(DESIGN_STORAGE_KEY, id);
  } catch {
    // The design still applies for this session when storage is unavailable.
  }
  applyDesign(id);
}

export function initializeWorkspaceDesign() {
  function restore() {
    let stored = null;
    try {
      stored = window.localStorage.getItem(DESIGN_STORAGE_KEY);
    } catch {
      // A new session starts with the original workspace in this case.
    }
    applyDesign(normalizeDesign(stored));
  }
  restore();
  function onStorage(event: StorageEvent) {
    if ((event.key === DESIGN_STORAGE_KEY || event.key === null) && event.storageArea === window.localStorage) restore();
  }
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}

function subscribe(listener: () => void) {
  window.addEventListener(changeEvent, listener);
  return () => window.removeEventListener(changeEvent, listener);
}

export function useWorkspaceDesign() {
  return useSyncExternalStore(subscribe, () => normalizeDesign(document.documentElement.dataset.workspaceDesign), () => defaultDesign);
}
