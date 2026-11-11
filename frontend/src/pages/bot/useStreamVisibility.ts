import { useEffect, useState } from "react";
import { usePageVisibility } from "./usePageVisibility";

/**
 * How long live streams stay open after the page is hidden. Windows reports a
 * fully covered browser window as hidden, so switching to a trading platform
 * would otherwise drop and renegotiate every feed on each glance away.
 */
export const STREAM_HIDDEN_GRACE_MS = 2 * 60_000;

/**
 * True while the page is visible or has been hidden for less than the grace.
 * Streams stay subscribed through brief tab switches and resume seamlessly;
 * polling should keep using {@link usePageVisibility} and pause immediately.
 */
export function useStreamVisibility(graceMs = STREAM_HIDDEN_GRACE_MS): boolean {
  const visible = usePageVisibility();
  // A page that starts hidden never opened streams, so it has no grace.
  const [graceExpired, setGraceExpired] = useState(() => !visible);
  if (visible && graceExpired) {
    // Showing the page re-enables streams in the same render.
    setGraceExpired(false);
  }
  useEffect(() => {
    if (visible) return undefined;
    const timer = setTimeout(() => setGraceExpired(true), graceMs);
    return () => clearTimeout(timer);
  }, [graceMs, visible]);
  return visible || !graceExpired;
}
