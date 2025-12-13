export type LoginKeyResponse = {
  token: string | null;
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

export async function loginWithApiKey(args: { userName: string; apiKey: string }) {
  const res = await fetch("/topstep/api/Auth/loginKey", {
    method: "POST",
    headers: {
      accept: "text/plain",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(args),
  });

  const text = await res.text();

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${text || "No body"}`);
  }

  return JSON.parse(text) as LoginKeyResponse;
}
