"use client";

import * as React from "react";
import { Image as ImageIcon, Type } from "lucide-react";

import { BrandCard } from "@/components/campaigns/sequence/setup/brand-card";
import { brandProblems } from "@/components/campaigns/sequence/setup/setup-copy";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { BrandKit, EmailFormat } from "@/types/domain";

type Props = {
  workspaceId: string;
  campaignId: string;
  format: EmailFormat | null;
  onFormat: (format: EmailFormat) => void;
  brand: BrandKit;
  onBrand: (brand: BrandKit) => void;
  companyName: string;
  siteUrl: string | null;
  brandDetected: boolean;
  brandWarnings: string[];
  onBack: () => void;
  onContinue: () => void;
};

const OPTIONS: {
  value: EmailFormat;
  title: string;
  description: string;
  icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean }>;
}[] = [
  {
    value: "TEXT",
    title: "Text Email",
    description:
      "Plain and personal, like a message typed by a person. No logo, colours or design.",
    icon: Type,
  },
  {
    value: "HTML",
    title: "HTML Email",
    description: "A designed email that matches your brand: your logo, colours and font.",
    icon: ImageIcon,
  },
];

/** Choose Text or HTML; for HTML, review the brand the email will use. */
export function StyleStep({
  workspaceId,
  campaignId,
  format,
  onFormat,
  brand,
  onBrand,
  companyName,
  siteUrl,
  brandDetected,
  brandWarnings,
  onBack,
  onContinue,
}: Props) {
  const brandValid = Object.keys(brandProblems(brand)).length === 0;
  const canContinue = format === "TEXT" || (format === "HTML" && brandValid);
  return (
    <div className="space-y-4">
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-slate-900">Choose your email style</legend>
        <div className="grid gap-3 sm:grid-cols-2">
          {OPTIONS.map((option) => {
            const selected = format === option.value;
            const Icon = option.icon;
            return (
              <label
                key={option.value}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors focus-within:ring-2 focus-within:ring-brand-600",
                  selected
                    ? "border-brand-500 bg-brand-50"
                    : "border-slate-200 bg-white hover:border-slate-300",
                )}
              >
                <input
                  type="radio"
                  name="email-format"
                  value={option.value}
                  checked={selected}
                  onChange={() => onFormat(option.value)}
                  className="mt-1"
                />
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                    <Icon className="h-4 w-4 text-slate-500" aria-hidden />
                    {option.title}
                  </span>
                  <span className="mt-0.5 block text-sm text-slate-500">
                    {option.description}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>

      {format === "HTML" ? (
        <BrandCard
          workspaceId={workspaceId}
          campaignId={campaignId}
          brand={brand}
          onChange={onBrand}
          companyName={companyName}
          siteUrl={siteUrl}
          detected={brandDetected}
          warnings={brandWarnings}
        />
      ) : null}

      <div className="flex items-center justify-between gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onBack}>
          Back
        </Button>
        <Button type="button" disabled={!canContinue} onClick={onContinue}>
          Continue
        </Button>
      </div>
    </div>
  );
}
