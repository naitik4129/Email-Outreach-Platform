import { describe, expect, it } from "vitest";

import {
  formatBytes,
  MAX_ATTACHMENTS,
  MAX_FILE_BYTES,
  MAX_INLINE_IMAGES,
  validateFileForUpload,
} from "@/components/campaigns/sequence/attachment-rules";
import type { StepAttachment } from "@/types/domain";

function attachment(
  n: number,
  overrides: Partial<StepAttachment> = {},
): StepAttachment {
  return {
    id: `a${n}`,
    step_id: "s",
    filename: `f${n}.pdf`,
    content_type: "application/pdf",
    size_bytes: 1000,
    disposition: "ATTACHMENT",
    content_id: `cid${n}`,
    created_at: "",
    ...overrides,
  };
}

describe("validateFileForUpload", () => {
  it("accepts an allowed file", () => {
    expect(validateFileForUpload({ name: "a.pdf", size: 100 }, "ATTACHMENT", [])).toBeNull();
    expect(validateFileForUpload({ name: "A.PNG", size: 100 }, "INLINE", [])).toBeNull();
  });

  it("rejects empty and oversized files with the size in the message", () => {
    expect(validateFileForUpload({ name: "a.pdf", size: 0 }, "ATTACHMENT", [])).toMatch(/empty/i);
    expect(
      validateFileForUpload({ name: "big.pdf", size: MAX_FILE_BYTES + 1 }, "ATTACHMENT", []),
    ).toMatch(/at most 2\.5 MB/);
  });

  it.each(["x.exe", "x.svg", "x.html", "x.zip", "noextension"])("rejects %s", (name) => {
    expect(validateFileForUpload({ name, size: 10 }, "ATTACHMENT", [])).toMatch(/isn't allowed/i);
  });

  it("only allows images inline", () => {
    expect(validateFileForUpload({ name: "a.pdf", size: 10 }, "INLINE", [])).toMatch(
      /PNG, JPG, GIF or WebP/,
    );
  });

  it("enforces the count limits per kind", () => {
    const files = Array.from({ length: MAX_ATTACHMENTS }, (_, i) => attachment(i));
    expect(validateFileForUpload({ name: "n.pdf", size: 10 }, "ATTACHMENT", files)).toMatch(
      new RegExp(`at most ${MAX_ATTACHMENTS} attachments`),
    );
    // Inline images have their own budget.
    expect(validateFileForUpload({ name: "n.png", size: 10 }, "INLINE", files)).toBeNull();
    const images = Array.from({ length: MAX_INLINE_IMAGES }, (_, i) =>
      attachment(i, { disposition: "INLINE", size_bytes: 10 }),
    );
    expect(validateFileForUpload({ name: "n.png", size: 10 }, "INLINE", images)).toMatch(
      new RegExp(`at most ${MAX_INLINE_IMAGES} images`),
    );
  });

  it("enforces the total size across the step", () => {
    const existing = [attachment(1, { size_bytes: MAX_FILE_BYTES - 5 })];
    expect(validateFileForUpload({ name: "n.pdf", size: 100 }, "ATTACHMENT", existing)).toMatch(
      /total at most/,
    );
  });
});

describe("formatBytes", () => {
  it("formats sizes readably", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(200 * 1024)).toBe("200 KB");
    expect(formatBytes(MAX_FILE_BYTES)).toBe("2.5 MB");
  });
});
