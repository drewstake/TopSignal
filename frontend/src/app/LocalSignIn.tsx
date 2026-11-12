import { useEffect, useState } from "react";
import { canAutoSignInOnThisDevice, continueOnThisDevice, localAuthStatus, signInLocally, type LocalAuthStatus } from "../lib/localAuth";

export function LocalSignIn() {
  const [status, setStatus] = useState<LocalAuthStatus | null>(null);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    let active = true;
    void localAuthStatus().then(async (next) => {
      if (!active) return;
      setStatus(next); setError("");
      if (next.enabled && next.mode === "trusted_device" && canAutoSignInOnThisDevice()) {
        setBusy(true);
        await continueOnThisDevice();
      }
    })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : "Local sign-in unavailable"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [retry]);
  if (status?.enabled === false) return null;
  if (!status) return <div className="text-sm text-app-muted">
    {error ? <><p role="alert">{error}</p><button type="button" className="mt-2 underline" onClick={() => setRetry((v) => v + 1)}>Retry local sign-in</button></> : "Connecting to local sign-in…"}
  </div>;
  if (status.mode === "trusted_device") return <div className="space-y-4">
    <p className="text-sm leading-5 text-app-muted">Use your existing accounts and live trading features through this computer’s worker connection. No password or setup code needed.</p>
    {error && <p role="alert" className="text-sm text-app-negative">{error}</p>}
    <button type="button" disabled={busy} className="w-full rounded-lg bg-app-accent px-4 py-2 text-sm font-medium text-app-accent-contrast disabled:opacity-50" onClick={() => {
      setBusy(true); setError("");
      void continueOnThisDevice().catch((err: unknown) => setError(err instanceof Error ? err.message : "Could not connect"))
        .finally(() => setBusy(false));
    }}>{busy ? "Connecting…" : "Continue on this device"}</button>
    <p className="text-xs leading-5 text-app-muted">Your session survives refreshes for up to 12 hours. Signing out does not stop TopBot.</p>
  </div>;
  const setup = status.needs_setup;
  const inputClass = "mt-1 w-full rounded-lg border border-app-border bg-app-bg px-3 py-2 text-app-text focus:outline-none focus:ring-2 focus:ring-app-accent/40";
  return <form className="space-y-4" onSubmit={(event) => {
    event.preventDefault();
    if (setup && password !== confirmation) { setError("Passwords do not match."); return; }
    setBusy(true); setError("");
    void signInLocally(password, setup ? code.trim() : undefined)
      .then(() => { setPassword(""); setConfirmation(""); setCode(""); })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : "Local sign-in failed"))
      .finally(() => setBusy(false));
  }}>
    <div>
      <h2 className="font-medium">{setup ? "Set up local sign-in" : "Local sign-in"}</h2>
      <p className="mt-1 text-sm leading-5 text-app-muted">{setup ? "Use your one-time setup code and choose a password." : "Enter your password for this connected worker."} Your accounts and live trading features stay connected.</p>
    </div>
    {setup && <label className="block text-sm">Setup code<input className={inputClass} type="password" autoComplete="off" required maxLength={128} value={code} onChange={(e) => setCode(e.target.value)} /></label>}
    <label className="block text-sm">Password<input className={inputClass} type="password" autoComplete={setup ? "new-password" : "current-password"} required minLength={setup ? 14 : 1} maxLength={128} value={password} onChange={(e) => setPassword(e.target.value)} /></label>
    {setup && <><p className="text-xs text-app-muted">Use at least 14 characters. A long, unique passphrase works well.</p><label className="block text-sm">Confirm password<input className={inputClass} type="password" autoComplete="new-password" required minLength={14} maxLength={128} value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label></>}
    {error && <p role="alert" className="text-sm text-app-negative">{error}</p>}
    <button type="submit" disabled={busy} className="w-full rounded-lg bg-app-accent px-4 py-2 text-sm font-medium text-app-accent-contrast disabled:opacity-50">{busy ? "Signing in…" : setup ? "Create password and sign in" : "Sign in locally"}</button>
    <p className="text-xs leading-5 text-app-muted">This tab stays signed in for up to 12 hours, including page refreshes. Signing out does not stop TopBot.</p>
  </form>;
}
