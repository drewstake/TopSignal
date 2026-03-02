import { describe, expect, it } from "vitest";

import { sortAccountsForSelection } from "./accountOrdering";

describe("sortAccountsForSelection", () => {
  it("orders combine accounts before regular and practice accounts", () => {
    const sorted = sortAccountsForSelection([
      { id: 7003, name: "PA-7003", is_main: false },
      { id: 7002, name: "Account 7002", is_main: false },
      { id: 7001, name: "100KTC-7001", is_main: false },
      { id: 7004, name: "50KTC-7004", is_main: false },
      { id: 7005, name: "Practice 7005", is_main: false },
    ]);

    expect(sorted.map((account) => account.id)).toEqual([7004, 7001, 7002, 7003, 7005]);
  });

  it("keeps main account first within the same account group", () => {
    const sorted = sortAccountsForSelection([
      { id: 8002, name: "100KTC-8002", is_main: false },
      { id: 8001, name: "100KTC-8001", is_main: true },
    ]);

    expect(sorted.map((account) => account.id)).toEqual([8001, 8002]);
  });
});
