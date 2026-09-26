import type { SequenceStep } from "@/types/domain";

// Waits are stored as whole minutes (backend: 1..525600, i.e. up to one year).
export const MAX_WAIT_MINUTES = 525_600;

export type DurationUnit = "minutes" | "hours" | "days";

const UNIT_MINUTES: Record<DurationUnit, number> = {
  minutes: 1,
  hours: 60,
  days: 1440,
};

export function toMinutes(value: number, unit: DurationUnit): number {
  return value * UNIT_MINUTES[unit];
}

// Largest unit that represents the duration exactly, so 2880 shows as "2 days"
// and 90 as "90 minutes" rather than silently rounding.
export function splitDuration(minutes: number): { value: number; unit: DurationUnit } {
  if (minutes >= 1440 && minutes % 1440 === 0) return { value: minutes / 1440, unit: "days" };
  if (minutes >= 60 && minutes % 60 === 0) return { value: minutes / 60, unit: "hours" };
  return { value: minutes, unit: "minutes" };
}

export function validateDuration(value: number, unit: DurationUnit): string | null {
  if (!Number.isFinite(value) || !Number.isInteger(value)) return "Enter a whole number.";
  if (value < 1) return "Wait must be at least 1.";
  if (toMinutes(value, unit) > MAX_WAIT_MINUTES) return "Wait can be at most 365 days.";
  return null;
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// 1500 -> "1 day 1 hour". Never rounds.
export function formatDuration(minutes: number): string {
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  const parts: string[] = [];
  if (days) parts.push(plural(days, "day"));
  if (hours) parts.push(plural(hours, "hour"));
  if (mins) parts.push(plural(mins, "minute"));
  return parts.length ? parts.join(" ") : "0 minutes";
}

export type StepTiming = {
  // Elapsed minutes from the first email to this step's earliest send time
  // (sum of the waits before it). Excludes sending-window delays.
  offsetMinutes: number;
  // "Day N" counted in 24h blocks after the first email (Day 1 = first email).
  day: number;
};

export function computeTimings(steps: SequenceStep[]): Map<string, StepTiming> {
  const timings = new Map<string, StepTiming>();
  let offset = 0;
  for (const step of steps) {
    if (step.kind === "WAIT") offset += step.wait_duration_minutes ?? 0;
    timings.set(step.id, { offsetMinutes: offset, day: 1 + Math.floor(offset / 1440) });
  }
  return timings;
}

// The WAIT that immediately precedes an email step, if any.
export function precedingWait(steps: SequenceStep[], step: SequenceStep): SequenceStep | null {
  const index = steps.findIndex((s) => s.id === step.id);
  const previous = index > 0 ? steps[index - 1] : null;
  return previous && previous.kind === "WAIT" ? previous : null;
}

// Whole-day wait that puts `step` on `targetDay`, given the day of the email
// before the preceding wait. Returns null when the target isn't after that day.
export function waitMinutesForDay(previousEmailDay: number, targetDay: number): number | null {
  if (!Number.isInteger(targetDay) || targetDay <= previousEmailDay) return null;
  const minutes = (targetDay - previousEmailDay) * 1440;
  return minutes > MAX_WAIT_MINUTES ? null : minutes;
}

// Structural problems the server's preflight would reject, shown early.
export function sequenceProblems(steps: SequenceStep[]): string[] {
  const problems: string[] = [];
  if (steps.length === 0) return problems;
  if (steps[0].kind !== "EMAIL") problems.push("The sequence must start with an email.");
  if (steps[steps.length - 1].kind !== "EMAIL") problems.push("The sequence must end with an email.");
  if (steps.some((s, i) => i > 0 && s.kind === steps[i - 1].kind)) {
    problems.push("Emails and waits must alternate.");
  }
  return problems;
}
