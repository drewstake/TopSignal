export type AccountType = "XFA" | "Combine" | "Practice" | "Unknown";

export function detectAccountTypeFromName(name: string): AccountType {
  const n = (name || "").toUpperCase();

  // XFA names you showed: "EXPRESS...", "EXPRESS-V2-..."
  if (n.includes("EXPRESS")) return "XFA";

  // Practice names you showed: "PRACTICE...", "PRAC-V2-..."
  if (n.startsWith("PRACTICE") || n.startsWith("PRAC-") || n.startsWith("PRAC_")) return "Practice";

  // Combine names you showed: "S1...", "50KTC...", "150KTC...", "TC-V2-..."
  if (
    n.startsWith("S1") ||
    n.includes("KTC") ||
    n.includes("TC-V2") ||
    n.includes("50K") ||
    n.includes("150K")
  ) {
    return "Combine";
  }

  return "Unknown";
}
