"use client";

import * as React from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { ChipList } from "@/components/campaigns/sequence/setup/chip-list";
import {
  BUSINESS_LIMITS,
  PROFILE_LIMITS,
  describeWarnings,
} from "@/components/campaigns/sequence/setup/setup-copy";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { analyzeCompany } from "@/lib/personalization-api";
import { normalizeWebsiteUrl } from "@/lib/website-url";
import type {
  BrandKit,
  CompanyAnalysis,
  CompanyAnalysisSuggestions,
  CompanyProfile,
} from "@/types/domain";

export type CompanyConfirmed = {
  company: CompanyProfile;
  // Only a website yields a detected brand; null for typed business info.
  brand: BrandKit | null;
  suggestions: CompanyAnalysisSuggestions | null;
  warnings: string[];
};

type Failure = {
  message: string;
  code: string | null;
  details: Record<string, unknown> | null;
  count: number;
};

type Props = {
  workspaceId: string;
  campaignId: string;
  // The company already saved on the campaign, if any: shown for review directly.
  initialCompany: CompanyProfile | null;
  onConfirm: (result: CompanyConfirmed) => void;
  onSkip: () => void;
};

const textareaClass =
  "min-h-[84px] w-full rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600 disabled:bg-slate-50 disabled:text-slate-600";

function hostOf(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).hostname;
  } catch {
    return "";
  }
}

export function CompanyStep({
  workspaceId,
  campaignId,
  initialCompany,
  onConfirm,
  onSkip,
}: Props) {
  const [website, setWebsite] = React.useState("");
  const [businessOpen, setBusinessOpen] = React.useState(false);
  const [businessName, setBusinessName] = React.useState("");
  const [businessDescription, setBusinessDescription] = React.useState("");
  const [showErrors, setShowErrors] = React.useState(false);

  const [profile, setProfile] = React.useState<CompanyProfile | null>(initialCompany);
  const [analysis, setAnalysis] = React.useState<CompanyAnalysis | null>(null);
  const [analyzing, setAnalyzing] = React.useState(false);
  const [failure, setFailure] = React.useState<Failure | null>(null);
  const [confirmingReset, setConfirmingReset] = React.useState(false);
  const failures = React.useRef(0);
  // Every request gets a number. A response is used only if it is still the
  // latest one, so a late answer to a cancelled or replaced request is ignored.
  const latestRun = React.useRef(0);

  const websiteResult = normalizeWebsiteUrl(website);
  const name = businessName.trim();
  const description = businessDescription.trim();
  const businessProblems = {
    name:
      name.length < BUSINESS_LIMITS.nameMin
        ? "Enter your company name."
        : name.length > BUSINESS_LIMITS.nameMax
          ? `Keep the name under ${BUSINESS_LIMITS.nameMax} characters.`
          : undefined,
    description:
      description.length < BUSINESS_LIMITS.descriptionMin
        ? `Describe what your company does in at least ${BUSINESS_LIMITS.descriptionMin} characters.`
        : description.length > BUSINESS_LIMITS.descriptionMax
          ? `Keep the description under ${BUSINESS_LIMITS.descriptionMax} characters.`
          : undefined,
  };
  const businessValid = !businessProblems.name && !businessProblems.description;
  const valid = businessOpen ? businessValid : websiteResult.ok;
  // Continue stays available once something is typed: a disabled button would
  // swallow Enter and never say what is wrong, so problems are explained on submit.
  const hasInput = businessOpen ? Boolean(name || description) : website.trim() !== "";

  async function analyze() {
    setShowErrors(true);
    if (!valid || analyzing) return;
    const run = ++latestRun.current;
    setAnalyzing(true);
    setFailure(null);
    try {
      const result = await analyzeCompany(
        workspaceId,
        campaignId,
        businessOpen
          ? { business: { company_name: name, description } }
          : { url: websiteResult.ok ? websiteResult.url : website },
      );
      if (run !== latestRun.current) return;
      failures.current = 0;
      setAnalysis(result);
      setProfile(result.profile);
    } catch (error) {
      if (run !== latestRun.current) return;
      failures.current += 1;
      setFailure({
        message:
          error instanceof ApiError
            ? error.message
            : "We couldn't complete that request. Please try again.",
        code: error instanceof ApiError ? error.code : null,
        details: error instanceof ApiError ? error.details : null,
        count: failures.current,
      });
    } finally {
      if (run === latestRun.current) setAnalyzing(false);
    }
  }

  function cancel() {
    latestRun.current += 1;
    setAnalyzing(false);
  }

  function useBusinessInfoInstead() {
    const suggested = failure?.details?.company_name;
    setBusinessOpen(true);
    setWebsite("");
    if (typeof suggested === "string" && suggested && !businessName) setBusinessName(suggested);
    setFailure(null);
    setShowErrors(false);
  }

  function removeBusinessInfo() {
    setBusinessOpen(false);
    setBusinessName("");
    setBusinessDescription("");
    setShowErrors(false);
    setFailure(null);
  }

  function startOver() {
    latestRun.current += 1;
    failures.current = 0;
    setProfile(null);
    setAnalysis(null);
    setFailure(null);
    setConfirmingReset(false);
    setShowErrors(false);
  }

  // --- analyzing -------------------------------------------------------------
  if (analyzing) {
    return (
      <div role="status" className="flex flex-col items-start gap-3 py-2">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
          <Loader2 className="h-4 w-4 animate-spin text-brand-600" aria-hidden="true" />
          {businessOpen ? "Understanding your business…" : "Studying your website…"}
        </div>
        <p className="text-sm text-slate-500">
          {businessOpen
            ? "This usually takes a few seconds."
            : "We read your key pages and look for your logo, colours and fonts. This can take up to 30 seconds."}
        </p>
        <Button type="button" variant="outline" size="sm" onClick={cancel}>
          Cancel
        </Button>
      </div>
    );
  }

  // --- review ------------------------------------------------------------------
  if (profile) {
    const warnings = describeWarnings(analysis?.warnings ?? []);
    const set = (patch: Partial<CompanyProfile>) =>
      setProfile((current) => (current ? { ...current, ...patch } : current));
    const nameMissing = !profile.company_name.trim();
    const host = hostOf(analysis?.final_url ?? profile.url);
    return (
      <div className="space-y-4">
        {analysis ? (
          <Alert variant="success">
            {analysis.source === "WEBSITE"
              ? `We read ${analysis.pages_read} page${analysis.pages_read === 1 ? "" : "s"} of ${host || "your website"}. `
              : "Here's what we understood from your description. "}
            Check the details below and correct anything that&apos;s off.
          </Alert>
        ) : (
          <p className="text-sm text-slate-500">
            These are the company details saved for this campaign. Edit anything that has changed.
          </p>
        )}
        {warnings.map((text) => (
          <Alert key={text} variant="warning">
            {text}
          </Alert>
        ))}

        <Field label="Company name" required error={nameMissing ? "Enter your company name." : undefined}>
          <Input
            value={profile.company_name}
            maxLength={BUSINESS_LIMITS.nameMax}
            onChange={(event) => set({ company_name: event.target.value })}
          />
        </Field>
        <Field label="What your company does">
          <textarea
            className={textareaClass}
            value={profile.summary}
            maxLength={PROFILE_LIMITS.summary}
            onChange={(event) => set({ summary: event.target.value })}
          />
        </Field>
        <ChipList
          label="Products and services"
          values={profile.services}
          maxItems={PROFILE_LIMITS.services.items}
          maxChars={PROFILE_LIMITS.services.chars}
          onChange={(services) => set({ services })}
        />
        <ChipList
          label="Industries"
          values={profile.industries}
          maxItems={PROFILE_LIMITS.industries.items}
          maxChars={PROFILE_LIMITS.industries.chars}
          onChange={(industries) => set({ industries })}
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Who you sell to">
            <Input
              value={profile.audience}
              maxLength={PROFILE_LIMITS.audience}
              onChange={(event) => set({ audience: event.target.value })}
            />
          </Field>
          <Field label="Tone of voice">
            <Input
              value={profile.tone_of_voice}
              maxLength={PROFILE_LIMITS.tone}
              onChange={(event) => set({ tone_of_voice: event.target.value })}
            />
          </Field>
        </div>
        <ChipList
          label="Key messages"
          values={profile.key_messages}
          maxItems={PROFILE_LIMITS.keyMessages.items}
          maxChars={PROFILE_LIMITS.keyMessages.chars}
          onChange={(key_messages) => set({ key_messages })}
        />

        <div className="flex flex-wrap items-center justify-between gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => (analysis ? setConfirmingReset(true) : startOver())}
          >
            Use a different website or business info
          </Button>
          <Button
            type="button"
            disabled={nameMissing}
            onClick={() =>
              onConfirm({
                company: {
                  ...profile,
                  company_name: profile.company_name.trim(),
                  summary: profile.summary.trim(),
                  audience: profile.audience.trim(),
                  tone_of_voice: profile.tone_of_voice.trim(),
                },
                brand: analysis?.brand ?? null,
                suggestions: analysis?.suggestions ?? null,
                warnings: analysis?.warnings ?? [],
              })
            }
          >
            Continue
          </Button>
        </div>

        <ConfirmDialog
          open={confirmingReset}
          title="Start over?"
          description="This discards the analysis and any changes you made to these details."
          confirmLabel="Start over"
          tone="danger"
          onConfirm={startOver}
          onCancel={() => setConfirmingReset(false)}
        />
      </div>
    );
  }

  // --- input -------------------------------------------------------------------
  const websiteError =
    showErrors && !businessOpen && !websiteResult.ok ? websiteResult.message : undefined;
  const isWebsiteFailure = failure?.code?.startsWith("website_") ?? false;
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void analyze();
      }}
      className="space-y-4"
      aria-label="Company website or business information"
    >
      <h4 className="text-sm font-semibold text-slate-900">
        Enter your company website or business info
      </h4>
      <p className="text-sm text-slate-500">
        We use it to understand what you offer and, for an HTML email, to match your brand. You
        don&apos;t need a website: you can describe your business instead.
      </p>

      {failure ? (
        <Alert variant="error">
          <p>{failure.message}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button type="button" size="sm" variant="outline" onClick={() => void analyze()}>
              Try again
            </Button>
            {isWebsiteFailure ? (
              <Button type="button" size="sm" variant="outline" onClick={useBusinessInfoInstead}>
                Enter business info instead
              </Button>
            ) : null}
          </div>
          {failure.count >= 3 ? (
            <p className="mt-2 text-xs">
              Still not working? You can skip this step and write your objective and emails
              yourself.
            </p>
          ) : null}
        </Alert>
      ) : null}

      <Field label="Website" error={websiteError}>
        <Input
          type="text"
          inputMode="url"
          autoComplete="url"
          placeholder="acme.com"
          value={website}
          disabled={businessOpen}
          onChange={(event) => setWebsite(event.target.value)}
        />
      </Field>

      <div className="flex items-center gap-3" aria-hidden="true">
        <span className="h-px flex-1 bg-slate-200" />
        <span className="text-xs font-medium uppercase text-slate-500">or</span>
        <span className="h-px flex-1 bg-slate-200" />
      </div>

      {businessOpen ? (
        <div className="space-y-4 rounded-lg border border-slate-200 p-4" role="group" aria-label="Business information">
          <div className="flex items-center justify-between">
            <h5 className="text-sm font-semibold text-slate-900">Business information</h5>
            <button
              type="button"
              aria-label="Remove business information"
              onClick={removeBusinessInfo}
              className="rounded p-1 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
          <Field
            label="Company name"
            required
            error={showErrors ? businessProblems.name : undefined}
          >
            <Input
              placeholder="Enter name"
              value={businessName}
              maxLength={BUSINESS_LIMITS.nameMax + 20}
              onChange={(event) => setBusinessName(event.target.value)}
            />
          </Field>
          <div>
            <Field
              label="Description"
              required
              error={showErrors ? businessProblems.description : undefined}
            >
              <textarea
                className={textareaClass}
                placeholder="What does your company do, and who is it for?"
                value={businessDescription}
                onChange={(event) => setBusinessDescription(event.target.value)}
              />
            </Field>
            <p className="mt-1 text-right text-xs text-slate-500" aria-live="polite">
              {description.length} / {BUSINESS_LIMITS.descriptionMax}
            </p>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setBusinessOpen(true)}
          disabled={website.trim() !== ""}
          className="inline-flex items-center gap-1.5 text-sm font-medium text-slate-600 hover:text-slate-900 disabled:cursor-not-allowed disabled:text-slate-300"
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Add Business info
        </button>
      )}

      <div className="flex items-center justify-between gap-2">
        <Button type="button" variant="ghost" size="sm" onClick={onSkip}>
          Skip this step
        </Button>
        <Button type="submit" disabled={!hasInput}>
          Continue
        </Button>
      </div>
    </form>
  );
}
