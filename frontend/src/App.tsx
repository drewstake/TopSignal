import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { RouterProvider } from "react-router-dom";
import { router } from "./app/routes";
import { bootstrapSupabaseSession, hasSupabaseConfig, signInWithGoogle, subscribeSupabaseAuthChanges } from "./lib/supabase";

function formatGoogleSignInError(err: unknown): string {
  const fallback = "Failed to start Google sign-in";
  if (!(err instanceof Error)) {
    return fallback;
  }

  const normalized = err.message.toLowerCase();
  if (normalized.includes("unsupported provider") || normalized.includes("provider is not enabled")) {
    return "Google sign-in is disabled for this Supabase project. Enable Google under Supabase Dashboard > Authentication > Providers.";
  }
  return err.message;
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(hasSupabaseConfig);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!hasSupabaseConfig) {
      return;
    }

    let mounted = true;
    void bootstrapSupabaseSession()
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

    const unsubscribe = subscribeSupabaseAuthChanges((nextSession) => {
      if (!mounted) {
        return;
      }
      setSession(nextSession);
    });

    return () => {
      mounted = false;
      unsubscribe();
    };
  }, []);

  if (!hasSupabaseConfig) {
    return <RouterProvider router={router} />;
  }

  if (loading) {
    return <div className="min-h-screen bg-slate-950 p-8 text-sm text-slate-300">Loading authentication...</div>;
  }

  if (!session) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-950 p-6 text-slate-100">
        <div className="w-full max-w-md space-y-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-6">
          <h1 className="text-xl font-semibold">Sign in to TopSignal</h1>
          <p className="text-sm text-slate-300">Use your Supabase account to access your cloud-synced data.</p>
          {error ? <p className="text-sm text-rose-300">{error}</p> : null}
          <button
            type="button"
            className="w-full rounded-lg bg-slate-100 px-4 py-2 text-sm font-medium text-slate-900"
            onClick={() => {
              void signInWithGoogle().catch((err) => setError(formatGoogleSignInError(err)));
            }}
          >
            Continue with Google
          </button>
        </div>
      </div>
    );
  }

  return <RouterProvider router={router} />;
}
