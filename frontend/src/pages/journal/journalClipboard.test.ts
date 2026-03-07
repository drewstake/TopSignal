import { afterEach, describe, expect, it, vi } from "vitest";

import { copyTextToClipboard, getClipboardImageFile, handleClipboardImagePaste } from "./journalClipboard";

const originalNavigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");

afterEach(() => {
  if (originalNavigatorDescriptor) {
    Object.defineProperty(globalThis, "navigator", originalNavigatorDescriptor);
  } else {
    Reflect.deleteProperty(globalThis, "navigator");
  }
});

describe("getClipboardImageFile", () => {
  it("returns the first image file from clipboard items", () => {
    const imageFile = { name: "clip.png" } as unknown as File;
    const items = [
      {
        kind: "string",
        type: "text/plain",
        getAsFile: () => null,
      },
      {
        kind: "file",
        type: "image/png",
        getAsFile: () => imageFile,
      },
    ];

    const result = getClipboardImageFile(items);

    expect(result).toBe(imageFile);
  });
});

describe("handleClipboardImagePaste", () => {
  it("prevents default and invokes upload callback when image exists", () => {
    const imageFile = { name: "clip.png" } as unknown as File;
    const onFile = vi.fn();
    const preventDefault = vi.fn();

    const handled = handleClipboardImagePaste(
      {
        clipboardData: {
          items: [
            {
              kind: "file",
              type: "image/png",
              getAsFile: () => imageFile,
            },
          ],
        },
        preventDefault,
      },
      onFile,
    );

    expect(handled).toBe(true);
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(onFile).toHaveBeenCalledWith(imageFile);
  });

  it("returns false when clipboard has no image", () => {
    const onFile = vi.fn();
    const preventDefault = vi.fn();

    const handled = handleClipboardImagePaste(
      {
        clipboardData: {
          items: [
            {
              kind: "string",
              type: "text/plain",
              getAsFile: () => null,
            },
          ],
        },
        preventDefault,
      },
      onFile,
    );

    expect(handled).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
    expect(onFile).not.toHaveBeenCalled();
  });
});

describe("copyTextToClipboard", () => {
  it("uses the Clipboard API when available", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      value: {
        clipboard: {
          writeText,
        },
      },
    });

    await expect(copyTextToClipboard("Journal notes")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("Journal notes");
  });

  it("returns false when no clipboard implementation is available", async () => {
    Reflect.deleteProperty(globalThis, "navigator");

    await expect(copyTextToClipboard("Journal notes")).resolves.toBe(false);
  });
});
