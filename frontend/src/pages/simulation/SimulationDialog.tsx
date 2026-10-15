import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

// Native modal dialogs keep keyboard focus inside and restore it to the opener.
export function SimulationDialog({ label, drawer = false, alert = false, onClose, children }: {
  label: string; drawer?: boolean; alert?: boolean; onClose: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    const opener = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    node?.showModal();
    return () => { node?.close(); document.body.style.overflow = overflow; opener?.focus({ preventScroll: true }); };
  }, []);
  return createPortal(<dialog ref={dialog} className={`simulation-page simulation-dialog${drawer ? ' simulation-drawer' : ''}`} role={alert ? 'alertdialog' : 'dialog'} aria-modal="true" aria-label={label}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header className="simulation-dialog-heading"><h2>{label}</h2><button onClick={onClose} aria-label={`Close ${label.toLowerCase()}`}>×</button></header>
    <div className="simulation-dialog-content">{children}</div>
  </dialog>, document.body);
}
