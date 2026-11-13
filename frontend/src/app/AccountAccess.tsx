import { Fragment, useEffect, useState, type ReactNode } from "react";
import type { Session } from "../lib/localAuth";
import { LocalSignIn } from "./LocalSignIn";
import { localAuthConfigured } from "../lib/localAuth";
import { isDemoModeEnabled } from "../lib/demoMode";
import { bootstrapSession, subscribeAuthChanges } from "../lib/auth";
const hasAuthConfig = localAuthConfigured;


export function AccountAccess({ children }: { children: ReactNode }) {
  const [demoModeAtStartup] = useState(isDemoModeEnabled);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(hasAuthConfig && !demoModeAtStartup);
  const [error, setError] = useState<string | null>(null);
  const canOpenOfflineWorkspace = import.meta.env.DEV &&
    ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);

  useEffect(() => {
    if (!hasAuthConfig || demoModeAtStartup) {
      return;
    }

    let mounted = true;
    // Show retry access if the host is slow to verify a saved session.
    const loadingTimeout = window.setTimeout(() => { if (mounted) setLoading(false); }, 4000);
    void bootstrapSession()
      .then((nextSession) => {
        if (!mounted) {
          return;
        }
        setSession(nextSession);
        setLoading(false);
      })
      .catch((err) => {
        if (!mounted) {
          return;
        }
        setError(err instanceof Error ? err.message : "Failed to initialize authentication");
        setLoading(false);
      });

    const unsubscribe = subscribeAuthChanges((nextSession) => {
      if (!mounted) {
        return;
      }
      setSession(nextSession);
      setLoading(false);
    });

    return () => {
      mounted = false;
      window.clearTimeout(loadingTimeout);
      unsubscribe();
    };
  }, [demoModeAtStartup]);

  if (!hasAuthConfig || demoModeAtStartup) {
    return <>
      {import.meta.env.DEV && import.meta.env.VITE_OFFLINE_MODE === "true" ? (
        <div role="status" className="border-b border-app-border bg-app-surface px-4 py-2 text-center text-sm text-app-text">
          {import.meta.env.VITE_LOCAL_PROJECTX === "true"
            ? import.meta.env.VITE_LOCAL_LIVE_ORDERS === "true"
              ? "Local workspace · Topstep API enabled · Live order routing enabled · Saved on this computer"
              : "Local workspace · Topstep API enabled · Dry-run bot enabled · Saved on this computer · Live orders disabled"
            : "Offline workspace · Saved on this computer · Broker connections are unavailable"}
        </div>
      ) : null}
      {children}
    </>;
  }

  if (loading) {
    return <div className="min-h-screen bg-app-bg p-8 text-sm text-app-muted">Loading authentication...</div>;
  }

  if (!session) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-app-bg p-6 text-app-text">
        <div className="w-full max-w-md space-y-4 rounded-2xl border border-app-border bg-app-surface/70 p-6">
          <h1 className="text-xl font-semibold">Sign in to TopSignal</h1>
          <p className="text-sm text-app-muted">Access your connected worker and trading workspace.</p>
          {error ? <p className="text-sm text-app-negative">{error}</p> : null}
          <LocalSignIn />
          {canOpenOfflineWorkspace ? (
            <div className="space-y-2">
              <a
                href="http://127.0.0.1:5174"
                className="block w-full rounded-lg border border-app-border px-4 py-2 text-center text-sm font-medium transition hover:bg-app-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-app-accent/35"
              >
                Continue offline
              </a>
              <p className="text-xs leading-5 text-app-muted">
                Open your local workspace through the local service. Data is saved on this computer and does not automatically sync to the cloud.
              </p>
              <details className="text-xs leading-5 text-app-muted">
                <summary className="cursor-pointer">If the offline workspace isn’t running</summary>
                <p className="mt-2">For local storage with Topstep account refreshes and dry-run automation, run <code>npm run dev:local</code> from the TopSignal folder. Live orders stay disabled.</p>
                <p className="mt-2">To work without broker connections, use <code>npm run dev:offline</code> instead. Both modes open with Continue offline and save to the same local workspace.</p>
              </details>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  return <Fragment key={session.user.id}>{children}</Fragment>;
}
