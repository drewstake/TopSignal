import { describe, expect, it } from "vitest";
import { buildTopstepLabsExpense } from "./topstepLabs";

const purchase = {
  dropId: "001" as const,
  quantity: 5,
  unitFeeCents: 7500,
  expenseDate: "2026-10-26",
  sourceId: "test-purchase",
};

describe("Labs purchase validation", () => {
  it.each([0, -1, 1.5, 6, NaN, Infinity])("rejects invalid quantity %s before saving", (quantity) => {
    expect(() => buildTopstepLabsExpense({ ...purchase, quantity })).toThrow("Quantity must be a whole number from 1 to 5.");
  });

  it.each([0, -1, 0.5, NaN, Infinity, 429_496_730])("rejects invalid unit fee %s or overflowing totals", (unitFeeCents) => {
    expect(() => buildTopstepLabsExpense({ ...purchase, unitFeeCents })).toThrow("Enter a valid one-time fee.");
  });
});
