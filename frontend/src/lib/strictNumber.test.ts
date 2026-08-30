import { describe, expect, it } from "vitest";

import { parseStrictFiniteNumber, parseStrictInteger } from "./strictNumber";

describe("strict numeric parsing", () => {
  it("accepts complete finite decimal and exponent values", () => {
    expect(parseStrictFiniteNumber(" 2500.50 ")).toBe(2500.5);
    expect(parseStrictFiniteNumber(".75")).toBe(0.75);
    expect(parseStrictFiniteNumber("1e3")).toBe(1000);
    expect(parseStrictFiniteNumber("-12.5")).toBe(-12.5);
  });

  it("rejects partial, formatted, and non-decimal values", () => {
    for (const value of ["", "250USD", "2,500", "5.9oops", "0x10", "Infinity", "NaN"]) {
      expect(parseStrictFiniteNumber(value)).toBeNull();
    }
  });

  it("requires complete safe integers", () => {
    expect(parseStrictInteger("5")).toBe(5);
    expect(parseStrictInteger("5.0")).toBe(5);
    expect(parseStrictInteger("5.9")).toBeNull();
    expect(parseStrictInteger("123abc")).toBeNull();
    expect(parseStrictInteger("9007199254740992")).toBeNull();
  });
});
