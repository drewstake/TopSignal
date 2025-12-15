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
import { useTheme } from "../../lib/theme";

export default function SettingsPage() {
  const [username, setUsername] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [gatewayUrl, setGatewayUrl] = useState("https://api.topstepx.com");
  const [showKey, setShowKey] = useState(false);

  const [status, setStatus] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const { theme, setTheme } = useTheme();

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
      <div className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40 lg:col-span-7">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-lg font-semibold text-zinc-900 dark:text-zinc-900 dark:text-zinc-100">
              Topstep Settings
            </div>
            <div className="mt-1 text-sm text-zinc-600 dark:text-zinc-600 dark:text-zinc-400">
              Credentials save to localStorage on this device. Session token saves
              to sessionStorage.
            </div>
          </div>

          <div
            className={
              "rounded-full border px-3 py-1 text-xs " +
              (connected
                ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-200"
                : "border-zinc-200 bg-zinc-50 text-zinc-700 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-700 dark:text-zinc-300")
            }
          >
            {connected ? "Connected" : "Not connected"}
          </div>
        </div>

        <div className="mt-5 grid gap-4">
          <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/30 dark:text-zinc-800 dark:text-zinc-200">
            <div>
              <div className="text-xs text-zinc-600 dark:text-zinc-600 dark:text-zinc-400">Appearance</div>
              <div className="font-medium text-zinc-900 dark:text-zinc-900 dark:text-zinc-100">Theme</div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setTheme("light")}
                className={
                  "rounded-lg border px-3 py-1 text-sm " +
                  (theme === "light"
                    ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-200 dark:bg-zinc-200 dark:text-zinc-900"
                    : "border-zinc-200 bg-white text-zinc-800 hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-800 dark:text-zinc-200")
                }
              >
                Light
              </button>
              <button
                type="button"
                onClick={() => setTheme("dark")}
                className={
                  "rounded-lg border px-3 py-1 text-sm " +
                  (theme === "dark"
                    ? "border-zinc-900 bg-zinc-900 text-white dark:border-zinc-200 dark:bg-zinc-200 dark:text-zinc-900"
                    : "border-zinc-200 bg-white text-zinc-800 hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-800 dark:text-zinc-200")
                }
              >
                Dark
              </button>
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs text-zinc-600 dark:text-zinc-600 dark:text-zinc-400">Username</div>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="h-10 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm text-zinc-900 outline-none dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200"
              placeholder="your Topstep username or email"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>

          <div>
            <div className="mb-1 text-xs text-zinc-600 dark:text-zinc-600 dark:text-zinc-400">API Key</div>
            <div className="flex gap-2">
              <input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                type={showKey ? "text" : "password"}
                className="h-10 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm text-zinc-900 outline-none dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200"
                placeholder="paste your API key"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="h-10 rounded-xl border border-zinc-300 bg-white px-3 text-sm text-zinc-800 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200"
              >
                {showKey ? "Hide" : "Show"}
              </button>
            </div>
          </div>

          <div>
            <div className="mb-1 text-xs text-zinc-600 dark:text-zinc-600 dark:text-zinc-400">Gateway URL (optional)</div>
            <input
              value={gatewayUrl}
              onChange={(e) => setGatewayUrl(e.target.value)}
              className="h-10 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm text-zinc-900 outline-none dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200"
              placeholder="https://api.topstepx.com"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
            />
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={onSaveCreds}
              className="rounded-xl border border-zinc-900 bg-zinc-900 px-3 py-2 text-sm text-white dark:border-zinc-200 dark:bg-zinc-200 dark:text-zinc-900"
            >
              Save
            </button>

            <button
              onClick={onConnect}
              disabled={connecting}
              className="rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200 disabled:opacity-50"
            >
              {connecting ? "Connecting..." : "Connect"}
            </button>

            <button
              onClick={onDisconnect}
              className="rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200"
            >
              Disconnect
            </button>

            <button
              onClick={onClearAll}
              className="rounded-xl border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-800 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/40 dark:text-zinc-800 dark:text-zinc-200"
            >
              Clear all
            </button>
          </div>

          {status ? (
            <div className="rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 text-sm text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/30 dark:text-zinc-800 dark:text-zinc-200">
              {status}
            </div>
          ) : null}

          <div className="rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-950/30 dark:text-zinc-600 dark:text-zinc-400">
            Tip: keep secrets out of Git. Add <span className="text-zinc-800 dark:text-zinc-800 dark:text-zinc-200">loginKey.json</span> and{" "}
            <span className="text-zinc-800 dark:text-zinc-800 dark:text-zinc-200">.env.local</span> to <span className="text-zinc-800 dark:text-zinc-800 dark:text-zinc-200">.gitignore</span>.
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-zinc-200 bg-white p-5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900/40 lg:col-span-5">
        <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-800 dark:text-zinc-200">
          How requests will work
        </div>
        <div className="mt-2 text-sm text-zinc-700 dark:text-zinc-600 dark:text-zinc-400">
          After you connect, call other endpoints with:
          <div className="mt-2 rounded-xl border border-zinc-200 bg-zinc-50 px-3 py-2 font-mono text-xs text-zinc-800 dark:border-zinc-800 dark:bg-zinc-950/30 dark:text-zinc-800 dark:text-zinc-200">
            Authorization: Bearer &lt;token&gt;
          </div>
        </div>
      </div>
    </div>
  );
}
