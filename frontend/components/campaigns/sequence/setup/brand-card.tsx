"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { EmailFrame } from "@/components/campaigns/personalization/email-frame";
import {
  CTA_LABEL_MAX,
  FONT_OPTIONS,
  brandProblems,
  describeWarnings,
  isHexColor,
} from "@/components/campaigns/sequence/setup/setup-copy";
import { Alert } from "@/components/ui/alert";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { previewEmailLayout } from "@/lib/personalization-api";
import type { BrandFontKey, BrandKit } from "@/types/domain";

type Props = {
  workspaceId: string;
  campaignId: string;
  brand: BrandKit;
  onChange: (brand: BrandKit) => void;
  companyName: string;
  siteUrl: string | null;
  // False when there was no website to read, so nothing could be detected.
  detected: boolean;
  // Codes from the analysis (logo_not_found, colors_defaulted, ...).
  warnings: string[];
};

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

function ColorField({
  label,
  value,
  error,
  onChange,
}: {
  label: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
}) {
  const id = React.useId();
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} picker`}
          value={isHexColor(value) ? value : "#000000"}
          onChange={(event) => onChange(event.target.value)}
          className="h-10 w-12 shrink-0 cursor-pointer rounded-md border border-slate-200 bg-white p-1"
        />
        <Input
          id={id}
          value={value}
          maxLength={7}
          spellCheck={false}
          aria-invalid={Boolean(error)}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
      {error ? (
        <p role="alert" className="text-xs font-medium text-red-600">
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Logo, colours, font and button for an HTML email, with a live preview. */
export function BrandCard({
  workspaceId,
  campaignId,
  brand,
  onChange,
  companyName,
  siteUrl,
  detected,
  warnings,
}: Props) {
  const set = (patch: Partial<BrandKit>) => onChange({ ...brand, ...patch });
  const problems = brandProblems(brand);
  const valid = Object.keys(problems).length === 0;
  const [logoBroken, setLogoBroken] = React.useState(false);

  // Preview through the real renderer so it cannot drift from what is sent.
  const request = useDebounced(
    JSON.stringify({ brand, company_name: companyName, site_url: siteUrl }),
    400,
  );
  const preview = useQuery({
    queryKey: ["workspace", workspaceId, "campaigns", campaignId, "layout-preview", request],
    queryFn: () =>
      previewEmailLayout(workspaceId, campaignId, {
        ...JSON.parse(request),
        cta_label: null,
      }),
    enabled: valid,
    retry: false,
    staleTime: 5 * 60 * 1000,
    placeholderData: (previous) => previous,
  });

  const notes = describeWarnings([...warnings, ...(preview.data?.warnings ?? [])]);
  const logoOk = Boolean(brand.logo_url) && !problems.logo_url && !logoBroken;

  return (
    <div className="space-y-4 rounded-lg border border-slate-200 p-4" aria-label="Brand settings">
      <h5 className="text-sm font-semibold text-slate-900">Your brand</h5>
      {!detected ? (
        <Alert variant="info">
          We couldn&apos;t detect your brand because no website was provided. Set your colours and
          logo below, or choose Text Email instead.
        </Alert>
      ) : null}
      {notes.map((text) => (
        <Alert key={text} variant="warning">
          {text}
        </Alert>
      ))}

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-4">
          <Field label="Logo web address" error={problems.logo_url}>
            <Input
              type="text"
              inputMode="url"
              placeholder="https://acme.com/logo.png"
              value={brand.logo_url ?? ""}
              onChange={(event) => {
                setLogoBroken(false);
                set({ logo_url: event.target.value.trim() ? event.target.value : null });
              }}
            />
          </Field>
          {logoOk ? (
            // A remote image the user chose; a broken URL is reported, not hidden.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={brand.logo_url as string}
              alt="Logo preview"
              onError={() => setLogoBroken(true)}
              className="h-10 max-w-[12rem] rounded border border-slate-200 bg-white object-contain p-1"
            />
          ) : brand.logo_url && logoBroken ? (
            <p role="alert" className="text-xs font-medium text-amber-700">
              We couldn&apos;t load that image. Check the address, or clear it to use your company
              name.
            </p>
          ) : null}
          <div className="grid grid-cols-2 gap-3">
            <ColorField label="Main colour" value={brand.primary} error={problems.primary} onChange={(primary) => set({ primary })} />
            <ColorField label="Accent colour" value={brand.accent} error={problems.accent} onChange={(accent) => set({ accent })} />
            <ColorField label="Text colour" value={brand.text} error={problems.text} onChange={(text) => set({ text })} />
            <ColorField label="Background" value={brand.background} error={problems.background} onChange={(background) => set({ background })} />
          </div>
          <Field label="Font">
            <Select
              value={brand.font_key}
              onChange={(event) => set({ font_key: event.target.value as BrandFontKey })}
            >
              {FONT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Button link (optional)" error={problems.cta_url}>
            <Input
              type="text"
              inputMode="url"
              placeholder="https://acme.com/book-a-demo"
              value={brand.cta_url ?? ""}
              onChange={(event) =>
                set({ cta_url: event.target.value.trim() ? event.target.value : null })
              }
            />
          </Field>
          {brand.cta_url ? (
            <Field label="Button text" error={problems.cta_label}>
              <Input
                value={brand.cta_label}
                maxLength={CTA_LABEL_MAX}
                placeholder="Book a demo"
                onChange={(event) => set({ cta_label: event.target.value })}
              />
            </Field>
          ) : null}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-medium text-slate-500">Preview</p>
          {!valid ? (
            <p className="text-sm text-slate-500">Fix the highlighted fields to see the preview.</p>
          ) : preview.data ? (
            <EmailFrame html={preview.data.html} title="Email design preview" />
          ) : preview.isError ? (
            <p role="alert" className="text-sm text-red-600">
              We couldn&apos;t build the preview. Check your settings and try again.
            </p>
          ) : (
            <div role="status" className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Building preview…
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
