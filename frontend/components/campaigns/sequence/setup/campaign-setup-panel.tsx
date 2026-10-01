"use client";

import * as React from "react";
import { Check, Pencil } from "lucide-react";

import {
  EMPTY_OBJECTIVE,
  ObjectiveForm,
} from "@/components/campaigns/personalization/objective-form";
import { useUnsavedChangesGuard } from "@/components/campaigns/sequence/use-step-draft";
import {
  CompanyStep,
  type CompanyConfirmed,
} from "@/components/campaigns/sequence/setup/company-step";
import { DEFAULT_BRAND } from "@/components/campaigns/sequence/setup/setup-copy";
import { StyleStep } from "@/components/campaigns/sequence/setup/style-step";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type {
  BrandKit,
  CompanyAnalysisSuggestions,
  CompanyProfile,
  EmailFormat,
  PersonalizationConfig,
  PersonalizationState,
} from "@/types/domain";

type Step = "company" | "style" | "objective";

type Props = {
  workspaceId: string;
  campaignId: string;
  state: PersonalizationState;
  readOnly: boolean;
  // Whether the server can make AI calls. When false the panel is just the
  // manual objective form, exactly as before AI authoring existed.
  aiAvailable: boolean;
  saving: boolean;
  saveError: string | null;
  // Rejects on failure (the error is shown through `saveError`); resolves on save.
  onSave: (config: PersonalizationConfig) => Promise<unknown>;
};

const STEPS: { id: Step; label: string }[] = [
  { id: "company", label: "Your company" },
  { id: "style", label: "Email style" },
  { id: "objective", label: "Objective" },
];

/** The objective prefilled from what we learned, never overwriting the user's text. */
function seedObjective(
  saved: PersonalizationConfig | null,
  suggestions: CompanyAnalysisSuggestions | null,
  company: CompanyProfile | null,
): PersonalizationConfig {
  const base = { ...EMPTY_OBJECTIVE, ...(saved ?? {}) };
  const fill = (current: string, suggested: string | undefined) =>
    current.trim() ? current : (suggested ?? "");
  return {
    ...base,
    objective: fill(base.objective, suggestions?.objective),
    offer: fill(base.offer, suggestions?.offer),
    cta: fill(base.cta, suggestions?.cta),
    tone: fill(base.tone, suggestions?.tone || company?.tone_of_voice),
    target: fill(base.target, company?.audience),
  };
}

function hostOf(url: string | null | undefined) {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function Summary({
  config,
  onEdit,
  readOnly,
}: {
  config: PersonalizationConfig | null;
  onEdit: () => void;
  readOnly: boolean;
}) {
  const company = config?.company;
  const format = config?.email_format;
  const brand = config?.brand;
  return (
    <div className="space-y-3">
      <dl className="grid gap-3 text-sm sm:grid-cols-3">
        <div>
          <dt className="text-xs font-medium text-slate-500">Company</dt>
          <dd className="mt-0.5 text-slate-900">
            {company ? (
              <>
                {company.company_name}
                <span className="block text-xs text-slate-500">
                  {company.source === "WEBSITE"
                    ? hostOf(company.url) || "From website"
                    : "From business info"}
                </span>
              </>
            ) : (
              <span className="text-slate-400">Not added</span>
            )}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-slate-500">Email style</dt>
          <dd className="mt-0.5 flex items-center gap-2 text-slate-900">
            {format === "HTML" ? "HTML email" : format === "TEXT" ? "Text email" : (
              <span className="text-slate-400">Not chosen</span>
            )}
            {format === "HTML" && brand ? (
              <span className="inline-flex gap-1" aria-label="Brand colours">
                {[brand.primary, brand.accent].map((color) => (
                  <span
                    key={color}
                    className="h-4 w-4 rounded-full border border-slate-200"
                    style={{ backgroundColor: color }}
                  />
                ))}
              </span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-medium text-slate-500">Objective</dt>
          <dd className="mt-0.5 line-clamp-2 text-slate-900">
            {config?.objective || <span className="text-slate-400">Not set</span>}
          </dd>
        </div>
      </dl>
      {!company && config ? (
        <p className="text-xs text-slate-500">
          Add your website or business info and we can write the emails for you.
        </p>
      ) : null}
      {!readOnly ? (
        <Button type="button" variant="outline" size="sm" onClick={onEdit}>
          <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
          Edit setup
        </Button>
      ) : null}
    </div>
  );
}

/**
 * The guided setup on the Sequence tab for a hyper-personalized campaign:
 * company (website or typed business info) -> email style -> objective. Nothing is
 * saved until the last step, in one request.
 */
export function CampaignSetupPanel({
  workspaceId,
  campaignId,
  state,
  readOnly,
  aiAvailable,
  saving,
  saveError,
  onSave,
}: Props) {
  const saved = state.config;
  const [step, setStep] = React.useState<Step | null>(
    saved ? null : aiAvailable ? "company" : "objective",
  );
  const [reached, setReached] = React.useState<Step[]>(
    saved || !aiAvailable ? ["objective"] : ["company"],
  );

  const [company, setCompany] = React.useState<CompanyProfile | null>(saved?.company ?? null);
  const [format, setFormat] = React.useState<EmailFormat | null>(saved?.email_format ?? null);
  const [brand, setBrand] = React.useState<BrandKit>(saved?.brand ?? DEFAULT_BRAND);
  const [brandDetected, setBrandDetected] = React.useState(saved?.company?.source === "WEBSITE");
  const [brandWarnings, setBrandWarnings] = React.useState<string[]>([]);
  const [suggestions, setSuggestions] = React.useState<CompanyAnalysisSuggestions | null>(null);
  const [seed, setSeed] = React.useState<PersonalizationConfig | null>(saved);
  const [formKey, setFormKey] = React.useState(0);

  // Leaving with half-finished setup would lose it (nothing is saved until the end).
  useUnsavedChangesGuard(step === "style" || (step === "objective" && aiAvailable && !saved));

  function go(next: Step) {
    setReached((current) => (current.includes(next) ? current : [...current, next]));
    setStep(next);
  }

  function confirmCompany(result: CompanyConfirmed) {
    setCompany(result.company);
    setSuggestions(result.suggestions);
    if (result.brand) {
      setBrand(result.brand);
      setBrandDetected(true);
    } else {
      // Typed business info has no brand to read; keep anything already saved.
      if (!saved?.brand) setBrand(DEFAULT_BRAND);
      setBrandDetected(false);
    }
    setBrandWarnings(
      result.warnings.filter((w) => ["logo_not_found", "logo_svg_only", "colors_defaulted"].includes(w)),
    );
    go("style");
  }

  function skipCompany() {
    setCompany(null);
    setSuggestions(null);
    setBrandDetected(false);
    go("style");
  }

  function continueToObjective() {
    setSeed(seedObjective(saved, suggestions, company));
    setFormKey((key) => key + 1);
    go("objective");
  }

  async function save(config: PersonalizationConfig) {
    const full: PersonalizationConfig = {
      ...config,
      company: company ?? null,
      email_format: format ?? saved?.email_format ?? null,
      brand: format === "HTML" ? brand : null,
    };
    try {
      await onSave(full);
      setStep(null);
    } catch {
      // The failure is shown by the form through `saveError`; stay on this step.
    }
  }

  const collapsed = step === null;
  return (
    <section
      id="objective"
      aria-label="Campaign setup"
      className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-card"
    >
      <div>
        <h3 className="text-base font-semibold text-slate-900">Campaign setup</h3>
        <p className="mt-1 text-sm text-slate-500">
          {aiAvailable
            ? "Tell us about your company and what you want to achieve. We write the emails for you, and each lead gets a version written for them."
            : "Tell us what you are trying to achieve. Every email is written from this and your reference emails."}
        </p>
      </div>

      {!aiAvailable && !readOnly ? (
        <Alert variant="info">
          AI drafting isn&apos;t available in this environment, so you&apos;ll write the objective and
          the emails yourself.
        </Alert>
      ) : null}

      {collapsed ? (
        <Summary
          config={saved}
          readOnly={readOnly}
          onEdit={() => {
            setSeed(saved);
            setFormKey((key) => key + 1);
            setReached(aiAvailable ? ["company", "style", "objective"] : ["objective"]);
            setStep(aiAvailable ? "company" : "objective");
          }}
        />
      ) : (
        <>
          {aiAvailable ? (
            <ol className="flex flex-wrap items-center gap-2 text-xs" aria-label="Setup steps">
              {STEPS.map((item, index) => {
                const isCurrent = step === item.id;
                const canJump = reached.includes(item.id) && !isCurrent;
                return (
                  <li key={item.id} className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={!canJump}
                      aria-current={isCurrent ? "step" : undefined}
                      onClick={() => setStep(item.id)}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium",
                        isCurrent
                          ? "bg-brand-50 text-brand-700"
                          : reached.includes(item.id)
                            ? "text-slate-700 hover:bg-slate-100"
                            : "text-slate-400",
                      )}
                    >
                      <span className="flex h-4 w-4 items-center justify-center rounded-full bg-slate-100 text-[10px] text-slate-600">
                        {reached.includes(item.id) && !isCurrent ? (
                          <Check className="h-3 w-3" aria-hidden="true" />
                        ) : (
                          index + 1
                        )}
                      </span>
                      {item.label}
                    </button>
                    {index < STEPS.length - 1 ? (
                      <span className="text-slate-300" aria-hidden="true">
                        /
                      </span>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          ) : null}

          {step === "company" ? (
            <CompanyStep
              workspaceId={workspaceId}
              campaignId={campaignId}
              initialCompany={company}
              onConfirm={confirmCompany}
              onSkip={skipCompany}
            />
          ) : null}

          {step === "style" ? (
            <StyleStep
              workspaceId={workspaceId}
              campaignId={campaignId}
              format={format}
              onFormat={setFormat}
              brand={brand}
              onBrand={setBrand}
              companyName={company?.company_name ?? ""}
              siteUrl={company?.url ?? null}
              brandDetected={brandDetected}
              brandWarnings={brandWarnings}
              onBack={() => setStep("company")}
              onContinue={continueToObjective}
            />
          ) : null}

          {step === "objective" ? (
            <div className="space-y-3">
              {suggestions && (suggestions.objective || suggestions.offer || suggestions.cta) ? (
                <Alert variant="info">
                  We suggested these from what we learned about your company. Check them and edit
                  anything that isn&apos;t right.
                </Alert>
              ) : null}
              <ObjectiveForm
                key={formKey}
                initial={seed}
                readOnly={false}
                saving={saving}
                error={saveError}
                onSave={(config) => void save(config)}
                saveLabel={aiAvailable ? "Save setup" : "Save objective"}
                alwaysAllowSave={aiAvailable}
              />
              {aiAvailable ? (
                <div>
                  <Button type="button" variant="ghost" size="sm" onClick={() => setStep("style")}>
                    Back
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}
