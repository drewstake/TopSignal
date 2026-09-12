import { useSyncExternalStore } from "react";

const subscribe = (onChange: () => void) => {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
};
const isVisible = () => !document.hidden;

/** Display subscriptions pause independently of the backend bot worker. */
export function usePageVisibility(): boolean {
  return useSyncExternalStore(subscribe, isVisible, () => true);
}
