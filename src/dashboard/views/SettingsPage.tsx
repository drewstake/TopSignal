import { useEffect, useState } from "react";
import {
  clearTopstepCreds,
  loadTopstepCreds,
  saveTopstepCreds,
} from "../../lib/storage";
import {
  clearSessionToken,
  hasSessionToken,
  saveSessionToken,
} from "../../lib/session";
import { loginWithApiKey } from "../../api/topstep";

export default function SettingsPage() {
  const [username, setUsername] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [gatewayUrl, setGatewayUrl] = useState("https://api.topstepx.com");
  const [showKey, setShowKey] = useState(false);

  const [status, setStatus] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);

  useEffect(() => {
    const c = loadTopstepCreds();
    if (c) {
      setUsername(c.username || "");
      setApiKey(c.apiKey || "");
      setGatewayUrl(c.gatewayUrl || "https://api.topstepx.com");
    }
    setConnected(hasSessionToken());
  }, []);

  function onSaveCreds() {
    setStatus(null);

    if (!username.trim()) {
      setStatus("Username is required.");
      return;
    }
    if (!apiKey.trim()) {
      setStatus("API key is required.");
      return;
    }

    saveTopstepCreds({
      username: username.trim(),
      apiKey: apiKey.trim(),
      gatewayUrl: gatewayUrl.trim() || undefined,
    });

    setStatus("Saved credentials locally on this device.");
  }

  async function onConnect() {
    setStatus(null);

    if (!username.trim()) {
      setStatus("Enter your username first.");
      return;
    }
    if (!apiKey.trim()) {
      setStatus("Enter your API key first.");
      return;
    }

    setConnecting(true);

    try {
      const data = await loginWithApiKey({
        userName: username.trim(),
        apiKey: apiKey.trim(),
      });

      if (!data.success || data.errorCode !== 0 || !data.token) {
        throw new Error(`Login failed (errorCode ${data.errorCode}).`);
      }

      saveSessionToken(data.token);
      setConnected(true);
      setStatus("Connected. Session token saved for this browser session.");
    } catch (e) {
      clearSessionToken();
      setConnected(false);
      const msg = e instanceof Error ? e.message : "Login failed.";
      setStatus(msg);
    } finally {
      setConnecting(false);
    }
  }

  function onDisconnect() {
    clearSessionToken();
    setConnected(false);
    setStatus("Disconnected. Session token cleared.");
  }

  function onClearAll() {
    clearTopstepCreds();
    clearSessionToken();
    setUsername("");
    setApiKey("");
    setGatewayUrl("https://api.topstepx.com");
    setConnected(false);
    setStatus("Cleared.");
  }

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-12">
      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5 lg:col-span-7">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-100">
              Topstep Settings
            </div>
            <div className="mt-1 text-sm text-zinc-400">
              Credentials save to localStorage on this device. Session token saves
              to sessionStorage.
            </div>
          </div>

          <div
            className={
              "rounded-full border px-3 py-1 text-xs " +
              (connected
                ? "border-emerald-700 bg-emerald-950/40 text-emerald-200"
                : "border-zinc-800 bg-zinc-950/40 text-zinc-300")
            }
          >
            {connected ? "Connected" : "Not connected"}
          </div>
        </div>

        <div className="mt-5 grid gap-4">
          <div>
            <div className="mb-1 text-xs text-zinc-400">Username</div>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="h-10 w-full rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 text-sm text-zinc-200 outline-none"
              placeholder="your Topstep username or email"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>

          <div>
            <div className="mb-1 text-xs text-zinc-400">API Key</div>
            <div className="flex gap-2">
              <input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                type={showKey ? "text" : "password"}
                className="h-10 w-full rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 text-sm text-zinc-200 outline-none"
                placeholder="paste your API key"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="h-10 rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 text-sm text-zinc-200"
              >
                {showKey ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs text-zinc-400">Gateway URL (optional)</div>
            <input
              value={gatewayUrl}
              onChange={(e) => setGatewayUrl(e.target.value)}
              className="h-10 w-full rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 text-sm text-zinc-200 outline-none"
              placeholder="https://api.topstepx.com"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={onSaveCreds}
              className="rounded-xl border border-zinc-600 bg-zinc-200 px-3 py-2 text-sm text-zinc-900"
            >
              Save
            </button>

            <button
              onClick={onConnect}
              disabled={connecting}
              className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200 disabled:opacity-50"
            >
              {connecting ? "Connecting..." : "Connect"}
            </button>

            <button
              onClick={onDisconnect}
              className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200"
            >
              Disconnect
            </button>

            <button
              onClick={onClearAll}
              className="rounded-xl border border-zinc-800 bg-zinc-950/40 px-3 py-2 text-sm text-zinc-200"
            >
              Clear all
            </button>
          </div>

          {status ? (
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-sm text-zinc-200">
              {status}
            </div>
          ) : null}

          <div className="rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 text-xs text-zinc-400">
            Tip: keep secrets out of Git. Add <span className="text-zinc-200">loginKey.json</span> and{" "}
            <span className="text-zinc-200">.env.local</span> to <span className="text-zinc-200">.gitignore</span>.
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-zinc-800 bg-zinc-900/40 p-5 lg:col-span-5">
        <div className="text-sm font-semibold text-zinc-200">
          How requests will work
        </div>
        <div className="mt-2 text-sm text-zinc-400">
          After you connect, call other endpoints with:
          <div className="mt-2 rounded-xl border border-zinc-800 bg-zinc-950/30 px-3 py-2 font-mono text-xs text-zinc-200">
            Authorization: Bearer &lt;token&gt;
          </div>
        </div>
      </div>
    </div>
  );
}
