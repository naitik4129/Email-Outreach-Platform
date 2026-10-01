// A company website typed by a person. This only gives instant feedback: the
// server applies the authoritative, SSRF-hardened rules (https/443 only, public
// addresses only, no credentials) before it fetches anything.

const MAX_LENGTH = 2048;
const HOST_LABEL = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/i;
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

export type WebsiteUrlResult =
  | { ok: true; url: string }
  | { ok: false; message: string };

const HELP = "Enter a public website address such as acme.com.";

/**
 * Accepts "acme.com", "www.acme.com/about", "http://acme.com" (upgraded to https)
 * or "https://acme.com". Returns the normalized https URL or a specific message.
 */
export function normalizeWebsiteUrl(raw: string): WebsiteUrlResult {
  const value = raw.trim();
  if (!value) return { ok: false, message: "Enter your company website." };
  if (value.length > MAX_LENGTH) return { ok: false, message: "That address is too long." };
  if (/\s/.test(value)) return { ok: false, message: "A website address can't contain spaces." };

  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(value);
  const hasAuthority = /^[a-z][a-z0-9+.-]*:\/\//i.test(value);
  if (scheme && !hasAuthority) {
    // "mailto:a@b.co", "javascript:x" ... but not "acme.com:443" (host:port).
    if (!/^[^/]*:\d+(\/|$)/.test(value)) return { ok: false, message: HELP };
  }
  if (hasAuthority && !/^https?:\/\//i.test(value)) {
    return { ok: false, message: "Only web addresses (https) are supported." };
  }

  const withScheme = hasAuthority ? value.replace(/^http:\/\//i, "https://") : `https://${value}`;
  let parsed: URL;
  try {
    parsed = new URL(withScheme);
  } catch {
    return { ok: false, message: HELP };
  }
  if (parsed.username || parsed.password) {
    return { ok: false, message: "Remove the username or password from the address." };
  }
  if (parsed.port && parsed.port !== "443") {
    return { ok: false, message: "Custom ports aren't supported. Enter the plain website address." };
  }
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (IPV4.test(host) || host.includes(":") || host === "localhost") {
    return { ok: false, message: HELP };
  }
  const labels = host.split(".");
  if (labels.length < 2 || labels.some((label) => !HOST_LABEL.test(label))) {
    return { ok: false, message: HELP };
  }
  const path = parsed.pathname === "/" ? "/" : parsed.pathname;
  return { ok: true, url: `https://${host}${path}${parsed.search}` };
}
