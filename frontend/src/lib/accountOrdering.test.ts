import { describe, expect, it } from "vitest";

import { sortAccountsForSelection } from "./accountOrdering";

describe("sortAccountsForSelection", () => {
  it("orders express accounts before combine, regular, and practice accounts", () => {
    const sorted = sortAccountsForSelection([
      { id: 7003, name: "PA-7003", is_main: false },
      { id: 7002, name: "Account 7002", is_main: false },
      { id: 7001, name: "100KTC-7001", is_main: false },
      { id: 7004, name: "50KTC-7004", is_main: false },
      { id: 7005, name: "Practice 7005", is_main: false },
      { id: 7006, name: "EXPRESS-V2-DLL-192577-7006", is_main: false },
      { id: 7007, name: "150KTC-7007", is_main: false },
    ]);

    expect(sorted.map((account) => account.id)).toEqual([7006, 7004, 7001, 7007, 7002, 7003, 7005]);
  });

  it("keeps main account first within the same account group", () => {
    const sorted = sortAccountsForSelection([
      { id: 8002, name: "100KTC-8002", is_main: false },
      { id: 8001, name: "100KTC-8001", is_main: true },
    ]);

    expect(sorted.map((account) => account.id)).toEqual([8001, 8002]);
  });

  it("keeps the main express account first among express accounts", () => {
    const sorted = sortAccountsForSelection([
      { id: 8101, name: "EXPRESS-V2-DLL-192577-50519642", is_main: false },
      { id: 8102, name: "150KTC-V2-DLL-192577-16577193", is_main: false },
      { id: 8103, name: "EXPRESS-V2-DLL-192577-16782575", is_main: true },
      { id: 8104, name: "50KTC-V2-DLL-192577-11530403", is_main: false },
    ]);

    expect(sorted.map((account) => account.id)).toEqual([8103, 8101, 8104, 8102]);
  });

  it("uses the provider name to preserve combine grouping after local renames", () => {
    const sorted = sortAccountsForSelection([
      { id: 9002, name: "Desk B", provider_name: "Account 9002", is_main: false },
      { id: 9001, name: "Primary Eval", provider_name: "50KTC-9001", is_main: false },
    ]);

    expect(sorted.map((account) => account.id)).toEqual([9001, 9002]);
  });
});
