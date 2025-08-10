// src/features/lockout/LockoutAllButton.jsx
import { useMemo, useState } from "react";
import Button from "../../components/ui/Button";
import Modal from "../../components/ui/Modal";
import { addPersonalLockouts, buildLockoutsForToday } from "../../services/lockout";
import { useAccounts } from "../../hooks/useAccounts";

export default function LockoutAllButton({ log }) {
  // active accounts only (your hook already supports this)
  const { accounts } = useAccounts(true);

  // Express Funded heuristic — adjust if you have a real flag
  const xfaAccounts = useMemo(
    () => (accounts || []).filter((a) => (a?.name || "").toUpperCase().includes("XFA")),
    [accounts]
  );

  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function confirm() {
    if (!xfaAccounts.length) return;
    setSubmitting(true);
    try {
      const ids = xfaAccounts.map((a) => Number(a.id));
      // Use env VITE_LOCKOUT_UNTIL_HHMM if provided; otherwise default EOD.
      const entries = buildLockoutsForToday(ids);
      await addPersonalLockouts(entries);
      log?.(`Locked out ${ids.length} XFA accounts (until configured cutoff)`);
      setOpen(false);
    } catch (e) {
      alert(e?.message || "Lockout failed");
      log?.(`Lockout failed: ${e?.message || e}`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <Button
        variant="danger"
        className="px-3 py-1.5"
        onClick={() => setOpen(true)}
        disabled={xfaAccounts.length === 0}
        title={xfaAccounts.length ? "Lock out ALL XFA" : "No XFA accounts detected"}
      >
        Lockout All (Today)
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Confirm daily lockout for ALL Express Funded (XFA)"
        actions={
          <button
            disabled={submitting}
            onClick={confirm}
            className="px-4 py-2 rounded-xl bg-gradient-to-br from-rose-500 to-orange-600 text-white shadow-lg hover:shadow-xl transition disabled:opacity-60"
          >
            {submitting ? "Locking…" : "Confirm Lockout"}
          </button>
        }
      >
        <div className="space-y-3 text-sm">
          <p>
            This will lock out <b>all XFA (Express Funded)</b> accounts detected in your profile for the rest of the
            day. You can optionally set a specific cutoff time with{" "}
            <code>VITE_LOCKOUT_UNTIL_HHMM</code> (e.g., <code>17:30</code>).
          </p>
          <div className="text-xs text-zinc-400">
            Affected accounts ({xfaAccounts.length}):{" "}
            {xfaAccounts.map((a) => a.name || a.id).join(", ") || "—"}
          </div>
        </div>
      </Modal>
    </>
  );
}
