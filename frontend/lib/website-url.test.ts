import { describe, expect, it } from "vitest";

import { normalizeWebsiteUrl } from "@/lib/website-url";

describe("normalizeWebsiteUrl", () => {
  it.each([
    ["acme.com", "https://acme.com/"],
    ["  Acme.com  ", "https://acme.com/"],
    ["www.acme.com/about", "https://www.acme.com/about"],
    ["http://acme.com", "https://acme.com/"],
    ["HTTPS://Acme.COM/Pricing?x=1", "https://acme.com/Pricing?x=1"],
    ["https://acme.co.uk", "https://acme.co.uk/"],
    ["https://acme.com:443/", "https://acme.com/"],
    ["acme.com:443", "https://acme.com/"],
    ["https://sub.domain.acme.io/a/b", "https://sub.domain.acme.io/a/b"],
    ["acme.com.", "https://acme.com/"],
  ])("accepts %s", (input, expected) => {
    expect(normalizeWebsiteUrl(input)).toEqual({ ok: true, url: expected });
  });

  it.each([
    ["", "Enter your company website."],
    ["   ", "Enter your company website."],
    ["my company", "can't contain spaces"],
    ["localhost", "public website"],
    ["acme", "public website"],
    ["10.0.0.1", "public website"],
    ["http://192.168.1.1/", "public website"],
    ["https://[::1]/", "public website"],
    ["https://user:pw@acme.com", "username or password"],
    ["https://acme.com:8443", "Custom ports"],
    ["acme.com:8080", "Custom ports"],
    ["ftp://acme.com", "Only web addresses"],
    ["mailto:a@acme.com", "public website"],
    ["javascript:alert(1)", "public website"],
    ["https://-bad-.com", "public website"],
    ["https://acme..com", "public website"],
    [`https://${"a".repeat(2100)}.com`, "too long"],
  ])("rejects %s with a specific message", (input, fragment) => {
    const result = normalizeWebsiteUrl(input);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.message).toContain(fragment);
  });
});
