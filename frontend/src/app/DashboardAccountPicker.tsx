import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { Icon } from "../components/ui/Icon";
import { buildAccountAwarePath } from "../lib/accountSelection";
import type { AccountInfo } from "../lib/types";
import { AccountActionDialog, type AccountEditAction } from "./AccountActionDialog";

type AccountRow = { account: AccountInfo; label: string; note: string };

export function DashboardAccountPicker({ rows, selectedId, loading, readOnly, onSelect }: {
  rows: AccountRow[];
  selectedId: number | null;
  loading: boolean;
  readOnly: boolean;
  onSelect: (id: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<{ id: number; action: AccountEditAction } | null>(null);
  const [scope, setScope] = useState({ selectedId, readOnly });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  const panelId = useId();
  const selected = rows.find(row => row.account.id === selectedId);
  const target = edit && rows.find(row => row.account.id === edit.id)?.account;
  const caption = selected?.label ?? (loading ? "Loading accounts…" : rows.length ? "Choose an account" : "No accounts");
  const triggerLabel = selected?.note ? `${caption} — ${selected.note}` : caption;

  if (scope.selectedId !== selectedId || scope.readOnly !== readOnly) {
    setScope({ selectedId, readOnly });
    setEdit(null);
    setOpen(false);
  } else if (edit && (!target || target.is_archived)) {
    setEdit(null);
  }

  useEffect(() => {
    if (!open) return;
    const panel = root.current;
    const first = panel?.querySelector<HTMLElement>('[aria-current="true"]') ??
      panel?.querySelector<HTMLElement>(".account-picker-select, .account-picker-manage");
    first?.focus();
    function outside(event: PointerEvent) {
      if (!panel?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);

  useEffect(() => {
    if (!edit && !loading && restoreFocus.current) {
      if (document.activeElement === document.body) trigger.current?.focus();
      restoreFocus.current = false;
    }
  }, [edit, loading]);

  function editRow(id: number, action: AccountEditAction) {
    setOpen(false);
    setEdit({ id, action });
  }

  return <div ref={root} className="account-picker"
    onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}
    onKeyDown={event => {
      if (open && event.key === "Escape") {
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      if (edit) return;
      event.preventDefault();
      if (!open) { setOpen(true); return; }
      const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>(".account-picker-select"));
      if (!buttons.length) return;
      const index = buttons.findIndex(button => button.parentElement?.contains(event.target as Node));
      const next = event.key === "ArrowDown" ? (index + 1) % buttons.length : (index > 0 ? index - 1 : buttons.length - 1);
      buttons[next].focus();
    }}>
    <button ref={trigger} type="button" id="app-active-account" value={selectedId ?? ""}
      aria-label={`Active Account: ${triggerLabel}`}
      aria-expanded={open} aria-controls={open ? panelId : undefined} disabled={loading}
      className="account-picker-trigger" title={triggerLabel}
      onClick={() => setOpen(!open)}>
      <span>{triggerLabel}</span><Icon name="chevron" />
    </button>
    {open && <div id={panelId} className="account-picker-panel" role="group" aria-label="Choose or edit an account">
      {readOnly && <p className="account-picker-note">Demo accounts are read-only.</p>}
      {rows.length > 0 && <ul className="account-picker-list">
        {rows.map(({ account, label, note }) => {
          const active = account.id === selectedId;
          const locked = readOnly || loading || account.is_archived;
          const rename = `Rename ${label}`;
          const main = account.is_main ? `Main account: ${label}` : `Set ${label} as main`;
          return <li key={account.id} className="account-picker-row" data-active={active}>
            <button type="button" className="account-picker-icon" aria-label={rename} title={rename} disabled={locked}
              onClick={() => editRow(account.id, "rename")}><Icon name="edit" /></button>
            <button type="button" className="account-picker-select" aria-current={active ? "true" : undefined} disabled={loading}
              onClick={() => { setOpen(false); trigger.current?.focus(); onSelect(account.id); }}>
              <span className="account-picker-name">{label}</span>
              {(active || account.is_main || note) && <span className="account-picker-meta">
                {[active && "Selected", account.is_main && "Main", note].filter(Boolean).join(" · ")}
              </span>}
            </button>
            <button type="button" className="account-picker-icon" aria-label={main} title={main}
              data-main={account.is_main} disabled={locked || account.is_main}
              onClick={() => editRow(account.id, "main")}><Icon name="star" /></button>
          </li>;
        })}
      </ul>}
      <Link className="account-picker-manage" to={buildAccountAwarePath("/accounts", selectedId)}>Manage accounts <Icon name="arrow" /></Link>
    </div>}
    {edit && target && !target.is_archived && !readOnly && createPortal(<AccountActionDialog key={target.id}
      action={edit.action} account={target} onClose={() => {
        restoreFocus.current = true;
        setEdit(current => current === edit ? null : current);
      }} />, document.body)}
  </div>;
}
