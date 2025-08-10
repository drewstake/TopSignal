import { useEffect, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { searchAccounts } from "../services/accounts";

export function useAccounts(onlyActive = true) {
  const { authed } = useAuth();
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    setLoading(true);
    searchAccounts({ onlyActiveAccounts: onlyActive })
      .then((res) => { if (!cancelled) setAccounts(res); })
      .catch((e) => { if (!cancelled) setError(e); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [authed, onlyActive]);

  return { accounts, loading, error };
}
