import type { BrandFontKey, BrandKit } from "@/types/domain";

// Mirrors the server (ADR-0016); the server validates again. These only give
// instant feedback and keep the copy in one place.
export const BUSINESS_LIMITS = {
  nameMin: 2,
  nameMax: 120,
  descriptionMin: 40,
  descriptionMax: 1500,
} as const;

export const PROFILE_LIMITS = {
  summary: 1000,
  audience: 400,
  tone: 200,
  services: { items: 8, chars: 80 },
  industries: { items: 6, chars: 60 },
  keyMessages: { items: 5, chars: 150 },
} as const;

export const FONT_OPTIONS: { value: BrandFontKey; label: string }[] = [
  { value: "sans", label: "Clean (Arial)" },
  { value: "humanist", label: "Friendly (Verdana)" },
  { value: "serif", label: "Classic (Georgia)" },
  { value: "times", label: "Formal (Times New Roman)" },
  { value: "modern", label: "Modern (Tahoma)" },
  { value: "mono", label: "Typewriter (Courier)" },
];

const WARNING_TEXT: Record<string, string> = {
  analysis_model_failed:
    "We couldn't fully analyze your company, so this is only a starting point. Please check " +
    "the details and fill in anything missing.",
  business_info_thin:
    "There wasn't much to go on, so we couldn't suggest an objective. You can write it yourself.",
  site_read_partial:
    "We could only read part of your website. Add anything that's missing below.",
  site_text_limited:
    "We could only read a short summary of your website (it may load its content after the page " +
    "opens). Please check the details below and add anything that's missing.",
  logo_not_found:
    "We couldn't find your logo. Paste its web address below, or your company name will be used " +
    "instead.",
  logo_svg_only:
    "Your logo is an SVG, which email programs can't show. Paste the web address of a PNG or JPG " +
    "version instead.",
  colors_defaulted:
    "We couldn't detect your brand colours, so we've used neutral ones. Choose your own below.",
  text_contrast_low:
    "The text and background colours are too close, so the email may be hard to read.",
  button_contrast_low: "The button text may be hard to read on this colour.",
  followups_require_progression:
    "Follow-up emails are switched off for this deployment, so only the first email would be " +
    "sent. Ask your administrator to enable follow-ups.",
};

export function describeWarning(code: string): string | null {
  return WARNING_TEXT[code] ?? null;
}

export function describeWarnings(codes: readonly string[]): string[] {
  return codes.map(describeWarning).filter((text): text is string => Boolean(text));
}

// The same neutral defaults the server uses for a brand kit with nothing detected.
export const DEFAULT_BRAND: BrandKit = {
  logo_url: null,
  logo_alt: "",
  primary: "#1f2937",
  accent: "#2563eb",
  text: "#111827",
  background: "#ffffff",
  font_key: "sans",
  cta_url: null,
  cta_label: "",
};

export const CTA_LABEL_MAX = 40;
export const BRAND_URL_MAX = 1000;

const HEX = /^#[0-9a-fA-F]{6}$/;
// No whitespace, quotes, angle brackets, backslashes or braces: the same
// characters the server refuses, so a value that passes here is accepted there.
const HTTPS = /^https:\/\/[^\s"'<>\\{}`]{3,}$/;

export const isHexColor = (value: string) => HEX.test(value.trim());

// https only, and something that looks like a host.
export function isHttpsUrl(value: string): boolean {
  const v = value.trim();
  if (v.length > BRAND_URL_MAX || !HTTPS.test(v)) return false;
  const host = v.slice("https://".length).split("/")[0];
  return host.includes(".");
}

/** Problems that would make the server reject the brand kit, keyed by field. */
export function brandProblems(brand: BrandKit): Partial<Record<keyof BrandKit, string>> {
  const problems: Partial<Record<keyof BrandKit, string>> = {};
  for (const key of ["primary", "accent", "text", "background"] as const) {
    if (!isHexColor(brand[key])) problems[key] = "Use a colour like #1f2937.";
  }
  if (brand.logo_url && !isHttpsUrl(brand.logo_url)) {
    problems.logo_url = "Enter a web address that starts with https://.";
  }
  if (brand.cta_url && !isHttpsUrl(brand.cta_url)) {
    problems.cta_url = "Enter a web address that starts with https://.";
  }
  if (brand.cta_label.includes("{{") || brand.cta_label.includes("}}")) {
    problems.cta_label = "Remove the curly braces.";
  }
  return problems;
}
