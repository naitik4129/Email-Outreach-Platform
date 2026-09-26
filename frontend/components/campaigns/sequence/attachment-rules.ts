import type { StepAttachment } from "@/types/domain";

// Mirrors backend app/modules/campaigns/attachments.py. The server is the
// authority; these checks only give an immediate, friendly answer before a
// file is uploaded.
export const MAX_FILE_BYTES = 2_621_440; // 2.5 MiB
export const MAX_TOTAL_BYTES = 2_621_440;
export const MAX_ATTACHMENTS = 5;
export const MAX_INLINE_IMAGES = 10;

export const IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "gif", "webp"];
export const FILE_EXTENSIONS = [
  ...IMAGE_EXTENSIONS,
  "pdf",
  "txt",
  "csv",
  "docx",
  "xlsx",
  "pptx",
];

export const ATTACHMENT_ACCEPT = FILE_EXTENSIONS.map((e) => `.${e}`).join(",");
export const IMAGE_ACCEPT = IMAGE_EXTENSIONS.map((e) => `.${e}`).join(",");

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

// null = OK to try uploading.
export function validateFileForUpload(
  file: { name: string; size: number },
  disposition: "ATTACHMENT" | "INLINE",
  existing: StepAttachment[],
): string | null {
  if (file.size === 0) return "That file is empty.";
  if (file.size > MAX_FILE_BYTES) {
    return `"${file.name}" is ${formatBytes(file.size)}. Files can be at most ${formatBytes(MAX_FILE_BYTES)}.`;
  }
  const ext = extensionOf(file.name);
  const allowed = disposition === "INLINE" ? IMAGE_EXTENSIONS : FILE_EXTENSIONS;
  if (!allowed.includes(ext)) {
    return disposition === "INLINE"
      ? "Images can be PNG, JPG, GIF or WebP."
      : "This file type isn't allowed. Attach images, PDF, text/CSV, or Word/Excel/PowerPoint files.";
  }
  const count = existing.filter((a) => a.disposition === disposition).length;
  if (disposition === "ATTACHMENT" && count >= MAX_ATTACHMENTS) {
    return `A step can have at most ${MAX_ATTACHMENTS} attachments.`;
  }
  if (disposition === "INLINE" && count >= MAX_INLINE_IMAGES) {
    return `A step can have at most ${MAX_INLINE_IMAGES} images.`;
  }
  const total = existing.reduce((sum, a) => sum + a.size_bytes, 0) + file.size;
  if (total > MAX_TOTAL_BYTES) {
    return `Files on one step can total at most ${formatBytes(MAX_TOTAL_BYTES)}.`;
  }
  return null;
}
