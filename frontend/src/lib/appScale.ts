import { useSyncExternalStore } from "react";

export const APP_ZOOM_STORAGE_KEY = "topsignal.zoom";
export const APP_SCALE_CHANGED_EVENT = "topsignal-scale-changed";
export const DEFAULT_APP_ZOOM = 80;
export const APP_ZOOM_OPTIONS = Array.from({ length: 19 }, (_, index) => 60 + index * 5);

function normalizeZoom(value: number): number {
  return APP_ZOOM_OPTIONS.includes(value) ? value : DEFAULT_APP_ZOOM;
}

export function readStoredAppZoom(): number {
  try {
    return normalizeZoom(Number(window.localStorage.getItem(APP_ZOOM_STORAGE_KEY)));
  } catch {
    return DEFAULT_APP_ZOOM;
  }
}

function applyAppZoom(value: number): void {
  document.documentElement.style.setProperty("--app-scale", String(value / 100));
  window.dispatchEvent(new Event(APP_SCALE_CHANGED_EVENT));
}

export function selectAppZoom(value: number): void {
  const zoom = normalizeZoom(value);
  try {
    window.localStorage.setItem(APP_ZOOM_STORAGE_KEY, String(zoom));
  } catch {
    // Keep the current session adjustable when browser storage is unavailable.
  }
  applyAppZoom(zoom);
}

export function initializeAppScale(): () => void {
  applyAppZoom(readStoredAppZoom());
  const handleStorage = (event: StorageEvent) => {
    if ((event.key === APP_ZOOM_STORAGE_KEY || event.key === null)
      && event.storageArea === window.localStorage) {
      applyAppZoom(readStoredAppZoom());
    }
  };
  window.addEventListener("storage", handleStorage);
  return () => window.removeEventListener("storage", handleStorage);
}

function subscribe(listener: () => void): () => void {
  window.addEventListener(APP_SCALE_CHANGED_EVENT, listener);
  return () => window.removeEventListener(APP_SCALE_CHANGED_EVENT, listener);
}

function getCurrentZoom(): number {
  return normalizeZoom(Math.round(Number(document.documentElement.style.getPropertyValue("--app-scale")) * 100));
}

export function useAppZoom(): number {
  return useSyncExternalStore(subscribe, getCurrentZoom, () => DEFAULT_APP_ZOOM);
}

/** Convert viewport coordinates to the app's CSS layout coordinates. */
export function getAppScale(): number {
  if (typeof document === "undefined") return 1;
  const scale = Number.parseFloat(getComputedStyle(document.documentElement).zoom);
  return Number.isFinite(scale) && scale > 0 ? scale : 1;
}
