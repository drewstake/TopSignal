export type RouteLoadStatus = "checking" | "unreachable" | "missing-asset" | "reachable" | "unknown";

export function isModuleLoadFailure(message: string): boolean {
  return /failed to fetch dynamically imported module|error loading dynamically imported module|importing a module script failed|loading chunk .+ failed|unable to preload css/i.test(message);
}

export async function checkRouteLoad(message: string, signal: AbortSignal): Promise<RouteLoadStatus> {
  try {
    const response = await fetch(new URL("/index.html", window.location.origin), {
      method: "HEAD", cache: "no-store", signal, credentials: "omit", redirect: "error",
    });
    if (!response.ok) return "unreachable";
    if (!response.headers.get("content-type")?.includes("text/html")) return "unknown";
    const match = message.match(/https?:\/\/[^\s"'<>]+/);
    if (match) {
      const asset = new URL(match[0]);
      // Error text is untrusted. Never probe an external URL or authenticated API.
      if (asset.origin === window.location.origin && asset.pathname.startsWith("/assets/") && /\.(js|css)$/.test(asset.pathname)) {
        asset.search = "";
        asset.hash = "";
        const result = await fetch(asset, { method: "HEAD", cache: "no-store", signal, credentials: "omit", redirect: "error" });
        if (result.status === 404 || result.status === 410) return "missing-asset";
        if (!result.ok) return "unknown";
      }
    }
    return "reachable";
  } catch {
    return "unreachable";
  }
}

export const routeLoadMessages: Record<RouteLoadStatus, string> = {
  checking: "Checking the dashboard connection…",
  unreachable: "The dashboard server did not respond successfully. Check this PC's network, Tailscale and TopSignal Host Connection task, then check the host PC. Host and trading status are unknown from this screen.",
  "missing-asset": "The dashboard is reachable, but the page file requested by this tab is missing. This can happen after a deployment. Reload to get the current version; unsaved page changes may be lost.",
  reachable: "The dashboard is reachable now. Reload to retry loading this screen; unsaved page changes may be lost. This check does not verify backend or trading health.",
  unknown: "The connection check was inconclusive. Reload to retry, or inspect the connection task and host logs. Backend and trading status are unknown from this screen.",
};
