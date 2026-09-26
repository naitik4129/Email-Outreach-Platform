import { Editor, type Extensions } from "@tiptap/core";
import { EmailImage } from "@/components/email-editor/email-image";
import { Color, FontFamily, FontSize, TextStyle } from "@tiptap/extension-text-style";
import Placeholder from "@tiptap/extension-placeholder";
import TextAlign from "@tiptap/extension-text-align";
import StarterKit from "@tiptap/starter-kit";

// Fonts that render predictably across mail clients. Kept in step with the
// backend sanitizer's font-family allow-list (letters, digits, spaces, commas).
export const EMAIL_FONTS = [
  { label: "Default", value: "" },
  { label: "Arial", value: "Arial, Helvetica, sans-serif" },
  { label: "Georgia", value: "Georgia, serif" },
  { label: "Times New Roman", value: "Times New Roman, Times, serif" },
  { label: "Verdana", value: "Verdana, Geneva, sans-serif" },
  { label: "Trebuchet MS", value: "Trebuchet MS, sans-serif" },
  { label: "Courier New", value: "Courier New, monospace" },
] as const;

export const EMAIL_FONT_SIZES = ["10", "11", "12", "13", "14", "16", "18", "20", "24", "28", "32"];

export function buildEmailExtensions(placeholder?: string): Extensions {
  return [
    StarterKit.configure({
      heading: { levels: [1, 2, 3] },
      // The backend allow-list only keeps href, so target/rel would be stripped
      // on save anyway; keep the editor's output honest.
      link: { openOnClick: false, autolink: false, HTMLAttributes: { target: null, rel: null } },
      trailingNode: false,
    }),
    TextStyle,
    Color,
    FontFamily,
    FontSize,
    TextAlign.configure({ types: ["heading", "paragraph"] }),
    EmailImage,
    ...(placeholder ? [Placeholder.configure({ placeholder })] : []),
  ];
}

// --- inline images (cid: <-> displayable URL) -----------------------------

// A 1x1 transparent GIF: keeps the image node in the document (and in the
// saved HTML) while the real display URL is still loading.
const IMAGE_PLACEHOLDER =
  "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";

const CID_IMG = /<img\b[^>]*\bsrc=(["'])cid:([A-Za-z0-9_.@-]+)\1[^>]*>/gi;
const DATA_CID_IMG = /<img\b[^>]*\bdata-cid=(["'])([A-Za-z0-9_.@-]+)\1[^>]*>/gi;
const SRC_CID_ATTR = /\bsrc=(["'])cid:[^"']*\1/i;
const SRC_ATTR = /\bsrc=(["'])[^"']*\1/i;
const DATA_CID_ATTR = /\s*\bdata-cid=(["'])[^"']*\1/i;
const CID_SRC_ANYWHERE = /\bsrc=(["'])cid:([A-Za-z0-9_.@-]+)\1/gi;

// Stored HTML -> what the editor can display. Only <img> tags that use cid: are
// touched, so every other byte of the HTML is left exactly as it was.
export function cidToEditorHtml(html: string, urls: Record<string, string>): string {
  if (!html.includes("cid:")) return html;
  return html.replace(CID_IMG, (tag: string, _quote: string, id: string) =>
    tag.replace(SRC_CID_ATTR, () => `src="${urls[id] ?? IMAGE_PLACEHOLDER}" data-cid="${id}"`),
  );
}

// Editor HTML -> stored HTML: the display URL goes back to cid:<id>.
export function editorHtmlToCid(html: string): string {
  if (!html.includes("data-cid")) return html;
  return html.replace(DATA_CID_IMG, (tag: string, _quote: string, id: string) =>
    tag.replace(DATA_CID_ATTR, "").replace(SRC_ATTR, () => `src="cid:${id}"`),
  );
}

// Preview HTML (server-rendered, still cid:) -> displayable, for the iframe.
export function cidToPreviewHtml(html: string, urls: Record<string, string>): string {
  if (!html.includes("cid:")) return html;
  return html.replace(CID_SRC_ANYWHERE, (match: string, _quote: string, id: string) =>
    urls[id] ? `src="${urls[id]}"` : match,
  );
}

export function extractCids(html: string): string[] {
  return Array.from(html.matchAll(CID_SRC_ANYWHERE), (m) => m[2]);
}

// Drop every <img> that references the given content id (used when its file is
// removed, so the body never points at a file that no longer exists).
export function removeCidImages(html: string, contentId: string): string {
  return html.replace(CID_IMG, (tag: string, _quote: string, id: string) =>
    id === contentId ? "" : tag,
  );
}

// --- link validation -------------------------------------------------------

// Mirrors the server allow-list (http, https, mailto). A bare domain gets
// https:// so "example.com" works; anything else (javascript:, data:, {{var}} in
// the scheme position) is rejected.
export function normalizeLinkUrl(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  if (/^(https?:\/\/|mailto:)\S+$/i.test(value)) return value;
  if (/^[^\s:{}/]+\.[a-z]{2,}(?:[/?#]\S*)?$/i.test(value)) return `https://${value}`;
  return null;
}

// --- cursor insertion into plain inputs -----------------------------------

export function insertAtCursor(
  value: string,
  selectionStart: number | null,
  selectionEnd: number | null,
  insert: string,
): { value: string; caret: number } {
  const start = selectionStart ?? value.length;
  const end = selectionEnd ?? start;
  return {
    value: value.slice(0, start) + insert + value.slice(end),
    caret: start + insert.length,
  };
}

// --- round-trip check ------------------------------------------------------

const BLOCK_TAGS = new Set([
  "p", "div", "ul", "ol", "li", "blockquote", "h1", "h2", "h3", "hr", "pre",
  "table", "thead", "tbody", "tr", "td", "th",
]);
const TAG_ALIASES: Record<string, string> = { b: "strong", i: "em", strike: "s", del: "s" };
const IGNORED_ATTRS = new Set(["target", "rel", "class"]);

function escapeText(text: string) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function canonicalStyle(style: string) {
  return style
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [prop, ...rest] = part.split(":");
      return `${prop.trim().toLowerCase()}:${rest.join(":").trim().replace(/\s+/g, " ").toLowerCase()}`;
    })
    .sort()
    .join(";");
}

function canonicalChildren(parent: Node): string {
  const kids = Array.from(parent.childNodes);
  return kids
    .map((kid, index) => {
      if (kid.nodeType === 3) {
        const text = (kid.textContent ?? "").replace(/\s+/g, " ");
        if (!text.trim()) {
          const prev = kids[index - 1];
          const next = kids[index + 1];
          const isBlock = (n?: Node) =>
            !n || (n.nodeType === 1 && BLOCK_TAGS.has((n as Element).tagName.toLowerCase()));
          if (isBlock(prev) || isBlock(next)) return "";
        }
        return escapeText(text);
      }
      if (kid.nodeType === 1) return canonicalElement(kid as Element);
      return "";
    })
    .join("");
}

function canonicalElement(el: Element): string {
  const rawTag = el.tagName.toLowerCase();
  const tag = TAG_ALIASES[rawTag] ?? rawTag;
  const attrs = Array.from(el.attributes)
    .filter((attr) => !IGNORED_ATTRS.has(attr.name) && attr.value !== "")
    .map((attr) =>
      attr.name === "style"
        ? `style="${canonicalStyle(attr.value)}"`
        : `${attr.name}="${attr.value}"`,
    )
    .filter((attr) => attr !== 'style=""')
    .sort()
    .join(" ");
  const open = attrs ? `<${tag} ${attrs}>` : `<${tag}>`;
  if (["br", "hr", "img"].includes(tag)) return open;

  // The editor wraps list-item text in <p>; hand-written HTML doesn't.
  let inner: Node = el;
  if (tag === "li" && el.children.length === 1 && el.children[0].tagName === "P") {
    const only = el.children[0];
    if (only.attributes.length === 0 && el.childNodes.length === 1) inner = only;
  }
  return `${open}${canonicalChildren(inner)}</${tag}>`;
}

export function canonicalizeHtml(html: string): string {
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  return canonicalChildren(doc.body).trim();
}

// Normalized editor output for `html`, or null if the editor can't be created.
export function normalizeThroughEditor(html: string): string | null {
  try {
    const editor = new Editor({ extensions: buildEmailExtensions(), content: html });
    const out = editor.getHTML();
    editor.destroy();
    return out;
  } catch {
    return null;
  }
}

// True when the visual editor would reproduce `html` without dropping anything.
// Used to decide whether stored (possibly hand-written or legacy) HTML is safe
// to open in the visual editor or must be edited as source.
export function roundTripsCleanly(html: string): boolean {
  if (!html.trim()) return true;
  const out = normalizeThroughEditor(html);
  if (out === null) return false;
  return canonicalizeHtml(out) === canonicalizeHtml(html);
}
