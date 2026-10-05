import { useEffect, useId, useRef } from "react";
import { Button } from "../../../components/ui/Button";
import { Icon } from "../../../components/ui/Icon";
import { COPY_TRADE_ENABLE_CONFIRMATION_MESSAGE } from "../copyTrade";

interface CopyTradeConfirmationProps {
  onCancel: () => void;
  onConfirm: () => void;
}

export function CopyTradeConfirmation({ onCancel, onConfirm }: CopyTradeConfirmationProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const descriptionId = useId();
  const [title, description, notice] = COPY_TRADE_ENABLE_CONFIRMATION_MESSAGE.split("\n\n");

  useEffect(() => {
    const dialog = dialogRef.current;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    dialog?.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      dialog?.close();
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="copy-trade-confirmation"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
          onCancel();
        }
      }}
    >
      <div className="copy-trade-confirmation-body">
        <div className="copy-trade-confirmation-icon"><Icon name="link" /></div>
        <p className="copy-trade-confirmation-eyebrow">DASHBOARD ANALYTICS</p>
        <h2 id={titleId}>{title}</h2>
        <div id={descriptionId}>
          <p className="copy-trade-confirmation-description">{description}</p>
          <div className="copy-trade-confirmation-notice">
            <Icon name="shield" />
            <p>{notice}</p>
          </div>
        </div>
      </div>
      <div className="copy-trade-confirmation-actions">
        <Button variant="ghost" onClick={onCancel} autoFocus>Cancel</Button>
        <Button onClick={onConfirm}>Enable Copy Trade Mode <Icon name="arrow" /></Button>
      </div>
    </dialog>
  );
}
