import { useEffect, useMemo, useState } from "react";
import { connectMarket } from "../services/market";
import { useAuth } from "../context/AuthContext";
import { flushSync } from "react-dom";

// tiny mock fallback so this hook is self-sufficient in dev
function mockSub(onTick) {
  let price = 19000 + Math.random() * 200;
  onTick(Number(price.toFixed(2)));
  const id = setInterval(() => {
    const drift = (Math.random() - 0.5) * 10;
    price = Math.max(1000, price + drift);
    onTick(Number(price.toFixed(2)));
  }, 2000);
  return () => clearInterval(id);
}

export function useMarketPrice(contractId) {
  const { authed } = useAuth();
  const [price, setPrice] = useState(null);
  const [basePrice, setBasePrice] = useState(null);
  const [feedSource, setFeedSource] = useState(""); // "SignalR: ..." | "Mock"
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!authed) return;
    let stop = () => {};
    let cancelled = false;

    function extractPrice(d) {
      const vals = [
        d?.lastPrice,
        d?.LastPrice,
        d?.lastTradedPrice,
        d?.LastTradedPrice,
        d?.tradePrice,
        d?.TradePrice,
        d?.price,
        d?.Price,
        d?.last?.price,
        d?.Last?.Price,
        d?.lastTrade?.price,
        d?.LastTrade?.Price,
      ];
      for (const v of vals) {
        const n = Number(v);
        if (v != null && !Number.isNaN(n)) return n;
      }
      const bid = Number(d?.bestBid ?? d?.BestBid);
      const ask = Number(d?.bestAsk ?? d?.BestAsk);
      if (!Number.isNaN(bid) && !Number.isNaN(ask)) return (bid + ask) / 2;
      return null;
    }

    (async () => {
      if (!contractId) {
        setFeedSource("Mock");
        stop = mockSub((p) => {
          if (cancelled) return;
          setPrice(p);
          setBasePrice((bp) => (bp == null ? p : bp));
        });
        return;
      }
      try {
        const { stop: s } = await connectMarket({
          contractId,
          onQuote: (d) => {
            const p = extractPrice(d);
            if (p != null) {
              setPrice(p);
              setBasePrice((bp) => (bp == null ? p : bp));
            }
          },
          onTrade: (d) => {
            const p = extractPrice(d);
            if (p != null) {
              flushSync(() => setPrice(p));
              setBasePrice((bp) => (bp == null ? p : bp));
            }
          },
        });
        if (cancelled) { s(); return; }
        stop = s;
        setFeedSource(`SignalR: ${contractId}`);
        setError(null);
      } catch (e) {
        setError(e);
        setFeedSource("Mock");
        stop = mockSub((p) => {
          if (cancelled) return;
          setPrice(p);
          setBasePrice((bp) => (bp == null ? p : bp));
        });
      }
    })();

      return () => {
        cancelled = true;
        try { stop?.(); } catch { /* ignore */ }
      };
  }, [authed, contractId]);

  const delta = useMemo(() => (price != null && basePrice != null ? price - basePrice : 0), [price, basePrice]);
  const deltaPct = useMemo(() => (basePrice ? (delta / basePrice) * 100 : 0), [delta, basePrice]);

  return { price, basePrice, delta, deltaPct, feedSource, error };
}
