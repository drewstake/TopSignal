import { useEffect, useRef } from "react";
import Card from "../../components/ui/Card";
import { timeStamp } from "../../lib/format";

export default function TradingLog({ logs, botRunning }) {
  const logRef = useRef(null);
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  return (
    <Card className="p-6 h-[620px] flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold tracking-wide">Trading Log</h3>
        <span className="text-xs text-zinc-400">{logs.length} events</span>
      </div>
      <div ref={logRef} className="flex-1 overflow-auto pr-1">
        <ul className="space-y-2">
          {logs.length === 0 && <li className="text-zinc-400 text-sm">No events yet. Start the bot to stream logs.</li>}
          {logs.map((l) => (
            <li key={l.id} className="text-sm">
              <span className="text-zinc-400 tabular-nums">[{timeStamp(l.t)}]</span>{" "}
              <span className="text-zinc-100">{l.msg}</span>
            </li>
          ))}
        </ul>
      </div>
      <div className="pt-4 border-t border-white/10 flex items-center gap-2 text-xs text-zinc-400">
        <div className={`h-2 w-2 rounded-full ${botRunning ? "bg-emerald-400 animate-pulse" : "bg-zinc-500"}`} />
        <span>{botRunning ? "Streaming mock events" : "Idle"}</span>
      </div>
    </Card>
  );
}
