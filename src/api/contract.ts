export type TopstepContract = {
  id: string;
  name: string;
  description: string;
  tickSize: number;
  tickValue: number;
  activeContract: boolean;
  symbolId: string;
};

export type ContractByIdResponse = {
  contract: TopstepContract | null;
  success: boolean;
  errorCode: number;
  errorMessage: string | null;
};

function getSessionToken(): string | null {
  try {
    const raw = localStorage.getItem("topsignal.topstep.sessionToken.v1");
    return raw || null;
  } catch {
    return null;
  }
}

export async function searchContractById(contractId: string): Promise<ContractByIdResponse> {
  const token = getSessionToken();
  const res = await fetch("/topstep/api/Contract/searchById", {
    method: "POST",
    headers: {
      accept: "application/json",
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ contractId }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Contract/searchById failed (${res.status}). ${text}`);
  }

  return res.json();
}
