import type { ExpenseCreateInput } from "./types";

// One-time prices supplied for the October 2026 Lab Drops. Keep drop identities
// separate from standard combine plan sizes (especially the two $3K challenges).
export const TOPSTEP_LAB_DROPS = [
  { id: "001", name: "$25K Static", kind: "Trading Combine", feeCents: 7_500 },
  { id: "006", name: "$50K Static", kind: "Trading Combine", feeCents: 14_900 },
  { id: "007", name: "The $1.5K Challenge", kind: "Challenge", feeCents: 9_900 },
  { id: "002", name: "$250K Freedom", kind: "Trading Combine", feeCents: 49_900 },
  { id: "008", name: "The $3K Challenge - Return", kind: "Challenge", feeCents: 19_900 },
  { id: "009", name: "The $3K Challenge - Revenge", kind: "Challenge", feeCents: 6_500 },
] as const;

export type TopstepLabDropId = typeof TOPSTEP_LAB_DROPS[number]["id"];

export function buildTopstepLabsExpense(input: {
  dropId: TopstepLabDropId;
  quantity: number;
  unitFeeCents: number;
  expenseDate: string;
  sourceId: string;
  accountId?: number;
  description?: string;
  tags?: string[];
}): ExpenseCreateInput {
  const drop = TOPSTEP_LAB_DROPS.find((item) => item.id === input.dropId);
  if (!drop) throw new Error("Choose a Topstep Lab Drop.");
  if (!Number.isInteger(input.quantity) || input.quantity < 1 || input.quantity > 5) {
    throw new Error("Quantity must be a whole number from 1 to 5.");
  }
  const totalCents = input.unitFeeCents * input.quantity;
  if (!Number.isInteger(input.unitFeeCents) || input.unitFeeCents <= 0 || totalCents > 2_147_483_647) {
    throw new Error("Enter a valid one-time fee.");
  }
  return {
    source_id: `topstep_labs:${input.sourceId}`,
    provider: "topstep",
    expense_date: input.expenseDate,
    amount_cents: totalCents,
    currency: "USD",
    category: "evaluation_fee",
    // Labs do not use the standard combine's size/pricing model. Persist their
    // identity in the existing description/tags, without inferring recurring fees.
    account_id: input.quantity === 1 ? input.accountId : undefined,
    description: `Topstep Labs Drop #${drop.id} - ${drop.name}; ${input.quantity} x $${(input.unitFeeCents / 100).toFixed(2)} one-time fee${input.description?.trim() ? `; ${input.description.trim()}` : ""}`,
    tags: Array.from(new Set([
      "topstep_labs", `lab_drop_${drop.id}`, "one_time_fee", `quantity_${input.quantity}`,
      ...(input.tags ?? []),
    ])),
  };
}
