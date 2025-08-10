import { useEffect, useRef, useState } from "react";
import { searchOpenPositions, closeContract, partialCloseContract } from "../services/positions";

export function usePositions({ accountId, pollMs = 5000 } = {}) {
  const [positions, setPositions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const t = useRef(null);

  async function refresh() {
    if (!accountId) return;
    setLoading(true);
    try {
      const list = await searchOpenPositions({ accountId: Number(accountId) });
      setPositions(list);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }

  async function close(contractId) {
    await closeContract({ accountId: Number(accountId), contractId });
    setTimeout(refresh, 200);
  }

  async function partialClose(contractId, size) {
    await partialCloseContract({ accountId: Number(accountId), contractId, size });
    setTimeout(refresh, 200);
  }

  useEffect(() => {
    if (!accountId) return;
    refresh();
    if (pollMs > 0) {
      t.current = setInterval(refresh, pollMs);
      return () => clearInterval(t.current);
    }
  }, [accountId, pollMs]); // eslint-disable-line react-hooks/exhaustive-deps

  return { positions, loading, error, refresh, close, partialClose };
}
