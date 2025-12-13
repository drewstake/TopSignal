import { loadSessionToken } from "../lib/session";

async function parseJsonOrThrow(res: Response) {
  const text = await res.text();

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text || "No body"}`);
  }

  if (!text) return null;

  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Expected JSON but got: ${text}`);
  }
}

export async function topstepPost<T>(path: string, body: unknown = {}): Promise<T> {
  const token = loadSessionToken();
  if (!token) throw new Error("No session token. Connect in Settings first.");

  const res = await fetch(`/topstep${path}`, {
    method: "POST",
    headers: {
      accept: "text/plain",
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  return (await parseJsonOrThrow(res)) as T;
}
