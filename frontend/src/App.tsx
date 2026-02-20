import { useEffect, useState } from "react";

type Trade = {
  id: number;
  symbol: string;
  side: string;
  pnl?: number | null;
  opened_at: string;
};

export default function App() {
  const [trades, setTrades] = useState<Trade[]>([]);

  useEffect(() => {
    fetch("http://127.0.0.1:8000/trades?limit=10")
      .then((r) => r.json())
      .then(setTrades)
      .catch(console.error);
  }, []);

  return (
    <div style={{ padding: 16 }}>
      <h1>TopSignal</h1>
      <h2>Recent Trades</h2>
      <ul>
        {trades.map((t) => (
          <li key={t.id}>
            {t.symbol} {t.side} | PnL: {t.pnl ?? "N/A"} | {t.opened_at}
          </li>
        ))}
      </ul>
    </div>
  );
}