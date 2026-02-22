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
