import { createClient, type Session } from "@supabase/supabase-js";

const LOCAL_SUPABASE_URLS = new Set(["http://127.0.0.1:54321", "http://localhost:54321"]);
const SUPABASE_URL_RAW = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string | undefined;
const SUPABASE_URL = SUPABASE_URL_RAW?.trim().replace(/\/+$/, "");

export type SupabaseRuntimeMode = "disabled" | "local" | "cloud";
export const supabaseRuntimeMode: SupabaseRuntimeMode = !SUPABASE_URL
  ? "disabled"
  : LOCAL_SUPABASE_URLS.has(SUPABASE_URL)
    ? "local"
    : "cloud";
export const usesLocalSupabase = supabaseRuntimeMode === "local";

export const hasSupabaseConfig = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);

export const supabase = hasSupabaseConfig
  ? createClient(SUPABASE_URL!, SUPABASE_ANON_KEY!, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;

let currentAccessToken: string | null = null;
let currentEmail: string | null = null;

export async function bootstrapSupabaseSession(): Promise<Session | null> {
  if (!supabase) {
    currentAccessToken = null;
    currentEmail = null;
    return null;
  }

  const { data } = await supabase.auth.getSession();
  currentAccessToken = data.session?.access_token ?? null;
  currentEmail = data.session?.user?.email ?? null;
  return data.session ?? null;
}

export function subscribeSupabaseAuthChanges(onChange: (session: Session | null) => void): () => void {
  if (!supabase) {
    return () => {};
  }

  const { data } = supabase.auth.onAuthStateChange((_event, session) => {
    currentAccessToken = session?.access_token ?? null;
    currentEmail = session?.user?.email ?? null;
    onChange(session);
  });

  return () => {
    data.subscription.unsubscribe();
  };
}

export async function signInWithGoogle(): Promise<void> {
  if (!supabase) {
    throw new Error("Supabase is not configured");
  }

  const { error } = await supabase.auth.signInWithOAuth({
    provider: "google",
    options: {
      redirectTo: window.location.origin,
    },
  });
  if (error) {
    throw new Error(error.message);
  }
}

export async function signOutSupabase(): Promise<void> {
  if (!supabase) {
    return;
  }
  const { error } = await supabase.auth.signOut();
  if (error) {
    throw new Error(error.message);
  }
}

export async function getAccessToken(): Promise<string | null> {
  if (currentAccessToken) {
    return currentAccessToken;
  }
  if (!supabase) {
    return null;
  }

  const { data } = await supabase.auth.getSession();
  currentAccessToken = data.session?.access_token ?? null;
  currentEmail = data.session?.user?.email ?? null;
  return currentAccessToken;
}

export function getAccessTokenSync(): string | null {
  return currentAccessToken;
}

export function getCurrentUserEmailSync(): string | null {
  return currentEmail;
}
