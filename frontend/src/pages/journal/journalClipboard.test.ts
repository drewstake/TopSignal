import { describe, expect, it, vi } from "vitest";

import { getClipboardImageFile, handleClipboardImagePaste } from "./journalClipboard";

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
