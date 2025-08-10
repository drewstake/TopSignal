// src/features/lockout/LockoutAccountButton.jsx
import { useMemo, useState } from "react";
import Button from "../../components/ui/Button";
import Modal from "../../components/ui/Modal";
import { addPersonalLockouts, buildLockoutsForToday } from "../../services/lockout";

/**
 * Lock out the currently-selected account until 5:30 PM local time.
 * If it's already past 5:30 PM, it will lock until 5:30 PM tomorrow.
 */
export default function LockoutAccountButton({ accountId, accountName, log }) {
  const disabled = !accountId;
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const label = useMemo(
    () => (accountName ? `Lockout ${accountName}` : "Lockout This Account"),
    [accountName]
  );

  async function confirm() {
    if (!accountId) return;
    setSubmitting(true);
    try {
      const entries = buildLockoutsForToday([Number(accountId)], { hour: 17, minute: 30 });
      await addPersonalLockouts(entries);
      log?.(`Locked out account ${accountName || accountId} until 5:30 PM (local)`);
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
        disabled={disabled}
        onClick={() => setOpen(true)}
        title={disabled ? "Select an account first" : "Lock out this account until 5:30 PM"}
      >
        {label}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Confirm lockout for this account"
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
            This will create a personal lockout for{" "}
            <b>{accountName || `Account #${accountId}`}</b> from now until{" "}
            <i>5:30 PM local time</i>. If it’s already past 5:30 PM, it will apply to{" "}
            <i>5:30 PM tomorrow</i>.
          </p>
          <p className="text-[11px] text-amber-300/90">
            Uses the <code>userapi.topstepx.com</code> front-end endpoint and may require you to be signed in to Topstep’s website in this browser.
            Cookies are sent with the request.
          </p>
        </div>
      </Modal>
    </>
  );
}
