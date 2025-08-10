import { createPortal } from "react-dom";

export default function Modal({ open, onClose, title, children, actions }) {
  if (!open) return null;
  const handleBackdrop = (e) => {
    if (e.target === e.currentTarget) onClose?.();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={handleBackdrop}
    >
      <div className="w-full max-w-md rounded-2xl bg-zinc-900 border border-white/10 shadow-2xl">
        <div className="px-5 py-4 border-b border-white/10 flex items-center justify-between">
          <h3 className="text-sm font-semibold tracking-wide">{title}</h3>
          <button
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-200 text-sm px-2 py-1 rounded-md hover:bg-white/10"
          >
            ✕
          </button>
        </div>
        <div className="px-5 py-4 text-zinc-200">{children}</div>
        {actions && (
          <div className="px-5 py-4 border-t border-white/10 flex items-center justify-end gap-2">
            {actions}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
