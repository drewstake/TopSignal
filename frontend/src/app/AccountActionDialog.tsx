import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { writeStoredMainAccountId } from "../lib/accountSelection";
import { accountsApi } from "../lib/api";
import { getDemoAccountLabel } from "../lib/demoMode";
import type { AccountInfo } from "../lib/types";

export type AccountEditAction = "rename" | "main";

export function AccountActionDialog({ account, action, onClose }: {
  account: AccountInfo;
  action: AccountEditAction;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [name, setName] = useState(account.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const renaming = action === "rename";

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    dialog?.querySelector("input")?.select();
    return () => dialog?.close();
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    if (renaming && !name.trim()) {
      setError("Account name cannot be empty.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      if (renaming) {
        await accountsApi.renameAccountDisplayName(account.id, name.trim());
      } else {
        await accountsApi.setMainAccount(account.id);
        writeStoredMainAccountId(account.id);
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the account change.");
      setSaving(false);
    }
  }

  return (
    <dialog ref={dialogRef} aria-labelledby={titleId}
      className="fixed m-auto w-[min(26rem,calc(100%-2rem))] rounded-xl border border-app-border bg-app-surface p-5 text-app-text shadow-xl backdrop:bg-black/60"
      onCancel={event => { event.preventDefault(); if (!saving) onClose(); }}>
      <form onSubmit={save} className="space-y-4">
        <div>
          <h2 id={titleId} className="text-lg font-semibold">{renaming ? "Rename account" : "Set as main account"}</h2>
          <p className="mt-1 break-words text-sm text-app-muted">{getDemoAccountLabel(account)}</p>
        </div>
        {renaming && <label className="block text-sm">
          Display name
          <Input className="mt-1" value={name} onChange={event => setName(event.target.value)} disabled={saving} autoFocus />
        </label>}
        {error && <p role="alert" className="text-sm text-app-negative">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose} disabled={saving}>Cancel</Button>
          <Button type="submit" disabled={saving}>{saving ? "Saving…" : renaming ? "Save name" : "Set as main"}</Button>
        </div>
      </form>
    </dialog>
  );
}
