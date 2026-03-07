interface ClipboardItemLike {
  kind?: string;
  type?: string;
  getAsFile?: () => File | null;
}

interface ClipboardPasteEventLike {
  clipboardData?: {
    items?: ArrayLike<ClipboardItemLike>;
  } | null;
  preventDefault: () => void;
}

async function fallbackCopyText(text: string) {
  if (typeof document === "undefined") {
    return false;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";

  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();

  const copied = typeof document.execCommand === "function" ? document.execCommand("copy") : false;
  document.body.removeChild(textarea);
  return copied;
}

export function getClipboardImageFile(items: ArrayLike<ClipboardItemLike> | undefined | null): File | null {
  if (!items) {
    return null;
  }

  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (!item || item.kind !== "file") {
      continue;
    }

    const type = (item.type ?? "").toLowerCase();
    if (!type.startsWith("image/")) {
      continue;
    }

    const file = item.getAsFile?.() ?? null;
    if (file) {
      return file;
    }
  }

  return null;
}

export function handleClipboardImagePaste(event: ClipboardPasteEventLike, onFile: (file: File) => void): boolean {
  const imageFile = getClipboardImageFile(event.clipboardData?.items);
  if (!imageFile) {
    return false;
  }

  event.preventDefault();
  onFile(imageFile);
  return true;
}

export async function copyTextToClipboard(text: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return fallbackCopyText(text);
    }
  }

  return fallbackCopyText(text);
}
