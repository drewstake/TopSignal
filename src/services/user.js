// src/services/user.js
const DEV_BASE = "/ts-userapi";
const PROD_BASE = import.meta.env.VITE_USER_API_BASE || "https://userapi.topstepx.com";
const USER_API_BASE = import.meta.env.DEV ? DEV_BASE : PROD_BASE;

/** GET /User/me -> { id, username, ... } */
export async function getMe({ signal } = {}) {
  const res = await fetch(`${USER_API_BASE}/User/me`, {
    method: "GET",
    credentials: "include", // send cookies
    mode: "cors",
    signal,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`User/me failed (HTTP ${res.status}) ${text}`);
  }
  return res.json();
}
