import { describe, expect, it } from "vitest";

import { ApiError } from "../../lib/api";
import { getVersionConflictServerEntry } from "./journalConflict";

const serverEntry = {
  id: 11,
  account_id: 13001,
  entry_date: "2026-02-21",
  title: "Server title",
  mood: "Neutral",
  tags: ["nq"],
  body: "Server body",
  version: 4,
  stats_source: null,
  stats_json: null,
  stats_pulled_at: null,
  is_archived: false,
  created_at: "2026-02-21T00:00:00.000Z",
  updated_at: "2026-02-21T00:01:00.000Z",
} as const;

describe("getVersionConflictServerEntry", () => {
  it("extracts server payload from a 409 version_conflict ApiError", () => {
    const error = new ApiError(
      "version_conflict",
      409,
      {
        detail: "version_conflict",
        server: serverEntry,
      },
      "version_conflict",
    );

    const parsed = getVersionConflictServerEntry(error);

    expect(parsed).toEqual(serverEntry);
  });

  it("returns null for non-conflict errors", () => {
    const error = new ApiError("bad request", 400, { detail: "bad_request" }, "bad_request");

    const parsed = getVersionConflictServerEntry(error);

    expect(parsed).toBeNull();
  });
});
