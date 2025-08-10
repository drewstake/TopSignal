import { useState } from "react";
import { LogIn, Loader2 } from "lucide-react";
import Card from "../../components/ui/Card";
import Button from "../../components/ui/Button";
import Label from "../../components/ui/Label";
import { useAuth } from "../../context/AuthContext";

function AuthScreen() {
  const { login, setUsername } = useAuth();
  const [userName, setUserName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [loading, setLoading] = useState(false);

  async function onSubmit(e) {
    e.preventDefault();
    setLoading(true);
    try {
      await login({ userName: userName.trim(), apiKey: apiKey.trim() });
      setUsername(userName.trim());
    } catch (err) {
      alert(err?.message || "Auth failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen w-full grid place-items-center text-zinc-100 bg-[radial-gradient(1200px_800px_at_20%_-10%,rgba(99,102,241,0.25),transparent),radial-gradient(1200px_800px_at_100%_10%,rgba(236,72,153,0.2),transparent)] bg-zinc-950">
      <Card className="w-[480px] p-8 relative overflow-hidden">
        <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-gradient-to-br from-indigo-500/30 to-fuchsia-600/30 blur-2xl" />
        <div className="flex items-center gap-3 mb-6">
          <div className="h-9 w-9 rounded-xl bg-gradient-to-br from-indigo-500 to-fuchsia-600 shadow-lg" />
          <h2 className="text-lg font-semibold tracking-tight">TopSignal • Sign In</h2>
        </div>

        <form onSubmit={onSubmit} className="space-y-5">
          <div>
            <Label>Username</Label>
            <input
              value={userName}
              onChange={(e) => setUserName(e.target.value)}
              placeholder="e.g. andrew.nguyen"
              className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            />
          </div>

          <div>
            <Label>API Key</Label>
            <input
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="paste your key"
              className="w-full rounded-xl bg-zinc-900/60 border border-white/10 px-4 py-3 text-zinc-100 focus:outline-none focus:ring-2 focus:ring-indigo-500/60"
            />
          </div>

          <Button className="w-full group" disabled={loading}>
            {loading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Signing in…
              </>
            ) : (
              <>
                <LogIn className="h-4 w-4" /> Login
              </>
            )}
          </Button>

          <p className="text-xs text-zinc-400">
            Uses JWT; market data via SignalR (<code>VITE_NQ_CONTRACT_ID</code>).
          </p>
        </form>
      </Card>
    </div>
  );
}

// Export BOTH default and named, so imports work either way
export default AuthScreen;
export { AuthScreen };
