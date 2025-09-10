import { createContext, useContext, useEffect, useRef, useState } from "react";
import { getToken, clearToken } from "../lib/storage";
import { loginWithKey } from "../services/auth";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(null);
  const [username, setUsername] = useState("");
  const [authed, setAuthed] = useState(false);
  const didInit = useRef(false);

  useEffect(() => {
    if (didInit.current) return;
    didInit.current = true;
    const t = getToken?.();
    if (t) {
      setTokenState(t);
      setAuthed(true);
    }
  }, []);

  async function login({ userName, apiKey }) {
    const data = await loginWithKey({ userName, apiKey }); // stores token for us
    setTokenState(data.token);
    setAuthed(true);
    setUsername(userName);
    return data;
  }

  function logout() {
    clearToken?.();
    setTokenState(null);
    setAuthed(false);
    setUsername("");
  }

  return (
    <AuthCtx.Provider value={{ token, authed, username, setUsername, login, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx); // eslint-disable-line react-refresh/only-export-components
