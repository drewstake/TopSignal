import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./supabase", () => ({
  getAccessToken: vi.fn(async () => null),
}));

import { accountsApi } from "./api";

describe("accountsApi", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            account_id: 7301,
            entry_date: "2026-05-12",
            journal_entry_id: 42,
            created: true,
            updated: false,
            skipped: false,
            skip_reason: null,
            source_trade_count: 1,
            recap_markdown: "# Daily Recap",
            generated_at: "2026-05-13T12:00:00Z",
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        ),
      ),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("calls the ProjectX AI recap route", async () => {
    await accountsApi.generateAIJournalRecap(7301, {
      entry_date: "2026-05-12",
      mode: "append_or_create",
      include_existing_notes: true,
    });

    const fetchMock = vi.mocked(fetch);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/projectx/accounts/7301/journal/ai-recap");
    expect(init?.method).toBe("POST");
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(JSON.parse(String(init?.body))).toEqual({
      entry_date: "2026-05-12",
      mode: "append_or_create",
      include_existing_notes: true,
    });
  });
});
