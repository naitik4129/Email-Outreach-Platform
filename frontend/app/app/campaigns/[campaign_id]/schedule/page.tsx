"use client";

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api-client";
import { createCampaignSettings, listCampaignSettings } from "@/lib/campaigns-api";
import { canDraftCampaign } from "@/lib/permissions";
import { useWorkspace } from "@/lib/workspace-context";

const WEEKDAYS: { value: number; label: string }[] = [
  { value: 1, label: "Mon" },
  { value: 2, label: "Tue" },
  { value: 3, label: "Wed" },
  { value: 4, label: "Thu" },
  { value: 5, label: "Fri" },
  { value: 6, label: "Sat" },
  { value: 7, label: "Sun" },
];

function errorMessage(error: unknown) {
  if (error instanceof ApiError) return error.message;
  return "We couldn't complete that request. Please try again.";
}

function supportedTimezones(): string[] {
  // Standard runtime source of truth -- avoids hand-maintaining an
  // incomplete/stale IANA zone list in this repo.
  try {
    if (typeof Intl.supportedValuesOf === "function") {
      return Intl.supportedValuesOf("timeZone");
    }
  } catch {
    // fall through
  }
  return [
    "UTC",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Kolkata",
    "Asia/Singapore",
    "Asia/Tokyo",
    "Australia/Sydney",
  ];
}

export default function CampaignSchedulePage() {
  const params = useParams<{ campaign_id: string }>();
  const campaignId = params.campaign_id;
  const queryClient = useQueryClient();
  const { activeWorkspaceId, activeWorkspace } = useWorkspace();
  const mayDraft = canDraftCampaign(activeWorkspace?.role_code);
  const timezones = useMemo(supportedTimezones, []);

  const [actionError, setActionError] = useState<string | null>(null);
  const [timezone, setTimezone] = useState("UTC");
  const [weekdays, setWeekdays] = useState<Set<number>>(new Set([1, 2, 3, 4, 5]));
  const [windowStart, setWindowStart] = useState("09:00");
  const [windowEnd, setWindowEnd] = useState("17:00");
  const [dailyLimit, setDailyLimit] = useState("50");

  const settingsKey = [
    "workspace",
    activeWorkspaceId,
    "campaigns",
    campaignId,
    "settings",
  ];

  const settingsQuery = useQuery({
    queryKey: settingsKey,
    queryFn: () =>
      activeWorkspaceId && campaignId
        ? listCampaignSettings(activeWorkspaceId, campaignId)
        : Promise.reject(new Error("No active workspace")),
    enabled: Boolean(activeWorkspaceId && campaignId),
  });

  const createMutation = useMutation({
    mutationFn: () => {
      if (!activeWorkspaceId || !campaignId) throw new Error("Not ready");
      return createCampaignSettings(activeWorkspaceId, campaignId, {
        timezone,
        weekdays: Array.from(weekdays).sort((a, b) => a - b),
        window_start_local: `${windowStart}:00`,
        window_end_local: `${windowEnd}:00`,
        daily_limit: dailyLimit.trim() ? Number(dailyLimit) : null,
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: settingsKey });
    },
    onError: (err) => setActionError(errorMessage(err)),
  });

  const current = settingsQuery.data?.[0] ?? null;

  return (
    <div className="max-w-xl space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Schedule</h2>
        <p className="text-sm text-slate-500">
          Sending days, hours, and daily limit. Actual send timestamps are
          resolved at activation, respecting DST.
        </p>
      </div>

      {actionError && <Alert variant="error">{actionError}</Alert>}

      {current && (
        <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-sm">
          <h3 className="font-semibold text-slate-900">Current schedule</h3>
          <dl className="mt-2 space-y-1 text-slate-600">
            <div>Timezone: {current.timezone}</div>
            <div>
              Days:{" "}
              {WEEKDAYS.filter((d) => current.weekdays.includes(d.value))
                .map((d) => d.label)
                .join(", ")}
            </div>
            <div>
              Window: {current.window_start_local.slice(0, 5)}&ndash;
              {current.window_end_local.slice(0, 5)}
            </div>
            <div>Daily limit: {current.daily_limit ?? "Unlimited"}</div>
          </dl>
        </div>
      )}

      {mayDraft && (
        <form
          className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm"
          onSubmit={(e) => {
            e.preventDefault();
            if (weekdays.size === 0) {
              setActionError("Select at least one sending day.");
              return;
            }
            setActionError(null);
            createMutation.mutate();
          }}
        >
          <h3 className="text-sm font-semibold text-slate-900">
            {current ? "Update schedule" : "Configure schedule"}
          </h3>

          <Field label="Timezone" required>
            <select
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
            >
              {timezones.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Sending days" required>
            <div className="flex flex-wrap gap-2">
              {WEEKDAYS.map((d) => (
                <button
                  key={d.value}
                  type="button"
                  onClick={() => {
                    const next = new Set(weekdays);
                    if (next.has(d.value)) next.delete(d.value);
                    else next.add(d.value);
                    setWeekdays(next);
                  }}
                  className={`rounded-md border px-3 py-1.5 text-sm font-medium ${
                    weekdays.has(d.value)
                      ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                      : "border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </Field>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Window start" required>
              <Input
                type="time"
                value={windowStart}
                onChange={(e) => setWindowStart(e.target.value)}
              />
            </Field>
            <Field label="Window end" required>
              <Input
                type="time"
                value={windowEnd}
                onChange={(e) => setWindowEnd(e.target.value)}
              />
            </Field>
          </div>
          <p className="-mt-2 text-xs text-slate-500">
            Same-day window only; overnight windows aren&apos;t supported.
          </p>

          <Field label="Daily send limit">
            <Input
              type="number"
              min={1}
              value={dailyLimit}
              onChange={(e) => setDailyLimit(e.target.value)}
            />
          </Field>

          <div className="flex justify-end">
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending && (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              )}
              Save schedule
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
